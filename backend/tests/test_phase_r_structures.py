"""Phase R (V2.1-2.6): iron butterfly, short/long straddle and strangle, calendar spread -
resolution, credit/debit/undefined-risk economics, group exits, paper and LIVE execution, API."""
import asyncio
import json
from datetime import date

import pytest

from app.core.enums import OptionStrategy, SignalDirection, StrikeRule
from app.core.models import RiskConfig
from app.db.models import TradeRecord, User
from app.execution.multileg import execute_structure
from app.instruments.contracts import ContractResolutionError
from app.instruments.spreads import describe_structure, is_debit, resolve_structure, structure_metrics
from app.trading.position_monitor import group_exit
from tests.master_fixture import NIFTY_LOT
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_rules import TODAY, _load_master
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_multileg import SPOT, _OptionBroker, _premium, _rules
from tests.test_trading_worker import _signal, _tenant, _trades

FAR_BUMP = 40.0   # a later expiry's option is worth more: the calendar's long leg


def _run(coro):
    return asyncio.run(coro)


class _CalendarBroker(_OptionBroker):
    """Quotes the 08 OCT expiry FAR_BUMP above the 01 OCT one, as time value would."""
    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        price = await super().get_ltp_for_symbol(symbol, exchange)
        return price + FAR_BUMP if "08 OCT" in symbol else price


def _resolve(strategy, direction=SignalDirection.LONG, width=2, **kw):
    async def go():
        async with _session_factory() as session:
            return await resolve_structure(session, "NIFTY 50", _rules(**kw), strategy, direction, spread_width=width, spot=SPOT, today=TODAY)
    return _run(go())


def _legs(structure):
    return [(l.role, l.contract.right, l.contract.strike, l.contract.expiry) for l in structure.legs]


def _metrics(structure, broker=None, **kw):
    broker = broker or _OptionBroker()
    premiums = {l.contract.tradingsymbol: _run(broker.get_ltp_for_symbol(l.contract.tradingsymbol, "NFO")) for l in structure.legs}
    return structure_metrics(structure, premiums, target_credit_pct=kw.get("target"), stop_credit_pct=kw.get("stop"))


E1, E2 = date(2026, 10, 1), date(2026, 10, 8)


def test_new_structures_resolve_the_right_legs():
    _load_master()
    fly = _resolve(OptionStrategy.IRON_BUTTERFLY)
    assert _legs(fly) == [("SHORT", "PE", 24500.0, E1), ("LONG", "PE", 24400.0, E1), ("SHORT", "CE", 24500.0, E1), ("LONG", "CE", 24600.0, E1)]
    assert fly.width_points == 100.0 and not fly.debit

    straddle = _resolve(OptionStrategy.SHORT_STRADDLE, SignalDirection.SHORT)
    assert _legs(straddle) == [("SHORT", "PE", 24500.0, E1), ("SHORT", "CE", 24500.0, E1)] and straddle.width_points == 0.0
    assert any("undefined risk" in n for n in straddle.notes)

    strangle = _resolve(OptionStrategy.SHORT_STRANGLE, strike_rule=StrikeRule.OTM, strike_offset=2)
    assert _legs(strangle) == [("SHORT", "PE", 24400.0, E1), ("SHORT", "CE", 24600.0, E1)]

    long_straddle = _resolve(OptionStrategy.LONG_STRADDLE)
    assert _legs(long_straddle) == [("LONG", "PE", 24500.0, E1), ("LONG", "CE", 24500.0, E1)] and long_straddle.debit
    assert all(l.contract.position.value == "BUY" for l in long_straddle.legs)

    long_strangle = _resolve(OptionStrategy.LONG_STRANGLE)   # offset defaults to 1 step OTM
    assert _legs(long_strangle) == [("LONG", "PE", 24450.0, E1), ("LONG", "CE", 24550.0, E1)]

    cal_long = _resolve(OptionStrategy.CALENDAR_SPREAD, SignalDirection.LONG)
    assert _legs(cal_long) == [("SHORT", "PE", 24500.0, E1), ("LONG", "PE", 24500.0, E2)] and cal_long.expiry == E1 and cal_long.debit
    cal_short = _resolve(OptionStrategy.CALENDAR_SPREAD, SignalDirection.SHORT)
    assert _legs(cal_short) == [("SHORT", "CE", 24500.0, E1), ("LONG", "CE", 24500.0, E2)]

    # Direction discipline is unchanged for the directional spreads.
    with pytest.raises(ContractResolutionError, match="not entered on a LONG signal"):
        _resolve(OptionStrategy.BEAR_CALL_SPREAD, SignalDirection.LONG)
    # Wingless structures ignore spread_width; winged ones still need it.
    assert _resolve(OptionStrategy.SHORT_STRADDLE, width=0).legs
    with pytest.raises(ContractResolutionError, match="spread_width"):
        _resolve(OptionStrategy.IRON_BUTTERFLY, width=0)

    for s in (OptionStrategy.IRON_BUTTERFLY, OptionStrategy.SHORT_STRADDLE, OptionStrategy.SHORT_STRANGLE,
              OptionStrategy.LONG_STRADDLE, OptionStrategy.LONG_STRANGLE, OptionStrategy.CALENDAR_SPREAD):
        text = describe_structure(s, 2, _rules())
        assert text and s.value.split("_")[0].lower() in text
    assert is_debit(OptionStrategy.CALENDAR_SPREAD) and not is_debit(OptionStrategy.IRON_BUTTERFLY)


def test_credit_metrics_defined_and_undefined_risk():
    _load_master()
    fly = _metrics(_resolve(OptionStrategy.IRON_BUTTERFLY))
    # sell 24500 PE 120 + 24500 CE 110, buy 24400 PE 80 + 24600 CE 70 -> credit 80, wings 100 wide.
    assert fly.net_credit == 80.0 and fly.max_profit == 80.0 and fly.max_loss == 20.0 and fly.risk_per_unit == 20.0
    assert fly.breakevens == [24420.0, 24580.0] and fly.target_value == 40.0 and fly.stop_value == 160.0
    assert fly.defined_risk and not fly.debit
    assert fly.short_strikes == {} and fly.underlying_exits == {"below": 24420.0, "above": 24580.0}   # ATM shorts: breakevens are the exits

    strangle = _metrics(_resolve(OptionStrategy.SHORT_STRANGLE, strike_rule=StrikeRule.OTM, strike_offset=2))
    assert strangle.short_strikes == {"PE": 24400.0, "CE": 24600.0} and strangle.underlying_exits == {}   # OTM shorts: strike breach stays

    straddle = _metrics(_resolve(OptionStrategy.SHORT_STRADDLE))
    assert straddle.net_credit == 230.0 and straddle.max_profit == 230.0 and straddle.max_loss is None
    assert not straddle.defined_risk and straddle.risk_per_unit == 230.0       # default stop 100% of the credit
    assert straddle.breakevens == [24270.0, 24730.0] and straddle.stop_value == 460.0
    assert straddle.short_strikes == {} and straddle.underlying_exits == {"below": 24270.0, "above": 24730.0}
    tight = _metrics(_resolve(OptionStrategy.SHORT_STRADDLE), stop=50, target=25)
    assert tight.risk_per_unit == 115.0 and tight.stop_value == 345.0 and tight.target_value == 172.5
    d = straddle.as_dict()
    assert d["defined_risk"] is False and d["max_loss"] is None and d["risk_per_unit"] == 230.0


def test_debit_metrics_straddle_strangle_and_calendar():
    _load_master()
    ls = _metrics(_resolve(OptionStrategy.LONG_STRADDLE))
    assert ls.debit and ls.net_credit == -230.0 and ls.max_loss == 230.0 and ls.max_profit is None and ls.risk_per_unit == 230.0
    assert ls.breakevens == [24270.0, 24730.0]
    assert ls.target_value == 345.0 and ls.stop_value == 115.0       # +50% / -50% of the debit by default
    assert ls.short_strikes == {}                                      # nothing to breach
    capped = _metrics(_resolve(OptionStrategy.LONG_STRADDLE), stop=150, target=20)
    assert capped.stop_value == 0.0 and capped.target_value == 276.0  # stop % is capped at the whole debit

    cal = _metrics(_resolve(OptionStrategy.CALENDAR_SPREAD), broker=_CalendarBroker())
    assert cal.debit and cal.net_credit == -FAR_BUMP and cal.max_loss == FAR_BUMP and cal.breakevens == [] and cal.short_strikes == {}
    assert cal.legs[0]["expiry"] == E1.isoformat() and cal.legs[1]["expiry"] == E2.isoformat()

    # A debit structure quoting as a net credit means inconsistent quotes: refused, never entered.
    with pytest.raises(ContractResolutionError, match="net credit"):
        _metrics(_resolve(OptionStrategy.CALENDAR_SPREAD))            # same premium both expiries -> zero, not a debit


def test_group_exit_for_debit_structures():
    meta = json.dumps({"debit": True, "stop_value": 115.0, "target_value": 345.0, "short_strikes": {}})
    legs = [TradeRecord(id=1, leg_role="LONG", group_meta=meta), TradeRecord(id=2, leg_role="LONG")]
    assert group_exit(legs, {1: 120.0, 2: 110.0}, SPOT) is None                          # worth 230 = the debit
    assert "Structure target" in group_exit(legs, {1: 200.0, 2: 160.0}, 24900.0)          # worth 360 >= 345
    assert "Structure stop" in group_exit(legs, {1: 60.0, 2: 50.0}, SPOT)                 # worth 110 <= 115
    # An ATM short straddle keeps the credit value rules; the underlying exits are its breakevens,
    # never the (at-the-money) short strikes.
    short_meta = json.dumps({"debit": False, "stop_value": 460.0, "target_value": 115.0, "short_strikes": {},
                             "underlying_exits": {"below": 24270.0, "above": 24730.0}})
    shorts = [TradeRecord(id=3, leg_role="SHORT", group_meta=short_meta), TradeRecord(id=4, leg_role="SHORT")]
    assert group_exit(shorts, {3: 120.0, 4: 110.0}, SPOT) is None
    assert group_exit(shorts, {3: 120.0, 4: 110.0}, 24501.0) is None                        # through the ATM strike is not an exit
    assert "Spread stop" in group_exit(shorts, {3: 300.0, 4: 170.0}, SPOT)
    assert "Upper breakeven 24730" in group_exit(shorts, {3: 120.0, 4: 110.0}, 24731.0)
    assert "Lower breakeven 24270" in group_exit(shorts, {3: 120.0, 4: 110.0}, 24269.0)


def _execute(t, structure, *, mode="PAPER", broker=None, key, capital=1_000_000, target=None, stop=None, rules=None):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_structure(session, user, mode=mode, strategy_id="ema_rsi_scalper_1m", signal=_signal(), structure=structure,
                                           rules=rules or _rules(), target_credit_pct=target, stop_credit_pct=stop, idempotency_key=key,
                                           broker=broker if mode == "LIVE" else None, quote_broker=broker or _OptionBroker(),
                                           risk_config=RiskConfig(capital=capital, risk_per_trade_pct=1.0))
    return _run(go())


def test_paper_long_straddle_sized_off_the_debit():
    _load_master()
    t = _tenant("r-long-straddle@example.com")
    refused = _execute(t, _resolve(OptionStrategy.LONG_STRADDLE), key="r-ls-1")          # 10,000 risk < 17,250 debit per lot
    assert not refused.executed and any("lot" in r.lower() and ("risk" in r.lower() or "below" in r.lower()) for r in refused.reasons[-1:])
    result = _execute(t, _resolve(OptionStrategy.LONG_STRADDLE), key="r-ls-2", capital=5_000_000)   # 50,000 / 17,250 -> 2 lots
    assert result.executed, result.reasons
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and all(tr.leg_role == "LONG" and tr.direction == "LONG" and tr.option_position == "BUY" for tr in trades)
    assert all(tr.quantity == 2 * NIFTY_LOT and tr.option_strategy == "LONG_STRADDLE" for tr in trades)
    meta = json.loads(trades[0].group_meta)
    assert meta["debit"] is True and meta["net_credit"] == -230.0 and meta["lots"] == 2 and meta["stop_value"] == 115.0
    assert any("Net debit 230/unit" in r for r in result.reasons)
    # Per-leg informational stop for a bought leg is the debit stop %, never zero.
    assert all(tr.stop_loss > 0 for tr in trades)


def test_paper_short_straddle_sized_off_the_stop_not_a_max_loss():
    _load_master()
    t = _tenant("r-short-straddle@example.com")
    refused = _execute(t, _resolve(OptionStrategy.SHORT_STRADDLE), key="r-ss-1")          # stop 100%: 230 x 75 = 17,250 > 10,000
    assert not refused.executed and any("UNDEFINED max loss" in r for r in refused.reasons)
    result = _execute(t, _resolve(OptionStrategy.SHORT_STRADDLE), key="r-ss-2", stop=20)   # 46 x 75 = 3,450 -> 2 lots
    assert result.executed, result.reasons
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and all(tr.leg_role == "SHORT" and tr.option_position == "WRITE" and tr.quantity == 2 * NIFTY_LOT for tr in trades)
    meta = json.loads(trades[0].group_meta)
    assert meta["defined_risk"] is False and meta["max_loss"] is None and meta["stop_value"] == 276.0
    assert meta["short_strikes"] == {} and meta["underlying_exits"] == {"below": 24270.0, "above": 24730.0}


def test_live_long_only_structure_is_capped_by_its_debit(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    _load_master()
    t = _tenant("r-live-ls@example.com")
    broker = _OptionBroker(margin_per_lot=None, available=500_000.0)       # no calculator needed: no short leg
    result = _execute(t, _resolve(OptionStrategy.LONG_STRADDLE), mode="LIVE", broker=broker, key="r-live-1", capital=5_000_000)
    assert result.executed, result.reasons
    assert [o.transaction_type.value for o in broker.placed] == ["BUY", "BUY"] and all(o.quantity == 2 * NIFTY_LOT for o in broker.placed)
    assert any("Debit 17,250/lot" in r for r in result.reasons)

    poor = _OptionBroker(margin_per_lot=None, available=10_000.0)          # 8,000 usable < 17,250 per lot
    t2 = _tenant("r-live-ls-poor@example.com")
    result = _execute(t2, _resolve(OptionStrategy.LONG_STRADDLE), mode="LIVE", broker=poor, key="r-live-2", capital=5_000_000)
    assert not result.executed and any("Insufficient funds for the debit" in r for r in result.reasons) and poor.placed == []


def test_deployment_api_and_preview_for_new_structures(monkeypatch):
    _load_master()
    headers = _auth("r-api@example.com")
    _store_broker(headers, token_status="VALID")
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="LONG_STRADDLE", stop_credit_pct=150)
    assert bad.status_code == 400 and "at most 100" in bad.json()["detail"]
    short = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="SHORT_STRANGLE", strike_rule="OTM", strike_offset=2)
    assert short.status_code == 201, short.text
    assert short.json()["option_position"] == "WRITE" and "short strangle" in short.json()["contract_rules"] and "undefined risk" in short.json()["contract_rules"]
    headers2 = _auth("r-api-2@example.com")
    _store_broker(headers2, token_status="VALID")
    long = _create(headers2, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="LONG_STRANGLE", target_credit_pct=40, stop_credit_pct=60)
    assert long.status_code == 201, long.text
    assert long.json()["option_position"] == "BUY" and "debit" in long.json()["contract_rules"]

    monkeypatch.setattr("app.deployments.routes.build_adapter", lambda record, client=None: _CalendarBroker())
    preview = client.post(f"/api/deployments/preview-contract?as_of={TODAY.isoformat()}", headers=headers, json={
        "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_strategy": "CALENDAR_SPREAD", "spot": SPOT,
    }).json()
    for direction, right in (("LONG", "PE"), ("SHORT", "CE")):
        st = preview["structures"][direction]
        assert [l["role"] for l in st["legs"]] == ["SHORT", "LONG"] and {l["right"] for l in st["legs"]} == {right}
        assert st["legs"][0]["expiry"] == E1.isoformat() and st["legs"][1]["expiry"] == E2.isoformat() and st["debit"] is True
        assert st["metrics"]["debit"] is True and st["metrics"]["max_loss"] == FAR_BUMP and st["metrics"]["risk_per_unit"] == FAR_BUMP
    assert "calendar spread" in preview["rules"]
