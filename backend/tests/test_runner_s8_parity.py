"""Runner S8 (docs/design/RUNNER_PARITY.md): PAPER parity - the same recorded bar series through ATP's own PAPER worker path
and through the runner loop gives the same trades. Fails on any mismatch.

Server half (this process): the trading worker's own cycle (`TradingWorker.run_cycle`) for one organisation per case, its
market data from the case's bars and step prices (a MarketDataService whose candles / prices are the case's - the
worker's own frames call, freshness check, closed-bar cut, strategy, pipeline, monitor, square-off and expiry settlement
run unchanged), the contracts in the instrument master, the expiry-day close in the licensed Daily store.
Runner half (its own process, tests/runner_parity_job.py): the runner loop on its local SQLite store, the same cases.
Compared per trade: symbol, side, quantity, leg role, the cycle of the entry and of the exit, entry / exit price, exit
reason, settlement time, P&L and the signal bar; and the orders in placement order (hedge first). Closed bars only: the
last bar of every series is still forming at the entry cycle and is never read.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.brokers import token_lifecycle
from app.db.models import (
    BrokerCredentialRecord,
    CustomStrategyRecord,
    InstrumentRecord,
    MdCandleRecord,
    OrderRecord,
    StrategyDeploymentRecord,
    TradeRecord,
    UserPrefsRecord,
)
from app.market_data.service import MarketDataService
from app.platform import controls
from app.strategy_engine.declarative import CustomStrategyConfig
from app.workers import trading_worker as tw
from app.workers.trading_worker import TradingWorker
from tests import runner_parity_cases as P
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import _FakeBroker, _tenant

BACKEND = Path(__file__).resolve().parents[1]
NIFTY_KEY = "NSE_INDEX:NIFTY50"
pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")


def _run(coro):
    return asyncio.run(coro)


class CaseData(MarketDataService):
    """The case's recorded bars and step prices as the worker's market data (nothing else of the service is changed)."""

    def __init__(self, broker, case: dict, clock: dict, prices: dict) -> None:  # type: ignore[no-untyped-def]
        super().__init__(broker)
        self.case, self.clock, self.prices = case, clock, prices

    async def get_frames(self, symbol, exchange, base_timeframe, timeframes, *args, **kwargs):  # type: ignore[no-untyped-def]
        held = self.case["frames"].get(symbol, {})
        stamp = pd.Timestamp(self.clock["now"]).tz_convert("UTC")
        return {tf: held[tf][held[tf].index <= stamp] for tf in dict.fromkeys([base_timeframe, *timeframes]) if tf in held}

    async def get_ltp(self, symbol, exchange="NSE", now=None):  # type: ignore[no-untyped-def]
        if symbol not in self.prices:
            raise LookupError(f"no price for {symbol}")
        return float(self.prices[symbol])

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):  # type: ignore[no-untyped-def]
        return await self.get_ltp(symbol, exchange)


class CaseBroker(_FakeBroker):
    def __init__(self, prices: dict) -> None:
        super().__init__()
        self.prices = prices

    async def get_ltp(self, symbols):  # type: ignore[no-untyped-def]
        return {s: float(self.prices[s]) for s in symbols if s in self.prices}

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):  # type: ignore[no-untyped-def]
        name = KEYS.get(symbol, symbol)          # Upstox quotes a contract by its instrument key first (contract_ltp)
        if name not in self.prices:
            raise KeyError(f"No LTP returned for {exchange}:{symbol}")
        return float(self.prices[name])


KEYS = {row["instrument_key"]: row["tradingsymbol"] for row in P.contracts()}


@pytest.fixture(scope="module")
def market_store():
    """The synthetic contracts in ATP's instrument master and the index's licensed Daily bars (expiry-day close)."""
    from app.market_lake import ingest
    from app.market_lake.bhavcopy_backfill import bar_end

    async def put():
        async with _session_factory() as session:
            for row in P.contracts():
                session.add(InstrumentRecord(**{k: (pd.Timestamp(v).date() if k == "expiry" else v) for k, v in row.items()}))
            bars = [ingest.LakeBar(NIFTY_KEY, "day", bar_end(d.date()), c, c, c, c, 0)
                    for d, c in zip(P.daily().index.tz_convert("Asia/Kolkata")[:2], P.daily()["close"][:2])]
            await ingest.write_candles_bulk(session, bars, "nse_bhav:index")
            await session.commit()

    async def clear():
        async with _session_factory() as session:
            await session.execute(delete(InstrumentRecord).where(InstrumentRecord.instrument_key.like("NSE_FO|PAR%")))
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == NIFTY_KEY,
                                                               MdCandleRecord.ts >= P.T1 - timedelta(days=3),
                                                               MdCandleRecord.ts <= P.T4 + timedelta(days=3)))
            await session.commit()
    _run(clear())
    _run(put())
    yield
    _run(clear())


def _set_flags(flags: dict) -> dict:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            before = {k: stored.get(k) for k in flags}
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []} if on is not None else None
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {k: v for k, v in stored.items() if v}}, None)
            await session.commit()
            return before
    return _run(go())


def _server_case(monkeypatch, name: str, case: dict) -> dict:
    who = _tenant(f"parity-{name.replace('_', '-')}@example.com")
    tenant_id, user_id = who["tenant_id"], who["user_id"]

    async def setup():
        async with _session_factory() as session:
            record = CustomStrategyRecord(tenant_id=tenant_id, user_id=user_id, name=f"parity-{name}",
                                          config_json=CustomStrategyConfig(**P.STRATEGY).model_dump_json())
            session.add(record)
            session.add(UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace="trade_settings",
                                        value_json=json.dumps({"values": {"exits.square_off_time": P.SQUARE_OFF}})))
            await session.flush()
            dep = StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id=f"custom:{record.id}", exchange="NSE", timeframe="5min",
                                           mode="PAPER", status="ACTIVE", entry_mode="AUTO", order_style="MARKET", max_lots=1,
                                           created_by=user_id, broker_name="upstox", **case["dep"])
            session.add(dep)
            # his broker login stays valid over the case's cycle times (the cases run in a later year than the wall clock)
            for cred in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)):
                cred.token_expires_at = P.T4 + timedelta(days=1)
                cred.last_verified_at = P.T1 - timedelta(minutes=1)
            await session.commit()
            return dep.id
    dep_id = _run(setup())
    prices: dict[str, float] = {}
    clock: dict = {}
    broker = CaseBroker(prices)
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: broker)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: broker)

    async def no_sync(session, exchanges, client=None):  # type: ignore[no-untyped-def]
        return None
    monkeypatch.setattr(tw, "sync_upstox", no_sync)
    from app.execution import paper_adapter
    placed: list[list[str]] = []                             # the PAPER venue's own order sequence (hedge first)
    original = paper_adapter.PaperExecutionAdapter.place_order

    async def spy(self, order):                              # type: ignore[no-untyped-def]
        placed.append([order.symbol, order.transaction_type.value])
        return await original(self, order)
    monkeypatch.setattr(paper_adapter.PaperExecutionAdapter, "place_order", spy)
    worker = TradingWorker(_session_factory, cycle_seconds=60, market_data_factory=lambda b: CaseData(b, case, clock, prices))
    before = _set_flags(case.get("flags", {}))
    snapshots, errors = [], []
    try:
        for now, step_prices in case["steps"]:
            prices.update(step_prices)
            clock["now"] = now
            report = _run(worker.run_cycle(now=now))
            errors.extend(report.errors)

            async def snap():
                async with _session_factory() as session:
                    return await P.snapshot(session, tenant_id, TradeRecord, OrderRecord)
            trades, orders = _run(snap())
            snapshots.append(trades)
    finally:
        _set_flags({k: (v or {}).get("on") if v else None for k, v in before.items()})

    async def stop():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.status = "STOPPED"
            await session.commit()
            return dep.last_error
    last_error = _run(stop())
    return {"trades": P.summarise(snapshots), "orders": orders, "placed": placed, "errors": errors, "last_error": last_error}


@pytest.fixture(scope="module")
def runner_side(tmp_path_factory) -> dict:
    home = tmp_path_factory.mktemp("runner-parity-home")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL", "ATP_RUNNER"))}
    env.update(PYTHONPATH=str(BACKEND))
    run = subprocess.run([sys.executable, "tests/runner_parity_job.py", str(home)], cwd=BACKEND, env=env, capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    line = next(x for x in run.stdout.splitlines() if x.startswith("RUNNER_PARITY "))
    return json.loads(line[len("RUNNER_PARITY "):])


def _diff(server: dict, runner: dict) -> list[str]:
    out = []
    if len(server["trades"]) != len(runner["trades"]):
        out.append(f"trades: server {len(server['trades'])}, runner {len(runner['trades'])}")
    for i, (a, b) in enumerate(zip(server["trades"], runner["trades"])):
        out += [f"trade {i + 1} {k}: server {a[k]!r}, runner {b[k]!r}" for k in P.FIELDS if a[k] != b[k]]
    for what in ("orders", "placed"):
        if server[what] != runner[what]:
            out.append(f"{what}: server {server[what]}, runner {runner[what]}")
    return out


@pytest.mark.parametrize("name", list(P.CASES))
def test_paper_parity_server_worker_and_runner_loop(name, monkeypatch, market_store, runner_side):
    server = _server_case(monkeypatch, name, P.CASES[name])
    runner = runner_side[name]
    report = {"case": name, "server": server, "runner": runner}
    print("PARITY " + json.dumps(report, default=str))
    assert server["trades"], f"{name}: the server path made no trade ({server['last_error']}; {server['errors'][:3]})"
    assert runner["trades"], f"{name}: the runner made no trade ({runner['last_error']}; {runner['errors'][:3]})"
    assert all(t["exit_step"] is not None for t in server["trades"] + runner["trades"]), name   # every case ends flat
    if name == "two_leg_hedge_first":                       # his smart-execution answer ON: the bought (hedge) leg first
        assert server["placed"][:2] == [[P.PE_BOUGHT, "BUY"], [P.PE_SOLD, "SELL"]], server["placed"]
    mismatches = _diff(server, runner)
    assert not mismatches, f"{name}: " + "; ".join(mismatches)


def test_the_diff_catches_a_changed_field():
    row = {k: None for k in P.FIELDS}
    side = {"trades": [dict(row, exit_price=10.0)], "orders": [], "placed": []}
    assert _diff(side, side) == []
    assert _diff(side, {**side, "trades": [dict(row, exit_price=10.5)]}) == ["trade 1 exit_price: server 10.0, runner 10.5"]
    assert _diff(side, {**side, "placed": [["X", "BUY"]]})


def test_one_direction_per_symbol_is_the_same_rule_on_the_runner(tmp_path):
    """The worker's M55 S6 check moved unchanged to order_defaults.opposite_position; the runner loop calls it before an entry."""
    import inspect

    from app.core.models import OPPOSITE_DENY
    from app.db.models import RiskSettingsRecord, Tenant, User
    from app.execution.order_defaults import opposite_position
    from app.runner import loop
    from tests.test_runner_s4_link import Store
    assert "order_defaults.opposite_position(session, dep, signal.direction)" in inspect.getsource(loop.Runner._evaluate)
    assert "opposite_position(session, dep, direction)" in inspect.getsource(TradingWorker._opposite_position)
    store = Store(tmp_path / "runner.db")

    async def go():
        await store.create()
        async with store.sessions() as session:
            session.add(Tenant(id=1, name="t"))
            session.add(User(id=1, tenant_id=1, email="opp@example.com", hashed_password="-"))
            session.add(RiskSettingsRecord(tenant_id=1, opposite_positions=OPPOSITE_DENY))
            session.add(TradeRecord(tenant_id=1, user_id=1, deployment_id=7, strategy_id="custom:1", symbol="PAROPP", direction="LONG", quantity=1, entry_price=10.0,
                                    stop_loss=9.0, mode="PAPER", entry_time=P.T1))
            await session.commit()
            dep = StrategyDeploymentRecord(id=8, tenant_id=1, symbol="PAROPP", mode="PAPER")
            return (await opposite_position(session, dep, "SHORT"), await opposite_position(session, dep, "LONG"))
    short, long_ = _run(go())
    assert short and "one direction per symbol" in short and long_ is None
