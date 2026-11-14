"""Runner S2 loop job (docs/design/USER_RUNNER.md, step S2): the runner's own loop on a local SQLite FILE.

Run as a separate process by tests/test_runner_s2_loop.py with the runner folder as argv[1]: runner mode, the folder's
SQLite file and no Redis are set BEFORE the pipeline is imported (as `python -m app.runner` does). Steps:
  1. one organisation + one user + his saved DSL strategy (CLOSE crosses above SMA(5), his own ATR stop) + a PAPER
     deployment, and a LIVE deployment of the same strategy (his LIVE switch is off);
  2. a PAPER-only fake feed: a steady fall, then a closed bar back above the SMA, then a FORMING bar that would undo
     the cross (closed bars only: it must not count);
  3. cycle 1 -> signal -> PAPER entry through execute_signal_for_user; the LIVE deployment places nothing;
  4. cycle 1 again on the same bar -> no second entry;
  5. the local kill switch on, the F&O ban list unknown (no ATP link) and the price past the stop -> cycle 2 closes the
     position on its stop (exits never blocked) and places nothing new;
Prints ONE json line: what happened, the audit log, and the app modules the process loaded.
Generic made-up numbers only (no market data, no symbol-specific content).
"""
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.runner.__main__ import prepare_process
from app.runner.settings import RunnerSettings

SETTINGS = RunnerSettings.load(Path(sys.argv[1]))
prepare_process(SETTINGS)

NOW = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)          # a weekday, inside the NSE session (10:00 IST)


def bars() -> pd.DataFrame:
    closes = [100.0 - 0.5 * i for i in range(30)] + [90.0, 80.0]    # ... fall, the cross bar, then the forming bar
    index = pd.date_range(end=NOW, periods=len(closes), freq="5min", tz="UTC")   # the last bar starts at NOW (forming)
    df = pd.DataFrame({"open": closes, "high": [c + 0.4 for c in closes], "low": [c - 0.4 for c in closes],
                       "close": closes, "volume": [1000.0] * len(closes)}, index=index)
    df.iloc[-2, df.columns.get_loc("open")] = 85.0
    df.iloc[-2, df.columns.get_loc("low")] = 84.8
    return df


async def main() -> dict:
    from sqlalchemy import select

    from app.core.runtime_mode import ban_list, is_runner
    from app.db.base import Base
    from app.db.models import CustomStrategyRecord, OrderRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User
    from app.db.session import _session_factory, get_engine
    from app.runner import audit
    from app.runner.feed import FakeFeed
    from app.runner.loop import LIVE_NEEDS_BROKER, Runner
    from app.strategy_engine.declarative import Condition, CustomStrategyConfig, Operand

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    config = CustomStrategyConfig(
        name="runner-s2", timeframe="5min", stop_loss_atr_mult=1.0, atr_period=5,
        long_conditions=[Condition(left=Operand(type="indicator", indicator="CLOSE"), operator="CROSSES_ABOVE",
                                   right=Operand(type="indicator", indicator="SMA", period=5))])
    async with _session_factory() as session:
        tenant = Tenant(name="runner-s2")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email="runner-s2@example.com", hashed_password="-")
        session.add(user)
        await session.flush()
        record = CustomStrategyRecord(tenant_id=tenant.id, user_id=user.id, name="runner-s2", config_json=config.model_dump_json())
        session.add(record)
        await session.flush()
        common = dict(tenant_id=tenant.id, strategy_id=f"custom:{record.id}", symbol="RUNNERX", exchange="NSE", timeframe="5min",
                      status="ACTIVE", entry_mode="AUTO", holding="SWING", order_style="MARKET", max_lots=1, created_by=user.id)
        paper = StrategyDeploymentRecord(mode="PAPER", **common)
        live = StrategyDeploymentRecord(mode="LIVE", **common)
        session.add_all([paper, live])
        await session.commit()
        tenant_id, paper_id, live_id = tenant.id, paper.id, live.id

    feed = FakeFeed()
    feed.set_frames("RUNNERX", {"5min": bars()})
    feed.set_price("RUNNERX", 90.0)
    runner = Runner(SETTINGS, feed)
    out: dict = {"runner_mode": is_runner(), "ban_list": ban_list(), "live_switch": SETTINGS.live_enabled(),
                 "live_needs_broker": LIVE_NEEDS_BROKER}

    first = await runner.run_cycle(now=NOW)
    again = await runner.run_cycle(now=NOW + timedelta(seconds=20))
    async with _session_factory() as session:
        trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id)))
        out["after_entry"] = dict(placed=first.entries_placed, again_placed=again.entries_placed, errors=first.errors + again.errors,
                                  trades=len(trades), open=sum(1 for t in trades if t.exit_time is None),
                                  modes=sorted({t.mode for t in trades}), stop=trades[0].stop_loss if trades else None,
                                  deployment=trades[0].deployment_id if trades else None)
        live_dep = await session.get(StrategyDeploymentRecord, live_id)
        paper_dep = await session.get(StrategyDeploymentRecord, paper_id)
        out["live_error"] = live_dep.last_error
        out["paper_error"] = paper_dep.last_error
        stop = trades[0].stop_loss if trades else 0.0

    SETTINGS.set_killed(True, origin="user_local")             # his own kill switch: entries stop, exits must not
    feed.set_price("RUNNERX", (stop or 0.0) - 1.0)
    second = await runner.run_cycle(now=NOW + timedelta(minutes=1))
    async with _session_factory() as session:
        trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id)))
        orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id)))
        out["after_exit"] = dict(closed=second.exits_closed, paused=second.entries_paused, errors=second.errors,
                                 open=sum(1 for t in trades if t.exit_time is None),
                                 reasons=[t.exit_reason for t in trades if t.exit_time is not None],
                                 orders=len(orders), order_modes=sorted({o.mode for o in orders}))
    out["audit_kinds"] = [row["kind"] for row in audit.read(SETTINGS.audit_path)]
    out["audit_rows"] = audit.read(SETTINGS.audit_path)
    out["audit_ok"] = audit.verify(SETTINGS.audit_path)[0]
    out["dialect"] = engine.dialect.name
    out["database"] = str(engine.url.database)
    await engine.dispose()
    return out


if __name__ == "__main__":
    report = asyncio.run(main())
    report["loaded"] = sorted(name for name in sys.modules if name == "app" or name.startswith(("app.", "atp_private")))
    print("RUNNER_S2 " + json.dumps(report, default=str))
