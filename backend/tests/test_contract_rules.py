"""Phase F2: contract rules - expiry/strike selection, option right by position, resolution
against the master, deployment validation/storage, and the preview endpoint."""
import asyncio
from datetime import date

import pytest

from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, OrderSide, SignalDirection, StrikeRule
from app.instruments.contracts import (
    ContractResolutionError, ContractRules, option_right, resolve_contract, select_expiry, select_strike,
)
from app.instruments.master import parse_upstox_master, replace_master
from tests.master_fixture import NIFTY_EXPIRIES, NIFTY_LOT, RELIANCE_LOT, build_master
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _create, _store_broker

TODAY = date(2026, 9, 28)


def _run(coro):
    return asyncio.run(coro)


def _load_master():
    rows = parse_upstox_master(build_master(), "NSE")

    async def go():
        async with _session_factory() as session:
            await replace_master(session, "upstox", ["NSE", "NFO"], rows)
    _run(go())


def test_option_right_by_position():
    assert option_right(SignalDirection.LONG, OptionPosition.BUY) == "CE"
    assert option_right(SignalDirection.SHORT, OptionPosition.BUY) == "PE"
    assert option_right(SignalDirection.LONG, OptionPosition.WRITE) == "PE"
    assert option_right(SignalDirection.SHORT, OptionPosition.WRITE) == "CE"


def test_select_expiry_rules():
    assert select_expiry(NIFTY_EXPIRIES, ExpiryRule.NEAREST, TODAY) == date(2026, 10, 1)
    assert select_expiry(NIFTY_EXPIRIES, ExpiryRule.NEXT, TODAY) == date(2026, 10, 8)
    assert select_expiry(NIFTY_EXPIRIES, ExpiryRule.MONTHLY, TODAY) == date(2026, 10, 29)
    # Expiry day itself still counts as available; past expiries never do.
    assert select_expiry(NIFTY_EXPIRIES, ExpiryRule.NEAREST, date(2026, 10, 1)) == date(2026, 10, 1)
    assert select_expiry(NIFTY_EXPIRIES, ExpiryRule.NEAREST, date(2026, 10, 2)) == date(2026, 10, 8)
    assert select_expiry([], ExpiryRule.NEAREST, TODAY) is None
    assert select_expiry([date(2026, 10, 1)], ExpiryRule.NEXT, TODAY) == date(2026, 10, 1)


def test_select_strike_atm_itm_otm():
    strikes = [float(s) for s in range(24000, 25001, 50)]
    assert select_strike(strikes, 24512.0, "CE", StrikeRule.ATM) == 24500.0
    assert select_strike(strikes, 24525.0, "CE", StrikeRule.ATM) == 24500.0   # tie -> lower strike
    assert select_strike(strikes, 24512.0, "CE", StrikeRule.ITM, 2) == 24400.0
    assert select_strike(strikes, 24512.0, "CE", StrikeRule.OTM, 1) == 24550.0
    assert select_strike(strikes, 24512.0, "PE", StrikeRule.ITM, 1) == 24550.0
    assert select_strike(strikes, 24512.0, "PE", StrikeRule.OTM, 3) == 24350.0
    assert select_strike(strikes, 30000.0, "CE", StrikeRule.OTM, 5) == 25000.0  # clamped to the listed range
    assert select_strike([], 24500.0, "CE", StrikeRule.ATM) is None


def test_resolve_option_and_future_from_master():
    _load_master()

    async def go():
        async with _session_factory() as session:
            buy = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY, expiry_rule=ExpiryRule.NEAREST)
            long_ce = await resolve_contract(session, "NIFTY 50", buy, SignalDirection.LONG, spot=24512.0, today=TODAY)
            short_pe = await resolve_contract(session, "NIFTY 50", buy, SignalDirection.SHORT, spot=24512.0, today=TODAY)
            write = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.WRITE, expiry_rule=ExpiryRule.MONTHLY,
                                  strike_rule=StrikeRule.OTM, strike_offset=2)
            long_write = await resolve_contract(session, "NIFTY", write, SignalDirection.LONG, spot=24512.0, today=TODAY)
            fut = await resolve_contract(session, "NIFTY 50", ContractRules(kind=InstrumentKind.FUTURE), SignalDirection.SHORT, spot=None, today=TODAY)
            stock = await resolve_contract(session, "RELIANCE", buy, SignalDirection.LONG, spot=1448.0, today=TODAY)
            return long_ce, short_pe, long_write, fut, stock
    long_ce, short_pe, long_write, fut, stock = _run(go())

    assert long_ce.right == "CE" and long_ce.strike == 24500.0 and long_ce.expiry == date(2026, 10, 1)
    assert long_ce.entry_side == OrderSide.BUY and long_ce.trade_direction == SignalDirection.LONG
    assert long_ce.lot_size == NIFTY_LOT and long_ce.exchange == "NFO" and long_ce.underlying_symbol == "NIFTY 50"
    assert long_ce.instrument_key.startswith("NSE_FO|") and "24500 CE" in long_ce.tradingsymbol
    assert short_pe.right == "PE" and short_pe.entry_side == OrderSide.BUY
    # Writing on a LONG signal sells a PE two steps out of the money on the monthly.
    assert long_write.right == "PE" and long_write.strike == 24400.0 and long_write.expiry == date(2026, 10, 29)
    assert long_write.entry_side == OrderSide.SELL and long_write.trade_direction == SignalDirection.SHORT
    assert fut.kind == InstrumentKind.FUTURE and fut.right is None and fut.entry_side == OrderSide.SELL and fut.expiry == date(2026, 10, 29)
    assert stock.lot_size == RELIANCE_LOT and stock.strike == 1440.0 and stock.underlying_symbol == "RELIANCE"


def test_resolution_errors_are_explicit():
    _load_master()

    async def go(symbol, rules, direction, spot):
        async with _session_factory() as session:
            return await resolve_contract(session, symbol, rules, direction, spot=spot, today=TODAY)
    buy = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY)
    with pytest.raises(ContractResolutionError, match="instrument master"):
        _run(go("TCS", buy, SignalDirection.LONG, 3500.0))
    with pytest.raises(ContractResolutionError, match="spot"):
        _run(go("NIFTY 50", buy, SignalDirection.LONG, None))
    with pytest.raises(ContractResolutionError, match="direction"):
        _run(go("NIFTY 50", buy, SignalDirection.NO_TRADE, 24500.0))
    with pytest.raises(ContractResolutionError, match="underlying itself"):
        _run(go("RELIANCE", ContractRules(), SignalDirection.LONG, 1400.0))


def test_deployment_rules_validation_and_storage():
    _load_master()
    headers = _auth("rules-owner@example.com")
    _store_broker(headers)

    # An index cannot be traded as the underlying.
    resp = _create(headers, symbol="NIFTY 50")
    assert resp.status_code == 400 and "index" in resp.json()["detail"]

    # Option rules on an UNDERLYING deployment are rejected; futures take no strike rules.
    assert _create(headers, option_position="BUY").status_code == 400
    assert _create(headers, symbol="NIFTY 50", instrument_kind="FUTURE", strike_rule="OTM").status_code == 400
    assert _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", strike_rule="ATM", strike_offset=2).status_code == 400
    assert _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", premium_stop_pct=2).status_code == 422

    # Defaults are filled: BUY, nearest, ATM, 30% premium stop.
    created = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["instrument_kind"] == "OPTION" and body["option_position"] == "BUY" and body["expiry_rule"] == "NEAREST"
    assert body["strike_rule"] == "ATM" and body["strike_offset"] == 0 and body["premium_stop_pct"] == 30.0
    assert body["contract_rules"] == "buy option, nearest expiry, ATM, premium stop 30%"

    # Same symbol/strategy/mode with a different kind is a different deployment; same kind is a duplicate.
    fut = _create(headers, symbol="NIFTY 50", instrument_kind="FUTURE", expiry_rule="MONTHLY")
    assert fut.status_code == 201 and fut.json()["contract_rules"] == "future, monthly expiry"
    assert _create(headers, symbol="NIFTY 50", instrument_kind="OPTION").status_code == 409

    # WRITE defaults to a 50% premium ceiling; OTM without an offset means one step.
    write = _create(headers, symbol="RELIANCE", instrument_kind="OPTION", option_position="WRITE", strike_rule="OTM", max_lots=2)
    assert write.status_code == 201, write.text
    assert write.json()["premium_stop_pct"] == 50.0 and write.json()["strike_offset"] == 1 and write.json()["max_lots"] == 2

    # An underlying with no contracts in the master is refused with the fix spelled out.
    missing = _create(headers, symbol="TCS", instrument_kind="OPTION")
    assert missing.status_code == 409 and "instrument master" in missing.json()["detail"]

    listed = client.get("/api/deployments", headers=headers).json()
    assert {d["instrument_kind"] for d in listed} == {"OPTION", "FUTURE"}
    audit = client.get("/api/audit-logs", headers=headers).json()
    assert any("buy option" in a["detail"] for a in audit if a["event"] == "deployment_created")


def test_preview_contract_endpoint():
    _load_master()
    headers = _auth("rules-preview@example.com")
    body = {"symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_position": "BUY", "strike_rule": "OTM", "strike_offset": 1, "spot": 24512}
    resp = client.post("/api/deployments/preview-contract", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["spot_source"] == "supplied" and out["kind"] == "OPTION"
    assert out["contracts"]["LONG"]["right"] == "CE" and out["contracts"]["LONG"]["strike"] == 24550.0
    assert out["contracts"]["SHORT"]["right"] == "PE" and out["contracts"]["SHORT"]["strike"] == 24450.0
    assert out["contracts"]["LONG"]["lot_size"] == NIFTY_LOT

    # No spot and no usable broker: the preview says so rather than failing.
    no_spot = client.post("/api/deployments/preview-contract", headers=headers, json={"symbol": "NIFTY 50", "instrument_kind": "OPTION"}).json()
    assert no_spot["spot"] is None and "spot" in no_spot["contracts"]["LONG"]["error"].lower()

    fut = client.post("/api/deployments/preview-contract", headers=headers, json={"symbol": "NIFTY 50", "instrument_kind": "FUTURE"}).json()
    assert fut["contracts"]["LONG"]["entry_side"] == "BUY" and fut["contracts"]["SHORT"]["entry_side"] == "SELL"

    plain = client.post("/api/deployments/preview-contract", headers=headers, json={"symbol": "RELIANCE"}).json()
    assert plain["kind"] == "UNDERLYING"


def test_worker_refuses_derived_contract_until_execution_is_wired(monkeypatch):
    """Guard for the window between F2 and F3: an option deployment must not trade the index."""
    from app.db.models import StrategyDeploymentRecord
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker
    t = _tenant("rules-worker@example.com")
    dep_id = _deploy(t, symbol="NIFTY 50")

    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.instrument_kind, dep.option_position = "OPTION", "BUY"
            await session.commit()
    _run(mark())
    worker = _worker(monkeypatch, _FakeBroker())
    _force_signal(monkeypatch, _signal)
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == []
    assert "not enabled" in _get(StrategyDeploymentRecord, dep_id).last_error
