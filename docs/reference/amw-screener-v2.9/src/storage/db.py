"""Persistence (SQLAlchemy Core). Default SQLite file; set DATABASE_URL for PostgreSQL.

Tables: preferences, watchlists, presets, strategy_configs (versioned), signals (dedup by key),
alert_rules, alert_events (dedup per rule+signal+channel), journal (paper trades), scan_runs, audit_log.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table,
                        Text, UniqueConstraint, CheckConstraint, LargeBinary, and_, create_engine, delete, func, insert, or_,
                        select, update)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

SCHEMA_VERSION = 13  # 1 core, 2 strategy lab, 3 BYOK AI, 4 signal/scan ownership, 5 reports + schedules, 6 broker feed,
#                      7 durable job records, shared rate buckets, forward tests, 8 system notification events,
#                      9 index signals(user_id, first_seen) for signal lists (PERF P-001), 10 job_runs.result,
#                      11 instrument master, 12 immutable original signal fields (A-005),
#                      13 reports.legal_hold (Q-005)
md = MetaData()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


schema_version = Table("schema_version", md, Column("version", Integer, primary_key=True), Column("applied_at", DateTime))
users = Table("users", md, Column("id", Integer, primary_key=True), Column("username", String(60), unique=True, nullable=False),
              Column("role", String(20), nullable=False, default="analyst"), Column("token_hash", String(64), unique=True),
              Column("plan", String(20), default="free"), Column("created", DateTime, default=utcnow))
preferences = Table("preferences", md, Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
                    Column("key", String(100), primary_key=True), Column("value", JSON))
watchlists = Table("watchlists", md, Column("id", Integer, primary_key=True),
                   Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                   Column("name", String(100), nullable=False), Column("symbols", JSON, nullable=False),
                   Column("created", DateTime, default=utcnow), UniqueConstraint("user_id", "name"))
presets = Table("presets", md, Column("id", Integer, primary_key=True),
                Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                Column("name", String(100), nullable=False),
                Column("kind", String(20), nullable=False), Column("payload", JSON, nullable=False), Column("created", DateTime, default=utcnow),
                UniqueConstraint("user_id", "name", "kind"))
strategy_configs = Table("strategy_configs", md, Column("id", Integer, primary_key=True), Column("strategy", String(40), nullable=False),
                         Column("version", Integer, nullable=False), Column("config", JSON, nullable=False), Column("note", Text),
                         Column("active", Boolean, default=False), Column("created", DateTime, default=utcnow),
                         UniqueConstraint("strategy", "version"))
signals = Table("signals", md, Column("key", String(32), primary_key=True),
                Column("user_id", Integer, index=True), Column("canonical_key", String(32), index=True),
                Column("ticker", String(30), nullable=False, index=True),
                Column("strategy", String(40), index=True), Column("strategy_version", Integer), Column("setup", String(120)),
                Column("timeframe", String(5)), Column("direction", String(10)), Column("signal_time", String(20)),
                Column("data_time", String(20)), Column("status", String(20)), Column("label", String(30)), Column("score", Float),
                Column("rr", Float), Column("stop", Float), Column("target", Float), Column("payload", JSON),
                Column("state", String(25), nullable=False, default="candidate"), Column("state_reason", Text),
                Column("first_seen", DateTime, default=utcnow, index=True), Column("updated", DateTime, default=utcnow))
signal_events = Table("signal_events", md, Column("id", Integer, primary_key=True),
                      Column("key", String(32), ForeignKey("signals.key", ondelete="CASCADE"), index=True),
                      Column("from_state", String(25)), Column("to_state", String(25), nullable=False),
                      Column("reason", Text), Column("ts", DateTime, default=utcnow))
alert_rules = Table("alert_rules", md, Column("id", Integer, primary_key=True),
                    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                    Column("name", String(100), nullable=False),
                    Column("enabled", Boolean, default=True), Column("strategies", JSON), Column("min_score", Float, default=0),
                    Column("directions", JSON), Column("timeframes", JSON), Column("symbols", JSON),
                    Column("cooldown_minutes", Integer, default=120), Column("channels", JSON), Column("created", DateTime, default=utcnow))
alert_events = Table("alert_events", md, Column("id", Integer, primary_key=True),
                     Column("rule_id", Integer, ForeignKey("alert_rules.id", ondelete="CASCADE")), Column("signal_key", String(32)),
                     Column("ticker", String(30)), Column("message", Text), Column("channel", String(20)), Column("status", String(20)),
                     Column("error", Text), Column("created", DateTime, default=utcnow),
                     UniqueConstraint("rule_id", "signal_key", "channel"), Index("ix_alert_rule_ticker", "rule_id", "ticker", "created"))
journal = Table("journal", md, Column("id", Integer, primary_key=True),
                Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                Column("signal_key", String(32)), Column("symbol", String(30), nullable=False),
                Column("strategy", String(60)), Column("direction", String(10)), Column("mode", String(10), default="paper"),
                Column("entry_time", String(25)), Column("entry_price", Float), Column("qty", Integer), Column("stop", Float),
                Column("target", Float), Column("exit_time", String(25)), Column("exit_price", Float),
                Column("brokerage", Float, default=0), Column("slippage", Float, default=0), Column("notes", Text), Column("tags", JSON),
                Column("planned_risk", Float), Column("followed_rules", Boolean), Column("review", Text),
                Column("status", String(10), default="open"), Column("created", DateTime, default=utcnow),
                CheckConstraint("qty > 0", name="ck_journal_qty_pos"))
scan_runs = Table("scan_runs", md, Column("id", Integer, primary_key=True), Column("user_id", Integer, index=True),
                  Column("started", DateTime), Column("finished", DateTime),
                  Column("universe", String(60)), Column("symbols", Integer), Column("signals", Integer), Column("qualified", Integer),
                  Column("status", String(20)), Column("detail", Text))
audit_log = Table("audit_log", md, Column("id", Integer, primary_key=True), Column("ts", DateTime, default=utcnow),
                  Column("actor", String(60)), Column("action", String(60)), Column("detail", Text))


billing_events = Table("billing_events", md, Column("id", Integer, primary_key=True),
                       Column("provider_event_id", String(100), unique=True, nullable=False),
                       Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE")), Column("type", String(60)),
                       Column("plan", String(20)), Column("payload", JSON), Column("ts", DateTime, default=utcnow))

lab_strategies = Table("lab_strategies", md, Column("id", String(32), primary_key=True),
                       Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                       Column("name", String(100), nullable=False), Column("description", Text, default=""),
                       Column("language", String(20), nullable=False), Column("draft_source", Text), Column("draft_ir", JSON),
                       Column("draft_spec", JSON), Column("archived", Boolean, default=False),
                       Column("created", DateTime, default=utcnow), Column("updated", DateTime, default=utcnow),
                       UniqueConstraint("user_id", "name"))
lab_versions = Table("lab_versions", md, Column("id", Integer, primary_key=True),
                     Column("strategy_id", String(32), ForeignKey("lab_strategies.id", ondelete="CASCADE"), nullable=False, index=True),
                     Column("version", Integer, nullable=False), Column("language", String(20), nullable=False),
                     Column("source", Text), Column("ir", JSON), Column("spec", JSON, nullable=False),
                     Column("source_sha256", String(64), nullable=False), Column("note", Text),
                     Column("created", DateTime, default=utcnow), UniqueConstraint("strategy_id", "version"))
lab_runs = Table("lab_runs", md, Column("id", Integer, primary_key=True),
                 Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                 Column("strategy_id", String(32), index=True), Column("version", Integer), Column("kind", String(20)),
                 Column("status", String(25)), Column("language", String(20)), Column("runtime", String(100)),
                 Column("config", JSON), Column("stats", JSON), Column("error", Text), Column("logs", Text),
                 Column("started", DateTime, default=utcnow), Column("finished", DateTime))

ai_credentials = Table("ai_credentials", md, Column("id", Integer, primary_key=True),
                       Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                       Column("provider", String(20), nullable=False), Column("ciphertext", LargeBinary, nullable=False),
                       Column("master_key_id", String(16), nullable=False), Column("masked", String(40), nullable=False),
                       Column("model", String(100)), Column("validated_at", DateTime), Column("created", DateTime, default=utcnow),
                       Column("updated", DateTime, default=utcnow), UniqueConstraint("user_id", "provider"))
ai_usage = Table("ai_usage", md, Column("id", Integer, primary_key=True),
                 Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                 Column("task_id", String(32), index=True), Column("task_type", String(40)), Column("provider", String(20)),
                 Column("model", String(100)), Column("status", String(20)), Column("error_code", String(40)),
                 Column("attempt", Integer, default=1), Column("fallback_from", String(20)),
                 Column("input_tokens", Integer), Column("output_tokens", Integer), Column("cached_input_tokens", Integer),
                 Column("cache_write_tokens", Integer), Column("usage_source", String(20)),
                 Column("est_cost_usd", Float), Column("price_basis", String(200)), Column("latency_s", Float),
                 Column("provider_request_id", String(100)), Column("ts", DateTime, default=utcnow, index=True))

reports = Table("reports", md, Column("id", Integer, primary_key=True),
                Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                Column("signal_key", String(32)), Column("format", String(10)), Column("mode", String(12)),
                Column("status", String(20)), Column("filename", String(200)), Column("bytes", Integer),
                Column("snapshot_match", Boolean), Column("error", Text), Column("created", DateTime, default=utcnow),
                Column("finished", DateTime), Column("legal_hold", Boolean, default=False))   # v13: never auto-deleted
schedules = Table("schedules", md, Column("id", Integer, primary_key=True),
                  Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                  Column("name", String(100), nullable=False), Column("kind", String(20), nullable=False),
                  Column("config", JSON, nullable=False), Column("at_time", String(5)), Column("interval_minutes", Integer),
                  Column("trading_days_only", Boolean, default=True), Column("enabled", Boolean, default=True),
                  Column("last_run", DateTime), Column("last_status", String(30)), Column("last_error", Text),
                  Column("created", DateTime, default=utcnow))

broker_connections = Table("broker_connections", md, Column("id", Integer, primary_key=True),
                           Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                           Column("broker", String(20), nullable=False), Column("app_id", String(60), nullable=False),
                           Column("secret_ct", LargeBinary, nullable=False), Column("token_ct", LargeBinary),
                           Column("master_key_id", String(16), nullable=False), Column("redirect_uri", String(300)),
                           Column("token_issued", DateTime), Column("token_expires", DateTime),
                           Column("login_state", String(64)), Column("login_state_expires", DateTime),
                           # feed lease: exactly one collector per user across all AMW processes (UI + API)
                           Column("feed_holder", String(80)), Column("feed_heartbeat", DateTime), Column("feed_status", JSON),
                           Column("created", DateTime, default=utcnow), Column("updated", DateTime, default=utcnow),
                           UniqueConstraint("user_id", "broker"))
minute_bars = Table("minute_bars", md, Column("id", Integer, primary_key=True),
                    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    Column("symbol", String(40), nullable=False), Column("ts", String(32), nullable=False),
                    Column("open", Float, nullable=False), Column("high", Float, nullable=False), Column("low", Float, nullable=False),
                    Column("close", Float, nullable=False), Column("volume", Float), Column("source", String(20), nullable=False),
                    Column("revisions", Integer, default=0), Column("received_at", DateTime, default=utcnow),
                    UniqueConstraint("user_id", "symbol", "ts"), Index("ix_minute_bars_user_symbol_ts", "user_id", "symbol", "ts"),
                    Index("ix_minute_bars_user_received", "user_id", "received_at", "id"))

# v7: durable record of every background job (survives restarts; orphans are marked FAILED with a reason)
job_runs = Table("job_runs", md, Column("id", String(16), primary_key=True), Column("user_id", Integer, index=True),
                 Column("kind", String(30), nullable=False), Column("status", String(20), nullable=False),
                 Column("progress", Float, default=0.0), Column("message", Text), Column("error", Text),
                 Column("params", JSON), Column("holder", String(80)), Column("heartbeat", DateTime),
                 Column("created", DateTime, default=utcnow), Column("started", DateTime), Column("finished", DateTime),
                 Column("result", Text),                                    # v10: JSON result of background API jobs
                 Index("ix_job_runs_status", "status"))
# v7: fixed-window request counters shared by every process (e.g. the FYERS per-minute budget of one account)
rate_buckets = Table("rate_buckets", md, Column("key", String(80), primary_key=True), Column("window", String(20), primary_key=True),
                     Column("count", Integer, nullable=False, default=0))
# v7: paper forward test of each qualified signal (entry at the next bar's open, exit on stop/target/expiry)
forward_tests = Table("forward_tests", md, Column("id", Integer, primary_key=True),
                      Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                      Column("signal_key", String(40), nullable=False), Column("ticker", String(40), nullable=False),
                      Column("strategy", String(60)), Column("strategy_version", Integer), Column("timeframe", String(5)),
                      Column("direction", String(10)), Column("signal_time", String(32)), Column("stop", Float),
                      Column("target", Float), Column("max_bars", Integer), Column("status", String(12), nullable=False),
                      Column("entry_time", String(32)), Column("entry_price", Float), Column("exit_time", String(32)),
                      Column("exit_price", Float), Column("exit_reason", String(30)), Column("r_multiple", Float),
                      Column("bars_held", Integer), Column("note", Text), Column("created", DateTime, default=utcnow),
                      Column("updated", DateTime, default=utcnow), UniqueConstraint("user_id", "signal_key"))

# v8: system notifications (signal invalidated/completed, daily summary, feed failure, forward-test closed, ...)
notification_events = Table("notification_events", md, Column("id", Integer, primary_key=True),
                            Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
                            Column("kind", String(30), nullable=False), Column("dedup_key", String(80), nullable=False),
                            Column("channel", String(20), nullable=False), Column("status", String(20)), Column("error", Text),
                            Column("message", Text), Column("created", DateTime, default=utcnow, index=True),
                            UniqueConstraint("user_id", "kind", "dedup_key", "channel"))

Index("ix_signals_user_first_seen", signals.c.user_id, signals.c.first_seen)   # v9 (PERF P-001)

# v11: instrument master (built from official NSE lists the app loads; one row per Yahoo-style ticker)
instruments = Table("instruments", md, Column("ticker", String(40), primary_key=True), Column("symbol", String(30), index=True),
                    Column("exchange", String(10), default="NSE"), Column("name", String(200)), Column("isin", String(12), index=True),
                    Column("series", String(5)), Column("industry", String(120)), Column("sources", Text),
                    Column("active", Boolean, default=True), Column("updated", DateTime, default=utcnow))

# signal lifecycle: allowed transitions (documented in docs/STRATEGY_RULES.md)
TRANSITIONS: dict[str, set[str]] = {
    "candidate": {"awaiting_confirmation", "qualified", "rejected", "invalidated", "expired", "insufficient_data"},
    "awaiting_confirmation": {"qualified", "rejected", "invalidated", "expired"},
    "qualified": {"active", "invalidated", "expired", "completed"},
    "active": {"completed", "invalidated"},
    "insufficient_data": {"candidate", "qualified", "rejected"},
    "rejected": set(), "invalidated": set(), "expired": set(), "completed": set(),
}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def default_url(root: Path) -> str:
    return os.environ.get("DATABASE_URL") or f"sqlite:///{(root / 'data' / 'amw.db').as_posix()}"


class Store:
    """All tenant data access is scoped by user_id; there is no method that reads another user's private rows."""

    def __init__(self, url: str):
        kw: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(url, **kw)
        self.url = url
        self.migrate()

    @property
    def backend(self) -> str:
        return self.engine.dialect.name

    # additive column migrations for databases created by older versions: version -> [(table, column, DDL type)]
    COLUMN_MIGRATIONS = {4: [("signals", "user_id", "INTEGER"), ("signals", "canonical_key", "VARCHAR(32)"),
                             ("scan_runs", "user_id", "INTEGER")],
                         10: [("job_runs", "result", "TEXT", False)],
                         13: [("reports", "legal_hold", "BOOLEAN DEFAULT FALSE", False)]}          # 4th item False = no index

    INDEX_MIGRATIONS = {9: ["CREATE INDEX IF NOT EXISTS ix_signals_user_first_seen ON signals (user_id, first_seen)"]}

    # A-005: the original signal record is immutable. Only the lifecycle projection (state, state_reason, updated) may
    # change; every change is also appended to signal_events. Enforced by a database trigger on SQLite and PostgreSQL.
    IMMUTABLE_SIGNAL_COLUMNS = ("key", "canonical_key", "user_id", "ticker", "strategy", "strategy_version", "setup",
                                "timeframe", "direction", "signal_time", "data_time", "status", "label", "score", "rr",
                                "stop", "target", "payload", "first_seen")

    def _immutability_ddl(self) -> list[str]:
        cols = ", ".join(self.IMMUTABLE_SIGNAL_COLUMNS)
        if self.engine.dialect.name == "postgresql":
            cond = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in self.IMMUTABLE_SIGNAL_COLUMNS if c != "payload")
            cond += " OR NEW.payload::text IS DISTINCT FROM OLD.payload::text"
            return ["CREATE OR REPLACE FUNCTION amw_signal_immutable() RETURNS trigger AS $$ BEGIN "
                    f"IF {cond} THEN RAISE EXCEPTION 'signal record is immutable (A-005)'; END IF; RETURN NEW; END; "
                    "$$ LANGUAGE plpgsql",
                    "DROP TRIGGER IF EXISTS signals_immutable ON signals",
                    "CREATE TRIGGER signals_immutable BEFORE UPDATE ON signals FOR EACH ROW EXECUTE FUNCTION amw_signal_immutable()"]
        return [f"CREATE TRIGGER IF NOT EXISTS signals_immutable BEFORE UPDATE OF {cols} ON signals "
                "BEGIN SELECT RAISE(ABORT, 'signal record is immutable (A-005)'); END"]

    def migrate(self) -> None:
        """Idempotent, ordered, additive migrations (new tables via create_all; new columns via ALTER TABLE ADD COLUMN).
        Legacy signal rows without an owner keep user_id NULL and are visible only to the system/admin view."""
        from sqlalchemy import inspect, text
        md.create_all(self.engine)
        insp = inspect(self.engine)
        with self.engine.begin() as c:
            cur = c.execute(select(func.max(schema_version.c.version))).scalar() or 0
            for v in range(cur + 1, SCHEMA_VERSION + 1):
                for ddl in self.INDEX_MIGRATIONS.get(v, []):
                    c.execute(text(ddl))
                if v == 12:
                    for ddl in self._immutability_ddl():
                        c.execute(text(ddl))
                for table, col, ddl, *opt in self.COLUMN_MIGRATIONS.get(v, []):
                    if col not in {x["name"] for x in insp.get_columns(table)}:
                        c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                        if not opt or opt[0]:
                            c.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{col} ON {table} ({col})"))
                c.execute(insert(schema_version).values(version=v, applied_at=utcnow()))

    def schema_version(self) -> int | None:
        with self.engine.connect() as c:
            return c.execute(select(func.max(schema_version.c.version))).scalar()

    def audit(self, action: str, detail: str, actor: str = "system") -> None:
        with self.engine.begin() as c:
            c.execute(insert(audit_log).values(actor=actor, action=action, detail=detail[:2000], ts=utcnow()))

    # ---------------- users
    def create_user(self, username: str, role: str = "analyst", plan: str = "free") -> tuple[int, str]:
        if role not in ("admin", "analyst", "viewer"):
            raise ValueError("role must be admin/analyst/viewer")
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as c:
            uid = c.execute(insert(users).values(username=username, role=role, plan=plan, token_hash=hash_token(token),
                                                 created=utcnow())).inserted_primary_key[0]
        self.audit("user_create", f"user {username} role {role}")
        return uid, token          # plaintext token returned ONCE, only the hash is stored

    def user_by_token(self, token: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(users).where(users.c.token_hash == hash_token(token))).first()
        return dict(r._mapping) if r else None

    def ensure_local_user(self) -> int:
        """Single-user desktop mode: a local admin without a token."""
        with self.engine.begin() as c:
            r = c.execute(select(users.c.id).where(users.c.username == "local")).first()
            if r:
                return r[0]
            return c.execute(insert(users).values(username="local", role="admin", plan="pro", created=utcnow())).inserted_primary_key[0]

    def set_plan(self, user_id: int, plan: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.id == user_id).values(plan=plan))

    def get_user(self, user_id: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(users).where(users.c.id == user_id)).first()
        return dict(r._mapping) if r else None

    # ---------------- preferences
    def get_pref(self, user_id: int, key: str, default=None):
        with self.engine.connect() as c:
            v = c.execute(select(preferences.c.value).where(preferences.c.user_id == user_id, preferences.c.key == key)).scalar()
        return default if v is None else v

    def set_pref(self, user_id: int, key: str, value) -> None:
        with self.engine.begin() as c:
            w = (preferences.c.user_id == user_id) & (preferences.c.key == key)
            if c.execute(select(preferences.c.key).where(w)).first():
                c.execute(update(preferences).where(w).values(value=value))
            else:
                c.execute(insert(preferences).values(user_id=user_id, key=key, value=value))

    # ---------------- watchlists & presets
    def save_watchlist(self, user_id: int, name: str, symbols: list[str]) -> None:
        name = name.strip()
        if not name:
            raise ValueError("watchlist name required")
        with self.engine.begin() as c:
            w = (watchlists.c.user_id == user_id) & (watchlists.c.name == name)
            if c.execute(select(watchlists.c.id).where(w)).first():
                c.execute(update(watchlists).where(w).values(symbols=symbols))
            else:
                c.execute(insert(watchlists).values(user_id=user_id, name=name, symbols=symbols, created=utcnow()))

    def watchlists(self, user_id: int) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(watchlists).where(watchlists.c.user_id == user_id).order_by(watchlists.c.name))]

    def delete_watchlist(self, user_id: int, name: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(watchlists).where(watchlists.c.user_id == user_id, watchlists.c.name == name))

    def save_preset(self, user_id: int, name: str, kind: str, payload: dict) -> None:
        with self.engine.begin() as c:
            w = (presets.c.user_id == user_id) & (presets.c.name == name) & (presets.c.kind == kind)
            if c.execute(select(presets.c.id).where(w)).first():
                c.execute(update(presets).where(w).values(payload=payload))
            else:
                c.execute(insert(presets).values(user_id=user_id, name=name, kind=kind, payload=payload, created=utcnow()))

    def presets(self, user_id: int, kind: str) -> list[dict]:
        with self.engine.connect() as c:
            q = select(presets).where(presets.c.user_id == user_id, presets.c.kind == kind).order_by(presets.c.name)
            return [dict(r._mapping) for r in c.execute(q)]

    # ---------------- versioned strategy configs (global, admin-managed)
    def save_strategy_config(self, strategy: str, config: dict, note: str = "", activate: bool = True, actor: str = "system") -> int:
        with self.engine.begin() as c:
            v = (c.execute(select(func.max(strategy_configs.c.version)).where(strategy_configs.c.strategy == strategy)).scalar() or 0) + 1
            config = {**config, "version": v}
            if activate:
                c.execute(update(strategy_configs).where(strategy_configs.c.strategy == strategy).values(active=False))
            c.execute(insert(strategy_configs).values(strategy=strategy, version=v, config=config, note=note, active=activate, created=utcnow()))
        self.audit("strategy_config", f"{strategy} v{v} saved (active={activate})", actor)
        return v

    def strategy_versions(self, strategy: str) -> list[dict]:
        with self.engine.connect() as c:
            q = select(strategy_configs).where(strategy_configs.c.strategy == strategy).order_by(strategy_configs.c.version.desc())
            return [dict(r._mapping) for r in c.execute(q)]

    def activate_version(self, strategy: str, version: int, actor: str = "system") -> None:
        with self.engine.begin() as c:
            c.execute(update(strategy_configs).where(strategy_configs.c.strategy == strategy).values(active=False))
            c.execute(update(strategy_configs).where(strategy_configs.c.strategy == strategy, strategy_configs.c.version == version).values(active=True))
        self.audit("strategy_activate", f"{strategy} v{version}", actor)

    def active_strategy_configs(self) -> dict[str, dict]:
        with self.engine.connect() as c:
            return {r.strategy: r.config for r in c.execute(select(strategy_configs).where(strategy_configs.c.active.is_(True)))}

    # ---------------- signals + lifecycle
    @staticmethod
    def initial_state(row: dict) -> str:
        if str(row.get("label", "")).startswith("Rejected (data)"):
            return "insufficient_data"
        return {"qualified": "qualified", "candidate": "awaiting_confirmation"}.get(row["status"], "rejected")

    @staticmethod
    def scoped_key(user_id: int | None, canonical_key: str) -> str:
        """Stored primary key: unique per owner, so two users' identical setups are separate records."""
        return hashlib.sha1(f"{user_id}|{canonical_key}".encode()).hexdigest()[:16]

    def record_signals(self, rows: list[dict], user_id: int | None) -> int:
        """Insert new signals for ONE owner (dedup per owner by canonical key = ticker|strategy|setup|tf|direction|signal bar).
        Returns NEW count. Rows are stored with their full payload as the immutable canonical record."""
        new = 0
        if not rows:
            return 0
        keyed = [(self.scoped_key(user_id, r["key"]), r) for r in rows]
        with self.engine.begin() as c:
            existing = set(c.execute(select(signals.c.key).where(signals.c.key.in_([k for k, _ in keyed]))).scalars())
            for k, r in keyed:
                if k in existing:
                    continue
                st = self.initial_state(r)
                ins = self._insert_ignore(signals).values(
                    key=k, canonical_key=r["key"], user_id=user_id, ticker=r["ticker"], strategy=r["strategy"],
                    strategy_version=r["strategy_version"], setup=r["setup"],
                    timeframe=r["timeframe"], direction=r["direction"], signal_time=r["signal_time"], data_time=r["data_time"],
                    status=r["status"], label=r["label"], score=r["score"], rr=r.get("rr"), stop=r.get("stop"), target=r.get("target"),
                    payload=json.loads(json.dumps(r, default=str)), state=st, state_reason="created", first_seen=utcnow(), updated=utcnow())
                # RETURNING tells reliably (rowcount is not dependable for ON CONFLICT on every driver) whether the row
                # was inserted; none = inserted concurrently by another scan/process, so not new (AUDIT A-002)
                if c.execute(ins.returning(signals.c.key)).first() is None:
                    existing.add(k)
                    continue
                c.execute(insert(signal_events).values(key=k, from_state=None, to_state=st, reason="created", ts=utcnow()))
                existing.add(k)
                new += 1
        return new

    def _insert_ignore(self, table):
        """INSERT ... ON CONFLICT DO NOTHING (SQLite and PostgreSQL): a concurrent duplicate is skipped atomically
        instead of aborting the whole transaction."""
        if self.backend == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            return pg_insert(table).on_conflict_do_nothing()
        from sqlalchemy.dialects.sqlite import insert as sq_insert
        return sq_insert(table).on_conflict_do_nothing()

    def _owned(self, key: str, user_id: int | None):
        w = signals.c.key == key
        return w & (signals.c.user_id.is_(None) if user_id is None else signals.c.user_id == user_id)

    def transition(self, key: str, to_state: str, reason: str, user_id: int | None = None) -> bool:
        """Lifecycle change as an append-only event (A-005). Compare-and-set on the current state makes it idempotent:
        a retry or a concurrent process that already applied the same change gets False and writes no second event.
        The original signal fields are never touched (database trigger)."""
        with self.engine.begin() as c:
            cur = c.execute(select(signals.c.state).where(self._owned(key, user_id))).scalar()
            if cur is None:
                raise KeyError(key)
            if cur == to_state:
                return False                                   # already applied (retry): no duplicate event
            if to_state not in TRANSITIONS.get(cur, set()):
                raise ValueError(f"illegal transition {cur} -> {to_state}")
            n = c.execute(update(signals).where(signals.c.key == key, signals.c.state == cur)
                          .values(state=to_state, state_reason=reason, updated=utcnow())).rowcount
            if n != 1:
                return False                                   # another process changed it first
            c.execute(insert(signal_events).values(key=key, from_state=cur, to_state=to_state, reason=reason, ts=utcnow()))
            return True

    def signal_events(self, key: str, user_id: int | None = None) -> list[dict]:
        if self.get_signal(key, user_id) is None:
            return []
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(signal_events).where(signal_events.c.key == key).order_by(signal_events.c.id))]

    def open_signals(self, user_id: int | None) -> list[dict]:
        with self.engine.connect() as c:
            q = select(signals).where(signals.c.state.in_(["qualified", "awaiting_confirmation", "active"]),
                                      signals.c.user_id.is_(None) if user_id is None else signals.c.user_id == user_id)
            return [dict(r._mapping) for r in c.execute(q)]

    def signal_history(self, user_id: int | None, ticker: str | None = None, limit: int = 500, offset: int = 0,
                       include_payload: bool = True) -> list[dict]:
        cols = signals if include_payload else [c for c in signals.c if c.name != "payload"]
        q = (select(*([cols] if include_payload else cols)).where(signals.c.user_id.is_(None) if user_id is None else signals.c.user_id == user_id)
             .order_by(signals.c.first_seen.desc()).limit(limit).offset(offset))
        if ticker:
            q = q.where(signals.c.ticker == ticker)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def get_signal(self, key: str, user_id: int | None = None) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(signals).where(self._owned(key, user_id))).first()
        return dict(r._mapping) if r else None

    # ---------------- alerts (per user)
    def add_alert_rule(self, user_id: int, **kw) -> int:
        with self.engine.begin() as c:
            return c.execute(insert(alert_rules).values(user_id=user_id, created=utcnow(), **kw)).inserted_primary_key[0]

    def alert_rules(self, user_id: int | None = None, enabled_only: bool = False) -> list[dict]:
        """user_id=None is ONLY for the internal alert worker (evaluates every user's rules)."""
        q = select(alert_rules)
        if user_id is not None:
            q = q.where(alert_rules.c.user_id == user_id)
        if enabled_only:
            q = q.where(alert_rules.c.enabled.is_(True))
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def set_alert_enabled(self, user_id: int, rule_id: int, enabled: bool) -> None:
        with self.engine.begin() as c:
            c.execute(update(alert_rules).where(alert_rules.c.id == rule_id, alert_rules.c.user_id == user_id).values(enabled=enabled))

    def delete_alert_rule(self, user_id: int, rule_id: int) -> None:
        with self.engine.begin() as c:
            own = c.execute(select(alert_rules.c.id).where(alert_rules.c.id == rule_id, alert_rules.c.user_id == user_id)).first()
            if not own:
                return
            c.execute(delete(alert_events).where(alert_events.c.rule_id == rule_id))
            c.execute(delete(alert_rules).where(alert_rules.c.id == rule_id))

    def last_alert_time(self, rule_id: int, ticker: str) -> datetime | None:
        with self.engine.connect() as c:
            return c.execute(select(func.max(alert_events.c.created)).where(
                alert_events.c.rule_id == rule_id, alert_events.c.ticker == ticker, alert_events.c.status == "sent")).scalar()

    def add_alert_event(self, **kw) -> bool:
        try:
            with self.engine.begin() as c:
                c.execute(insert(alert_events).values(**{"created": utcnow(), **kw}))
            return True
        except IntegrityError:
            return False

    def alert_events(self, user_id: int, limit: int = 500) -> list[dict]:
        q = (select(alert_events).join(alert_rules, alert_rules.c.id == alert_events.c.rule_id)
             .where(alert_rules.c.user_id == user_id).order_by(alert_events.c.created.desc()).limit(limit))
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    # ---------------- journal / paper trading (per user)
    def add_trade(self, user_id: int, **kw) -> int:
        with self.engine.begin() as c:
            tid = c.execute(insert(journal).values(user_id=user_id, created=utcnow(), **kw)).inserted_primary_key[0]
        self.audit("journal_add", f"user {user_id} paper trade {tid} {kw.get('symbol')}")
        return tid

    def close_trade(self, user_id: int, trade_id: int, exit_price: float, exit_time: str, followed_rules: bool | None, review: str = "") -> bool:
        with self.engine.begin() as c:
            r = c.execute(update(journal).where(journal.c.id == trade_id, journal.c.user_id == user_id, journal.c.status == "open")
                          .values(exit_price=exit_price, exit_time=exit_time, followed_rules=followed_rules, review=review, status="closed"))
            return r.rowcount == 1

    def trades(self, user_id: int) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(journal).where(journal.c.user_id == user_id).order_by(journal.c.id.desc()))]

    # ---------------- billing (idempotent, authoritative events only)
    def apply_billing_event(self, provider_event_id: str, user_id: int, type_: str, plan: str | None, payload: dict) -> bool:
        try:
            with self.engine.begin() as c:
                c.execute(insert(billing_events).values(provider_event_id=provider_event_id, user_id=user_id, type=type_,
                                                        plan=plan, payload=payload, ts=utcnow()))
                if type_ in ("subscription.activated", "subscription.updated") and plan:
                    c.execute(update(users).where(users.c.id == user_id).values(plan=plan))
                elif type_ == "subscription.cancelled":
                    c.execute(update(users).where(users.c.id == user_id).values(plan="free"))
            self.audit("billing", f"{type_} user {user_id} plan {plan}")
            return True
        except IntegrityError:
            return False      # duplicate webhook delivery - ignored

    # ---------------- scans / health
    def log_scan(self, **kw) -> None:
        with self.engine.begin() as c:
            c.execute(insert(scan_runs).values(**kw))

    def scans(self, limit: int = 50, user_id: int | None = None) -> list[dict]:
        q = select(scan_runs).order_by(scan_runs.c.id.desc()).limit(limit)
        if user_id is not None:
            q = q.where(scan_runs.c.user_id == user_id)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def audit_rows(self, limit: int = 200) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(audit_log).order_by(audit_log.c.id.desc()).limit(limit))]

    def ping(self) -> float:
        t = time.perf_counter()
        with self.engine.connect() as c:
            c.execute(select(1))
        return round((time.perf_counter() - t) * 1000, 2)

    # ---------------- strategy lab (tenant-scoped)
    def lab_create(self, user_id: int, name: str, language: str, description: str = "", source: str | None = None,
                   ir: dict | None = None, spec: dict | None = None) -> str:
        sid = secrets.token_hex(8)
        with self.engine.begin() as c:
            c.execute(insert(lab_strategies).values(id=sid, user_id=user_id, name=name.strip(), description=description,
                                                    language=language, draft_source=source, draft_ir=ir, draft_spec=spec,
                                                    created=utcnow(), updated=utcnow()))
        self.audit("lab_create", f"user {user_id} strategy {sid} ({language})", str(user_id))
        return sid

    def lab_get(self, user_id: int, sid: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(lab_strategies).where(lab_strategies.c.id == sid, lab_strategies.c.user_id == user_id)).first()
        return dict(r._mapping) if r else None

    def lab_list(self, user_id: int) -> list[dict]:
        with self.engine.connect() as c:
            q = select(lab_strategies).where(lab_strategies.c.user_id == user_id, lab_strategies.c.archived.is_(False)).order_by(lab_strategies.c.name)
            return [dict(r._mapping) for r in c.execute(q)]

    def lab_save_draft(self, user_id: int, sid: str, source: str | None, ir: dict | None, spec: dict | None) -> bool:
        with self.engine.begin() as c:
            r = c.execute(update(lab_strategies).where(lab_strategies.c.id == sid, lab_strategies.c.user_id == user_id)
                          .values(draft_source=source, draft_ir=ir, draft_spec=spec, updated=utcnow()))
            return r.rowcount == 1

    def lab_archive(self, user_id: int, sid: str) -> bool:
        with self.engine.begin() as c:
            return c.execute(update(lab_strategies).where(lab_strategies.c.id == sid, lab_strategies.c.user_id == user_id)
                             .values(archived=True)).rowcount == 1

    def lab_publish(self, user_id: int, sid: str, note: str = "") -> int:
        """Freeze the current draft as an immutable version (never updated afterwards)."""
        st = self.lab_get(user_id, sid)
        if st is None:
            raise KeyError("strategy not found")
        payload = json.dumps({"s": st["draft_source"], "ir": st["draft_ir"], "spec": st["draft_spec"]}, sort_keys=True, default=str)
        with self.engine.begin() as c:
            v = (c.execute(select(func.max(lab_versions.c.version)).where(lab_versions.c.strategy_id == sid)).scalar() or 0) + 1
            c.execute(insert(lab_versions).values(strategy_id=sid, version=v, language=st["language"], source=st["draft_source"],
                                                  ir=st["draft_ir"], spec=st["draft_spec"] or {}, note=note, created=utcnow(),
                                                  source_sha256=hashlib.sha256(payload.encode()).hexdigest()))
        self.audit("lab_publish", f"user {user_id} strategy {sid} v{v}", str(user_id))
        return v

    def lab_versions(self, user_id: int, sid: str) -> list[dict]:
        if self.lab_get(user_id, sid) is None:
            return []
        with self.engine.connect() as c:
            q = select(lab_versions).where(lab_versions.c.strategy_id == sid).order_by(lab_versions.c.version.desc())
            return [dict(r._mapping) for r in c.execute(q)]

    def lab_version(self, user_id: int, sid: str, version: int) -> dict | None:
        if self.lab_get(user_id, sid) is None:
            return None
        with self.engine.connect() as c:
            r = c.execute(select(lab_versions).where(lab_versions.c.strategy_id == sid, lab_versions.c.version == version)).first()
        return dict(r._mapping) if r else None

    def lab_run_start(self, user_id: int, **kw) -> int:
        with self.engine.begin() as c:
            return c.execute(insert(lab_runs).values(user_id=user_id, started=utcnow(), status="RUNNING", **kw)).inserted_primary_key[0]

    def lab_run_finish(self, user_id: int, run_id: int, status: str, stats: dict | None = None, error: str | None = None,
                       logs: str = "", runtime: str | None = None) -> None:
        with self.engine.begin() as c:
            vals = {"status": status, "stats": stats, "error": error, "logs": (logs or "")[-20000:], "finished": utcnow()}
            if runtime:
                vals["runtime"] = runtime
            c.execute(update(lab_runs).where(lab_runs.c.id == run_id, lab_runs.c.user_id == user_id).values(**vals))

    def lab_runs(self, user_id: int, sid: str | None = None, limit: int = 100) -> list[dict]:
        q = select(lab_runs).where(lab_runs.c.user_id == user_id).order_by(lab_runs.c.id.desc()).limit(limit)
        if sid:
            q = q.where(lab_runs.c.strategy_id == sid)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def lab_active_runs(self, user_id: int) -> int:
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(lab_runs).where(lab_runs.c.user_id == user_id,
                                                                              lab_runs.c.status.in_(["QUEUED", "RUNNING"]))).scalar()

    # ---------------- BYOK AI credentials (encrypted, tenant-scoped) and usage
    def ai_put_credential(self, user_id: int, provider: str, api_key: str, model: str | None) -> None:
        from ..ai.crypto import encrypt
        from ..ai.redact import mask
        blob, kid = encrypt(api_key, user_id, provider)
        vals = {"ciphertext": blob, "master_key_id": kid, "masked": mask(api_key), "model": model,
                "validated_at": utcnow(), "updated": utcnow()}
        with self.engine.begin() as c:
            w = (ai_credentials.c.user_id == user_id) & (ai_credentials.c.provider == provider)
            if c.execute(select(ai_credentials.c.id).where(w)).first():
                c.execute(update(ai_credentials).where(w).values(**vals))
            else:
                c.execute(insert(ai_credentials).values(user_id=user_id, provider=provider, created=utcnow(), **vals))
        self.audit("ai_key_saved", f"user {user_id} provider {provider} key {mask(api_key)}", str(user_id))

    def ai_credentials(self, user_id: int) -> list[dict]:
        """Metadata only (masked) - never the ciphertext or plaintext."""
        q = select(ai_credentials.c.provider, ai_credentials.c.masked, ai_credentials.c.model, ai_credentials.c.validated_at,
                   ai_credentials.c.updated).where(ai_credentials.c.user_id == user_id)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def ai_get_key(self, user_id: int, provider: str) -> tuple[str, str | None] | None:
        """Backend-only: decrypt the caller's own key. Returns (key, model) or None."""
        from ..ai.crypto import decrypt
        with self.engine.connect() as c:
            r = c.execute(select(ai_credentials).where(ai_credentials.c.user_id == user_id, ai_credentials.c.provider == provider)).first()
        if not r:
            return None
        return decrypt(r.ciphertext, user_id, provider, r.master_key_id), r.model

    def ai_set_model(self, user_id: int, provider: str, model: str) -> bool:
        with self.engine.begin() as c:
            return c.execute(update(ai_credentials).where(ai_credentials.c.user_id == user_id, ai_credentials.c.provider == provider)
                             .values(model=model, updated=utcnow())).rowcount == 1

    def ai_delete_credential(self, user_id: int, provider: str) -> bool:
        with self.engine.begin() as c:
            n = c.execute(delete(ai_credentials).where(ai_credentials.c.user_id == user_id, ai_credentials.c.provider == provider)).rowcount
        if n:
            self.audit("ai_key_deleted", f"user {user_id} provider {provider}", str(user_id))
        return n == 1

    def ai_log(self, user_id: int, **kw) -> None:
        with self.engine.begin() as c:
            c.execute(insert(ai_usage).values(user_id=user_id, ts=utcnow(), **kw))

    def ai_usage(self, user_id: int, since=None, limit: int = 1000) -> list[dict]:
        q = select(ai_usage).where(ai_usage.c.user_id == user_id).order_by(ai_usage.c.id.desc()).limit(limit)
        if since is not None:
            q = q.where(ai_usage.c.ts >= since)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def ai_totals(self, user_id: int, since) -> dict:
        """Requests and AMW-estimated cost since a time (successful + failed provider calls; 'blocked' are not calls)."""
        with self.engine.connect() as c:
            r = c.execute(select(func.count(), func.coalesce(func.sum(ai_usage.c.est_cost_usd), 0.0))
                          .where(ai_usage.c.user_id == user_id, ai_usage.c.ts >= since, ai_usage.c.status.in_(["ok", "error"]))).first()
        return {"requests": int(r[0]), "est_cost_usd": float(r[1])}

    # ---------------- reports
    def report_start(self, user_id: int, key: str, fmt: str, mode: str) -> int:
        with self.engine.begin() as c:
            return c.execute(insert(reports).values(user_id=user_id, signal_key=key, format=fmt, mode=mode, status="RUNNING",
                                                    created=utcnow())).inserted_primary_key[0]

    def report_finish(self, user_id: int, rid: int, status: str, filename: str | None, nbytes: int, snapshot_match, error: str | None):
        with self.engine.begin() as c:
            c.execute(update(reports).where(reports.c.id == rid, reports.c.user_id == user_id).values(
                status=status, filename=filename, bytes=nbytes, snapshot_match=snapshot_match, error=error, finished=utcnow()))

    def report_get(self, user_id: int, rid: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(reports).where(reports.c.id == rid, reports.c.user_id == user_id)).first()
        return dict(r._mapping) if r else None

    def report_list(self, user_id: int, limit: int = 200) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(reports).where(reports.c.user_id == user_id)
                                                         .order_by(reports.c.id.desc()).limit(limit))]

    def reports_today(self, user_id: int) -> int:
        now = utcnow()
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(reports).where(
                reports.c.user_id == user_id, reports.c.created >= datetime(now.year, now.month, now.day))).scalar()

    # ---------------- schedules
    def schedule_add(self, user_id: int, **kw) -> int:
        with self.engine.begin() as c:
            return c.execute(insert(schedules).values(user_id=user_id, created=utcnow(), **kw)).inserted_primary_key[0]

    def schedule_list(self, user_id: int | None) -> list[dict]:
        q = select(schedules).order_by(schedules.c.id)
        if user_id is not None:
            q = q.where(schedules.c.user_id == user_id)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def schedule_update(self, user_id: int, sid: int, **kw) -> bool:
        with self.engine.begin() as c:
            return c.execute(update(schedules).where(schedules.c.id == sid, schedules.c.user_id == user_id).values(**kw)).rowcount == 1

    def schedule_claim(self, sid: int, prev_last_run, now) -> bool:
        """Atomic compare-and-set of a due slot: only ONE process (dashboard or API) wins a scheduled run (AUDIT A-008)."""
        w = schedules.c.last_run.is_(None) if prev_last_run is None else schedules.c.last_run == prev_last_run
        with self.engine.begin() as c:
            return c.execute(update(schedules).where(schedules.c.id == sid, w)
                             .values(last_run=now, last_status="STARTED", last_error=None)).rowcount == 1

    def schedule_delete(self, user_id: int, sid: int) -> bool:
        with self.engine.begin() as c:
            return c.execute(delete(schedules).where(schedules.c.id == sid, schedules.c.user_id == user_id)).rowcount == 1

    # ---------------- broker connections (encrypted app secret + access token; tenant-scoped)
    def broker_put(self, user_id: int, broker: str, app_id: str, secret: str, redirect_uri: str) -> None:
        from ..ai.crypto import encrypt
        ct, kid = encrypt(secret, user_id, f"{broker}-secret")
        vals = {"app_id": app_id, "secret_ct": ct, "master_key_id": kid, "redirect_uri": redirect_uri, "token_ct": None,
                "token_issued": None, "token_expires": None, "updated": utcnow()}
        with self.engine.begin() as c:
            w = (broker_connections.c.user_id == user_id) & (broker_connections.c.broker == broker)
            if c.execute(select(broker_connections.c.id).where(w)).first():
                c.execute(update(broker_connections).where(w).values(**vals))
            else:
                c.execute(insert(broker_connections).values(user_id=user_id, broker=broker, created=utcnow(), **vals))
        self.audit("broker_connected", f"user {user_id} {broker} app {app_id[:4]}…", str(user_id))

    def broker_get(self, user_id: int, broker: str) -> dict | None:
        """Backend-only. Returns metadata plus decrypted secret/token for the OWNER."""
        from ..ai.crypto import decrypt
        with self.engine.connect() as c:
            r = c.execute(select(broker_connections).where(broker_connections.c.user_id == user_id,
                                                           broker_connections.c.broker == broker)).first()
        if not r:
            return None
        d = dict(r._mapping)
        d["secret"] = decrypt(d.pop("secret_ct"), user_id, f"{broker}-secret", d["master_key_id"])
        tok = d.pop("token_ct")
        d["access_token"] = decrypt(tok, user_id, f"{broker}-token", d["master_key_id"]) if tok else None
        return d

    def broker_public(self, user_id: int, broker: str, now: datetime | None = None) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(broker_connections.c.app_id, broker_connections.c.redirect_uri, broker_connections.c.token_issued,
                                 broker_connections.c.token_expires, broker_connections.c.updated)
                          .where(broker_connections.c.user_id == user_id, broker_connections.c.broker == broker)).first()
        if not r:
            return None
        d = dict(r._mapping)
        d["app_id"] = d["app_id"][:4] + "…" + d["app_id"][-4:] if len(d["app_id"]) > 8 else "…"
        d["token_valid"] = bool(d["token_expires"] and d["token_expires"] > (now or utcnow()))
        return d

    def broker_set_state(self, user_id: int, broker: str, state: str, expires) -> None:
        with self.engine.begin() as c:
            c.execute(update(broker_connections).where(broker_connections.c.user_id == user_id, broker_connections.c.broker == broker)
                      .values(login_state=hashlib.sha256(state.encode()).hexdigest(), login_state_expires=expires))

    def broker_consume_state(self, user_id: int, broker: str, state: str, now: datetime | None = None) -> bool:
        """One-time OAuth state check (prevents completing a login this user did not start)."""
        h = hashlib.sha256((state or "").encode()).hexdigest()
        with self.engine.begin() as c:
            r = c.execute(select(broker_connections.c.login_state, broker_connections.c.login_state_expires)
                          .where(broker_connections.c.user_id == user_id, broker_connections.c.broker == broker)).first()
            ok = bool(r and r[0] and secrets.compare_digest(r[0], h) and r[1] and r[1] > (now or utcnow()))
            c.execute(update(broker_connections).where(broker_connections.c.user_id == user_id, broker_connections.c.broker == broker)
                      .values(login_state=None, login_state_expires=None))
        return ok

    def broker_set_token(self, user_id: int, broker: str, token: str | None, issued, expires) -> None:
        from ..ai.crypto import encrypt
        ct = encrypt(token, user_id, f"{broker}-token")[0] if token else None
        with self.engine.begin() as c:
            c.execute(update(broker_connections).where(broker_connections.c.user_id == user_id, broker_connections.c.broker == broker)
                      .values(token_ct=ct, token_issued=issued, token_expires=expires, updated=utcnow()))

    def broker_delete(self, user_id: int, broker: str) -> bool:
        with self.engine.begin() as c:
            n = c.execute(delete(broker_connections).where(broker_connections.c.user_id == user_id,
                                                           broker_connections.c.broker == broker)).rowcount
        if n:
            self.audit("broker_disconnected", f"user {user_id} {broker}", str(user_id))
        return n == 1

    # ---------------- feed lease + runtime status (shared by every AMW process through the database)
    def _bc(self, user_id: int, broker: str):
        return (broker_connections.c.user_id == user_id) & (broker_connections.c.broker == broker)

    def feed_lease_acquire(self, user_id: int, holder: str, now: datetime, stale_s: int = 90, broker: str = "fyers") -> bool:
        """Atomic compare-and-set: succeeds if no live holder (none, same holder, or heartbeat older than stale_s)."""
        bc = broker_connections.c
        free = or_(bc.feed_holder.is_(None), bc.feed_holder == holder, bc.feed_heartbeat.is_(None),
                   bc.feed_heartbeat < now - timedelta(seconds=stale_s))
        with self.engine.begin() as c:
            return c.execute(update(broker_connections).where(and_(self._bc(user_id, broker), free))
                             .values(feed_holder=holder, feed_heartbeat=now)).rowcount == 1

    def feed_lease_renew(self, user_id: int, holder: str, now: datetime, status: dict | None = None,
                         broker: str = "fyers") -> bool:
        """False when this holder lost the lease (stopped from another process / taken over after staleness)."""
        vals: dict[str, Any] = {"feed_heartbeat": now}
        if status is not None:
            vals["feed_status"] = json.loads(json.dumps(status, default=str))
        with self.engine.begin() as c:
            return c.execute(update(broker_connections).where(and_(self._bc(user_id, broker),
                                                                   broker_connections.c.feed_holder == holder))
                             .values(**vals)).rowcount == 1

    def feed_lease_release(self, user_id: int, holder: str | None = None, status: dict | None = None,
                           broker: str = "fyers") -> None:
        """holder=None releases whoever holds it (a stop request from any process)."""
        w = self._bc(user_id, broker)
        if holder is not None:
            w = and_(w, broker_connections.c.feed_holder == holder)
        vals: dict[str, Any] = {"feed_holder": None}
        if status is not None:
            vals["feed_status"] = json.loads(json.dumps(status, default=str))
        with self.engine.begin() as c:
            c.execute(update(broker_connections).where(w).values(**vals))

    def feed_runtime(self, user_id: int, broker: str = "fyers") -> dict | None:
        bc = broker_connections.c
        with self.engine.connect() as c:
            r = c.execute(select(bc.feed_holder, bc.feed_heartbeat, bc.feed_status).where(self._bc(user_id, broker))).first()
        return dict(r._mapping) if r else None

    def feed_holders(self, broker: str = "fyers") -> list[int]:
        """User ids with a stored connection (used for auto-resume)."""
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(select(broker_connections.c.user_id).where(broker_connections.c.broker == broker))]

    # ---------------- 1-minute bars (per user; data licensed to that user's broker account)
    def bars_upsert(self, user_id: int, bars: list[dict]) -> dict:
        """Insert new completed bars; a later different value for the same bar updates it and counts a revision.
        Returns {"new": [...], "revised": [...]}"""
        new, revised = [], []
        if not bars:
            return {"new": new, "revised": revised}
        with self.engine.begin() as c:
            for b in bars:
                w = (minute_bars.c.user_id == user_id) & (minute_bars.c.symbol == b["symbol"]) & (minute_bars.c.ts == b["ts"])
                cur = c.execute(select(minute_bars).where(w)).first()
                if cur is None:
                    c.execute(insert(minute_bars).values(user_id=user_id, received_at=utcnow(), revisions=0, **b))
                    new.append(b)
                elif any(abs((getattr(cur, k) or 0) - (b[k] or 0)) > 1e-9 for k in ("open", "high", "low", "close", "volume")):
                    c.execute(update(minute_bars).where(w).values(revisions=(cur.revisions or 0) + 1, received_at=utcnow(),
                                                                  **{k: b[k] for k in ("open", "high", "low", "close", "volume")}))
                    revised.append(b)
        return {"new": new, "revised": revised}

    def bars_get(self, user_id: int, symbol: str, since: str | None = None, limit: int = 2000) -> list[dict]:
        q = select(minute_bars).where(minute_bars.c.user_id == user_id, minute_bars.c.symbol == symbol)
        if since:
            q = q.where(minute_bars.c.ts >= since)
        q = q.order_by(minute_bars.c.ts.desc()).limit(limit)
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(q)]
        return rows[::-1]

    def bars_latest(self, user_id: int) -> list[dict]:
        sub = (select(minute_bars.c.symbol, func.max(minute_bars.c.ts).label("mts")).where(minute_bars.c.user_id == user_id)
               .group_by(minute_bars.c.symbol).subquery())
        q = select(minute_bars).join(sub, (minute_bars.c.symbol == sub.c.symbol) & (minute_bars.c.ts == sub.c.mts)).where(
            minute_bars.c.user_id == user_id).order_by(minute_bars.c.symbol)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def bars_cursor(self, user_id: int) -> tuple[datetime | None, int]:
        """Position after the newest stored/revised bar of this user (stream starts from here)."""
        with self.engine.connect() as c:
            r = c.execute(select(minute_bars.c.received_at, minute_bars.c.id).where(minute_bars.c.user_id == user_id)
                          .order_by(minute_bars.c.received_at.desc(), minute_bars.c.id.desc()).limit(1)).first()
        return (r[0], r[1]) if r else (None, 0)

    def bars_since(self, user_id: int, cursor: tuple[datetime | None, int], symbols: list[str] | None = None,
                   limit: int = 1000) -> tuple[list[dict], tuple[datetime | None, int]]:
        """Bars inserted or revised after `cursor`, oldest first, plus the new cursor. One writer per user (feed lease)
        keeps received_at in commit order, so this tail is gap-free across processes."""
        mb = minute_bars.c
        q = select(minute_bars).where(mb.user_id == user_id)
        t, i = cursor
        if t is not None:
            q = q.where(or_(mb.received_at > t, and_(mb.received_at == t, mb.id > i)))
        if symbols:
            q = q.where(mb.symbol.in_(symbols))
        q = q.order_by(mb.received_at, mb.id).limit(limit)
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(q)]
        return rows, ((rows[-1]["received_at"], rows[-1]["id"]) if rows else cursor)

    def bars_purge(self, user_id: int, before_ts: str) -> int:
        with self.engine.begin() as c:
            return c.execute(delete(minute_bars).where(minute_bars.c.user_id == user_id, minute_bars.c.ts < before_ts)).rowcount

    # ---------------- durable job records (v7)
    def job_record(self, job_id: str, **vals) -> None:
        vals = {k: (json.loads(json.dumps(v, default=str)) if k == "params" else v) for k, v in vals.items()}
        with self.engine.begin() as c:
            if c.execute(update(job_runs).where(job_runs.c.id == job_id).values(**vals)).rowcount == 0:
                c.execute(insert(job_runs).values(id=job_id, created=utcnow(), **vals))

    def job_runs_list(self, user_id: int | None = None, limit: int = 50) -> list[dict]:
        q = select(job_runs).order_by(job_runs.c.created.desc()).limit(limit)
        if user_id is not None:
            q = q.where(job_runs.c.user_id == user_id)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def job_run_get(self, job_id: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(job_runs).where(job_runs.c.id == job_id)).first()
        return dict(r._mapping) if r else None

    def recover_orphans(self, now: datetime, stale_s: int = 120, stale_untracked_s: int = 1800) -> dict:
        """Mark work interrupted by a crash/restart as FAILED with a visible reason (nothing disappears silently).
        A job is orphaned when it is queued/running and its holder's heartbeat is older than `stale_s`."""
        cut = now - timedelta(seconds=stale_s)
        cut2 = now - timedelta(seconds=stale_untracked_s)     # rows without a heartbeat: only clearly abandoned ones
        reason = "interrupted: the AMW process stopped or restarted before this finished - run it again"
        out = {"jobs": 0, "lab_runs": 0, "schedules": 0, "reports": 0, "scan_runs": 0}
        with self.engine.begin() as c:
            out["jobs"] = c.execute(update(job_runs).where(job_runs.c.status.in_(("queued", "running")),
                                                           or_(job_runs.c.heartbeat.is_(None), job_runs.c.heartbeat < cut))
                                    .values(status="failed", error=reason, finished=now)).rowcount
            out["lab_runs"] = c.execute(update(lab_runs).where(lab_runs.c.status.in_(("QUEUED", "RUNNING")), lab_runs.c.started < cut2)
                                        .values(status="FAILED", error=reason, finished=now)).rowcount
            out["schedules"] = c.execute(update(schedules).where(schedules.c.last_status == "STARTED", schedules.c.last_run < cut2)
                                         .values(last_status="FAILED", last_error=reason)).rowcount
            out["reports"] = c.execute(update(reports).where(reports.c.status.in_(("QUEUED", "RUNNING")), reports.c.created < cut2)
                                       .values(status="FAILED", error=reason, finished=now)).rowcount
            out["scan_runs"] = c.execute(update(scan_runs).where(scan_runs.c.status == "running", scan_runs.c.started < cut2)
                                         .values(status="failed", detail=reason, finished=now)).rowcount
        if any(out.values()):
            self.audit("recover_orphans", json.dumps(out))
        return out

    # ---------------- shared fixed-window rate counters (v7)
    def rate_take(self, key: str, window: str, limit: int) -> bool:
        """Atomically take one unit from (key, window) if fewer than `limit` were taken. Works across processes."""
        with self.engine.begin() as c:
            n = c.execute(update(rate_buckets).where(rate_buckets.c.key == key, rate_buckets.c.window == window,
                                                     rate_buckets.c.count < limit)
                          .values(count=rate_buckets.c.count + 1)).rowcount
            if n == 1:
                return True
            if c.execute(select(rate_buckets.c.count).where(rate_buckets.c.key == key, rate_buckets.c.window == window)).first():
                return False
        try:
            with self.engine.begin() as c:
                c.execute(insert(rate_buckets).values(key=key, window=window, count=1))
            return limit >= 1
        except IntegrityError:            # another process created the window first
            return self.rate_take(key, window, limit)

    def rate_purge(self, key_prefix: str, keep_windows: set[str]) -> int:
        with self.engine.begin() as c:
            return c.execute(delete(rate_buckets).where(rate_buckets.c.key.like(f"{key_prefix}%"),
                                                        rate_buckets.c.window.not_in(keep_windows))).rowcount

    # ---------------- forward tests (v7)
    def forward_enroll(self, user_id: int, rec: dict) -> bool:
        try:
            with self.engine.begin() as c:
                c.execute(insert(forward_tests).values(user_id=user_id, status="PENDING", created=utcnow(), updated=utcnow(), **rec))
            return True
        except IntegrityError:
            return False

    def forward_list(self, user_id: int, status: str | None = None, limit: int = 500) -> list[dict]:
        q = select(forward_tests).where(forward_tests.c.user_id == user_id)
        if status:
            q = q.where(forward_tests.c.status.in_(status.split(",")))
        q = q.order_by(forward_tests.c.id.desc()).limit(limit)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def forward_update(self, user_id: int, fid: int, **vals) -> None:
        with self.engine.begin() as c:
            c.execute(update(forward_tests).where(forward_tests.c.id == fid, forward_tests.c.user_id == user_id)
                      .values(updated=utcnow(), **vals))

    def forward_users(self) -> list[int]:
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(select(forward_tests.c.user_id).where(
                forward_tests.c.status.in_(("PENDING", "OPEN"))).distinct())]

    # ---------------- report retention (v7)
    def reports_older_than(self, user_id: int | None, before: datetime) -> list[dict]:
        q = select(reports).where(reports.c.created < before)
        if user_id is not None:
            q = q.where(reports.c.user_id == user_id)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def reports_completed(self, user_id: int) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(reports).where(reports.c.user_id == user_id,
                                                        reports.c.status == "COMPLETED").order_by(reports.c.created))]

    def report_mark_deleted(self, user_id: int, rid: int, reason: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(reports).where(reports.c.id == rid, reports.c.user_id == user_id)
                      .values(status="DELETED", error=reason[:300]))

    # ---------------- system notification events (v8)
    def notification_add(self, **kw) -> bool:
        """False when (user, kind, dedup_key, channel) was already recorded (duplicate suppressed by the database)."""
        try:
            with self.engine.begin() as c:
                c.execute(insert(notification_events).values(**{"created": utcnow(), **kw}))
            return True
        except IntegrityError:
            return False

    def notification_update(self, user_id: int, kind: str, dedup_key: str, channel: str, status: str, error: str | None) -> None:
        with self.engine.begin() as c:
            c.execute(update(notification_events).where(
                notification_events.c.user_id == user_id, notification_events.c.kind == kind,
                notification_events.c.dedup_key == dedup_key, notification_events.c.channel == channel)
                .values(status=status, error=(error or None) and error[:500]))

    def notifications(self, user_id: int, limit: int = 200) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(notification_events).where(
                notification_events.c.user_id == user_id).order_by(notification_events.c.created.desc()).limit(limit))]

    def notifications_sent_since(self, user_id: int, channel: str, since: datetime) -> int:
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(notification_events).where(
                notification_events.c.user_id == user_id, notification_events.c.channel == channel,
                notification_events.c.status == "sent", notification_events.c.created >= since)).scalar() or 0

    def list_users(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(users.c.id, users.c.username, users.c.role, users.c.plan))]

    def user_by_username(self, username: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(users.c.id, users.c.username, users.c.role).where(users.c.username == username)).first()
        return dict(r._mapping) if r else None

    # ---------------- instrument master (v11)
    def instruments_upsert(self, rows: list[dict], source: str) -> int:
        n = 0
        with self.engine.begin() as c:
            for r in rows:
                cur = c.execute(select(instruments).where(instruments.c.ticker == r["ticker"])).first()
                vals = {k: v for k, v in r.items() if v not in (None, "", "nan")}
                if cur is None:
                    c.execute(insert(instruments).values(sources=source, active=True, updated=utcnow(), **vals))
                    n += 1
                else:
                    srcs = sorted(set((cur.sources or "").split("|")) | {source} - {""})
                    keep = {k: v for k, v in vals.items() if k != "ticker" and (getattr(cur, k) in (None, "") or k != "isin")}
                    c.execute(update(instruments).where(instruments.c.ticker == r["ticker"])
                              .values(sources="|".join(srcs), updated=utcnow(), **keep))
        return n

    def instruments_find(self, where, limit: int = 20) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(instruments).where(where).order_by(instruments.c.symbol).limit(limit))]

    def instruments_count(self) -> int:
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(instruments)).scalar() or 0

    def report_set_hold(self, user_id: int, rid: int, hold: bool) -> bool:
        with self.engine.begin() as c:
            n = c.execute(update(reports).where(reports.c.id == rid, reports.c.user_id == user_id)
                          .values(legal_hold=bool(hold))).rowcount
        if n:
            self.audit("report_legal_hold", f"report {rid} of user {user_id} hold={bool(hold)}")
        return n == 1

