"""In-process background job manager (bounded workers, progress, cooperative cancellation, graceful shutdown).

Chosen over Celery/Redis because the app is single-user/single-process; a broker adds operational cost
without benefit here. Jobs survive Streamlit reruns because the manager is process-global.
Partial scan results are kept on the job so a cancelled scan can be resumed.

Durability (v2.5): when a Store is attached every job is also recorded in `job_runs` (status, progress, error,
holder, heartbeat). A heartbeat thread refreshes running jobs; on start-up `Store.recover_orphans` marks jobs whose
process died as FAILED with a visible reason, so no run disappears silently. Jobs started by another process (UI vs
API) are visible read-only through their database record.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    owner: int | None = None          # user id; None = system job
    status: str = "queued"           # queued | running | done | failed | cancelled
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "owner": self.owner, "status": self.status, "progress": round(self.progress, 3),
                "message": self.message, "error": self.error, "created": self.created, "started": self.started,
                "finished": self.finished}


HEARTBEAT_S = 30.0
MAX_API_ATTEMPTS = 3            # background API jobs: transient failures (I/O, timeouts, locked DB) are retried
RETRY_BACKOFF_S = 1.0


def _transient_types():
    import sqlite3
    out = [TimeoutError, ConnectionError, sqlite3.OperationalError]
    try:
        import requests
        out += [requests.Timeout, requests.ConnectionError]
    except ImportError:  # pragma: no cover
        pass
    try:
        from sqlalchemy.exc import OperationalError
        out.append(OperationalError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(out)


TRANSIENT = _transient_types()


def _ts(t: float | None):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).replace(tzinfo=None) if t else None


def _epoch(d) -> float | None:
    from datetime import timezone
    return d.replace(tzinfo=timezone.utc).timestamp() if d else None


class JobManager:
    def __init__(self, max_workers: int = 1, store=None):
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="amw-job")
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.store = store
        self.holder = f"{socket.gethostname()[:40]}:{os.getpid()}"
        self._stop = threading.Event()
        self._last_persist: dict[str, float] = {}
        if store is not None:
            threading.Thread(target=self._beat, name="amw-job-heartbeat", daemon=True).start()

    # ---- persistence (never lets a database problem break the job itself)
    def _persist(self, job: Job, **extra) -> None:
        if self.store is None:
            return
        try:
            from ..storage.db import utcnow
            extra.setdefault("status", job.status)
            self.store.job_record(job.id, user_id=job.owner, kind=job.kind, progress=float(job.progress),
                                  message=(job.message or "")[:500], error=(job.error or None) and job.error[:2000],
                                  holder=self.holder, heartbeat=utcnow(), started=_ts(job.started),
                                  finished=_ts(job.finished), **extra)
            self._last_persist[job.id] = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("job record write failed (job continues)")

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            for j in self.active():
                self._persist(j)

    def submit(self, kind: str, fn: Callable[..., Any], owner: int | None = None, params: dict | None = None,
               **kwargs) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, owner=owner)
        with self.lock:
            self.jobs[job.id] = job
        self._persist(job, params=params or {})

        def progress(p: float, msg: str = "") -> None:
            job.progress, job.message = p, msg
            if time.monotonic() - self._last_persist.get(job.id, 0) >= 5:
                self._persist(job)

        def run():
            job.status, job.started = "running", time.time()
            self._persist(job)
            final, err = "done", None
            try:
                job.result = fn(progress=progress, should_stop=job.cancel_event.is_set, job=job, **kwargs)
                final = "cancelled" if job.cancel_event.is_set() else "done"
            except Exception as e:  # noqa: BLE001
                final, err = "failed", f"{type(e).__name__}: {e}"
                log.error("job %s failed: %s", job.id, traceback.format_exc())
            finally:
                # record first, then publish: nobody may observe a final status whose record is not written yet
                job.error, job.finished = err, time.time()
                self._persist(job, status=final)
                job.status = final

        self.pool.submit(run)
        return job

    def submit_api(self, kind: str, owner: int, fn: Callable[[], dict], params: dict | None = None) -> Job:
        """Background variant of a synchronous API action: the JSON result is stored in job_runs.result so any
        process (dashboard or API) can return it (max 2 MB; larger results keep only the job status)."""
        def run(progress, should_stop, job):
            res = None
            for attempt in range(1, MAX_API_ATTEMPTS + 1):        # bounded retries for TRANSIENT errors only
                try:
                    progress(0.0, f"attempt {attempt}/{MAX_API_ATTEMPTS}")
                    res = fn()
                    break
                except TRANSIENT as e:
                    log.warning("job %s attempt %s transient error: %s", job.id, attempt, type(e).__name__)
                    if attempt == MAX_API_ATTEMPTS or should_stop():
                        raise
                    time.sleep(RETRY_BACKOFF_S * attempt)
            job.message = f"done after {attempt} attempt(s)"
            if self.store is not None:
                import json
                txt = json.dumps(res, default=str)
                self.store.job_record(job.id, result=txt if len(txt) <= 2_000_000 else json.dumps(
                    {"truncated": True, "bytes": len(txt)}))
            return res
        return self.submit(kind, run, owner=owner, params=params)

    @staticmethod
    def _from_record(r: dict) -> Job:
        j = Job(id=r["id"], kind=r["kind"], owner=r["user_id"], status=r["status"], progress=r["progress"] or 0.0,
                message=r["message"] or "", error=r["error"], created=_epoch(r["created"]) or 0.0,
                started=_epoch(r["started"]), finished=_epoch(r["finished"]))
        j.remote = True            # started by another process (or before a restart): read-only
        if r.get("result"):
            import json
            j.result = json.loads(r["result"])
        return j

    def get(self, job_id: str, owner: int | None = None, admin: bool = False) -> Job | None:
        """With `owner` given, returns the job only if it belongs to that user (admins see all)."""
        j = self.jobs.get(job_id)
        if j is None and self.store is not None:
            try:
                r = self.store.job_run_get(job_id)
            except Exception:  # noqa: BLE001
                r = None
            j = self._from_record(r) if r else None
        if j is None or owner is None or admin or j.owner == owner:
            return j
        return None

    def cancel(self, job_id: str, owner: int | None = None, admin: bool = False) -> bool:
        j = self.get(job_id, owner, admin)
        if j and j.status in ("queued", "running"):
            j.cancel_event.set()
            return True
        return False

    def list(self, owner: int | None = None, admin: bool = False, limit: int = 50) -> list[dict]:
        js = {j.id: j for j in self.jobs.values() if owner is None or admin or j.owner == owner}
        if self.store is not None:
            try:
                for r in self.store.job_runs_list(None if (owner is None or admin) else owner, limit):
                    js.setdefault(r["id"], self._from_record(r))
            except Exception:  # noqa: BLE001
                log.exception("job history read failed")
        return [j.public() for j in sorted(js.values(), key=lambda j: -j.created)][:limit]

    def active(self, owner: int | None = None) -> list[Job]:
        return [j for j in self.jobs.values() if j.status in ("queued", "running") and (owner is None or j.owner == owner)]

    def shutdown(self) -> None:
        self._stop.set()
        for j in self.active():
            j.cancel_event.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
