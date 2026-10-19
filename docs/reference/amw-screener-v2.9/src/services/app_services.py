"""Shared application services used by both the Streamlit UI and the FastAPI backend."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from ..config import ROOT, default_settings, merge, validate_settings
from ..data.provider import DiskCache, YFinanceProvider
from ..data.universe import custom_universe, load_universe
from ..screener.scanner import ScanResult, run_scan
from ..storage.db import Store, default_url, utcnow
from ..strategies.registry import enabled_strategies
from .alerts import process_alerts
from .jobs import JobManager
from .lifecycle import update_lifecycle

log = logging.getLogger(__name__)


class Services:
    def __init__(self, root: Path = ROOT, db_url: str | None = None, provider=None, ai_transport=None, feed_transport=None):
        self.root = root
        (root / "data").mkdir(exist_ok=True)
        from ..lab.sandbox import protect_app_dirs
        if db_url is None:
            protect_app_dirs()
        self.store = Store(db_url or default_url(root))
        self.jobs = JobManager(max_workers=1, store=self.store)
        self.base_cfg = default_settings()
        self._provider = provider
        self.last_scan: ScanResult | None = None
        self.local_user = self.store.ensure_local_user()
        try:                                   # work interrupted by a previous crash/restart becomes visibly FAILED
            self.store.recover_orphans(utcnow())
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("orphan recovery failed")
        from ..ai.gateway import AIGateway, SessionKeys
        from ..ai.redact import install_log_redaction
        install_log_redaction()
        self.ai = AIGateway(self.store, transport=ai_transport, session_keys=SessionKeys())
        from ..feed.collector import FeedManager
        self.feed = FeedManager(self, transport=feed_transport)
        from .scheduler import Scheduler
        self.scheduler = Scheduler(self)
        if os.environ.get("AMW_SCHEDULER", "0") == "1":
            self.scheduler.start()
        if os.environ.get("AMW_FEED_AUTORESUME", "0") == "1":
            try:
                self.feed.resume_all()          # the database lease keeps it to one collector per user across processes
            except Exception:  # noqa: BLE001 - never block start-up on a broker feed
                logging.getLogger(__name__).exception("feed auto-resume failed")

    # ---- configuration (YAML defaults + user overrides + active strategy versions from DB)
    def cfg(self, user_id: int | None = None) -> dict:
        uid = user_id or self.local_user
        cfg = merge(self.base_cfg, self.store.get_pref(uid, "settings_override", {}) or {})
        for name, sc in self.store.active_strategy_configs().items():
            if name in cfg["strategies"]:
                cfg["strategies"][name] = merge(cfg["strategies"][name], sc)
        validate_settings(cfg)
        return cfg

    def save_override(self, override: dict, user_id: int | None = None) -> None:
        uid = user_id or self.local_user
        validate_settings(merge(self.base_cfg, override))
        self.store.set_pref(uid, "settings_override", override)
        self.store.audit("settings", f"settings override saved by user {uid}")

    def provider(self, cfg: dict | None = None):
        if self._provider is not None:
            return self._provider
        cfg = cfg or self.cfg()
        if os.environ.get("AMW_PROVIDER") == "fixture":
            from ..data.fixture_provider import FixtureProvider
            self._provider = FixtureProvider()
            return self._provider
        if not hasattr(self, "_yf"):
            self._yf = YFinanceProvider(cfg, DiskCache(self.root / cfg["data"]["cache_dir"], cfg["data"]["ttl_minutes"]))
        return self._yf

    def provider_for(self, user_id: int | None):
        """The user's chosen data source: their own FYERS account when selected and logged in, else the default."""
        uid = user_id or self.local_user
        if self.store.get_pref(uid, "data_source", "default") == "fyers":
            p = self.feed.provider_for(uid)
            if p is not None:
                return p
        return self.provider(self.cfg(uid))

    def data_source_status(self, user_id: int | None) -> dict:
        """What scans for this user will actually use. A FYERS selection without today's login falls back to the
        default provider; the fallback is reported explicitly (and every signal record stamps the real provider)."""
        uid = user_id or self.local_user
        sel = self.store.get_pref(uid, "data_source", "default")
        p = self.provider_for(uid)
        fb = sel == "fyers" and not isinstance(p, _fyers_provider_cls())
        return {"selected": sel, "active": p.name, "data_kind": getattr(p, "data_kind", None), "fallback": fb,
                "reason": "FYERS not logged in today (sessions expire daily) - log in on the Live Data page" if fb else None}

    def universe(self, name: str, custom_text: str = "", force: bool = False, user_id: int | None = None):
        cfg = self.cfg(user_id)
        if name == "Custom":
            return custom_universe(custom_text)
        if name.startswith("Watchlist: "):
            wl = {w["name"]: w for w in self.store.watchlists(user_id or self.local_user)}.get(name[len("Watchlist: "):])
            return custom_universe(" ".join(wl["symbols"]) if wl else "", name)
        uni = load_universe(name, cfg, self.root, force_refresh=force)
        if not uni.is_fallback and uni.count:
            try:                                   # keep the instrument master in sync with official lists
                from ..data.instruments import upsert_universe
                upsert_universe(self.store, uni.symbols, uni.source)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("instrument master update failed")
        return uni

    # ---- scan job
    def start_scan(self, universe_df: pd.DataFrame, universe_name: str, strategies: list[str] | None = None,
                   limit: int | None = None, resume: ScanResult | None = None, user_id: int | None = None):
        cfg = self.cfg(user_id)
        owner = user_id or self.local_user
        strats = enabled_strategies(cfg, strategies)
        if not strats:
            raise ValueError("no enabled strategy selected")
        df = universe_df.head(limit) if limit else universe_df
        from ..saas.entitlements import check
        check(self.store, owner, "symbols_per_scan", len(df), cfg)
        check(self.store, owner, "concurrent_jobs", len(self.jobs.active(owner)) + 1, cfg)

        def job_fn(progress, should_stop, job):
            started = utcnow()
            partial = resume or ScanResult(universe=universe_name, started=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S UTC"))
            job.partial = partial

            def on_batch(rows, latest):
                live = [r for r in rows if r["status"] in ("qualified", "candidate")]
                self.store.record_signals(live, owner)
                from .forward import enroll
                enroll(self.store, live, owner)                 # paper forward test of each qualified signal
                process_alerts(self.store, live, owner)
                changes = update_lifecycle(self.store, rows, latest, owner)
                if changes:
                    from .notify_events import on_lifecycle
                    on_lifecycle(self.store, owner, changes)

            res = run_scan(df, cfg, self.provider_for(owner), strats, progress=progress, should_stop=should_stop,
                           resume=partial, universe_name=universe_name, on_batch=on_batch)
            job.partial = res
            self.last_scan = res
            self.store.log_scan(user_id=owner, started=started, finished=utcnow(), universe=universe_name, symbols=res.symbols_done,
                                signals=len(res.signals), qualified=sum(s["status"] == "qualified" for s in res.signals),
                                status="stopped" if res.stopped_early else "done", detail=",".join(res.strategies))
            return res

        return self.jobs.submit("scan", job_fn, owner=user_id or self.local_user)

    def health(self) -> dict:
        p = self.provider()
        try:
            db = {"backend": self.store.backend, "ping_ms": self.store.ping(), "schema_version": self.store.schema_version(), "ok": True}
        except Exception as e:  # noqa: BLE001
            db = {"backend": self.store.backend, "ok": False, "error": type(e).__name__}
        return {"database": db, "provider": p.health(), "jobs": self.jobs.list()[:10], "time_utc": str(utcnow())}


def _fyers_provider_cls():
    from ..feed.fyers import FyersHistoryProvider
    return FyersHistoryProvider
