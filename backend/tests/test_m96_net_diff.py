"""M96 L1 (owner: the OI difference, its signal and the banner taken from the Trade repo AS IS): the named cases of the
Trade "Net Diff" port (app/option_chain/net_diff.py), the Trade banner's message in neutral fact words
(app/option_chain/oi_words.trade_fact_message) and the /live wiring. Every chain is hand-made; each expected number is
worked out by hand here. The parity run against Trade's own code is tests/test_m96_trade_golden.py.
"""
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import net_diff, oi_regime, oi_words, snapshots
from app.option_chain.oi_regime import ChainTotals, Direction, OIClass, OISignal, SnapshotPoint, Strength
from tests.test_auth_api import client
from tests.test_oc1_option_chain_view import _setup_routes, _v2_owner, memory_cache  # noqa: F401 - the fixture
from tests.test_phase_aa_market_data_api import _store_credentials


def _row(strike, ce, pe, ce_ltp=10.0, pe_ltp=10.0):
    return OptionChainRow(strike=strike, call_oi=ce, put_oi=pe, call_ltp=ce_ltp, put_ltp=pe_ltp)


def _ladder(lo, hi, step, ce=1000, pe=1000):
    return [_row(float(k), ce, pe) for k in range(lo, hi + 1, step)]


# ------------------------------------------------------------------------------------------------ step, ATM, window

def test_step_per_symbol_as_trade_else_the_chain_spacing():
    assert net_diff.trade_step("NIFTY", [24000, 24050]) == 50
    assert net_diff.trade_step("banknifty", [52000, 52050]) == 100           # Trade's 100, whatever the listed gaps
    assert net_diff.trade_step("SENSEX", [80000, 80100]) == 100
    assert net_diff.trade_step("RELIANCE", [1200, 1210, 1220, 1240]) == 10   # not in Trade: the chain's own spacing
    assert net_diff.trade_step("RELIANCE", [1200]) is None


def test_atm_is_python_round_of_spot_over_step():
    assert net_diff.trade_atm(24_524.9, 50) == 24_500
    assert net_diff.trade_atm(24_525.0, 50) == 24_500         # 490.5 rounds half to even (Python's round, as Trade)
    assert net_diff.trade_atm(24_575.0, 50) == 24_600         # 491.5 -> 492
    assert net_diff.trade_atm(None, 50) is None and net_diff.trade_atm(0, 50) is None


def test_window_is_atm_plus_minus_six_steps_inclusive():
    rows = _ladder(24_000, 25_000, 50)
    shown = [r.strike for r in net_diff.window(rows, 24_500, 50)]
    assert shown == [24_200 + 50 * i for i in range(13)]      # 24200 .. 24800: 13 strikes
    bank = [r.strike for r in net_diff.window(_ladder(51_000, 54_000, 100), 52_500, 100)]
    assert bank[0] == 51_900 and bank[-1] == 53_100 and len(bank) == 13


# ------------------------------------------------------------------------------------------------ baseline and table

def test_baseline_is_the_first_reading_and_new_strikes_join_when_first_seen():
    base: net_diff.Baseline = {}
    first = [_row(100, 500, 800), _row(150, 300, 200)]
    assert net_diff.update_baseline(base, first) is True
    assert base == {100.0: (500, 800), 150.0: (300, 200)}
    later = [_row(100, 900, 700), _row(150, 350, 260), _row(200, 50, 10)]
    assert net_diff.update_baseline(base, later) is True                     # 200 is new; 100 / 150 keep the first
    assert base == {100.0: (500, 800), 150.0: (300, 200), 200.0: (50, 10)}
    assert net_diff.update_baseline(base, later) is False


def test_a_zero_call_baseline_is_replaced_by_the_first_reading_with_call_oi():
    base: net_diff.Baseline = {}
    net_diff.update_baseline(base, [_row(100, 0, 400)])
    net_diff.update_baseline(base, [_row(100, 0, 450)])                      # still no call OI: kept
    assert base[100.0] == (0, 400)
    net_diff.update_baseline(base, [_row(100, 120, 470)])                    # Trade replaces BOTH sides
    assert base[100.0] == (120, 470)
    net_diff.update_baseline(base, [_row(100, 0, 500)])                      # a later 0 changes nothing
    assert base[100.0] == (120, 470)


def test_net_diff_dominance_and_the_total_row_by_hand():
    base: net_diff.Baseline = {100.0: (1000, 1000), 150.0: (2000, 500), 200.0: (300, 300)}
    rows = [_row(100, 1500, 1200, 12.5, None), _row(150, 1800, 900), _row(200, 300.9, 300)]
    t = net_diff.table(rows, 150, 50, base)
    got = [(r.strike, r.ce_chg, r.pe_chg, r.diff, r.dominance) for r in t.rows]
    # 100: CE +500, PE +200 -> 200 - 500 = -300 Bears; 150: CE -200, PE +400 -> +600 Bulls; 200: int(300.9) = 300 -> 0
    assert got == [(100, 500, 200, -300, "Bears"), (150, -200, 400, 600, "Bulls"), (200, 0, 0, 0, "Neutral")]
    assert t.total == {"ce_chg": 300, "ce_oi": 3600, "pe_oi": 2400, "pe_chg": 600, "diff": 300}
    assert t.pcr == round(2400 / 3600, 2) == 0.67
    assert t.rows[0].ce_ltp == 12.5 and t.rows[0].pe_ltp is None             # a missing LTP stays missing
    j = net_diff.table_json(t)
    assert j["window"] == 6 and j["atm"] == 150 and j["rows"][1]["dominance"] == "Bulls" and j["total"]["diff"] == 300


def test_pcr_is_none_without_call_oi():
    t = net_diff.table([_row(100, 0, 50)], 100, 50, {100.0: (0, 50)})
    assert t.pcr is None


def test_cached_baseline_round_trips_and_bad_text_is_ignored():
    base = {100.0: (5, 6), 150.0: (7, 8)}
    text = json.dumps(net_diff.baseline_json(base, "2026-10-21T09:20:00+05:30"))
    assert net_diff.baseline_from_text(text) == ("2026-10-21T09:20:00+05:30", base)
    for bad in (None, "", "{", "[]", '{"since": 1}', '{"since": "x", "oi": [[1, 2]]}'):
        assert net_diff.baseline_from_text(bad) is None


# ------------------------------------------------------------------------------------------------ the banner message

def _state(put, call, *, direction=Direction.NEUTRAL, reason=None, stable=OISignal(Direction.NEUTRAL), diff=0.0):
    return oi_regime.BannerState(underlying="SENSEX", direction=direction, message="", put_class=put, call_class=call, stable_signal=stable,
                                 diff=diff, delta_diff=0.0, pcr=None, pcr_band=None, pcr_label="", atm_strike=0, underlying_price=0,
                                 max_pain=None, expiry=None, dte=None, first_of_day=False, mixed_reason=reason)


def test_fact_message_keeps_trades_meaning_in_neutral_words_with_the_symbol_name():
    W, SC, LU, F = OIClass.WRITING, OIClass.SHORT_COVERING, OIClass.LONG_UNWINDING, OIClass.FLAT
    assert oi_words.trade_fact_message(_state(W, SC), "SENSEX") == (
        "Put writing rising + Call short covering (resistance weakening) → SENSEX OI readings lean up")
    assert oi_words.trade_fact_message(_state(SC, W), "SENSEX") == (
        "Put short covering (support weakening) + Call writing rising → SENSEX OI readings lean down")
    assert oi_words.trade_fact_message(_state(LU, F), "nifty") == "Put unwinding (bears stepping back) → NIFTY OI readings lean up"
    assert oi_words.trade_fact_message(_state(F, LU), "NIFTY") == "Call unwinding (bulls stepping back) → NIFTY OI readings lean down"
    assert oi_words.trade_fact_message(_state(W, W), "NIFTY") == "Mixed readings: no clear direction"
    assert oi_words.trade_fact_message(_state(OIClass.INSUFFICIENT_DATA, OIClass.INSUFFICIENT_DATA), "NIFTY") == "No clear movement"
    stable = oi_words.trade_fact_message(_state(W, F, reason="stable", stable=OISignal(Direction.BEARISH, Strength.STRONG)), "NIFTY")
    assert stable == "Mixed readings: the recent momentum (Put writing rising) and the confirmed trend (down) disagree"
    level = oi_words.trade_fact_message(_state(F, W, reason="level", diff=1_234_567), "NIFTY")
    assert level == "Mixed readings: the recent momentum (Call writing rising) and the overall level (Diff: Put OI higher, +12.3L) disagree"
    for text in (stable, level):
        assert not any(w in text.lower() for w in ("short call", "short put", "cautious", "bullish", "bearish"))


def _totals(call, put, call_prem=100.0, put_prem=100.0):
    return ChainTotals(underlying_price=24_500, atm_strike=24_500, strike_step=50, total_call_oi=call, total_put_oi=put,
                       total_call_premium=call_prem, total_put_premium=put_prem, strikes=())


def test_evaluate_snapshot_records_why_a_reading_turned_mixed():
    s = oi_regime.OIRegimeSettings()
    prev = _totals(10_000, 10_000)
    # Put writing (put OI +5 %, premium -5 %) against a confirmed BEARISH trend: mixed by the stable check.
    hist = [SnapshotPoint(-2000, 9000, 11_000, OISignal(Direction.BEARISH, Strength.STRONG))]
    st = oi_regime.evaluate_snapshot("NIFTY", _totals(10_000, 10_500, put_prem=95.0), prev, hist, s)
    assert st.direction == Direction.MIXED and st.mixed_reason == "stable" and st.momentum == "Put writing rising"
    # Call writing while put OI leads by more than 10 % of the total: mixed by the level check.
    # (the hysteresis still holds the earlier BEARISH, so the stable check passes and the level check decides)
    st = oi_regime.evaluate_snapshot("NIFTY", _totals(10_500, 14_000, call_prem=95.0), prev, hist, s)
    assert st.direction == Direction.MIXED and st.mixed_reason == "level" and st.stable_signal.direction == Direction.BEARISH
    at = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    row = snapshots.entry_json(snapshots.TimelineEntry(at, at, st, _totals(10_500, 14_000)))
    assert row["fact_message"].startswith("Mixed readings: the recent momentum (Call writing rising) and the overall level")
    assert "+0.0L" in row["fact_message"]                                       # Trade's lakhs, one decimal (3,500 = 0.035 L)


# ------------------------------------------------------------------------------------------------ /live

def _nifty(ce_at, pe_at, spot=24_510.0):
    rows = [OptionChainRow(strike=float(k), call_oi=ce_at(k), put_oi=pe_at(k), call_ltp=5.0, put_ltp=5.0)
            for k in range(24_000, 25_001, 50)]
    return OptionChain(underlying="NIFTY", expiry="2026-10-27", underlying_ltp=spot, rows=rows)


def test_live_carries_trades_net_diff_building_then_ready(monkeypatch, memory_cache):  # noqa: F811 - the fixture
    headers, me = _v2_owner("m96-netdiff@example.com")
    _store_credentials(me)
    broker = _setup_routes(monkeypatch, _nifty(lambda k: 1000, lambda k: 2000))
    body = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()
    nd = body["net_diff"]
    assert nd["status"] == "building" and nd["atm"] == 24_500 and nd["step"] == 50 and len(nd["rows"]) == 13
    assert all(r["ce_chg"] == 0 and r["pe_chg"] == 0 and r["dominance"] == "Neutral" for r in nd["rows"])
    assert nd["pcr"] == 2.0 and body["summary"]["pcr_oi_atm"] == 2.0
    # The next read: 24500's put OI +300, its call OI +100; every other strike unchanged.
    key = next(k for k in memory_cache.store if k.startswith("m96:net_diff:"))
    held = json.loads(memory_cache.store[key])
    held["since"] = (datetime.fromisoformat(held["since"]) - timedelta(minutes=5)).isoformat()
    memory_cache.store[key] = json.dumps(held)
    broker.chain = _nifty(lambda k: 1100 if k == 24_500 else 1000, lambda k: 2300 if k == 24_500 else 2000)
    nd = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()["net_diff"]
    atm = next(r for r in nd["rows"] if r["strike"] == 24_500)
    assert nd["status"] == "ready" and (atm["ce_chg"], atm["pe_chg"], atm["diff"], atm["dominance"]) == (100, 300, 200, "Bulls")
    assert nd["total"] == {"ce_chg": 100, "ce_oi": 13_100, "pe_oi": 26_300, "pe_chg": 300, "diff": 200}


def test_live_without_a_spot_has_no_net_diff(monkeypatch, memory_cache):  # noqa: F811 - the fixture
    headers, me = _v2_owner("m96-netdiff-nospot@example.com")
    _store_credentials(me)
    _setup_routes(monkeypatch, _nifty(lambda k: 1000, lambda k: 2000, spot=None))
    body = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()
    # The quote's spot (114 in the shared harness) sits far from this ladder: the window is empty, nothing is made up.
    assert body["net_diff"]["rows"] == [] and body["net_diff"]["pcr"] is None


@pytest.mark.parametrize("pcr, band", [(0.69, "OVERSOLD"), (0.70, "MILD_BEARISH"), (0.89, "MILD_BEARISH"), (0.90, "SIDEWAYS"),
                                       (1.0, "SIDEWAYS"), (1.01, "MILD_BULLISH"), (1.3, "MILD_BULLISH"), (1.31, "OVERBOUGHT")])
def test_pcr_zone_edges_are_trades(pcr, band):
    assert oi_regime.pcr_band(pcr).value == band


def test_dte_is_calendar_days_as_trade():
    assert oi_regime.compute_dte("2026-11-13", date(2026, 11, 11)) == (date(2026, 11, 13), 2)
    assert oi_regime.compute_dte("2026-11-16", date(2026, 11, 13))[1] == 3     # Friday to Monday: 3 calendar days
