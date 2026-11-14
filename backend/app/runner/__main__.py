"""Runner S2 entry point: `python -m app.runner <command> --home <folder>`.

  run [--once] [--broker angel_one]   the loop (exits first, then entries) until Ctrl+C / SIGTERM; --once: one cycle
  live on | off | status              HIS OWN LIVE switch for this runner (default off); only this command writes it
  kill | resume                       YOUR kill switch: no new entries while on; exits always run (only you set it)
  audit-verify                        checks the local audit log's hash chain

Runner mode, the local SQLite file and "no Redis" are set BEFORE any pipeline module is imported (the database engine
is created at import). Broker credentials: until the runner's keystore (step S5) they are read from `<home>/broker.json`,
which must be readable by its owner only (mode 600); nothing from it is ever logged or written to the audit log.
Without `--broker` the runner has no broker: PAPER only, with no market data (used by dry runs and tests).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import stat
import sys
from typing import Any

from app.runner.settings import LIVE_ON, RunnerSettings

CLOSED_REDIS = "redis://127.0.0.1:1/0"   # no Redis in the runner: a closed port answers at once (never a hang)


def prepare_process(settings: RunnerSettings) -> None:
    """Runner mode + the local store, before the pipeline is imported."""
    settings.home.mkdir(parents=True, exist_ok=True)
    os.environ["ATP_RUNNER_MODE"] = "1"
    os.environ["DATABASE_URL"] = settings.database_url
    os.environ.setdefault("REDIS_URL", CLOSED_REDIS)
    from app.core.runtime_mode import enable_runner_mode
    enable_runner_mode()


def _broker_credentials(settings: RunnerSettings) -> Any:
    path = settings.home / "broker.json"
    if not path.exists():
        raise SystemExit(f"No broker details in {path} (the keystore arrives with a later runner step)")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"{path} must be readable by you only (chmod 600 {path})")
    from app.brokers.models import BrokerCredentials
    return BrokerCredentials(**json.loads(path.read_text()))


def build_feed(settings: RunnerSettings, broker_name: str | None) -> Any:
    from app.runner.feed import BrokerFeed, FakeFeed, protected
    if not broker_name:
        return FakeFeed()
    if broker_name != "angel_one":
        raise SystemExit("The runner's first broker is Angel One (--broker angel_one)")
    from app.brokers.angel_one import AngelOneBroker
    broker = protected(AngelOneBroker(_broker_credentials(settings)), ops_throttle=settings.ops_throttle)
    return BrokerFeed(broker, history_days_intraday=settings.history_days_intraday,
                      history_days_daily=settings.history_days_daily)


async def _create_schema() -> None:
    from app.db.base import Base
    from app.db.session import get_engine
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _run(settings: RunnerSettings, broker_name: str | None, once: bool) -> int:
    from app.runner.loop import Runner
    await _create_schema()
    runner = Runner(settings, build_feed(settings, broker_name))
    if once:
        runner.lock.acquire()
        try:
            summary = await runner.run_cycle()
        finally:
            runner.lock.release()
        print(json.dumps({"exits_closed": summary.exits_closed, "entries_placed": summary.entries_placed,
                          "deployments_evaluated": summary.deployments_evaluated, "entries_paused": summary.entries_paused,
                          "errors": summary.errors}, default=str))
        return 0
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await runner.run_forever(stop)
    from app.db.session import get_engine
    await get_engine().dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.runner", description="ATP runner (your own machine)")
    parser.add_argument("command", choices=["run", "live", "kill", "resume", "audit-verify"])
    parser.add_argument("value", nargs="?", help="live: on | off | status")
    parser.add_argument("--home", default=os.environ.get("ATP_RUNNER_HOME", "~/.atp-runner"))
    parser.add_argument("--broker", default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    settings = RunnerSettings.load(args.home)

    if args.command == "live":
        if args.value == "on":
            settings.home.mkdir(parents=True, exist_ok=True)
            settings.live_switch_path.write_text(LIVE_ON + "\n")
        elif args.value == "off":
            settings.live_switch_path.unlink(missing_ok=True)
        elif args.value not in (None, "status"):
            parser.error("live: on | off | status")
        print("LIVE on this runner: " + ("ON" if settings.live_enabled() else "OFF"))
        return 0
    if args.command == "kill":
        settings.set_killed(True, origin="user_local")          # his own command on his runner
        print("Kill switch ON: no new entries; exits continue")
        return 0
    if args.command == "resume":
        settings.set_killed(False, origin="user_local")
        print("Kill switch OFF")
        return 0
    if args.command == "audit-verify":
        from app.runner.audit import verify
        ok, rows, problem = verify(settings.audit_path)
        print(f"Audit log: {rows} rows, " + ("chain intact" if ok else f"BROKEN - {problem}"))
        return 0 if ok else 1
    prepare_process(settings)
    return asyncio.run(_run(settings, args.broker, args.once))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "prepare_process", "build_feed"]
