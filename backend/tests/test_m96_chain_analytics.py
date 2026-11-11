"""M96 N1 (owner 11 Nov): the option chain analytics served as the `analytics` key of GET /api/option-chain/analyses
(app/option_chain/analyses.py `chain_analytics`). T1: a hand-computed fixture chain for the build-up truth table at every
strike, the futures OI build-up, OI walls and their shift, the OI / change-in-OI / volume / per-strike PCR, DEX / GEX
and the flip level against an independent Black-Scholes, volume / OI and the user's own highlight threshold, the ATM IV
percentile / rank with history; no look-ahead (a snapshot read after the valuation instant is never used); the
no-broker state; and every text against the platform's advice-word lists."""
import math
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.core.config import RISK_FREE_RATE
from app.db.models import InstrumentRecord, OISnapshotRecord, StrikeOISnapshotRecord
from app.option_chain import analyses as A
from app.portfolio.risk_text import has_advice
from app.screener.taxonomy import advice_words
from tests.test_auth_api import _session_factory, client
from tests.test_oc2_option_chain_analyses import (
    CURRENT,
    EXPIRY,
    NOW,
    REF_AT,
    REFERENCE,
    URL,
    UTC,
    _ChainBroker,
    _install,
    _ist_today,
    _name,
    _v2_owner,
    chain,
    row,
    run,
)
from tests.test_phase_aa_market_data_api import _store_credentials

SNAP = A.Snapshot


def analytics(current: OptionChain, history=(), **kw):
    return A.chain_analytics(current, list(history), NOW, **kw)


# ---------------------------------------------------------------- 1. build-up at every strike + futures

def test_buildup_truth_table_at_every_strike_including_one_without_a_first_reading():
    current = chain([*CURRENT.rows, row(150, 9, 9, 1, 1)], 118.0)      # 150 was not stored at 09:20
    out = analytics(current, [SNAP(REF_AT, REFERENCE)])["buildup"]
    assert out["state"] == "ok" and out["reference_time"] == REF_AT.isoformat()
    by = {r["strike"]: r for r in out["rows"]}
    assert [by[k]["call_class"] for k in (100, 110, 120, 130, 140, 150)] == [
        A.LONG_BUILDUP, A.SHORT_BUILDUP, A.SHORT_COVERING, A.LONG_UNWINDING, A.NO_CLEAR_CHANGE, A.NOT_AVAILABLE]
    assert [by[k]["put_class"] for k in (100, 110, 120, 130, 140, 150)] == [
        A.NO_CLEAR_CHANGE, A.LONG_BUILDUP, A.SHORT_BUILDUP, A.NOT_AVAILABLE, A.LONG_UNWINDING, A.NOT_AVAILABLE]
    assert by[110]["call_price_change"] == -2 and by[110]["call_oi_change"] == 10 and by[120]["put_oi_change"] == 30
    assert out["counts"]["call"][A.NOT_AVAILABLE] == 1 and sum(out["counts"]["put"].values()) == 6


def test_buildup_needs_a_first_snapshot_and_says_why():
    plain = analytics(CURRENT)["buildup"]
    assert plain["state"] == "no_data" and plain["message"] == A.AN_NEED_HISTORY and plain["rows"] == []
    off = A.chain_analytics(CURRENT, [], NOW, history_off=True)["buildup"]
    assert off["message"] == A.AN_HISTORY_OFF and off["futures"]["message"] == A.AN_HISTORY_OFF


def test_futures_oi_buildup_over_the_session_without_look_ahead():
    def fut(minutes, price, oi):
        return SNAP(REF_AT + timedelta(minutes=minutes), REFERENCE, price, oi)
    history = [fut(0, 100.0, 1000.0), fut(10, 102.0, 1100.0), fut(20, 101.0, 900.0), fut(30, 99.0, 1200.0),
               SNAP(REF_AT + timedelta(minutes=35), REFERENCE),                    # no future read in this slot
               fut(60, 500.0, 5.0)]                                                 # read after NOW: never used
    out = analytics(CURRENT, history)["buildup"]["futures"]
    assert out["state"] == "ok" and [p["label"] for p in out["points"]] == [
        "No clear change", "Long build-up", "Short covering", "Short build-up"]
    assert out["latest"]["price_change"] == -1 and out["latest"]["oi_change"] == 200
    assert analytics(CURRENT, history[:1])["buildup"]["futures"]["message"] == A.AN_NO_FUTURE


# ---------------------------------------------------------------- 2. walls and their shift

def test_walls_now_and_the_shift_through_the_session():
    out = analytics(CURRENT, [SNAP(REF_AT, REFERENCE)])["walls"]
    # CURRENT call OI 15 30 35 50 40, put OI 50 45 60 10 3; added since 09:20: call +5 +10 -5 -10 +10, put 0 +5 +30 0 -2.
    assert out["now"] == {"call_oi_strike": 130, "call_oi": 50, "put_oi_strike": 120, "put_oi": 60,
                          "call_add_strike": 110, "call_add": 10, "put_add_strike": 120, "put_add": 30}   # 110 / 140 tie: the lower
    assert [(p["call_oi_strike"], p["put_oi_strike"], p["call_add_strike"], p["put_add_strike"]) for p in out["series"]] == [
        (130, 100, None, None), (130, 120, 110, 120)]
    assert out["series"][-1]["time"] == NOW.isoformat() and out["strikes_in_every_snapshot"] == 5


def test_walls_without_history_show_now_only_and_no_snapshot_after_now_is_used():
    later = SNAP(NOW + timedelta(minutes=5), chain([row(k, 999, 999, 1, 1) for k in (100, 110, 120, 130, 140)], 118.0))
    out = analytics(CURRENT, [later])["walls"]
    assert out["series"] == [] and out["now"]["call_add_strike"] is None and out["message"] == A.AN_NEED_HISTORY
    assert analytics(CURRENT, [later])["snapshots"] == 0


# ---------------------------------------------------------------- 3. PCR variants

VOL = chain([
    OptionChainRow(strike=100, call_oi=100, call_volume=50, call_change_oi=10, put_oi=200, put_volume=500, put_change_oi=40),
    OptionChainRow(strike=110, call_oi=0, call_volume=30, call_change_oi=-5, put_oi=100),
    OptionChainRow(strike=120, call_oi=400, call_volume=1200, call_change_oi=20, put_oi=50, put_volume=25, put_change_oi=10),
], 110.0)


def test_pcr_variants_over_the_whole_chain_with_the_brokers_change_field():
    out = analytics(VOL)["pcr"]
    assert out["oi"] == {"put": 350, "call": 500, "pcr": 0.7}
    assert out["change_oi"] == {"put": 50, "call": 25, "pcr": 2.0, "basis": "broker"}
    assert out["volume"] == {"put": 525, "call": 1280, "pcr": round(525 / 1280, 2)}
    assert [r["pcr"] for r in out["per_strike"]] == [2.0, None, round(50 / 400, 2)]     # call OI 0 -> no ratio


def test_change_pcr_falls_back_to_the_session_first_snapshot():
    out = analytics(CURRENT, [SNAP(REF_AT, REFERENCE)])["pcr"]
    assert out["oi"]["put"] == 168 and out["oi"]["call"] == 170 and out["oi"]["pcr"] == round(168 / 170, 2)
    assert out["change_oi"] == {"put": 33, "call": 10, "pcr": 3.3, "basis": "session"}
    assert out["volume"] == {"put": None, "call": None, "pcr": None}
    assert analytics(CURRENT)["pcr"]["change_oi"] == {"put": None, "call": None, "pcr": None, "basis": None}


# ---------------------------------------------------------------- 4. IV percentile / rank and IV change

def _iv_chain(iv: float, spot: float = 100.0) -> OptionChain:
    # quoted IV and no last price: the quote is read as it is (app/options_builder/chain.py option_iv)
    return chain([row(k, 10, 10, None, None, iv, iv) for k in (95, 100, 105)], spot)


HISTORY_20 = [(date(2026, 9, 1) + timedelta(days=i), 10.0 + i) for i in range(20)]      # 10 .. 29


def test_iv_percentile_and_rank_from_stored_history():
    out = analytics(_iv_chain(25.0), iv_history=HISTORY_20, min_sessions=20)["iv"]["rank"]
    assert out["atm_iv"] == 25.0 and out["sessions"] == 20 and out["enough"] is True
    assert out["percentile"] == 75.0                                   # 15 of 20 earlier closes below 25
    assert out["rank"] == round((25 - 10) / (29 - 10) * 100, 1) and (out["low"], out["high"]) == (10.0, 29.0)
    above = analytics(_iv_chain(35.0), iv_history=HISTORY_20, min_sessions=20)["iv"]["rank"]
    assert above["rank"] == 100.0 and above["percentile"] == 100.0 and above["high"] == 35.0


def test_iv_rank_below_the_minimum_says_not_enough_history():
    out = analytics(_iv_chain(25.0), iv_history=HISTORY_20[:19], min_sessions=20)["iv"]["rank"]
    assert out["enough"] is False and out["sessions"] == 19 and out["percentile"] is None and out["rank"] is None
    assert analytics(_iv_chain(25.0), min_sessions=20)["iv"]["rank"]["sessions"] == 0


def test_iv_change_per_strike_since_the_first_snapshot():
    out = analytics(_iv_chain(20.0), [SNAP(REF_AT, _iv_chain(18.0))])["iv"]
    assert [(r["strike"], r["call_iv"], r["call_iv_change"], r["put_iv_change"]) for r in out["per_strike"]] == [
        (95, 20.0, 2.0, 2.0), (100, 20.0, 2.0, 2.0), (105, 20.0, 2.0, 2.0)]
    assert analytics(_iv_chain(20.0))["iv"]["per_strike"][0]["call_iv_change"] is None


# ---------------------------------------------------------------- 5. DEX / GEX and the flip level

def _bs(spot, strike, t, sigma, call):
    """An independent Black-Scholes delta and gamma (no dividend), written out here."""
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    cdf = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    return (cdf if call else cdf - 1), pdf / (spot * sigma * math.sqrt(t))


LAKH = 1e5
EXPO = chain([row(99, 10 * LAKH, 100 * LAKH, None, None, 20.0, 20.0), row(100, 60 * LAKH, 40 * LAKH, None, None, 20.0, 20.0),
              row(101, 200 * LAKH, 10 * LAKH, None, None, 20.0, 20.0)], 100.0)


def test_dex_gex_per_strike_net_and_flip_by_hand():
    lot = 50.0
    out = analytics(EXPO, lot_size=lot)["exposure"]
    assert out["state"] == "ok" and out["lot_size"] == lot
    t = A._years(EXPIRY, NOW)
    spot = 100.0
    want_dex = want_gex = 0.0
    running, flips, prev = 0.0, [], None
    for r, got in zip(EXPO.rows, out["rows"]):
        cd, cg = _bs(spot, r.strike, t, 0.20, True)
        pd, pg = _bs(spot, r.strike, t, 0.20, False)
        dex = (r.call_oi * cd + r.put_oi * pd) * lot * spot / 1e7
        gex = (r.call_oi * cg - r.put_oi * pg) * lot * spot * spot * 0.01 / 1e7
        assert got["call_delta"] == pytest.approx(cd, abs=1e-4) and got["put_delta"] == pytest.approx(pd, abs=1e-4)
        assert got["net_dex"] == pytest.approx(dex, abs=0.01) and got["net_gex"] == pytest.approx(gex, abs=0.01)
        want_dex += dex
        want_gex += gex
        running += round(gex, 2)
        if prev is not None and (prev > 0) != (running > 0):
            flips.append(r.strike)
        prev = running
    assert out["net_dex"] == pytest.approx(want_dex, abs=0.05) and out["net_gex"] == pytest.approx(want_gex, abs=0.05)
    assert out["rows"][0]["cumulative_gex"] < 0 < out["rows"][-1]["cumulative_gex"]
    assert out["flips"] == flips == [101] and out["flip"] == 101
    assert "rupees crore" in out["units"]["dex"] and "1% move" in out["units"]["gex"]


def test_exposure_states_without_a_lot_size_a_spot_or_time_left():
    assert analytics(EXPO)["exposure"]["message"] == A.AN_NO_LOT                      # never assumed to be 1
    assert analytics(EXPO.model_copy(update={"underlying_ltp": None}), lot_size=50)["exposure"]["message"] == A.AN_NO_SPOT
    gone = EXPO.model_copy(update={"expiry": "2026-10-01"})
    assert analytics(gone, lot_size=50)["exposure"]["message"] == A.AN_NO_TIME


# ---------------------------------------------------------------- 6. volume / OI and the user's threshold

def test_volume_oi_ratio_and_the_highlight_only_with_his_own_threshold():
    none = analytics(VOL)["volume_oi"]
    assert none["threshold"] is None and none["flagged"] == 0
    assert [(r["call_ratio"], r["put_ratio"]) for r in none["rows"]] == [(0.5, 2.5), (None, None), (3.0, 0.5)]
    assert not any(r["call_unusual"] or r["put_unusual"] for r in none["rows"])
    two = analytics(VOL, unusual_threshold=2.0)["volume_oi"]
    assert two["flagged"] == 2 and two["rows"][0]["put_unusual"] and two["rows"][2]["call_unusual"]
    assert analytics(VOL, unusual_threshold=3.0)["volume_oi"]["flagged"] == 0       # above, not at, the threshold
    assert analytics(CURRENT)["volume_oi"]["state"] == "no_data"


# ---------------------------------------------------------------- whole answer

def test_one_failing_section_is_that_section_no_data_and_the_texts_hold_no_advice(monkeypatch, caplog):
    monkeypatch.setattr(A, "an_walls", lambda *a, **k: 1 / 0)
    out = analytics(CURRENT, [SNAP(REF_AT, REFERENCE)], lot_size=50, unusual_threshold=1.0)
    assert out["walls"] == {"state": "no_data", "method": A.AN_WALLS_METHOD, "message": A.UNREADABLE}
    assert "ZeroDivisionError" in caplog.text and out["note"] == "Information only, not advice."
    lines = [v for k in A.AN_KEYS for v in (out[k]["method"], out[k].get("message")) if v]
    lines += [A.AN_NO_LOT, A.AN_NO_SPOT, A.AN_NO_TIME, A.AN_NO_FUTURE, A.AN_NEED_HISTORY, A.AN_HISTORY_OFF]
    for line in lines:
        assert advice_words(line) == [] and not has_advice(line), line


# ---------------------------------------------------------------- the endpoint

def _store(underlying, chain_, at, tenant_id, future=(None, None), day=None):
    async def go():
        async with _session_factory() as session:
            snap = OISnapshotRecord(tenant_id=tenant_id, underlying=underlying, trade_date=day or _ist_today(), slot_start=at, captured_at=at,
                                    expiry=date.fromisoformat(chain_.expiry), underlying_price=chain_.underlying_ltp, strikes=len(chain_.rows),
                                    source="test", future_price=future[0], future_oi=future[1])
            session.add(snap)
            await session.flush()
            for r in chain_.rows:
                session.add(StrikeOISnapshotRecord(snapshot_id=snap.id, strike=r.strike, call_oi=r.call_oi, put_oi=r.put_oi,
                                                   call_ltp=r.call_ltp, put_ltp=r.put_ltp, call_iv=r.call_iv, put_iv=r.put_iv))
            await session.commit()
    run(go())


def _lot(underlying, expiry, lot):
    async def go():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker="upstox", exchange="NFO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:8]}",
                                         tradingsymbol=f"{underlying}X", underlying=underlying, instrument_type="CE",
                                         expiry=date.fromisoformat(expiry), strike=100.0, lot_size=lot))
            await session.commit()
    run(go())


def test_endpoint_serves_the_analytics_with_history_lot_size_and_his_threshold(monkeypatch):
    name = _name()
    expiry = (_ist_today() + timedelta(days=7)).isoformat()
    rows = [r.model_copy(update={"call_volume": 60.0, "put_volume": 6.0}) for r in CURRENT.rows]
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=expiry, underlying_ltp=118.0, rows=rows)}))
    headers, me = _v2_owner("m96")
    _store_credentials(me)
    now = datetime.now(UTC)
    first = OptionChain(underlying=name, expiry=expiry, underlying_ltp=116.0, rows=REFERENCE.rows)
    _store(name, first, now - timedelta(minutes=40), me["tenant_id"], (100.0, 1000.0))
    _store(name, first, now - timedelta(minutes=20), me["tenant_id"], (101.0, 1200.0))
    _lot(name, expiry, 25)
    assert client.put("/api/me/prefs/chain_analytics", headers=headers, json={"unusual_vol_oi": 1.5}).status_code == 200
    body = client.get(URL, headers=headers, params={"symbol": name, "expiry": expiry}).json()
    assert body["state"] == "ok", body
    got = body["analytics"]
    assert set(A.AN_KEYS) <= set(got) and got["snapshots"] == 2
    assert [r["call_class"] for r in got["buildup"]["rows"]] == [
        A.LONG_BUILDUP, A.SHORT_BUILDUP, A.SHORT_COVERING, A.LONG_UNWINDING, A.NO_CLEAR_CHANGE]
    assert got["buildup"]["futures"]["latest"]["label"] == "Long build-up"
    assert got["walls"]["now"]["put_add_strike"] == 120
    assert got["exposure"]["state"] == "ok" and got["exposure"]["lot_size"] == 25
    assert got["volume_oi"]["threshold"] == 1.5 and got["volume_oi"]["flagged"] >= 1     # call 60 / 35 at 120 = 1.71
    assert got["iv"]["rank"]["sessions"] == 0 and got["iv"]["rank"]["enough"] is False


def test_endpoint_without_a_threshold_highlights_nothing_and_no_broker_has_no_analytics(monkeypatch):
    name = _name()
    expiry = (_ist_today() + timedelta(days=7)).isoformat()
    rows = [r.model_copy(update={"call_volume": 600.0}) for r in CURRENT.rows]
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=expiry, underlying_ltp=118.0, rows=rows)}))
    headers, me = _v2_owner("m96b")
    nobroker = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert nobroker["state"] == "no_broker" and "analytics" not in nobroker
    _store_credentials(me)
    got = client.get(URL, headers=headers, params={"symbol": name}).json()["analytics"]
    assert got["volume_oi"]["threshold"] is None and got["volume_oi"]["flagged"] == 0
    assert got["exposure"]["message"] == A.AN_NO_LOT


def test_endpoint_iv_rank_reads_earlier_sessions_closes_only(monkeypatch):
    name = _name()
    expiry = (_ist_today() + timedelta(days=7)).isoformat()
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=expiry, underlying_ltp=100.0, rows=_iv_chain(25.0).rows)}))
    monkeypatch.setattr("app.core.config.OC_IV_RANK_MIN_SESSIONS", 3)
    headers, me = _v2_owner("m96c")
    _store_credentials(me)
    today = _ist_today()
    for i, iv in enumerate((10.0, 20.0, 30.0)):                        # three earlier sessions' last readings
        day = today - timedelta(days=3 - i)
        at = datetime(day.year, day.month, day.day, 9, 55, tzinfo=UTC)
        _store(name, OptionChain(underlying=name, expiry=expiry, underlying_ltp=100.0, rows=_iv_chain(iv).rows), at, me["tenant_id"], day=day)
    rank = client.get(URL, headers=headers, params={"symbol": name, "expiry": expiry}).json()["analytics"]["iv"]["rank"]
    assert rank["sessions"] == 3 and rank["enough"] is True and rank["percentile"] == round(2 / 3 * 100, 1)
    assert rank["rank"] == round((25 - 10) / (30 - 10) * 100, 1)
