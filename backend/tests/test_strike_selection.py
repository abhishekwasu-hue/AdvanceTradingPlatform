"""Phase H1: the strike-selection pipeline - chain filters around the rule strike, delta
targeting, explicit refusals, storage on deployments, preview and worker wiring."""
import asyncio
from datetime import date

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, SignalDirection, StrikeRule
from app.db.models import OrderRecord, StrategyDeploymentRecord
from app.instruments.contracts import ContractResolutionError, ContractRules, resolve_contract
from app.instruments.strike_selection import StrikeFilters, StrikeSelectionError, select_strike_with_chain
from sqlalchemy import select
from tests.master_fixture import NIFTY_LOT
from tests.test_auth_api import _session_factory, client
from tests.test_contract_rules import TODAY, _load_master
from tests.test_deployments_api import _auth, _create, _store_broker

EXPIRY = date(2026, 10, 1)
STRIKES = [float(s) for s in range(24000, 25001, 50)]


def _run(coro):
    return asyncio.run(coro)


def _chain(spot=24512.0) -> OptionChain:
    """A synthetic NIFTY chain: liquidity thins out away from the money, IV ~14%, and the ATM
    call is quoted wide on purpose."""
    rows = []
    for strike in STRIKES:
        dist = abs(strike - spot)
        oi = max(0.0, 5_000_000 - dist * 8_000)
        call_ltp = max(5.0, spot - strike + 90 - dist * 0.05) if strike <= spot else max(5.0, 90 - (strike - spot) * 0.6)
        put_ltp = max(5.0, strike - spot + 90 - dist * 0.05) if strike >= spot else max(5.0, 90 - (spot - strike) * 0.6)
        wide = strike == 24500.0
        rows.append(OptionChainRow(
            strike=strike, call_oi=oi, call_volume=oi / 10, call_ltp=round(call_ltp, 2),
            call_bid=round(call_ltp * (0.9 if wide else 0.995), 2), call_ask=round(call_ltp * (1.1 if wide else 1.005), 2),
            call_iv=14.0, put_oi=oi, put_volume=oi / 10, put_ltp=round(put_ltp, 2),
            put_bid=round(put_ltp * 0.995, 2), put_ask=round(put_ltp * 1.005, 2), put_iv=14.0,
        ))
    return OptionChain(underlying="NIFTY", expiry=EXPIRY.isoformat(), underlying_ltp=spot, rows=rows)


def test_filters_json_round_trip_and_description():
    f = StrikeFilters(min_oi=100000, max_spread_pct=2.0, target_delta=0.3)
    assert f.active and StrikeFilters().active is False
    assert StrikeFilters.from_json(f.to_json()) == f
    assert StrikeFilters().to_json() is None and StrikeFilters.from_json("garbage") == StrikeFilters()
    assert "OI>=100000" in f.describe() and "delta~0.3" in f.describe() and StrikeFilters().describe() == "no filters"


def test_spread_filter_skips_the_wide_rule_strike_for_the_next_one():
    result = select_strike_with_chain(STRIKES, 24500.0, "CE", _chain(), StrikeFilters(max_spread_pct=2.0),
                                      spot=24512.0, expiry=EXPIRY, as_of=TODAY)
    assert result.strike == 24450.0  # 24450 and 24550 tie on distance; ties go to the lower strike, as select_strike does
    assert any("Rule strike 24500 skipped: spread" in n for n in result.notes)
    by_strike = {c.strike: c for c in result.candidates}
    assert not by_strike[24500.0].passes and by_strike[24550.0].passes and by_strike[24450.0].passes


def test_delta_target_picks_the_nearest_delta_within_tolerance():
    result = select_strike_with_chain(STRIKES, 24500.0, "CE", _chain(), StrikeFilters(target_delta=0.30, delta_tolerance=0.12, search_steps=8),
                                      spot=24512.0, expiry=EXPIRY, as_of=TODAY)
    chosen = next(c for c in result.candidates if c.strike == result.strike)
    assert chosen.delta is not None and abs(abs(chosen.delta) - 0.30) <= 0.12 and result.strike > 24512.0
    assert all(abs(abs(c.delta) - 0.30) >= abs(abs(chosen.delta) - 0.30) for c in result.candidates if c.passes)
    assert "|delta|" in result.notes[0]


def test_no_passing_strike_is_an_explicit_refusal():
    with pytest.raises(StrikeSelectionError, match="No CE strike within 2 steps of 24500 passes"):
        select_strike_with_chain(STRIKES, 24500.0, "CE", _chain(), StrikeFilters(min_oi=10_000_000, search_steps=2),
                                 spot=24512.0, expiry=EXPIRY, as_of=TODAY)
    # A strike missing from the chain is a candidate that fails, not a crash.
    partial = OptionChain(underlying="NIFTY", expiry=EXPIRY.isoformat(), underlying_ltp=24512.0, rows=[r for r in _chain().rows if r.strike != 24500.0])
    result = select_strike_with_chain(STRIKES, 24500.0, "CE", partial, StrikeFilters(min_oi=1), spot=24512.0, expiry=EXPIRY, as_of=TODAY)
    assert result.strike != 24500.0 and next(c for c in result.candidates if c.strike == 24500.0).reasons == ["not in option chain"]


def test_resolver_applies_filters_through_the_chain_provider():
    _load_master()
    calls = []

    async def provider(underlying_symbol, expiry):
        calls.append((underlying_symbol, expiry))
        return _chain()

    async def broken(underlying_symbol, expiry):
        raise ConnectionError("chain endpoint down")

    rules = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY, strike_filters=StrikeFilters(max_spread_pct=2.0))

    async def go(chain_provider):
        async with _session_factory() as session:
            return await resolve_contract(session, "NIFTY 50", rules, SignalDirection.LONG, spot=24512.0, today=TODAY, chain_provider=chain_provider)

    resolved = _run(go(provider))
    assert resolved.strike == 24450.0 and "24450 CE" in resolved.tradingsymbol and calls == [("NIFTY 50", EXPIRY)]
    assert resolved.selection_notes and resolved.selection["rule_strike"] == 24500.0
    assert "selection_notes" in resolved.as_dict()
    with pytest.raises(ContractResolutionError, match="need the option chain"):
        _run(go(None))
    with pytest.raises(ContractResolutionError, match="unavailable: chain endpoint down"):
        _run(go(broken))
    # Rules without filters never touch the chain.
    plain = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY)
    async def plain_go():
        async with _session_factory() as session:
            return await resolve_contract(session, "NIFTY 50", plain, SignalDirection.LONG, spot=24512.0, today=TODAY, chain_provider=broken)
    assert _run(plain_go()).strike == 24500.0


def test_filters_are_validated_stored_and_echoed_on_deployments(monkeypatch):
    _load_master()
    headers = _auth("filters-owner@example.com")
    _store_broker(headers, token_status="VALID")

    bad = _create(headers, symbol="RELIANCE", strike_filters={"min_oi": 1000})
    assert bad.status_code == 400 and "OPTION or FUTURE" in bad.json()["detail"]
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="FUTURE", strike_filters={"min_oi": 1000})
    assert bad.status_code == 400 and "only apply to OPTION" in bad.json()["detail"]
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", strike_filters={"min_iv_pct": 30, "max_iv_pct": 10})
    assert bad.status_code == 400

    ok = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_position="BUY",
                 strike_filters={"min_oi": 100000, "max_spread_pct": 2.0, "target_delta": 0.3})
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["strike_filters"] == {"min_oi": 100000.0, "max_spread_pct": 2.0, "target_delta": 0.3}
    assert "filters: OI>=100000" in body["contract_rules"]

    # Preview: with a usable broker session the chain is fetched from it and the rationale returned.
    class _ChainAdapter:
        name = "upstox"
        async def get_option_chain(self, underlying, expiry=None):
            return _chain()
        async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
            return 24512.0
    monkeypatch.setattr("app.deployments.routes.build_adapter", lambda record, client=None: _ChainAdapter())
    preview = client.post("/api/deployments/preview-contract", headers=headers, json={
        "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_position": "BUY", "spot": 24512,
        "strike_filters": {"max_spread_pct": 2.0},
    }).json()
    assert preview["contracts"]["LONG"]["strike"] == 24450.0 and preview["contracts"]["LONG"]["selection"]["rule_strike"] == 24500.0
    assert any("Rule strike 24500 skipped" in n for n in preview["contracts"]["LONG"]["selection_notes"])
    assert preview["contracts"]["SHORT"]["strike"] == 24500.0  # the put side is quoted tight


def test_worker_trades_the_filtered_strike_and_records_why(monkeypatch):
    from app.workers import trading_worker as tw
    from tests.test_contract_execution import BIG
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker

    _load_master()
    t = _tenant("filters-worker@example.com")
    dep_id = _deploy(t, symbol="NIFTY 50")

    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.instrument_kind, dep.option_position, dep.max_lots = "OPTION", "BUY", 1
            dep.strike_filters = StrikeFilters(max_spread_pct=2.0).to_json()
            await session.commit()
    _run(mark())

    class Broker(_FakeBroker):
        chain_calls = 0
        async def get_ltp(self, symbols):
            return {s: (24512.0 if "NIFTY 50" in s else 150.0) for s in symbols}
        async def get_option_chain(self, underlying, expiry=None):
            Broker.chain_calls += 1
            assert underlying == "NIFTY 50" and expiry == EXPIRY
            return _chain()
    broker = Broker()
    monkeypatch.setattr(tw, "resolve_contract", lambda session, symbol, rules, direction, *, spot, today, broker="upstox", **kw:
                        resolve_contract(session, symbol, rules, direction, spot=24512.0, today=TODAY, broker=broker, **kw))
    worker = _worker(monkeypatch, broker)
    async def big_risk(tenant_id, session):
        return BIG
    monkeypatch.setattr("app.execution.signal_execution.get_tenant_risk_config", big_risk)
    _force_signal(monkeypatch, lambda: _signal())

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1, report.errors
    trade = _trades(t["tenant_id"])[0]
    assert "24450 CE" in trade.symbol and trade.quantity == NIFTY_LOT and Broker.chain_calls == 1

    async def order():
        async with _session_factory() as session:
            return await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"]).order_by(OrderRecord.id.desc()))
    assert "nearest passing strike" in _run(order()).reasons_json
    assert _get(StrategyDeploymentRecord, dep_id).last_error is None
