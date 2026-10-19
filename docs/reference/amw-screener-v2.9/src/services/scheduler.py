"""Scheduler for owner-scoped scheduled scans and scheduled scan reports.

* Schedules are rows in `schedules` (user-owned). `kind`: `scan` (alerts fire through the normal alert path) or
  `scan_report` (scan, then PDF reports for the top qualified signals; delivered to the user's linked Telegram).
* Timing in the exchange timezone: `at_time` "HH:MM" once per day, or `interval_minutes` within market hours.
  `trading_days_only` skips weekends and configured exchange holidays.
* The tick is driven by an injectable clock (tests) or a daemon thread (`start()`); a schedule never runs twice for the
  same slot, and a run is skipped while the previous run of the same schedule is still active.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

import pandas as pd

from ..data.validation import is_trading_day
from ..storage.db import utcnow

log = logging.getLogger(__name__)
KINDS = ("scan", "scan_report", "scanner")     # scanner = a saved universal scanner (published version)


def _local(now_utc: datetime, tz: str) -> pd.Timestamp:
    return pd.Timestamp(now_utc).tz_localize("UTC").tz_convert(tz)


def validate_schedule(store, user_id: int, kind: str, at_time: str | None, interval_minutes: int | None, cfg: dict,
                      config: dict | None = None) -> None:
    from ..saas.entitlements import check
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if bool(at_time) == bool(interval_minutes):
        raise ValueError("set exactly one of at_time (HH:MM) or interval_minutes")
    if at_time:
        try:
            h, m = map(int, at_time.split(":"))
            assert 0 <= h < 24 and 0 <= m < 60 and len(at_time) == 5
        except (ValueError, AssertionError):
            raise ValueError("at_time must be HH:MM")
    if interval_minutes:
        check(store, user_id, "min_schedule_interval_min", int(interval_minutes), cfg)
    check(store, user_id, "schedules", len(store.schedule_list(user_id)) + 1, cfg)
    if kind == "scanner":
        sid = (config or {}).get("scanner_id")
        if not sid or store.lab_get(user_id, sid) is None:
            raise ValueError("scanner schedule needs config.scanner_id of one of your scanners")
        if not store.lab_versions(user_id, sid):
            raise ValueError("publish a scanner version first (drafts are never scheduled)")


def slot_due(sch: dict, now_utc: datetime, market: dict) -> str | None:
    """Return a slot id if the schedule is due at `now_utc`, else None."""
    if not sch.get("enabled"):
        return None
    loc = _local(now_utc, market.get("timezone", "Asia/Kolkata"))
    if sch.get("trading_days_only", True) and not is_trading_day(loc, market.get("holidays", [])):
        return None
    hm = loc.strftime("%H:%M")
    last = sch.get("last_run")
    last_loc = _local(last, market.get("timezone", "Asia/Kolkata")) if last else None
    if sch.get("at_time"):
        if hm < sch["at_time"]:
            return None
        slot = f"{loc.date()}@{sch['at_time']}"
        if last_loc is not None and last_loc.date() == loc.date() and last_loc.strftime("%H:%M") >= sch["at_time"]:
            return None
        return slot
    iv = int(sch["interval_minutes"])
    o, c = market.get("session_open", "09:15"), market.get("session_close", "15:30")
    if not (o <= hm <= c):
        return None
    if last_loc is not None and (loc - last_loc).total_seconds() < iv * 60 - 1:
        return None
    return f"{loc.strftime('%Y-%m-%d %H:%M')}"


class Scheduler:
    def __init__(self, svc, clock=utcnow):
        self.svc = svc
        self.clock = clock
        self.running: dict[int, object] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def daily_maintenance(self, now: datetime) -> dict | None:
        """Once per day after the close (exchange time): mark paper forward tests, apply report retention."""
        market = self.svc.base_cfg["market"]
        loc = _local(now, market.get("timezone", "Asia/Kolkata"))
        if loc.strftime("%H:%M") < "15:45" or getattr(self, "_maint_day", None) == loc.date():
            return None
        self._maint_day = loc.date()
        from ..reports.builder import apply_retention
        from .forward import mark
        out = {"forward": {}, "retention": None}
        for uid in self.svc.store.forward_users():
            try:
                out["forward"][uid] = mark(self.svc, uid)
            except Exception as e:  # noqa: BLE001
                out["forward"][uid] = {"error": type(e).__name__}
        try:
            out["retention"] = apply_retention(self.svc, now)
        except Exception:  # noqa: BLE001
            log.exception("report retention failed")
        from .notify_events import daily_summary_text, notify
        day = str(loc.date())
        out["summaries"] = 0
        for u in self.svc.store.list_users():
            try:
                txt = daily_summary_text(self.svc, u["id"], day)
                if txt:
                    out["summaries"] += bool(notify(self.svc.store, u["id"], "daily_summary", f"daily:{day}", txt))
            except Exception:  # noqa: BLE001
                log.exception("daily summary failed for user %s", u.get("id"))
        return out

    def tick(self) -> list[dict]:
        now = self.clock()
        out = []
        try:
            self.daily_maintenance(now)
        except Exception:  # noqa: BLE001
            log.exception("daily maintenance failed")
        for sch in self.svc.store.schedule_list(None):
            job = self.running.get(sch["id"])
            if job is not None and job.status in ("queued", "running"):
                continue
            slot = slot_due(sch, now, self.svc.cfg(sch["user_id"])["market"])
            if not slot:
                continue
            if not self.svc.store.schedule_claim(sch["id"], sch.get("last_run"), now):
                continue                                   # another AMW process claimed this slot first
            try:
                job = self._launch(sch)
                self.running[sch["id"]] = job
                out.append({"schedule": sch["id"], "slot": slot, "job": job.id})
            except Exception as e:  # noqa: BLE001 - one bad schedule must not stop others
                self.svc.store.schedule_update(sch["user_id"], sch["id"], last_status="FAILED", last_error=str(e)[:300])
                out.append({"schedule": sch["id"], "slot": slot, "error": str(e)})
        return out

    def _launch(self, sch: dict):
        svc, uid, conf = self.svc, sch["user_id"], sch["config"] or {}
        uni = svc.universe(conf.get("universe", "Custom"), conf.get("symbols", ""), user_id=uid)
        if sch["kind"] == "scanner":
            from ..lab.service import start_lab_scan
            job, _ = start_lab_scan(svc, uid, conf["scanner_id"], conf.get("version"), uni.symbols, f"schedule:{sch['name']}")
            threading.Thread(target=self._after_scan, args=(sch, job), daemon=True).start()
            return job
        job = svc.start_scan(uni.symbols, f"schedule:{sch['name']}", conf.get("strategies"), conf.get("limit"), user_id=uid)
        if sch["kind"] == "scan_report":
            threading.Thread(target=self._after_report, args=(sch, job), daemon=True).start()
        else:
            threading.Thread(target=self._after_scan, args=(sch, job), daemon=True).start()
        return job

    def _wait(self, job):
        while job.status in ("queued", "running") and not self._stop.wait(0.5):
            pass

    def _after_scan(self, sch, job):
        self._wait(job)
        self.svc.store.schedule_update(sch["user_id"], sch["id"], last_status=job.status.upper(), last_error=job.error)

    def _after_report(self, sch, job):
        from ..reports import builder
        from ..services.alerts import telegram_client_for
        self._wait(job)
        uid, n = sch["user_id"], int((sch["config"] or {}).get("top_n", 3))
        if job.status != "done":
            self.svc.store.schedule_update(uid, sch["id"], last_status=job.status.upper(), last_error=job.error)
            return
        res = job.result
        keys = [self.svc.store.scoped_key(uid, r["key"]) for r in sorted(res.signals, key=lambda r: -r["score"])
                if r["status"] == "qualified"][:n]
        errors, sent = [], 0
        tc = telegram_client_for(self.svc.store, uid)
        for k in keys:
            try:
                name, data, _ = builder.generate(self.svc, uid, k, "pdf", "standard")
                if tc:
                    tc[0].send_document(tc[1], name, data, "AMW scheduled report (analysis only)")
                    sent += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{k[:8]}: {type(e).__name__}")
        status = "COMPLETED" if not errors else "PARTIALLY_COMPLETED"
        self.svc.store.schedule_update(uid, sch["id"], last_status=f"{status} ({len(keys)} reports, {sent} sent)",
                                       last_error="; ".join(errors)[:300] or None)

    def start(self, every_s: float = 30.0) -> None:
        if self._thread:
            return

        def loop():
            while not self._stop.wait(every_s):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("scheduler tick failed")
        self._thread = threading.Thread(target=loop, name="amw-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
