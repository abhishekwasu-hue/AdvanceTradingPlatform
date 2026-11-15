"""Runner S1 spike (docs/design/USER_RUNNER.md, step S1): the ONE entry pipeline on a local SQLite FILE.

Run as a separate process by tests/test_runner_s1_boundary.py, with DATABASE_URL pointing at a fresh SQLite file and
REDIS_URL at a closed port (no Postgres, no Redis). Steps, all through the server's own functions (no second copy):
  1. the schema from the models (Base.metadata.create_all) on the file;
  2. one organisation + one user (the runner holds one user);
  3. a DSL strategy (CustomStrategyConfig -> DeclarativeStrategy, ADR-0005) reads made-up generic bars -> a Signal;
  4. execute_signal_for_user(mode=PAPER) - entry refusals (kill switch, platform controls, by_default), risk, router,
     PaperBroker, order state machine, persisted position;
  5. position_monitor.monitor_open_positions with a price past the stop -> the one close path closes it (ADR-0004).
Prints ONE json line: what happened, and which app modules the run loaded (the boundary test checks them).
The bars are generic made-up numbers (no market data, no symbol-specific content).
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd


async def main() -> dict:
    from sqlalchemy import select

    from app.core.models import RiskConfig
    from app.db.base import Base
    from app.db.models import OrderRecord, Tenant, TradeRecord, User
    from app.db.session import _session_factory, get_engine
    from app.execution.signal_execution import execute_signal_for_user
    from app.strategy_engine.declarative import Condition, CustomStrategyConfig, DeclarativeStrategy, Operand
    from app.trading.position_monitor import monitor_open_positions

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _session_factory() as session:
        tenant = Tenant(name="runner-spike")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email="runner-spike@example.com", hashed_password="-")
        session.add(user)
        await session.commit()
        tenant_id, user_id = tenant.id, user.id

    # A DSL template: CLOSE crosses above SMA(5); the stop is the user's own ATR multiple (set here, as he would).
    config = CustomStrategyConfig(
        name="spike", timeframe="5min", stop_loss_atr_mult=1.0, atr_period=5,
        long_conditions=[Condition(left=Operand(type="indicator", indicator="CLOSE"), operator="CROSSES_ABOVE",
                                   right=Operand(type="indicator", indicator="SMA", period=5))])
    strategy = DeclarativeStrategy("custom:spike", config)
    closes = [100.0 - 0.5 * i for i in range(30)] + [90.0]   # a steady fall, then the last bar closes back above the SMA
    index = pd.date_range(end=datetime.now(timezone.utc).replace(second=0, microsecond=0), periods=len(closes), freq="5min")
    df = pd.DataFrame({"open": closes, "high": [c + 0.4 for c in closes], "low": [c - 0.4 for c in closes],
                       "close": closes, "volume": [1000.0] * len(closes)}, index=index)
    df.iloc[-1, df.columns.get_loc("open")] = 85.0
    df.iloc[-1, df.columns.get_loc("low")] = 84.8
    signal = strategy.analyze({"5min": df}, "SPIKE")

    out: dict = {"signal": signal.direction.value, "entry": signal.entry, "stop": signal.stop_loss}
    async with _session_factory() as session:
        user = await session.get(User, user_id)
        result, order = await execute_signal_for_user(
            session, user, mode="PAPER", strategy_id="custom:spike", signal=signal, idempotency_key="runner-spike-1",
            risk_config=RiskConfig(capital=100000.0), max_quantity=1, holding="SWING", order_style="MARKET")   # the bot's own values
        out.update(order_status=order.status, executed=bool(result.executed), order_mode=order.mode,
                   reasons=list(result.reasons)[:5])

    async with _session_factory() as session:
        trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id)))
        out["trades_open"] = sum(1 for t in trades if t.exit_time is None)
        below = (signal.stop_loss or 0.0) - 1.0

        async def price(symbol, exchange):
            return below
        outcomes = await monitor_open_positions(session, tenant_id, price, now=datetime.now(timezone.utc) + timedelta(minutes=1))
        out["closed"] = sum(1 for o in outcomes if o.closed)
    async with _session_factory() as session:
        trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id)))
        orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id)))
        out["trades_closed"] = sum(1 for t in trades if t.exit_time is not None)
        out["exit_reason"] = trades[0].exit_reason if trades else None
        out["orders"] = sorted({o.status for o in orders})
    out["dialect"] = engine.dialect.name
    out["database"] = str(engine.url.database)
    await engine.dispose()
    return out


if __name__ == "__main__":
    report = asyncio.run(main())
    report["loaded"] = sorted(name for name in sys.modules if name == "app" or name.startswith(("app.", "atp_private")))
    print("RUNNER_SPIKE " + json.dumps(report, default=str))
