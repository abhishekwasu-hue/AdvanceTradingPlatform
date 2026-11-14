"""Runner S2 (docs/design/USER_RUNNER.md, step S2): the runner's own loop, its lock, switches, audit log and seams.

1. The loop (a separate process, tests/runner_s2_loop_job.py, on a local SQLite file, no Postgres, no Redis): PAPER-only
   fake feed -> closed bars only -> signal -> PAPER entry through the one pipeline -> one entry per signal bar -> with
   the local kill switch on and the F&O ban list unknown (no ATP link) the stop still closes the position
   (ADR-0004); a LIVE deployment places nothing; the audit log holds every intent and result, its chain intact; the
   process never loads a forbidden module (billing and the data lake included since S2).
2. The in-process lock: one runner per folder, one cycle at a time.
3. LIVE: his own local switch, default OFF, read fresh; a test feed never trades LIVE.
4. The F&O ban list in runner mode: unknown -> PAPER allowed with a note, LIVE refused; known -> as listed. The server's
   path is unchanged (tests/test_d7_fo_ban.py).
5. The billing meter is never called in runner mode.
"""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.runner.audit import AuditLog, read, verify
from app.runner.lock import RunnerLock, RunnerLockHeld
from app.runner.manifest import RUNTIME_FORBIDDEN, is_forbidden
from app.runner.settings import RunnerSettings

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def loop(tmp_path_factory) -> dict:
    home = tmp_path_factory.mktemp("runner-home")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL", "ATP_RUNNER"))}
    env.update(PYTHONPATH=str(BACKEND))
    run = subprocess.run([sys.executable, "tests/runner_s2_loop_job.py", str(home)], cwd=BACKEND, env=env, capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    line = next(x for x in run.stdout.splitlines() if x.startswith("RUNNER_S2 "))
    report = json.loads(line[len("RUNNER_S2 "):])
    report["home"] = home
    return report


def test_the_loop_runs_on_the_runners_own_sqlite_file_in_runner_mode(loop):
    assert loop["runner_mode"] is True and loop["dialect"] == "sqlite"
    assert Path(loop["database"]) == loop["home"] / "runner.db"
    assert loop["ban_list"] is None and loop["live_switch"] is False          # no ATP link yet; his LIVE switch is off


def test_fake_feed_signal_becomes_one_paper_entry_on_closed_bars(loop):
    entry = loop["after_entry"]
    assert entry["errors"] == [] and entry["placed"] == 1 and entry["trades"] == 1 and entry["open"] == 1
    assert entry["modes"] == ["PAPER"] and entry["deployment"] is not None
    # the forming bar would undo the cross: the entry proves only closed bars were read (no look-ahead)
    assert entry["again_placed"] == 0                                         # one entry per signal bar
    assert loop["paper_error"] is None


def test_a_live_deployment_places_nothing(loop):
    assert loop["live_error"]                                                 # says why, in plain words
    assert loop["after_exit"]["order_modes"] == ["PAPER"] and loop["after_exit"]["orders"] == 1


def test_the_stop_exit_runs_with_the_kill_switch_on_and_no_atp_link(loop):
    out = loop["after_exit"]
    assert out["errors"] == [] and out["closed"] == 1 and out["open"] == 0 and out["reasons"] == ["Stop Loss"]
    assert out["paused"] and "exits continue" in out["paused"]
    assert out["orders"] == 1                                                 # nothing new placed while killed


def test_the_audit_log_holds_every_intent_and_result_and_its_chain_is_intact(loop):
    kinds = loop["audit_kinds"]
    assert kinds.count("entry_intent") == 1 and kinds.count("entry_result") == 1 and kinds.count("exit_result") == 1
    assert kinds.index("entry_intent") < kinds.index("entry_result") < kinds.index("exit_result")
    assert loop["audit_ok"] is True
    result = next(r for r in loop["audit_rows"] if r["kind"] == "entry_result")["data"]
    assert result["executed"] is True and result["order_mode"] == "PAPER" and result["idempotency_key"].startswith("deployment:")
    exit_row = next(r for r in loop["audit_rows"] if r["kind"] == "exit_result")["data"]
    assert exit_row["reason"] == "Stop Loss" and exit_row["mode"] == "PAPER"
    text = json.dumps(loop["audit_rows"]).lower()
    assert "password" not in text and "token" not in text.replace("idempotency_key", "")


def test_the_loop_process_never_loads_a_forbidden_module(loop):
    assert [m for m in loop["loaded"] if is_forbidden(m, RUNTIME_FORBIDDEN)] == []
    assert {"app.runner.loop", "app.execution.signal_execution", "app.trading.position_monitor"} <= set(loop["loaded"])
    assert "app.billing.service" not in loop["loaded"] and "app.market_lake.asof" not in loop["loaded"]


def test_one_runner_per_folder_and_one_cycle_at_a_time(tmp_path):
    import asyncio
    first, second = RunnerLock(tmp_path / "runner.lock"), RunnerLock(tmp_path / "runner.lock")
    first.acquire()
    with pytest.raises(RunnerLockHeld):
        second.acquire()
    assert (tmp_path / "runner.lock").read_text() == str(os.getpid())
    first.release()
    second.acquire()                                                          # free again once released
    second.release()

    async def nested() -> tuple[bool, bool]:
        async with first.cycle() as outer:
            async with first.cycle() as inner:
                return outer, inner
    assert asyncio.run(nested()) == (True, False)                             # a second cycle is skipped, never queued

    async def after() -> bool:
        async with first.cycle() as again:
            return again
    assert asyncio.run(after()) is True


def test_his_live_switch_is_off_by_default_and_only_on_reads_on(tmp_path):
    settings = RunnerSettings.load(tmp_path)
    assert settings.live_enabled() is False
    for text, on in (("on\n", True), ("ON", True), ("yes", False), ("", False), ("off", False)):
        settings.live_switch_path.write_text(text)
        assert settings.live_enabled() is on, text
    settings.live_switch_path.unlink()
    assert settings.live_enabled() is False and settings.killed() is False


def test_live_needs_a_real_broker_and_his_switch_read_fresh(tmp_path):
    from app.runner.feed import FakeFeed
    from app.runner.loop import LIVE_NEEDS_BROKER, LIVE_SWITCH_OFF, Runner
    settings = RunnerSettings.load(tmp_path)
    assert Runner(settings, FakeFeed())._live_refusal() == LIVE_NEEDS_BROKER

    class Real:
        paper_only = False
        broker = object()
    runner = Runner(settings, Real())                                         # type: ignore[arg-type]
    assert runner._live_refusal() == LIVE_SWITCH_OFF
    settings.live_switch_path.write_text("on")
    assert runner._live_refusal() is None
    settings.live_switch_path.unlink()
    assert runner._live_refusal() == LIVE_SWITCH_OFF                          # read every time, never cached


def test_the_cli_switches_write_only_his_own_files(tmp_path, capsys):
    from app.runner.__main__ import main
    assert main(["live", "status", "--home", str(tmp_path)]) == 0 and "OFF" in capsys.readouterr().out
    assert main(["live", "on", "--home", str(tmp_path)]) == 0 and "ON" in capsys.readouterr().out
    assert main(["live", "off", "--home", str(tmp_path)]) == 0 and "OFF" in capsys.readouterr().out
    assert main(["kill", "--home", str(tmp_path)]) == 0 and (tmp_path / "KILL").exists()
    assert main(["resume", "--home", str(tmp_path)]) == 0 and not (tmp_path / "KILL").exists()


def test_a_changed_or_missing_audit_row_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.write("entry_intent", n=i)
    assert verify(path) == (True, 3, None)
    AuditLog(path).write("entry_result", n=3)                                 # a restart continues the chain
    assert verify(path)[0] is True and [r["seq"] for r in read(path)] == [1, 2, 3, 4]
    lines = path.read_text().splitlines()
    row = json.loads(lines[1])
    row["data"]["n"] = 99
    path.write_text("\n".join([lines[0], json.dumps(row), *lines[2:]]) + "\n")
    assert verify(path)[0] is False
    path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")
    assert verify(path)[0] is False


@pytest.fixture
def runner_mode(monkeypatch):
    from app.core import runtime_mode
    monkeypatch.setattr(runtime_mode, "_runner", True)
    monkeypatch.setattr(runtime_mode, "_ban_list", None)
    return runtime_mode


def test_unknown_ban_list_allows_paper_and_refuses_a_live_fo_entry(runner_mode, monkeypatch):
    import asyncio

    from app.compliance import fo_limits
    from app.core import config
    when = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)
    refusal, note = asyncio.run(fo_limits.entry_refusal(None, "ANYUND", when, mode="LIVE"))   # type: ignore[arg-type]
    assert refusal == fo_limits.UNKNOWN_LIVE and "exits are not affected" in refusal and note is None
    refusal, note = asyncio.run(fo_limits.entry_refusal(None, "ANYUND", when, mode="PAPER"))  # type: ignore[arg-type]
    assert refusal is None and note and "not checked" in note
    monkeypatch.setattr(config, "FO_BAN_CHECK_ENABLED", True)
    runner_mode.set_ban_list(["anyund"])
    assert asyncio.run(fo_limits.entry_refusal(None, "ANYUND", when, mode="PAPER"))[0]     # type: ignore[arg-type]
    runner_mode.set_ban_list([])
    assert asyncio.run(fo_limits.entry_refusal(None, "ANYUND", when, mode="LIVE")) == (None, None)  # type: ignore[arg-type]


def test_the_billing_meter_is_not_called_in_runner_mode(runner_mode, monkeypatch):
    import ast
    source = (BACKEND / "app" / "execution" / "order_persistence.py").read_text()
    tree = ast.parse(source)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app.billing") for n in top)
    assert "if not runtime_mode.is_runner():" in source
    assert runner_mode.is_runner() is True


def test_only_the_user_turns_his_kill_switch_on_or_off(tmp_path):
    # owner rule (14 Nov): the kill switch is the user's own command; ATP / the admin never stops a user's runner
    from app.runner.settings import KILL_ORIGINS, NOT_YOURS
    settings = RunnerSettings.load(tmp_path)
    assert settings.killed() is False                                         # default OFF
    for origin in ("atp", "atp_admin", "admin", "platform", ""):
        with pytest.raises(PermissionError, match=NOT_YOURS):
            settings.set_killed(True, origin=origin)
        assert settings.killed() is False
    assert KILL_ORIGINS == ("user_local", "user_signed")
    settings.set_killed(True, origin="user_local")
    for origin in ("atp_admin", "platform"):
        with pytest.raises(PermissionError):
            settings.set_killed(False, origin=origin)                         # nor turn his kill off
    assert settings.killed() is True
    settings.set_killed(False, origin="user_signed")                          # S4: his own ATP button, signed for him
    assert settings.killed() is False


def test_no_runner_code_writes_the_kill_switch_except_its_one_setter():
    for path in (BACKEND / "app").rglob("*.py"):
        text = path.read_text()
        if path.name == "settings.py" and path.parent.name == "runner":
            continue
        assert "kill_path.write" not in text and "kill_path.unlink" not in text, path


def test_his_broker_session_goes_out_behind_the_ops_throttle_by_default(tmp_path):
    from app.brokers.rate_budget import RateLimitedBroker
    from app.runner.feed import protected

    class Adapter:
        name = "angel_one"
        history_by_date = False
        daily_days_per_request = 365
        access_token = None
    settings = RunnerSettings.load(tmp_path)
    assert settings.ops_throttle is True                                      # rule R-9: default ON
    wrapped = protected(Adapter(), ops_throttle=settings.ops_throttle)        # type: ignore[arg-type]
    assert isinstance(wrapped, RateLimitedBroker) and wrapped.ops is not None and wrapped.ops.rate == 10
    assert wrapped.credential_id is None                                      # in-process buckets only (no Redis)
    (tmp_path / "runner.json").write_text('{"ops_throttle": false}')
    assert RunnerSettings.load(tmp_path).ops_throttle is False
    assert protected(Adapter(), ops_throttle=False).ops is None               # type: ignore[arg-type,attr-defined]
