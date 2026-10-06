"""Phase U: ratio spreads, long butterfly and the free-form leg builder - payoff engine,
resolution with leg ratios, P&L-based group exits, paper/LIVE execution with per-leg
quantities, deployments API and preview."""
import asyncio
import json

import pytest

from app.core.enums import OptionStrategy, SignalDirection, StrikeRule
from app.db.models import TradeRecord
from app.instruments.contracts import ContractResolutionError
from app.instruments.payoff import PayoffLeg, analyse
from app.instruments.spreads import CustomLeg, describe_structure, parse_custom_legs, resolve_structure
from app.trading.position_monitor import group_exit
from tests.master_fixture import NIFTY_LOT
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_rules import TODAY, _load_master
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_multileg import SPOT, _OptionBroker, _rules
from tests.test_phase_r_structures import _execute, _metrics, _resolve
from tests.test_trading_worker import _tenant, _trades


def _run(coro):
    return asyncio.run(coro)


class _WingBroker(_OptionBroker):
    """The fake's default quote for an unknown strike is 30; a below-spot call must cost more
    than the ATM one for a butterfly to make sense."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.prices["24400 CE"] = 190.0
        self.prices["24600 PE"] = 200.0


def _resolve_custom(legs, direction=SignalDirection.LONG):
    async def go():
        async with _session_factory() as session:
            return await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.CUSTOM, direction, spread_width=2, spot=SPOT,
                                           today=TODAY, custom_legs=legs)
    return _run(go())


# --- payoff engine -----------------------------------------------------------------------

def test_payoff_engine_ratio_butterfly_and_naked_side():
    ratio = analyse([PayoffLeg("CE", "LONG", 24500, 110), PayoffLeg("CE", "SHORT", 24600, 70, ratio=2)])
    assert ratio.net_credit == 30.0 and ratio.peak == 130.0 and ratio.max_profit == 130.0
    assert ratio.max_loss is None and not ratio.upside_defined and ratio.downside_defined
    assert ratio.breakevens == [24730.0] and ratio.pnl_at(24600) == 130.0 and ratio.pnl_at(24830) == -100.0

    fly = analyse([PayoffLeg("CE", "LONG", 24400, 190), PayoffLeg("CE", "SHORT", 24500, 110, ratio=2), PayoffLeg("CE", "LONG", 24600, 70)])
    assert fly.net_credit == -40.0 and fly.max_loss == 40.0 and fly.max_profit == 60.0 and fly.defined_risk
    assert fly.breakevens == [24440.0, 24560.0]

    put_ratio = analyse([PayoffLeg("PE", "LONG", 24500, 120), PayoffLeg("PE", "SHORT", 24400, 80, ratio=2)])
    assert put_ratio.net_credit == 40.0 and put_ratio.peak == 140.0 and not put_ratio.downside_defined and put_ratio.upside_defined
    assert put_ratio.breakevens == [24260.0]

    straddle = analyse([PayoffLeg("CE", "LONG", 24500, 110), PayoffLeg("PE", "LONG", 24500, 120)])
    assert straddle.max_profit is None and straddle.max_loss == 230.0 and straddle.breakevens == [24270.0, 24730.0]

    with pytest.raises(ValueError):
        analyse([])


# --- resolution ----------------------------------------------------------------------------

def test_ratio_and_butterfly_legs_carry_ratios():
    _load_master()
    call_ratio = _resolve(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG)
    assert [(l.role, l.contract.right, l.contract.strike, l.ratio) for l in call_ratio.legs] == [("LONG", "CE", 24500.0, 1), ("SHORT", "CE", 24600.0, 2)]
    assert call_ratio.width_points == 100.0 and call_ratio.max_ratio == 2 and "undefined risk above" in call_ratio.notes[0]
    with pytest.raises(ContractResolutionError, match="not entered on a SHORT"):
        _resolve(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.SHORT)

    put_ratio = _resolve(OptionStrategy.PUT_RATIO_SPREAD, SignalDirection.SHORT)
    assert [(l.role, l.contract.right, l.contract.strike, l.ratio) for l in put_ratio.legs] == [("LONG", "PE", 24500.0, 1), ("SHORT", "PE", 24400.0, 2)]

    fly = _resolve(OptionStrategy.LONG_BUTTERFLY, SignalDirection.LONG)
    assert [(l.role, l.contract.right, l.contract.strike, l.ratio) for l in fly.legs] == [
        ("LONG", "CE", 24400.0, 1), ("SHORT", "CE", 24500.0, 2), ("LONG", "CE", 24600.0, 1)]
    fly_pe = _resolve(OptionStrategy.LONG_BUTTERFLY, SignalDirection.SHORT)
    assert all(l.contract.right == "PE" for l in fly_pe.legs) and fly_pe.debit
    assert fly.as_dict()["legs"][1]["ratio"] == 2


def test_custom_legs_resolve_validate_and_describe():
    _load_master()
    legs = parse_custom_legs(json.dumps([
        {"right": "PE", "role": "SHORT"}, {"right": "pe", "role": "long", "strike_rule": "OTM", "strike_offset": 2},
        {"right": "CE", "role": "SHORT", "strike_rule": "OTM", "strike_offset": 2, "ratio": 1},
    ]))
    assert legs[0] == CustomLeg("PE", "SHORT", StrikeRule.ATM, 0, 1) and legs[1].strike_offset == 2
    structure = _resolve_custom(legs)
    assert [(l.role, l.contract.right, l.contract.strike) for l in structure.legs] == [("SHORT", "PE", 24500.0), ("LONG", "PE", 24400.0), ("SHORT", "CE", 24600.0)]
    assert structure.strategy == OptionStrategy.CUSTOM and structure.width_points == 200.0 and structure.lot_size == NIFTY_LOT
    assert "custom structure: sell 1x ATM PE, buy 1x OTM2 PE, sell 1x OTM2 CE" in describe_structure(OptionStrategy.CUSTOM, 2, _rules(), legs)

    with pytest.raises(ValueError, match="at least two legs"):
        parse_custom_legs([{"right": "CE", "role": "SHORT"}])
    with pytest.raises(ValueError, match="same contract"):
        parse_custom_legs([{"right": "CE", "role": "SHORT"}, {"right": "CE", "role": "LONG", "strike_rule": "ATM"}])
    with pytest.raises(ValueError, match="ratio"):
        parse_custom_legs([{"right": "CE", "role": "SHORT", "ratio": 9}, {"right": "PE", "role": "SHORT"}])
    with pytest.raises(ContractResolutionError, match="has no legs"):
        _resolve_custom([])
    # OTM with offset 0 is ATM: two legs that resolve to one contract are refused at resolution.
    with pytest.raises(ContractResolutionError, match="same contract"):
        _resolve_custom([CustomLeg("CE", "SHORT", StrikeRule.ATM, 0), CustomLeg("CE", "LONG", StrikeRule.ITM, 0)])


# --- economics -----------------------------------------------------------------------------

def test_payoff_metrics_undefined_and_defined_risk():
    _load_master()
    m = _metrics(_resolve(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG))
    assert m.net_credit == 30.0 and m.max_loss is None and m.max_profit == 130.0 and m.defined_risk is False and m.debit is False
    assert m.breakevens == [24730.0] and m.underlying_exits == {"above": 24730.0} and m.short_strikes == {}
    # Default: stop at a loss equal to the peak profit (100%), target at half of it.
    assert m.pnl_stop == -130.0 and m.pnl_target == 65.0 and m.risk_per_unit == 130.0
    assert m.stop_value == 160.0 and m.target_value == -35.0
    tighter = _metrics(_resolve(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG), stop=40, target=80)
    assert tighter.pnl_stop == -52.0 and tighter.pnl_target == 104.0 and tighter.risk_per_unit == 52.0

    fly = _metrics(_resolve(OptionStrategy.LONG_BUTTERFLY, SignalDirection.LONG), broker=_WingBroker())
    assert fly.net_credit == -40.0 and fly.debit is True and fly.max_loss == 40.0 and fly.max_profit == 60.0 and fly.defined_risk
    assert fly.pnl_stop == -20.0 and fly.pnl_target == 30.0 and fly.risk_per_unit == 40.0 and fly.underlying_exits == {}
    assert fly.stop_value == 20.0 and fly.target_value == 70.0     # worth levels for a debit
    capped = _metrics(_resolve(OptionStrategy.LONG_BUTTERFLY, SignalDirection.LONG), broker=_WingBroker(), stop=300)
    assert capped.pnl_stop == -40.0                                  # never more than the max loss

    custom = _metrics(_resolve_custom(parse_custom_legs([{"right": "PE", "role": "SHORT"}, {"right": "PE", "role": "LONG", "strike_rule": "OTM", "strike_offset": 2},
                                                          {"right": "CE", "role": "SHORT", "strike_rule": "OTM", "strike_offset": 2}])))
    assert custom.net_credit == 110.0 and custom.max_loss is None and custom.breakevens == [24710.0] and custom.underlying_exits == {"above": 24710.0}
    assert custom.as_dict()["pnl_stop"] == -110.0

    # A structure that cannot profit at expiry is refused: buy 1 / sell 1 at the same strike... use a losing custom set.
    hopeless = _resolve_custom(parse_custom_legs([{"right": "CE", "role": "LONG", "strike_rule": "OTM", "strike_offset": 2},
                                                  {"right": "CE", "role": "SHORT", "strike_rule": "ATM"}]))   # buy 24600 @70, sell 24500 @110: +40 credit, loses 60 above
    assert _metrics(hopeless).max_loss == 60.0
    broker = _OptionBroker()
    broker.prices["24600 CE"] = 120.0      # now the long costs more than the short brings: pure loss
    with pytest.raises(ContractResolutionError, match="cannot profit at expiry"):
        _metrics(hopeless, broker=broker)


def test_group_exit_weights_ratio_legs_and_uses_pnl_levels():
    meta = json.dumps({"net_credit": 30.0, "pnl_stop": -130.0, "pnl_target": 65.0, "quantity": 75, "stop_value": 160.0, "target_value": -35.0,
                       "underlying_exits": {"above": 24730.0}, "short_strikes": {}})
    legs = [TradeRecord(id=1, leg_role="LONG", quantity=75, group_meta=meta), TradeRecord(id=2, leg_role="SHORT", quantity=150, group_meta=meta)]
    assert group_exit(legs, {1: 130.0, 2: 90.0}, 24550.0) is None                       # P&L 30 - (180 - 130) = -20
    assert "Structure stop (P&L -140.00/unit <= -130)" == group_exit(legs, {1: 130.0, 2: 150.0}, 24550.0)
    assert "Structure target (P&L 70.00/unit >= 65)" == group_exit(legs, {1: 60.0, 2: 10.0}, 24550.0)
    assert "Upper breakeven 24730 breached" in group_exit(legs, {1: 130.0, 2: 90.0}, 24731.0)   # the naked side


# --- execution -----------------------------------------------------------------------------

def test_paper_ratio_spread_books_per_leg_quantities():
    _load_master()
    t = _tenant("u-ratio-paper@example.com")
    result = _execute(t, _resolve(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG), key="u-ratio-1")   # 10,000 / (130 x 75) -> 1 lot
    assert result.executed, result.reasons
    trades = sorted(_trades(t["tenant_id"]), key=lambda tr: tr.id)
    assert [(tr.leg_role, tr.quantity, tr.option_position) for tr in trades] == [("LONG", NIFTY_LOT, "BUY"), ("SHORT", 2 * NIFTY_LOT, "WRITE")]
    meta = json.loads(trades[0].group_meta)
    assert meta["quantity"] == NIFTY_LOT and meta["lots"] == 1 and meta["pnl_stop"] == -130.0 and meta["max_loss"] is None
    assert set(meta["ratios"].values()) == {1, 2}
    assert any("UNDEFINED max loss" in r for r in result.reasons) and any("ratio legs x2 = 150" in r for r in result.reasons)
    assert all(o.quantity in (NIFTY_LOT, 2 * NIFTY_LOT) for o in result.orders)


def test_live_ratio_spread_places_the_long_first_and_doubles_the_short(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    _load_master()
    t = _tenant("u-ratio-live@example.com")
    broker = _OptionBroker(margin_per_lot=60_000.0, available=500_000.0)
    result = _execute(t, _resolve(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG), mode="LIVE", broker=broker, key="u-ratio-live-1")
    assert result.executed, result.reasons
    assert [(o.transaction_type.value, o.quantity) for o in broker.placed] == [("BUY", NIFTY_LOT), ("SELL", 2 * NIFTY_LOT)]


def test_paper_long_butterfly_sized_off_its_debit():
    _load_master()
    t = _tenant("u-fly-paper@example.com")
    fly = _resolve(OptionStrategy.LONG_BUTTERFLY, SignalDirection.LONG)
    result = _execute(t, fly, broker=_WingBroker(), key="u-fly-1")     # 10,000 / (40 x 75 = 3,000) -> 3 lots
    assert result.executed, result.reasons
    trades = sorted(_trades(t["tenant_id"]), key=lambda tr: tr.id)
    assert [(tr.leg_role, tr.quantity) for tr in trades] == [("LONG", 3 * NIFTY_LOT), ("SHORT", 6 * NIFTY_LOT), ("LONG", 3 * NIFTY_LOT)]
    meta = json.loads(trades[0].group_meta)
    assert meta["debit"] is True and meta["max_loss"] == 40.0 and meta["max_profit"] == 60.0 and meta["pnl_stop"] == -20.0
    assert any("Net debit 40/unit, max loss 40/unit (3,000/lot), max profit 60/unit" in r for r in result.reasons)


# --- API -----------------------------------------------------------------------------------

def test_deployment_api_custom_legs_and_preview(monkeypatch):
    _load_master()
    headers = _auth("u-api@example.com")
    _store_broker(headers, token_status="VALID")
    legs = [{"right": "PE", "role": "SHORT"}, {"right": "PE", "role": "LONG", "strike_rule": "OTM", "strike_offset": 2},
            {"right": "CE", "role": "SHORT", "strike_rule": "OTM", "strike_offset": 2}]
    assert _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CUSTOM").status_code == 400
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CUSTOM", custom_legs=legs[:1])
    assert bad.status_code == 400 and "at least two legs" in bad.json()["detail"]
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="IRON_CONDOR", custom_legs=legs)
    assert bad.status_code == 400 and "only apply when option_strategy is CUSTOM" in bad.json()["detail"]
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="LONG_BUTTERFLY", stop_credit_pct=150)
    assert bad.status_code == 400 and "at most 100" in bad.json()["detail"]
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CUSTOM", custom_legs=legs, strike_filters={"min_oi": 1000})
    assert bad.status_code == 400 and "Strike filters do not apply" in bad.json()["detail"]

    ok = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CUSTOM", custom_legs=legs)
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["option_strategy"] == "CUSTOM" and body["option_position"] == "WRITE"
    assert body["custom_legs"] == [{"right": "PE", "role": "SHORT", "strike_rule": "ATM", "strike_offset": 0, "ratio": 1},
                                   {"right": "PE", "role": "LONG", "strike_rule": "OTM", "strike_offset": 2, "ratio": 1},
                                   {"right": "CE", "role": "SHORT", "strike_rule": "OTM", "strike_offset": 2, "ratio": 1}]
    assert "custom structure: sell 1x ATM PE, buy 1x OTM2 PE, sell 1x OTM2 CE" in body["contract_rules"]
    listed = client.get("/api/deployments", headers=headers).json()
    assert listed[0]["custom_legs"] == body["custom_legs"]

    headers2 = _auth("u-api-2@example.com")
    _store_broker(headers2, token_status="VALID")
    ratio = _create(headers2, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CALL_RATIO_SPREAD", spread_width=2)
    assert ratio.status_code == 201 and "call ratio spread, buy 1 ATM CE / sell 2 2 step(s) higher" in ratio.json()["contract_rules"]

    monkeypatch.setattr("app.deployments.routes.build_adapter", lambda record, client=None: _WingBroker())
    preview = client.post("/api/deployments/preview-contract", headers=headers, json={
        "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_strategy": "CALL_RATIO_SPREAD", "spread_width": 2, "spot": SPOT,
    }).json()
    long_side = preview["structures"]["LONG"]
    assert [l["ratio"] for l in long_side["legs"]] == [1, 2] and long_side["metrics"]["pnl_stop"] == -130.0 and long_side["metrics"]["max_loss"] is None
    assert "not entered on a SHORT" in preview["structures"]["SHORT"]["error"]
    custom_preview = client.post("/api/deployments/preview-contract", headers=headers, json={
        "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_strategy": "CUSTOM", "custom_legs": legs, "spot": SPOT,
    }).json()
    assert custom_preview["structures"]["SHORT"]["metrics"]["net_credit"] == 110.0 and "custom structure" in custom_preview["rules"]
