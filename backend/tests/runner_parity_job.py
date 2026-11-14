"""Runner S8 parity, the runner half: every case of tests/runner_parity_cases.py through the runner loop, in its own process
(runner mode, the folder's SQLite file, no Redis - as `python -m app.runner` does). Run by tests/test_runner_s8_parity.py
with the runner folder as argv[1]. Each case: its own organisation + user in the local store, the same saved strategy and
deployment settings, a PAPER-only fake feed with the case's bars and step prices, `Runner.run_cycle` at each step.
Prints ONE json line: RUNNER_PARITY {case: {"trades": [...], "orders": [...]}}.
"""
import asyncio
import json
import sys
from pathlib import Path

from app.runner.__main__ import prepare_process
from app.runner.settings import RunnerSettings

SETTINGS = RunnerSettings.load(Path(sys.argv[1]))
prepare_process(SETTINGS)


async def main() -> dict:
    from app.db.base import Base
    from app.db.models import CustomStrategyRecord, OrderRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User, UserPrefsRecord
    from app.db.session import _session_factory, get_engine
    from app.platform import controls
    from app.runner import sync
    from app.runner.feed import FakeFeed
    from app.runner.loop import Runner
    from app.strategy_engine.declarative import CustomStrategyConfig
    from tests.runner_parity_cases import CASES, SQUARE_OFF, STRATEGY, contracts, snapshot, summarise

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _session_factory() as session:
        await sync.apply_reference(session, {"ban_list": {"underlyings": []}, "instruments": contracts()})

    from app.execution import paper_adapter
    placed: list[list[str]] = []                             # the PAPER venue's own order sequence (hedge first)
    original = paper_adapter.PaperExecutionAdapter.place_order

    async def spy(self, order):                              # type: ignore[no-untyped-def]
        placed.append([order.symbol, order.transaction_type.value])
        return await original(self, order)
    paper_adapter.PaperExecutionAdapter.place_order = spy    # type: ignore[method-assign]
    out: dict = {}
    for name, case in CASES.items():
        async with _session_factory() as session:
            tenant = Tenant(name=f"parity-{name}")
            session.add(tenant)
            await session.flush()
            user = User(tenant_id=tenant.id, email=f"parity-{name.replace('_', '-')}@example.com", hashed_password="-")
            session.add(user)
            await session.flush()
            record = CustomStrategyRecord(tenant_id=tenant.id, user_id=user.id, name=f"parity-{name}",
                                          config_json=CustomStrategyConfig(**STRATEGY).model_dump_json())
            session.add(record)
            session.add(UserPrefsRecord(tenant_id=tenant.id, user_id=user.id, namespace="trade_settings",
                                        value_json=json.dumps({"values": {"exits.square_off_time": SQUARE_OFF}})))
            await session.flush()
            dep = StrategyDeploymentRecord(tenant_id=tenant.id, strategy_id=f"custom:{record.id}", exchange="NSE", timeframe="5min",
                                           mode="PAPER", status="ACTIVE", entry_mode="AUTO", order_style="MARKET", max_lots=1,
                                           created_by=user.id, **case["dep"])
            session.add(dep)
            # the case's flags for his organisation, as the link writes ATP's answers into the local store (S4c)
            await controls._set(session, controls.KEY_FEATURE_FLAGS,
                                {"flags": {k: {"on": v, "tenants": []} for k, v in case.get("flags", {}).items()}}, None)
            await session.commit()
            tenant_id = tenant.id
        feed = FakeFeed()
        prices: dict[str, float] = {}
        for symbol, frames in case["frames"].items():
            feed.set_frames(symbol, frames)
        runner = Runner(SETTINGS, feed)
        placed.clear()
        errors: list[str] = []
        snapshots: list[list[dict]] = []
        for now, step_prices in case["steps"]:
            prices.update(step_prices)
            for symbol in step_prices:
                feed.set_price(symbol, (lambda s=symbol: prices[s]))
            summary = await runner.run_cycle(now=now)
            errors.extend(summary.errors)
            async with _session_factory() as session:
                trades, orders = await snapshot(session, tenant_id, TradeRecord, OrderRecord)
            snapshots.append(trades)
        async with _session_factory() as session:
            depl = await session.get(StrategyDeploymentRecord, dep.id)
        out[name] = {"trades": summarise(snapshots), "orders": orders, "placed": list(placed), "errors": errors, "last_error": depl.last_error}
    await engine.dispose()
    return out


if __name__ == "__main__":
    report = asyncio.run(main())
    print("RUNNER_PARITY " + json.dumps(report, default=str))
