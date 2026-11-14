"""Runner S4b loop job (docs/design/USER_RUNNER.md, step S4b): options / futures and multi-leg deployments on the runner.

Run as a separate process by tests/test_runner_s4b_fno.py with the runner folder as argv[1] (runner mode, the folder's
SQLite file, no Redis - as `python -m app.runner` does). The contracts reach the store through S4's own
`sync.apply_reference` (ATP's instrument master spelling); a PAPER-only fake feed serves the underlying bars, the
premiums and the index's Daily bars. Deployments (one saved DSL strategy: CLOSE crosses above SMA(5), his own ATR stop):
  A  index option, single leg: buy the ATM CE, nearest expiry, his own premium stop, INTRADAY;
  B  index bull put spread (two legs), width 2 steps, INTRADAY - smart execution ON in the local store (hedge first);
  C  stock option, single leg, INTRADAY - entered while the stock is not banned;
  D  the same stock, set ACTIVE after the ban list names it - its entry must be refused;
  E  index option, single leg, OTM by 2 steps, his own SWING holding - held past its expiry;
  F  index future, nearest expiry, INTRADAY.
Steps: T1 entries -> the ban list names the stock, D active -> T2 A's and C's premiums below their stops (C's exit runs
with the ban on), D refused -> T3 his square-off time (B's two legs closed, E kept) -> T4 two days later: E settled at the
expiry-day close of the Daily bar of that day (the forming Daily bar after it says otherwise: not read).
Prints ONE json line. Generic made-up numbers only (no market data).
"""
import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.runner.__main__ import prepare_process
from app.runner.settings import RunnerSettings

SETTINGS = RunnerSettings.load(Path(sys.argv[1]))
prepare_process(SETTINGS)

T1 = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)          # a weekday, inside the NSE session (10:00 IST)
T2 = T1 + timedelta(minutes=1)
T3 = datetime(2026, 11, 16, 9, 46, tzinfo=UTC)          # 15:16 IST: after his square-off time (15:15)
T4 = datetime(2026, 11, 18, 4, 30, tzinfo=UTC)          # two days later, 10:00 IST
NEAR, NEXT, STOCK_EXPIRY = date(2026, 11, 17), date(2026, 11, 24), date(2026, 11, 26)
INDEX, STOCK = "NIFTY 50", "RUNNERB"


def bars(top: float, step: float, cross: float, forming: float) -> pd.DataFrame:
    closes = [top - step * i for i in range(30)] + [cross, forming]   # a fall, the cross bar, then the forming bar
    index = pd.date_range(end=T1, periods=len(closes), freq="5min", tz="UTC")
    df = pd.DataFrame({"open": closes, "high": [c + step for c in closes], "low": [c - step for c in closes],
                       "close": closes, "volume": [1000.0] * len(closes)}, index=index)
    return df


def daily() -> pd.DataFrame:
    starts = [pd.Timestamp(d, tz="Asia/Kolkata").tz_convert("UTC") for d in ("2026-11-16", "2026-11-17", "2026-11-18")]
    closes = [24500.0, 24700.0, 25000.0]                     # 17 Nov: the expiry day; 18 Nov: still forming at T4
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [0.0] * 3},
                        index=pd.DatetimeIndex(starts))


def contracts() -> list[dict]:
    rows, key = [], 5000
    def add(und: str, expiry: date, strike: float, right: str, lot: int) -> None:
        nonlocal key
        key += 1
        rows.append({"broker": "upstox", "exchange": "NFO", "segment": "NSE_FO", "instrument_key": f"NSE_FO|{key}",
                     "tradingsymbol": f"{und} {int(strike)} {right} {expiry.strftime('%d %b %y').upper()}", "name": und,
                     "underlying": und, "instrument_type": right, "expiry": expiry.isoformat(), "strike": strike,
                     "lot_size": lot, "tick_size": 0.05, "weekly": True})
    for expiry in (NEAR, NEXT):
        for strike in range(24000, 25001, 50):
            for right in ("CE", "PE"):
                add("NIFTY", expiry, float(strike), right, 75)
    key += 1
    rows.append({"broker": "upstox", "exchange": "NFO", "segment": "NSE_FO", "instrument_key": f"NSE_FO|{key}",
                 "tradingsymbol": f"NIFTY FUT {NEAR.strftime('%d %b %y').upper()}", "name": "NIFTY", "underlying": "NIFTY",
                 "instrument_type": "FUT", "expiry": NEAR.isoformat(), "strike": None, "lot_size": 75, "tick_size": 0.05,
                 "weekly": False})
    for strike in range(900, 1101, 20):
        for right in ("CE", "PE"):
            add(STOCK, STOCK_EXPIRY, float(strike), right, 500)
    return rows


async def main() -> dict:
    from sqlalchemy import select

    from app.core import config
    from app.db.base import Base
    from app.db.models import CustomStrategyRecord, OrderRecord, PlatformControlRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User, UserPrefsRecord
    from app.db.session import _session_factory, get_engine
    from app.execution import paper_adapter
    from app.runner import audit, sync
    from app.runner.feed import FakeFeed
    from app.runner.loop import Runner
    from app.strategy_engine.declarative import Condition, CustomStrategyConfig, Operand

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    config_ = CustomStrategyConfig(
        name="runner-s4b", timeframe="5min", stop_loss_atr_mult=1.0, atr_period=5,
        long_conditions=[Condition(left=Operand(type="indicator", indicator="CLOSE"), operator="CROSSES_ABOVE",
                                   right=Operand(type="indicator", indicator="SMA", period=5))])
    async with _session_factory() as session:
        tenant = Tenant(name="runner-s4b")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email="runner-s4b@example.com", hashed_password="-")
        session.add(user)
        await session.flush()
        records = {k: CustomStrategyRecord(tenant_id=tenant.id, user_id=user.id, name=f"runner-s4b-{k}", config_json=config_.model_dump_json())
                   for k in "ABCDEF"}                         # one saved strategy per deployment (one row per strategy and symbol)
        session.add_all(records.values())
        # his own trade settings: the square-off time (his, 15:15); the smart-execution engine on in this local store
        session.add(UserPrefsRecord(tenant_id=tenant.id, user_id=user.id, namespace="trade_settings",
                                    value_json=json.dumps({"values": {"exits.square_off_time": "15:15"}})))
        session.add(PlatformControlRecord(key="feature_flags", value_json=json.dumps({"flags": {"smart_execution": {"on": True}}})))
        await session.flush()
        common = dict(tenant_id=tenant.id, exchange="NSE", timeframe="5min", mode="PAPER",
                      status="ACTIVE", entry_mode="AUTO", order_style="MARKET", max_lots=1, created_by=user.id,
                      instrument_kind="OPTION", option_position="BUY", expiry_rule="NEAREST", strike_rule="ATM", strike_offset=0)
        deps = {
            "A": StrategyDeploymentRecord(strategy_id=f"custom:{records['A'].id}", symbol=INDEX, holding="INTRADAY", premium_stop_pct=30.0, **common),
            "B": StrategyDeploymentRecord(strategy_id=f"custom:{records['B'].id}", symbol=INDEX, holding="INTRADAY", **{**common, "option_strategy": "BULL_PUT_SPREAD",
                                                                              "spread_width": 2, "option_position": None}),
            "C": StrategyDeploymentRecord(strategy_id=f"custom:{records['C'].id}", symbol=STOCK, holding="INTRADAY", premium_stop_pct=30.0, **common),
            "D": StrategyDeploymentRecord(strategy_id=f"custom:{records['D'].id}", symbol=STOCK, holding="INTRADAY", premium_stop_pct=30.0, **{**common, "status": "PAUSED"}),
            "E": StrategyDeploymentRecord(strategy_id=f"custom:{records['E'].id}", symbol=INDEX, holding="SWING", **{**common, "strike_rule": "OTM", "strike_offset": 2}),
            "F": StrategyDeploymentRecord(strategy_id=f"custom:{records['F'].id}", symbol=INDEX, holding="INTRADAY",
                                          **{**common, "instrument_kind": "FUTURE", "option_position": None, "strike_rule": None}),
        }
        session.add_all(deps.values())
        await session.commit()
        ids = {k: d.id for k, d in deps.items()}
        tenant_id = tenant.id
        counts_clear = await sync.apply_reference(session, {"ban_list": {"underlyings": []}, "instruments": contracts()})

    placed: list[tuple[str, str]] = []                      # the PAPER venue's order sequence (hedge first)
    original = paper_adapter.PaperExecutionAdapter.place_order

    async def spy(self, order):                              # type: ignore[no-untyped-def]
        placed.append((order.symbol, order.transaction_type.value))
        return await original(self, order)
    paper_adapter.PaperExecutionAdapter.place_order = spy    # type: ignore[method-assign]

    feed = FakeFeed()
    feed.set_frames(INDEX, {"5min": bars(24600.0, 5.0, 24500.0, 24400.0), "day": daily()})
    feed.set_frames(STOCK, {"5min": bars(1020.0, 0.5, 1010.0, 1000.0)})
    feed.set_price(INDEX, 24500.0)
    feed.set_price(STOCK, 1010.0)
    prices = {"NIFTY 24500 CE 17 NOV 26": 100.0, "NIFTY 24600 CE 17 NOV 26": 50.0, "NIFTY 24500 PE 17 NOV 26": 120.0,
              "NIFTY 24400 PE 17 NOV 26": 80.0, "RUNNERB 1000 CE 26 NOV 26": 20.0, "RUNNERB 1020 CE 26 NOV 26": 15.0,
              "NIFTY FUT 17 NOV 26": 24520.0}
    for symbol, price in prices.items():
        feed.set_price(symbol, (lambda s=symbol: prices[s]))
    runner = Runner(SETTINGS, feed)
    out: dict = {"fo_ban_check": config.FO_BAN_CHECK_ENABLED, "reference": counts_clear}

    async def snapshot(summary) -> dict:                     # type: ignore[no-untyped-def]
        async with _session_factory() as session:
            trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id).order_by(TradeRecord.id)))
            depl = {k: await session.get(StrategyDeploymentRecord, v) for k, v in ids.items()}
            orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id)))
            return dict(placed=summary.entries_placed, closed=summary.exits_closed, errors=summary.errors,
                        trades=[dict(dep=next(k for k, v in ids.items() if v == t.deployment_id), symbol=t.symbol, mode=t.mode,
                                     kind=t.instrument_kind, quantity=t.quantity, entry=t.entry_price, stop=t.stop_loss,
                                     exit_reason=t.exit_reason, exit_price=t.exit_price, exit_time=str(t.exit_time) if t.exit_time else None,
                                     group=bool(t.leg_group_id), role=t.leg_role, holding=t.holding, expiry=str(t.expiry))
                                for t in trades],
                        errors_by_dep={k: d.last_error for k, d in depl.items()}, orders=len(orders),
                        order_modes=sorted({o.mode for o in orders}))

    out["t1"] = await snapshot(await runner.run_cycle(now=T1))
    out["hedge_order"] = list(placed)
    async with _session_factory() as session:
        await sync.apply_reference(session, {"ban_list": {"underlyings": [STOCK]}, "instruments": contracts()})
        d = await session.get(StrategyDeploymentRecord, ids["D"])
        d.status = "ACTIVE"
        await session.commit()
    prices["NIFTY 24500 CE 17 NOV 26"] = 60.0                # below A's own 30% premium stop
    prices["RUNNERB 1000 CE 26 NOV 26"] = prices["RUNNERB 1020 CE 26 NOV 26"] = 10.0   # below C's
    out["t2"] = await snapshot(await runner.run_cycle(now=T2))
    out["t3"] = await snapshot(await runner.run_cycle(now=T3))
    out["t4"] = await snapshot(await runner.run_cycle(now=T4))
    out["audit_kinds"] = [row["kind"] for row in audit.read(SETTINGS.audit_path)]
    out["audit_ok"] = audit.verify(SETTINGS.audit_path)[0]
    await engine.dispose()
    return out


if __name__ == "__main__":
    report = asyncio.run(main())
    report["loaded"] = sorted(name for name in sys.modules if name == "app" or name.startswith(("app.", "atp_private")))
    print("RUNNER_S4B " + json.dumps(report, default=str))
