"""Analyze a chart: "Option strikes from your settings" and the signal-language check.

Made-up data only: a made-up underlying with a made-up strike ladder in the instrument master, the caller's own
made-up OPTION deployments on it, and the fake engine's made-up answer (trigger 120.5)."""
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.chart_analyze import STRIKES_FLAG, contract, signal_language, strikes
from app.db.models import InstrumentRecord, StrategyDeploymentRecord
from app.platform import controls
from tests.chart_analyze_fakes import answer
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import BASE, _fake_answer, _isolated, _owner, _run, _upload  # noqa: F401 - _isolated is the autouse fixture

LADDER = [float(k) for k in range(100, 145, 5)]          # 100, 105, ..., 140


def _strikes_flag(tenant_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(STRIKES_FLAG) or {"on": False, "tenants": []}
            flags[STRIKES_FLAG] = {"on": False, "tenants": sorted(set(entry.get("tenants") or []) | {tenant_id})}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _sowner(tag: str) -> dict:
    """A member of an organisation with chart_analyze AND chart_analyze_strikes on (the beta allow-lists)."""
    who = _owner(tag)
    _strikes_flag(who["tenant_id"])
    return who


def _underlying() -> str:
    return "ZZCA" + uuid.uuid4().hex[:6].upper()


def _ladder(underlying: str, expiry: date) -> None:
    async def go():
        async with _session_factory() as session:
            for right in ("CE", "PE"):
                for k in LADDER:
                    session.add(InstrumentRecord(broker="upstox", exchange="NFO", instrument_key=f"upstox|{underlying}{expiry:%y%m%d}{int(k)}{right}",
                                                 tradingsymbol=f"{underlying}{int(k)}{right}", underlying=underlying, instrument_type=right,
                                                 expiry=expiry, strike=k, lot_size=1))
            await session.commit()
    _run(go())


def _deployment(tenant_id: int, symbol: str, *, strategy: str = "BULL_PUT_SPREAD", rule: str = "OTM", offset: int = 1, width: int = 2,
                position: str = "WRITE", status: str = "ACTIVE", kind: str = "OPTION") -> int:
    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=tenant_id, strategy_id=f"made-up-{uuid.uuid4().hex[:6]}", symbol=symbol, exchange="NSE",
                                           timeframe="15min", mode="PAPER", status=status, instrument_kind=kind, option_position=position,
                                           expiry_rule="NEAREST", strike_rule=rule, strike_offset=offset, option_strategy=strategy,
                                           spread_width=width)
            session.add(dep)
            await session.commit()
            return dep.id
    return _run(go())


def _analysed(who: dict, monkeypatch, kind: str = "formed") -> int:
    _fake_answer(monkeypatch, kind)
    item = _upload(who).json()
    assert client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()["status"] == "done"
    return item["id"]


def _strikes(who: dict, item_id: int, symbol: str | None = None):
    params = {"symbol": symbol} if symbol else None
    return client.get(f"{BASE}/items/{item_id}/strikes", headers=who["headers"], params=params)


def _texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


# --- option strikes from your settings ----------------------------------------------------------------------------------

def test_a_bull_put_spread_setting_works_out_its_main_strike_and_wing_at_the_trigger(monkeypatch):
    who = _sowner("strikes")
    sym = _underlying()
    _ladder(sym, datetime.now(strikes.IST).date() + timedelta(days=3))
    _deployment(who["tenant_id"], sym)                                     # OTM 1, wing 2 strikes away
    body = _strikes(who, _analysed(who, monkeypatch), sym).json()
    assert body["source"] == "your_settings" and body["side"] == "bull_put" and body["symbol"] == sym
    assert body["reference"] == {"text": "the trigger level", "price": 120.5, "approximate": True}
    assert body["approximate_note"] == strikes.APPROXIMATE and body["message"] is None
    [item] = body["items"]
    assert item["mode"] == "Paper" and item["settings"] == "Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry"
    # Spot 120.5 -> at the money 120; one strike out of the money on the put side is lower (115); the wing two strikes lower (105).
    assert item["legs"] == [{"label": "Main strike", "right": "PE", "strike": 115.0, "ratio": 1},
                            {"label": "Wing", "right": "PE", "strike": 105.0, "ratio": 1}]
    assert item["note"] is None and item["expiry"]


def test_only_option_deployments_on_the_same_symbol_that_fit_the_side_are_listed(monkeypatch):
    who = _sowner("strikes-fit")
    sym = _underlying()
    _ladder(sym, datetime.now(strikes.IST).date() + timedelta(days=3))
    single = _deployment(who["tenant_id"], sym, strategy="SINGLE", rule="ATM", offset=0, position="BUY")
    _deployment(who["tenant_id"], sym, strategy="BEAR_CALL_SPREAD")              # the other side
    _deployment(who["tenant_id"], sym, strategy="IRON_CONDOR")                   # neutral: not listed
    _deployment(who["tenant_id"], sym, status="STOPPED")                         # stopped: not listed
    _deployment(who["tenant_id"], sym, kind="UNDERLYING")                        # no options
    _deployment(who["tenant_id"], _underlying())                                 # another symbol
    other = _sowner("strikes-other")
    _deployment(other["tenant_id"], sym)                                         # another organisation's
    body = _strikes(who, _analysed(who, monkeypatch), sym).json()
    assert [i["id"] for i in body["items"]] == [single]
    assert body["items"][0]["settings"] == "Single call option · main strike at the money · nearest expiry"
    assert body["items"][0]["legs"] == [{"label": "Main strike", "right": "CE", "strike": 120.0, "ratio": 1}]


def test_the_empty_states_say_why(monkeypatch):
    who = _sowner("strikes-empty")
    sym = _underlying()
    item_id = _analysed(who, monkeypatch)
    assert _strikes(who, item_id, sym).json()["message"] == strikes.NONE_FIT
    _deployment(who["tenant_id"], sym)                                           # a fitting setting, but no option list loaded
    [item] = _strikes(who, item_id, sym).json()["items"]
    assert item["legs"] is None and item["note"] == strikes.NO_MASTER
    none_id = _analysed(who, monkeypatch, "none")
    body = _strikes(who, none_id, sym).json()
    assert body["side"] == "none" and body["message"] == strikes.NO_SIDE and body["items"] == []


def test_the_symbol_read_from_the_picture_is_used_when_none_is_typed(monkeypatch):
    who = _sowner("strikes-read")
    body = _strikes(who, _analysed(who, monkeypatch)).json()
    assert body["symbol"] == "DEMO" and body["message"] == strikes.NONE_FIT


def test_not_analysed_other_users_and_bad_symbols_are_refused(monkeypatch):
    who = _sowner("strikes-refuse")
    item = _upload(who).json()
    assert _strikes(who, item["id"]).status_code == 409
    other = _sowner("strikes-refuse-2")
    assert _strikes(other, _analysed(who, monkeypatch)).status_code == 404
    assert _strikes(who, item["id"], "<script>").status_code == 422


def test_strikes_route_is_behind_both_flags(monkeypatch):
    who = _owner("strikes-off", flag=False)
    response = client.get(f"{BASE}/items/1/strikes", headers=who["headers"])
    assert response.status_code == 503 and response.headers.get("X-Feature-Disabled") == "chart_analyze"
    # chart_analyze on, chart_analyze_strikes off: the analysis works, the strikes route does not.
    only = _owner("strikes-sub-off")
    item_id = _analysed(only, monkeypatch)
    response = _strikes(only, item_id)
    assert response.status_code == 503 and response.headers.get("X-Feature-Disabled") == STRIKES_FLAG


def test_the_strikes_answer_uses_structure_words_only(monkeypatch):
    who = _sowner("strikes-words")
    sym = _underlying()
    _ladder(sym, datetime.now(strikes.IST).date() + timedelta(days=3))
    for strategy in ("SINGLE", "BULL_PUT_SPREAD", "CALL_RATIO_SPREAD"):
        _deployment(who["tenant_id"], sym, strategy=strategy, position="WRITE")
    body = _strikes(who, _analysed(who, monkeypatch), sym).json()
    assert len(body["items"]) == 3
    for text in _texts(body):
        assert signal_language.is_clean(text), text
    for text in (strikes.NO_SIDE, strikes.NO_SYMBOL, strikes.NO_REFERENCE, strikes.NO_MASTER, strikes.NONE_FIT, strikes.APPROXIMATE):
        assert signal_language.is_clean(text), text


def test_symbol_normalising():
    assert strikes.normalise_symbol("NSE:nifty  50") == "NIFTY 50"
    assert strikes.normalise_symbol("  ") is None and strikes.normalise_symbol(None) is None


# --- the signal-language check ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Buy near the area", "a SELL zone", "the next target", "Targets above", "place a stop below",
                                  "stop-loss under the low", "stoploss", "keep the SL tight", "sold off hard", "buying pressure"])
def test_call_words_are_found(text):
    assert not signal_language.is_clean(text)


@pytest.mark.parametrize("text", ["the buyer area held", "a seller area above", "buyers and sellers", "a close above the trigger line",
                                  "invalidation below the area", "the next area", "slow drift", "a higher low"])
def test_structure_words_pass(text):
    assert signal_language.is_clean(text)


def test_contract_withholds_any_result_text_with_a_call_word():
    raw = answer("forming")
    raw["story"] = ["The drawn candles rise into the seller area.", "A made-up target sits above."]
    raw["scenarios"]["alternative"] = "Made-up: price may stop at the line."
    raw["evidence"][0]["reason"] = "buyers held the made-up area"
    out = contract.validate(raw, mode="image_only", width=640, height=360)
    assert out["story"] == ["The drawn candles rise into the seller area.", contract.WITHHELD]
    assert out["scenarios"]["alternative"] == contract.WITHHELD
    assert out["evidence"][0]["reason"] == "buyers held the made-up area"
    # The fixed texts the server adds are the only place "buy or sell" appears: in the disclaimer, which says it is NOT a call.
    for text in _texts({k: v for k, v in out.items() if k != "disclaimer"}):
        assert signal_language.is_clean(text), text
