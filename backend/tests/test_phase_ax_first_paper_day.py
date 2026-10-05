"""Phase AX: the first-PAPER-day check (read-only), the worker's end-of-day summary, the housekeeping."""
import asyncio
import importlib.util
import os
import stat
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from app.ai import market_study as ms
from app.brokers.contract_symbols import wrap_contract_symbols
from app.core.enums import NotificationType
from app.db.models import NotificationRecord, SignalHistoryRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User
from app.platform import first_day
from app.workers import eod_summary
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _create, _store_broker
from tests.test_phase_ai_contract_symbols import _KiteLike
from tests.test_phase_k_commercial import _owner
from tests.test_trading_worker import _FakeBroker, _deploy, _tenant, _worker

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
FRIDAY_OPEN = datetime(2026, 9, 25, 10, 30, tzinfo=IST)
FRIDAY_EOD = datetime(2026, 9, 25, 15, 36, tzinfo=IST)


def _run(coro):
    return asyncio.run(coro)


def _tenant_user(me):
    async def go():
        async with _session_factory() as session:
            return await session.get(Tenant, me["tenant_id"]), await session.get(User, me["id"])
    return _run(go())


def _check(report, key):
    return {c.key: c for c in report.checks}[key]


def test_first_day_check_flags_the_blockers_then_passes_with_a_fake_broker():
    headers, me = _owner("ax-first@example.com", plan="business")
    tenant, user = _tenant_user(me)

    async def go(now=FRIDAY_OPEN, **kw):
        async with _session_factory() as session:
            t, u = await session.get(Tenant, tenant.id), await session.get(User, user.id)
            return await first_day.run(session, t, u, now=now, **kw)

    report = _run(go())
    assert not report.ready and report.counts["fail"] >= 3
    assert _check(report, "tenant.broker_credentials").status == "fail" and _check(report, "broker_smoke").status == "skip"
    assert _check(report, "paper_deployment").status == "fail" and _check(report, "alert_test").status == "fail"
    assert _check(report, "session").status == "ok" and "open now" in _check(report, "session").detail
    assert {c.key for c in report.checks} >= {"platform.migrations", "platform.worker", "platform.holidays", "tenant.kill_switch"}
    assert "tenant.mfa" not in {c.key for c in report.checks}      # LIVE-only hygiene rows stay out of a PAPER-day report
    text = report.render("mr")
    assert "❌" in text and "अडथळे दूर करा" in text and "काहीही बदललेले नाही" in text
    assert "Nothing was changed" in report.render("en") and report.as_dict()["read_only"] is True

    # A stored session with a VALID token, a PAPER deployment and a channel: the probes run.
    _store_broker(headers, token_status="VALID")
    created = _create(headers)
    assert created.status_code in (200, 201), created.text
    assert client.put("/api/alert-channels/webhook", headers=headers, json={"config": {"url": "https://hooks.example.com/x", "secret": "s3cr3t-webhook-secret-0123"}}).status_code in (200, 201)
    inner = _KiteLike()
    sent = []

    async def sender(channel, probe):
        sent.append((channel.channel_type, probe.title))

    report = _run(go(adapter_factory=lambda record: wrap_contract_symbols(inner), send_test_alert=True, sender=sender))
    smoke = [c for c in report.checks if c.key.startswith("broker_smoke.")]
    assert len(smoke) == 1 and smoke[0].status == "ok" and "8 ok" in smoke[0].detail and "upstox (primary)" in smoke[0].title
    assert _check(report, "paper_deployment").status == "ok" and "RELIANCE" in _check(report, "paper_deployment").detail.upper()
    fresh = [c for c in report.checks if c.key.endswith(".fresh")]
    assert len(fresh) == 1 and fresh[0].status == "fail" and fresh[0].detail == "never evaluated"       # market open, worker never ran it
    assert sent == [("WEBHOOK", "First PAPER day check")] and _check(report, "alert_test.WEBHOOK").status == "ok"
    # Without the flag no message leaves; with --no-smoke no broker call is made.
    quiet = _run(go(adapter_factory=lambda record: wrap_contract_symbols(inner), smoke=False))
    assert _check(quiet, "alert_test").status == "skip" and _check(quiet, "broker_smoke").status == "skip"
    # A broker that rejects the token is a failed probe, not a crash.
    class _Dead(_KiteLike):
        async def get_profile(self):
            raise RuntimeError("token expired")
    report = _run(go(adapter_factory=lambda record: wrap_contract_symbols(_Dead())))
    smoke = [c for c in report.checks if c.key.startswith("broker_smoke.")]
    assert smoke[0].status == "fail" and "profile" in smoke[0].detail and not report.ready
    # On a weekend the session row warns and the freshness rows are not judged.
    weekend = _run(go(now=datetime(2026, 9, 26, 10, 30, tzinfo=IST), smoke=False))
    assert _check(weekend, "session").status == "warn" and not [c for c in weekend.checks if c.key.endswith(".fresh")]


def test_pick_tenant_by_email_id_and_default():
    headers, me = _owner("ax-pick@example.com")

    async def go(selector):
        async with _session_factory() as session:
            tenant, user = await first_day.pick_tenant(session, selector)
            return (tenant.id if tenant else None, user.email if user else None)
    assert _run(go("ax-pick@example.com")) == (me["tenant_id"], "ax-pick@example.com")
    assert _run(go(str(me["tenant_id"]))) == (me["tenant_id"], "ax-pick@example.com")
    assert _run(go("nobody@example.com")) == (None, None)
    assert _run(go("999999")) == (None, None)


def test_script_parses_its_arguments():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "first_paper_day_check.py")
    spec = importlib.util.spec_from_file_location("first_paper_day_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.build_parser().parse_args(["--tenant", "ops@example.com", "--no-smoke", "--json"])
    assert args.tenant == "ops@example.com" and args.no_smoke and args.json and not args.send_test_alert and args.lang == "mr"


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def test_eod_summary_reads_the_day_and_the_worker_raises_it_once(monkeypatch):
    t = _tenant("ax-eod@example.com")
    dep_id = _deploy(t, symbol="RELIANCE")
    day_start = datetime(2026, 9, 25, 9, 15, tzinfo=IST)
    _add(
        SignalHistoryRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], strategy_id="ema_rsi_scalper_1m",
                            symbol="RELIANCE", direction="LONG", signal_time=day_start + timedelta(minutes=20), grade="HIGH_QUALITY", score=80),
        TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m", direction="LONG", mode="PAPER",
                    entry_time=day_start + timedelta(minutes=21), entry_price=100.0, quantity=10, stop_loss=98.0,
                    exit_time=day_start + timedelta(hours=2), exit_price=103.0, pnl=30.0, exit_reason="TARGET1"),
        TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m", direction="LONG", mode="PAPER",
                    entry_time=day_start + timedelta(hours=4), entry_price=101.0, quantity=10, stop_loss=99.0),
        TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m", direction="SHORT", mode="PAPER",
                    entry_time=day_start - timedelta(days=1), entry_price=101.0, quantity=10, stop_loss=103.0,
                    exit_time=day_start - timedelta(days=1, hours=-2), exit_price=100.0, pnl=10.0, exit_reason="EOD square-off"),
    )

    async def build():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.strategy_id = "ema_rsi_scalper_1m"
            await session.commit()
            return await eod_summary.build(session, t["tenant_id"], FRIDAY_EOD.astimezone(UTC))
    s = _run(build())
    assert s.day == "2026-09-25" and s.signals == 1 and s.trades_opened == 2 and s.trades_closed == 1 and s.net_pnl == 30.0
    assert s.by_mode == {"PAPER": 2} and s.exits == {"TARGET1": 1} and s.still_open == 1
    assert len(s.deployments) == 1 and s.deployments[0].signals_today == 1 and s.severity.value == "WARNING"     # a position is still open
    text = "\n".join(s.lines())
    assert "Open after square-off: 1 position(s)" in text and "Net P&L of today's exits: +30.00" in text and "not run today" in text
    assert s.title.startswith("EOD summary 2026-09-25: 1 signal(s), 2 entry(ies), 1 exit(s), net +30")

    # Due only from 15:35 IST on weekdays; the worker raises one notification per organisation per day.
    assert eod_summary.eod_due(FRIDAY_EOD) and not eod_summary.eod_due(datetime(2026, 9, 25, 15, 20, tzinfo=IST))
    assert not eod_summary.eod_due(datetime(2026, 9, 26, 16, 0, tzinfo=IST))
    worker = _worker(monkeypatch, _FakeBroker())

    async def notifications():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.event_type == NotificationType.EOD_SUMMARY.value)))
    first = _run(worker.run_cycle(now=FRIDAY_EOD.astimezone(UTC)))
    assert first.eod_summaries >= 1 and not first.market_open
    rows = _run(notifications())
    assert len(rows) == 1 and rows[0].severity == "WARNING" and "1 signal(s)" in rows[0].title
    second = _run(worker.run_cycle(now=(FRIDAY_EOD + timedelta(minutes=1)).astimezone(UTC)))
    assert second.eod_summaries == 0 and len(_run(notifications())) == 1
    # A worker restarted in the evening (fresh in-memory state) does not raise the day's summary again.
    restarted = _worker(monkeypatch, _FakeBroker())
    again = _run(restarted.run_cycle(now=(FRIDAY_EOD + timedelta(hours=4)).astimezone(UTC)))
    assert again.eod_summaries == 0 and len(_run(notifications())) == 1
    # A clean day (nothing open, nothing paused) is INFO.
    async def close_it():
        async with _session_factory() as session:
            for tr in await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"], TradeRecord.exit_time.is_(None))):
                tr.exit_time, tr.exit_price, tr.pnl, tr.exit_reason = day_start + timedelta(hours=6), 101.5, 5.0, "EOD square-off"
            await session.commit()
            return await eod_summary.build(session, t["tenant_id"], FRIDAY_EOD.astimezone(UTC))
    clean = _run(close_it())
    assert clean.severity.value == "INFO" and clean.still_open == 0 and clean.exits == {"TARGET1": 1, "EOD SQUARE-OFF": 1}


def test_opening_range_levels_without_timedelta_index_arithmetic():
    start = datetime(2026, 9, 21, 3, 45, tzinfo=UTC)
    idx = pd.DatetimeIndex([start + timedelta(minutes=i) for i in range(40)])
    closes = [100 + (i % 5) for i in range(40)]
    df = pd.DataFrame({"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes, "volume": [10.0] * 40}, index=idx)
    out = ms.levels(df, None)
    assert out["or_high"] == 105.0 and out["or_low"] == 99.0 and out["day_open"] == 100.0
    short = ms.levels(df.iloc[:10], None)
    assert "or_high" not in short            # the 15-minute range is published only once it is complete


def test_offsite_sync_copies_dumps_and_mirrors_the_wal_archive(tmp_path):
    """The off-site script with a recording stand-in for rclone: dumps are copied (never deleted off-site), the WAL
    archive is synced, --once exits with the result."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "backup" / "offsite_sync.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    fake = fake_bin / "rclone"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit ${{FAKE_RCLONE_EXIT:-0}}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "backups").mkdir()
    (tmp_path / "wal").mkdir()
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "OFFSITE_REMOTE": "spaces:test-bucket",
           "BACKUP_DIR": str(tmp_path / "backups"), "WAL_ARCHIVE_DIR": str(tmp_path / "wal")}
    run = subprocess.run(["sh", str(script), "--once"], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    calls = log.read_text().splitlines()
    assert calls[0].startswith(f"copy {tmp_path / 'backups'} spaces:test-bucket/backups") and calls[1].startswith(f"sync {tmp_path / 'wal'} spaces:test-bucket/wal_archive")
    assert "synced to spaces:test-bucket" in run.stderr
    failed = subprocess.run(["sh", str(script), "--once"], env={**env, "FAKE_RCLONE_EXIT": "3"}, capture_output=True, text=True)
    assert failed.returncode != 0
    assert subprocess.run(["sh", str(script), "--once"], env={k: v for k, v in env.items() if k != "OFFSITE_REMOTE"}, capture_output=True, text=True).returncode != 0
