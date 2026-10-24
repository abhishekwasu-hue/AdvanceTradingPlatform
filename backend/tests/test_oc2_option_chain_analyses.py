"""OC2 (owner's order, PART C1): the built-in option chain analyses (app/option_chain/analyses.py) and
`GET /api/option-chain/analyses` (app/option_chain/analyses_routes.py, behind `ui_v2`).

Every expected number below is worked out by hand in the comments from small made-up chains (strikes 100-240, an
underlying called DEMO) - no real market data. Covered: support / resistance and its session shift, the four build-up
cases plus zero and missing changes, the PCR windows (overall, ATM +/-5 strikes, the chain's ends, a tie), max pain on
a 5-strike chain, the expected move, the IV smile and skew (quoted in percent, as a fraction, solved from a price), the
sentences against the platform's advice-word lists, and the endpoint: flag off 404, login, plain states, tenancy."""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import OISnapshotRecord, StrikeOISnapshotRecord, UsageRecord
from app.market_data import candles_routes
from app.option_chain import analyses as A
from app.option_chain.greeks import BSInputs, black_scholes
from app.option_chain.models import OptionType
from app.portfolio.risk_text import has_advice
from app.screener.taxonomy import advice_words
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 21, 4, 30, tzinfo=UTC)           # 10:00 IST
REF_AT = datetime(2026, 10, 21, 3, 50, tzinfo=UTC)        # 09:20 IST, the session's first snapshot
EXPIRY = "2026-10-27"                                     # 6 days after NOW (IST)
URL = "/api/option-chain/analyses"
run = asyncio.run


def row(strike, call_oi=None, put_oi=None, call_ltp=None, put_ltp=None, call_iv=None, put_iv=None) -> OptionChainRow:
    return OptionChainRow(strike=strike, call_oi=call_oi, put_oi=put_oi, call_ltp=call_ltp, put_ltp=put_ltp, call_iv=call_iv, put_iv=put_iv)


def chain(rows: List[OptionChainRow], spot: Optional[float], expiry: str = EXPIRY) -> OptionChain:
    return OptionChain(underlying="DEMO", expiry=expiry, underlying_ltp=spot, rows=rows)


# Five strikes, spot 118 (ATM 120).        call OI: 10 20 40 60 30      put OI: 50 40 30 10 5
FIVE = chain([row(100, 10, 50), row(110, 20, 40), row(120, 40, 30), row(130, 60, 10), row(140, 30, 5)], 118.0)

# The session: the 09:20 snapshot and the 10:00 chain. Per strike: (call OI, put OI, call price, put price).
REFERENCE = chain([row(100, 10, 50, 20, 1), row(110, 20, 40, 12, 2), row(120, 40, 30, 6, 5), row(130, 60, 10, 3, 8), row(140, 30, 5, 1, 22)], 116.0)
CURRENT = chain([
    row(100, 15, 50, 22, 1),       # call: price +2, OI +5  -> long build-up    | put: 0, 0     -> no clear change
    row(110, 30, 45, 10, 3),       # call: price -2, OI +10 -> short build-up   | put: +1, +5   -> long build-up
    row(120, 35, 60, 7, 4),        # call: price +1, OI -5  -> short covering   | put: -1, +30  -> short build-up
    row(130, 50, 10, 2, None),     # call: price -1, OI -10 -> long unwinding   | put: no price -> not available
    row(140, 40, 3, 1, 20),        # call: price 0, OI +10  -> no clear change  | put: -2, -2   -> long unwinding
], 118.0)


def texts(result) -> List[str]:
    out = [result["title"], result["method"], *result["sentences"]]
    for t in result["tables"]:
        out.append(t["title"])
        out += [c["label"] for c in t["columns"]]
        out += [str(v) for r in t["rows"] for v in r.values() if isinstance(v, str) and not v[:4].isdigit()]
    if result["chart"]:
        out += [result["chart"]["x_label"], result["chart"]["y_label"]] + [s["label"] for s in result["chart"]["series"]]
    return out


# ---------------------------------------------------------------- the strike window

def test_atm_is_the_nearest_listed_strike_and_a_tie_goes_to_the_lower_one():
    rows = A.sorted_rows(FIVE)
    assert rows[A.atm_index(rows, 118.0)].strike == 120
    assert rows[A.atm_index(rows, 115.0)].strike == 110          # 5 from 110 and from 120: the lower strike
    assert A.atm_index(rows, None) is None and A.near_atm(rows, None) == []


def test_near_atm_window_is_atm_and_five_listed_strikes_each_side_clipped_at_the_ends():
    rows = [row(100 + 10 * i) for i in range(15)]                # 100 .. 240
    assert [r.strike for r in A.near_atm(rows, 171.0)] == [120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220]
    assert [r.strike for r in A.near_atm(rows, 102.0)] == [100, 110, 120, 130, 140, 150]      # ATM is the first strike
    assert [r.strike for r in A.near_atm(rows, 239.0)] == [190, 200, 210, 220, 230, 240]
    uneven = [row(s) for s in (90, 100, 105, 110, 112.5, 115, 120, 130, 150, 200, 300, 400, 500)]
    # ATM 112.5 is the 5th listed strike: 4 below it (all there are) and 5 above, whatever the gaps
    assert [r.strike for r in A.near_atm(uneven, 113.0)] == [90, 100, 105, 110, 112.5, 115, 120, 130, 150, 200]


# ---------------------------------------------------------------- 1. support / resistance

def test_support_resistance_now_from_the_highest_put_and_call_oi():
    out = A.support_resistance(FIVE, [], NOW)
    assert out["state"] == "ok" and out["values"] == {"support": 100, "resistance": 130, "spot": 118.0}
    top = out["tables"][0]["rows"]
    assert [(r["put_strike"], r["put_oi"]) for r in top] == [(100, 50), (110, 40), (120, 30)]
    assert [(r["call_strike"], r["call_oi"]) for r in top] == [(130, 60), (120, 40), (140, 30)]
    assert out["sentences"][0] == "The highest put OI is at 100 (50) and the highest call OI is at 130 (60)."
    # 118 - 100 = 18 above the put strike, 130 - 118 = 12 below the call strike
    assert out["sentences"][1] == "Spot 118.00 lies between them, 18.00 points above the put strike and 12.00 points below the call strike."
    assert out["sentences"][2] == A.NO_HISTORY
    assert len(out["tables"]) == 1


def test_support_resistance_tie_goes_to_the_lower_strike_and_spot_outside_the_pair():
    tie = chain([row(100, 5, 40), row(110, 5, 40), row(120, 7, 1)], 125.0)
    out = A.support_resistance(tie, [], NOW)
    assert out["values"]["support"] == 100 and out["values"]["resistance"] == 120
    assert out["sentences"][1] == "Spot 125.00 is 25.00 points above the highest put OI strike and 5.00 points above the highest call OI strike."
    empty = A.support_resistance(chain([row(100, 0, 0), row(110)], 105.0), [], NOW)
    assert empty["state"] == "no_data" and empty["tables"] == [] and empty["chart"] is None


def test_support_resistance_shift_through_the_session_over_the_strikes_in_every_snapshot():
    # An extra 150 strike with a huge put OI exists only in the first snapshot: the path never compares it.
    first = chain(REFERENCE.rows + [row(150, 1, 999)], 116.0)
    other_expiry = chain([row(100, 1, 5000)], 116.0, expiry="2026-11-03")
    history = [A.Snapshot(REF_AT, first), A.Snapshot(REF_AT + timedelta(minutes=5), other_expiry)]
    out = A.support_resistance(CURRENT, history, NOW)
    path = out["tables"][1]
    assert path["title"] == "Through the session (5 strikes in every snapshot)"
    # 09:20: put OI 50 40 30 10 5 -> 100; call OI 10 20 40 60 30 -> 130.  10:00: put 50 45 60 10 3 -> 120; call 15 30 35 50 40 -> 130
    assert [(r["support"], r["resistance"]) for r in path["rows"]] == [(100, 130), (120, 130)]
    assert [r["time"] for r in path["rows"]] == [REF_AT.isoformat(), NOW.isoformat()]
    assert out["sentences"][2] == ("Among the 5 strikes stored in every snapshot since 09:20 IST, the highest put OI strike moved from 100 to 120 "
                                   "and the highest call OI strike stayed at 130.")


def test_support_resistance_headline_and_session_shift_never_contradict():
    # Review case: the chain now has a 90 strike with the highest put OI, but the collector never stored 90. The headline
    # (whole chain) says 90; the shift (strikes in every snapshot) says 100 -> 120, and names the set it is read over.
    now_chain = chain([row(90, 1, 999)] + CURRENT.rows, 118.0)
    out = A.support_resistance(now_chain, [A.Snapshot(REF_AT, REFERENCE)], NOW)
    assert out["values"]["support"] == 90
    assert out["sentences"][0].startswith("The highest put OI is at 90 (999)")
    assert [r["support"] for r in out["tables"][1]["rows"]] == [100, 120]
    assert out["sentences"][2].startswith("Among the 5 strikes stored in every snapshot since 09:20 IST, the highest put OI strike moved from 100 to 120")
    assert "over every strike of the chain now" in out["method"]


# ---------------------------------------------------------------- 2. build-up

@pytest.mark.parametrize("price, oi, expected", [
    (2.0, 5.0, A.LONG_BUILDUP),          # price up, OI up
    (-2.0, 10.0, A.SHORT_BUILDUP),       # price down, OI up
    (1.0, -5.0, A.SHORT_COVERING),       # price up, OI down
    (-1.0, -10.0, A.LONG_UNWINDING),     # price down, OI down
    (0.0, 10.0, A.NO_CLEAR_CHANGE),      # no price change
    (3.0, 0.0, A.NO_CLEAR_CHANGE),       # no OI change
    (0.0, 0.0, A.NO_CLEAR_CHANGE),
    (None, 5.0, A.NOT_AVAILABLE),
    (1.0, None, A.NOT_AVAILABLE),
])
def test_build_up_classification(price, oi, expected):
    assert A.classify_build_up(price, oi) == expected


def test_build_up_per_strike_since_the_first_snapshot():
    later = chain(CURRENT.rows, 117.0)                        # a later snapshot: the reference stays the FIRST one
    out = A.build_up(CURRENT, [A.Snapshot(REF_AT + timedelta(minutes=20), later), A.Snapshot(REF_AT, REFERENCE)], NOW)
    rows = {r["strike"]: r for r in out["tables"][0]["rows"]}
    assert [rows[k]["call_class"] for k in (100, 110, 120, 130, 140)] == [
        A.LONG_BUILDUP, A.SHORT_BUILDUP, A.SHORT_COVERING, A.LONG_UNWINDING, A.NO_CLEAR_CHANGE]
    assert [rows[k]["put_class"] for k in (100, 110, 120, 130, 140)] == [
        A.NO_CLEAR_CHANGE, A.LONG_BUILDUP, A.SHORT_BUILDUP, A.NOT_AVAILABLE, A.LONG_UNWINDING]
    assert (rows[110]["call_price_change"], rows[110]["call_oi_change"]) == (-2.0, 10.0)
    assert (rows[120]["put_price_change"], rows[120]["put_oi_change"]) == (-1.0, 30.0)
    assert rows[130]["put_price_change"] is None and rows[130]["put_label"] == "Not available"
    assert out["tables"][0]["title"] == "Since 09:20 IST"
    assert out["sentences"] == [
        "Since the first snapshot at 09:20 IST, the call readings at the 5 strikes around the money were long build-up at 1 strike, "
        "short build-up at 1 strike, short covering at 1 strike, long unwinding at 1 strike and no clear change at 1 strike.",
        "The put readings were long build-up at 1 strike, short build-up at 1 strike, long unwinding at 1 strike, no clear change at 1 strike "
        "and not available at 1 strike.",
        # call OI changes +5 +10 -5 -10 +10: the first largest is 110; put OI changes 0 +5 +30 0 -2: 120
        "The largest call OI change was +10 at 110 (short build-up) and the largest put OI change was +30 at 120 (short build-up).",
    ]
    series = {s["key"]: [p["y"] for p in s["points"]] for s in out["chart"]["series"]}
    assert series == {"put_oi_change": [0, 5, 30, 0, -2], "call_oi_change": [5, 10, -5, -10, 10]}


def test_build_up_needs_an_earlier_snapshot_of_the_same_expiry():
    out = A.build_up(CURRENT, [A.Snapshot(REF_AT, chain(REFERENCE.rows, 116.0, expiry="2026-11-03"))], NOW)
    assert out["state"] == "no_data" and out["tables"] == []
    assert "none is stored for this expiry yet" in out["sentences"][0]


# ---------------------------------------------------------------- 3. PCR

def test_pcr_overall_and_near_atm_windows():
    # 15 strikes 100..240, call OI 10 everywhere, put OI 10 inside ATM 170 +/-5 strikes (120..220) and 100 outside.
    rows = [row(s, 10, 10 if 120 <= s <= 220 else 100) for s in range(100, 250, 10)]
    out = A.pcr(chain(rows, 171.0), [], NOW)
    # overall: puts 11 x 10 + 4 x 100 = 510, calls 15 x 10 = 150 -> 3.40; near ATM: 110 / 110 -> 1.00
    assert out["values"]["overall"] == 3.4 and out["values"]["near_atm"] == 1.0
    assert out["values"]["atm"] == 170 and out["values"]["window"] == list(range(120, 230, 10))
    now_rows = out["tables"][0]["rows"]
    assert now_rows[0] == {"scope": "Overall (15 strikes)", "put_oi": 510, "call_oi": 150, "pcr": 3.4}
    assert now_rows[1] == {"scope": "Near ATM: the 11 strikes from 120 to 220 around ATM 170", "put_oi": 110, "call_oi": 110, "pcr": 1.0}
    assert out["sentences"][:2] == [
        "Overall PCR is 3.40: put OI 510 against call OI 150 across 15 strikes.",
        "Near the money (the 11 strikes from 120 to 220 around ATM 170) PCR is 1.00, so put OI and call OI are equal there.",
    ]
    # At the low end: ATM 100, window 100..150 -> puts 100 + 100 + 4 x 10 = 240, calls 60 -> 4.00; the label says the real window
    low = A.pcr(chain(rows, 101.0), [], NOW)
    assert low["values"]["near_atm"] == 4.0
    assert low["tables"][0]["rows"][1]["scope"] == "Near ATM: the 6 strikes from 100 to 150 around ATM 100"
    assert low["sentences"][1] == "Near the money (the 6 strikes from 100 to 150 around ATM 100) PCR is 4.00, so put OI is larger than call OI there."


def test_pcr_path_through_the_session():
    out = A.pcr(CURRENT, [A.Snapshot(REF_AT, REFERENCE)], NOW)
    # 09:20: 135 / 160 = 0.84375; 10:00: (50+45+60+10+3) / (15+30+35+50+40) = 168 / 170 = 0.98824
    assert [p["pcr"] for p in out["tables"][1]["rows"]] == [0.84, 0.99]
    assert [p["atm"] for p in out["tables"][1]["rows"]] == [120, 120]
    assert out["sentences"][2] == "Since 09:20 IST the near-ATM PCR moved from 0.84 to 0.99."
    assert [p["y"] for p in out["chart"]["series"][0]["points"]] == [0.84, 0.99]
    assert A.pcr(chain([row(100, 0, 5)], 100.0), [], NOW)["state"] == "no_data"      # no call OI: no ratio


# ---------------------------------------------------------------- 4. max pain

def test_max_pain_by_hand_on_five_strikes():
    # Value of open options if expiry were at K: calls sum(call OI x max(0, K - s)) + puts sum(put OI x max(0, s - K))
    # K=100: 0 + (40x10 + 30x20 + 10x30 + 5x40 = 1500)                 = 1500
    # K=110: 10x10 = 100 + (30x10 + 10x20 + 5x30 = 650)                 = 750
    # K=120: (10x20 + 20x10 = 400) + (10x10 + 5x20 = 200)               = 600   <- smallest
    # K=130: (10x30 + 20x20 + 40x10 = 1100) + 5x10 = 50                 = 1150
    # K=140: (10x40 + 20x30 + 40x20 + 60x10 = 2400) + 0                 = 2400
    assert A.expiry_values(A.sorted_rows(FIVE)) == [(100, 0, 1500), (110, 100, 650), (120, 400, 200), (130, 1100, 50), (140, 2400, 0)]
    out = A.max_pain(FIVE, [], NOW)
    # distance = 120 - 118 = +2 points = 2 / 118 = 1.6949 % of spot
    assert out["values"] == {"max_pain": 120, "spot": 118.0, "distance": 2.0, "distance_pct": 1.69}
    assert [r["total"] for r in out["tables"][0]["rows"]] == [1500, 750, 600, 1150, 2400]
    assert [r["is_max_pain"] for r in out["tables"][0]["rows"]] == [False, False, True, False, False]
    assert out["sentences"] == [
        "Max pain is 120: an expiry there leaves the smallest total in-the-money value of open options (600 OI x points) among the 5 strikes.",
        "Max pain is 2.00 points (1.69% of spot) above spot 118.00.",
    ]
    from app.option_chain.analysis import compute_max_pain
    assert compute_max_pain(A.sorted_rows(FIVE)) == 120                  # the platform's existing max pain agrees


def test_max_pain_below_spot_and_a_tie_goes_to_the_lower_strike():
    # Symmetric: K=100: put 10x10 = 100; K=110: call 10x10 = 100 -> tie -> 100. Spot 104: 4 points below.
    out = A.max_pain(chain([row(100, 10, 0), row(110, 0, 10)], 104.0), [], NOW)
    assert out["values"]["max_pain"] == 100
    assert out["sentences"][1] == "Max pain is 4.00 points (3.85% of spot) below spot 104.00."
    assert A.max_pain(chain([row(100), row(110)], 104.0), [], NOW)["state"] == "no_data"


# ---------------------------------------------------------------- 5. expected move

def test_expected_move_spot_plus_minus_the_atm_straddle():
    now_chain = chain([row(110, call_ltp=12, put_ltp=3), row(120, call_ltp=6.5, put_ltp=8.0), row(130, call_ltp=2, put_ltp=14)], 118.0)
    first = chain([row(110, call_ltp=9, put_ltp=4), row(120, call_ltp=5.0, put_ltp=10.0), row(130, call_ltp=1, put_ltp=15)], 116.0)
    out = A.expected_move(now_chain, [A.Snapshot(REF_AT, first)], NOW)
    # ATM 120: straddle 6.5 + 8.0 = 14.5; range 118 - 14.5 = 103.5 to 132.5; 14.5 / 118 = 12.288 %; 27 Oct - 21 Oct = 6 days
    assert out["values"] == {"atm": 120, "straddle": 14.5, "lower": 103.5, "upper": 132.5, "move_pct": 12.29, "dte": 6}
    # 09:20: spot 116, ATM 120 (4 away, 110 is 6 away): straddle 5 + 10 = 15 -> 101 to 131
    assert [(r["straddle"], r["lower"], r["upper"]) for r in out["tables"][1]["rows"]] == [(15.0, 101.0, 131.0), (14.5, 103.5, 132.5)]
    assert out["sentences"] == [
        "The ATM straddle at 120 is 14.50 (call 6.50 + put 8.00).",
        "Taken as the move to expiry, it gives a range of 103.50 to 132.50, about 12.29% either side of spot 118.00, with 6 days to expiry.",
        "Since 09:20 IST the straddle moved from 15.00 to 14.50.",
    ]
    assert [s["key"] for s in out["chart"]["series"]] == ["upper", "spot", "lower"]


def test_expected_move_path_starts_at_the_first_snapshot_with_both_prices():
    now_chain = chain([row(120, call_ltp=6.5, put_ltp=8.0)], 118.0)
    missing = chain([row(120, call_ltp=5.0, put_ltp=None)], 116.0)
    later = chain([row(120, call_ltp=5.0, put_ltp=9.0)], 117.0)
    out = A.expected_move(now_chain, [A.Snapshot(REF_AT, missing), A.Snapshot(REF_AT + timedelta(minutes=15), later)], NOW)
    # 09:20 has no put price; 09:35: 5 + 9 = 14 -> now 14.5
    assert [r["straddle"] for r in out["tables"][1]["rows"]] == [None, 14.0, 14.5]
    assert out["sentences"][2] == "Since 09:35 IST the straddle moved from 14.00 to 14.50."
    only_missing = A.expected_move(now_chain, [A.Snapshot(REF_AT, missing)], NOW)
    assert only_missing["sentences"][2] == "No earlier snapshot of this session has both ATM prices, so the straddle's path starts now."


def test_expected_move_needs_both_atm_prices():
    out = A.expected_move(chain([row(120, call_ltp=6.5, put_ltp=None)], 118.0), [], NOW)
    assert out["state"] == "no_data" and "not both available" in out["sentences"][0]
    assert A.expected_move(chain([row(120, call_ltp=6.5, put_ltp=8)], None), [], NOW)["state"] == "no_data"


# ---------------------------------------------------------------- 6. IV smile / skew

PUT_IV = [30, 28, 26, 24, 22, 20, 19, 18, 17, 16, 15]
CALL_IV = [25, 23, 21, 19, 18, 19, 17, 16, 15, 14, 13]
SMILE = chain([row(100 + 10 * i, call_iv=c, put_iv=p) for i, (c, p) in enumerate(zip(CALL_IV, PUT_IV))], 150.0)


def test_iv_skew_by_hand():
    out = A.iv_skew(SMILE, [], NOW)
    pairs = out["tables"][1]["rows"]
    # k=1: put 140 (22) - call 160 (17) = 5; k=2: 24 - 16 = 8; k=3: 26 - 15 = 11; k=4: 28 - 14 = 14; k=5: 30 - 13 = 17
    assert [(p["put_strike"], p["call_strike"], p["difference"]) for p in pairs] == [
        (140, 160, 5.0), (130, 170, 8.0), (120, 180, 11.0), (110, 190, 14.0), (100, 200, 17.0)]
    # skew = 55 / 5 = 11.00; ATM IV = (19 + 20) / 2 = 19.50
    assert out["values"] == {"atm": 150, "atm_iv": 19.5, "skew": 11.0}
    assert out["sentences"] == [
        "ATM IV at 150 is 19.50% (call 19.00%, put 20.00%).",
        "Across 5 strike pairs either side of ATM, out-of-the-money put IV is 11.00 points above out-of-the-money call IV on average.",
        "In this window IV is lowest at 200 (call, 13.00%) and highest at 100 (put, 30.00%).",
    ]
    assert [p["y"] for p in out["chart"]["series"][0]["points"]] == PUT_IV


def test_iv_skew_reads_fractions_flat_skew_and_missing_iv():
    flat = chain([row(100 + 10 * i, call_iv=0.2, put_iv=0.2) for i in range(5)], 120.0)     # fractions: 0.2 -> 20 %
    out = A.iv_skew(flat, [], NOW)
    assert out["values"] == {"atm": 120, "atm_iv": 20.0, "skew": 0.0}
    assert out["sentences"][1] == ("Across 2 strike pairs either side of ATM, out-of-the-money put IV is about the same as "
                                   "out-of-the-money call IV (+0.00 points on average).")
    assert len(out["sentences"]) == 2                                   # one IV everywhere: no lowest / highest line
    assert A.iv_skew(chain([row(100), row(110)], 105.0), [], NOW)["state"] == "no_data"


def test_iv_is_solved_from_the_price_when_none_is_quoted():
    from app.core.config import RISK_FREE_RATE
    from app.options_builder.model import years_left
    t = years_left(date(2026, 10, 27), NOW)
    price = black_scholes(BSInputs(150.0, 150.0, t, RISK_FREE_RATE, 0.25, OptionType.CALL)).theoretical_price
    out = A.iv_skew(chain([row(140), row(150, call_ltp=price), row(160)], 150.0), [], NOW)
    assert out["tables"][0]["rows"][1]["call_iv"] == pytest.approx(25.0, abs=0.01)


# ---------------------------------------------------------------- the sentences: description only

def test_no_analysis_text_holds_advice_or_buy_sell_words():
    histories = [[], [A.Snapshot(REF_AT, REFERENCE)]]
    chains = (FIVE, CURRENT, SMILE, chain([row(100), row(110)], 105.0), chain([], None), chain(CURRENT.rows, 0.0))
    results = [r for h in histories for c in chains for r in A.compute_all(c, h, NOW)]
    results += [r for c in chains for r in A.compute_all(c, [], NOW, history_off=True)]
    assert {r["key"] for r in results} == set(A.KEYS)
    for result in results:
        assert 1 <= len(result["sentences"]) <= 3, result["key"]
        for line in texts(result):
            assert advice_words(line) == [], (result["key"], line)
            assert not has_advice(line), (result["key"], line)
            assert line.isascii(), (result["key"], line)
    from app.option_chain import analyses_routes as R
    for line in (R.NO_BROKER, R.UNAVAILABLE, R.NOT_OFFERED, R.NO_CHAIN, R.NO_HISTORY_BYOB, A.NO_HISTORY, A.HISTORY_OFF, A.BU_HISTORY_OFF,
                 A.UNREADABLE):
        assert advice_words(line) == [] and not has_advice(line), line


@pytest.mark.parametrize("spot", [0.0, -5.0, float("nan"), float("inf")])
def test_a_spot_of_zero_or_not_a_number_is_missing_never_a_failure(spot):
    # Review case: some brokers send 0.0 for "no price"; the expected move divided by it. Now it is a missing spot.
    straddle_rows = [r.model_copy(update={"call_ltp": 7.0, "put_ltp": 4.0}) for r in CURRENT.rows]
    out = {r["key"]: r for r in A.compute_all(chain(straddle_rows, spot), [A.Snapshot(REF_AT, chain(REFERENCE.rows, spot))], NOW)}
    assert [k for k in out] == list(A.KEYS)
    assert out["expected_move"]["state"] == "no_data"
    assert out["build_up"]["state"] == "no_data" and out["iv_skew"]["state"] == "no_data"
    assert out["max_pain"]["state"] == "ok" and out["max_pain"]["values"]["spot"] is None and len(out["max_pain"]["sentences"]) == 1
    assert out["pcr"]["values"]["overall"] == 0.9882 and out["pcr"]["values"]["near_atm"] is None
    assert out["support_resistance"]["sentences"][-1] == A.NO_HISTORY      # the 0-spot snapshot is not part of the session


def test_one_failing_analysis_becomes_that_tab_no_data_logged_by_type_only(monkeypatch, caplog):
    def broken(chain_, history, now, *, history_off=False):
        raise ValueError("secret detail 12345")
    monkeypatch.setattr(A, "ANALYSES", tuple((k, broken if k == "pcr" else fn) for k, fn in A.ANALYSES))
    with caplog.at_level("WARNING", logger="app.option_chain.analyses"):
        out = {r["key"]: r for r in A.compute_all(FIVE, [], NOW)}
    assert out["pcr"]["state"] == "no_data" and out["pcr"]["sentences"] == [A.UNREADABLE] and out["pcr"]["title"] == "PCR"
    assert all(out[k]["state"] == "ok" for k in ("support_resistance", "max_pain"))
    assert "ValueError" in caplog.text and "secret detail" not in caplog.text


def test_history_off_says_so_instead_of_none_stored_yet():
    # The caller passes no snapshots under the own-broker setting; with history_off the tabs give that reason.
    out = {r["key"]: r for r in A.compute_all(CURRENT, [], NOW, history_off=True)}
    assert out["build_up"]["state"] == "no_data" and out["build_up"]["sentences"] == [A.BU_HISTORY_OFF]
    for key in ("support_resistance", "pcr"):
        assert out[key]["sentences"][-1] == A.HISTORY_OFF and A.NO_HISTORY not in out[key]["sentences"]


def test_compute_all_is_six_tabs_in_order_and_never_invents_numbers():
    out = A.compute_all(chain([], None), [], NOW)
    assert [r["key"] for r in out] == list(A.KEYS)
    assert all(r["state"] == "no_data" and r["tables"] == [] and r["chart"] is None for r in out)


# ---------------------------------------------------------------- the endpoint

def _ist_today() -> date:
    return datetime.now(UTC).astimezone(IST).date()


def _name() -> str:
    return "OCTWO" + uuid.uuid4().hex[:6].upper()


class _ChainBroker(_Broker):
    def __init__(self, chains):
        super().__init__()
        self.chains = chains
        self.reads = []

    async def get_option_chain(self, underlying, expiry=None):
        self.reads.append((underlying, expiry))
        value = self.chains.get(underlying)
        if isinstance(value, Exception):
            raise value
        return value


def _install(monkeypatch, broker, used=None):
    def build(record):
        if used is not None:
            used.append(record.tenant_id)
        return broker
    monkeypatch.setattr(candles_routes, "build_adapter", build)


def _store_snapshot(underlying: str, chain_: OptionChain, captured_at: datetime, tenant_id: Optional[int] = None) -> None:
    async def go():
        async with _session_factory() as session:
            snap = OISnapshotRecord(tenant_id=tenant_id, underlying=underlying, trade_date=_ist_today(), slot_start=captured_at, captured_at=captured_at,
                                    expiry=date.fromisoformat(chain_.expiry), underlying_price=chain_.underlying_ltp, strikes=len(chain_.rows),
                                    source="test")
            session.add(snap)
            await session.flush()
            for r in chain_.rows:
                session.add(StrikeOISnapshotRecord(snapshot_id=snap.id, strike=r.strike, call_oi=r.call_oi, put_oi=r.put_oi,
                                                   call_ltp=r.call_ltp, put_ltp=r.put_ltp, call_iv=r.call_iv, put_iv=r.put_iv))
            await session.commit()
    run(go())


def _usage(tenant_id: int) -> float:
    from sqlalchemy import func, select

    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
                UsageRecord.tenant_id == tenant_id, UsageRecord.metric == "market_data_chains"))
    return float(run(go()))


def _v2_owner(tag: str):
    headers, me = _owner(f"oc2-{tag}-{uuid.uuid4().hex[:8]}@example.com")
    _set_flag("ui_v2", me["tenant_id"], on=True)
    return headers, me


def test_flag_off_is_404_and_login_is_required(monkeypatch):
    broker = _ChainBroker({})
    _install(monkeypatch, broker)
    headers, me = _owner(f"oc2-off-{uuid.uuid4().hex[:8]}@example.com")
    _store_credentials(me)
    res = client.get(URL, headers=headers, params={"symbol": "NIFTY"})
    assert res.status_code == 404 and res.headers.get("X-Feature-Disabled") == "ui_v2"
    assert broker.reads == []
    assert client.get(URL, params={"symbol": "NIFTY"}).status_code == 401


def test_no_broker_is_a_plain_state_without_numbers(monkeypatch):
    broker = _ChainBroker({})
    _install(monkeypatch, broker)
    headers, _ = _v2_owner("nobroker")
    body = client.get(URL, headers=headers, params={"symbol": "nifty"}).json()
    assert body["state"] == "no_broker" and body["analyses"] == [] and body["symbol"] == "NIFTY"
    assert "Connect your broker" in body["message"] and broker.reads == []
    assert client.get(URL, headers=headers, params={"symbol": "BAD/NAME"}).status_code == 422


def test_unavailable_and_empty_chains_are_plain_states(monkeypatch):
    from app.option_chain import analyses_routes as R
    broker = _ChainBroker({"FAILS": RuntimeError("upstream"), "NOPE": NotImplementedError("no chain"), "EMPTY": chain([], 100.0)})
    _install(monkeypatch, broker)
    headers, me = _v2_owner("states")
    _store_credentials(me)
    failed = client.get(URL, headers=headers, params={"symbol": "FAILS"}).json()
    assert failed["state"] == "unavailable" and failed["analyses"] == [] and failed["message"] == R.UNAVAILABLE
    nope = client.get(URL, headers=headers, params={"symbol": "NOPE"}).json()
    assert nope["state"] == "unavailable" and nope["message"] == "Your broker's option chain is not available here."
    assert _usage(me["tenant_id"]) == 0                     # no chain came back, nothing is counted
    empty = client.get(URL, headers=headers, params={"symbol": "EMPTY"}).json()
    assert empty["state"] == "no_data" and empty["analyses"] == []
    assert _usage(me["tenant_id"]) == 1                     # an empty chain is a read, counted as POST /option-chains counts it


def test_an_adapter_that_cannot_be_built_is_a_plain_state(monkeypatch):
    def build(record):
        raise ValueError("bad stored credential")
    monkeypatch.setattr(candles_routes, "build_adapter", build)
    headers, me = _v2_owner("adapter")
    _store_credentials(me)
    body = client.get(URL, headers=headers, params={"symbol": "NIFTY"}).json()
    assert body["state"] == "unavailable" and "credential" not in body["message"]


def test_a_spot_of_zero_from_the_broker_is_a_200_with_the_spot_missing(monkeypatch):
    from app.option_chain import as_of
    # B2: while the market is open (closed: our own last close fills a missing spot - tests/test_b2_as_of_spot.py).
    monkeypatch.setattr(as_of, "as_of_for", lambda now, holidays, exchange: as_of.AsOf(now, False))
    name = _name()
    rows = [r.model_copy(update={"call_ltp": 7.0, "put_ltp": 4.0}) for r in FIVE.rows]
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=0.0, rows=rows)}))
    headers, me = _v2_owner("zero")
    _store_credentials(me)
    res = client.get(URL, headers=headers, params={"symbol": name})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "ok" and body["spot"] is None
    states = {a["key"]: a["state"] for a in body["analyses"]}
    assert states["expected_move"] == "no_data" and states["max_pain"] == "ok" and states["pcr"] == "ok"


def test_a_switched_off_page_spends_no_rate_limit_budget(monkeypatch):
    from app.core.rate_limit import reset
    reset("market_data_chains")
    name = _name()
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=118.0, rows=FIVE.rows)}))
    headers, me = _owner(f"oc2-budget-{uuid.uuid4().hex[:8]}@example.com")
    _store_credentials(me)
    for _ in range(35):                                     # the limit is 30 a minute per user
        assert client.get(URL, headers=headers, params={"symbol": name}).status_code == 404
    _set_flag("ui_v2", me["tenant_id"], on=True)
    assert client.get(URL, headers=headers, params={"symbol": name}).status_code == 200


def test_ok_returns_the_six_analyses_with_the_session_history(monkeypatch):
    name = _name()
    expiry = (_ist_today() + timedelta(days=7)).isoformat()
    current = OptionChain(underlying=name, expiry=expiry, underlying_ltp=118.0, rows=CURRENT.rows)
    broker = _ChainBroker({name: current})
    _install(monkeypatch, broker)
    headers, me = _v2_owner("ok")
    _store_credentials(me)
    now = datetime.now(UTC)
    _store_snapshot(name, OptionChain(underlying=name, expiry=expiry, underlying_ltp=116.0, rows=REFERENCE.rows), now - timedelta(minutes=40), me["tenant_id"])
    other = (_ist_today() + timedelta(days=14)).isoformat()      # another expiry: not part of this chain's session
    _store_snapshot(name, OptionChain(underlying=name, expiry=other, underlying_ltp=116.0, rows=[row(100, 1, 9999)]), now - timedelta(minutes=30), me["tenant_id"])
    res = client.get(URL, headers=headers, params={"symbol": name.lower(), "expiry": expiry})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "ok" and body["symbol"] == name and body["expiry"] == expiry and body["spot"] == 118.0
    assert body["session"]["snapshots"] == 1 and body["session"]["note"] is None
    assert body["source"] == {"broker": "upstox", "account_label": "primary"}
    assert broker.reads == [(name, date.fromisoformat(expiry))]
    assert [a["key"] for a in body["analyses"]] == list(A.KEYS)
    by_key = {a["key"]: a for a in body["analyses"]}
    assert by_key["build_up"]["state"] == "ok"
    assert [r["call_class"] for r in by_key["build_up"]["tables"][0]["rows"]] == [
        A.LONG_BUILDUP, A.SHORT_BUILDUP, A.SHORT_COVERING, A.LONG_UNWINDING, A.NO_CLEAR_CHANGE]
    assert [r["pcr"] for r in by_key["pcr"]["tables"][1]["rows"]] == [0.84, 0.99]
    assert by_key["max_pain"]["values"]["max_pain"] == 120
    assert _usage(me["tenant_id"]) == 1


def test_tenancy_each_organisation_reads_through_its_own_broker_only(monkeypatch):
    name = _name()
    broker = _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=118.0, rows=FIVE.rows)})
    used: List[int] = []
    _install(monkeypatch, broker, used)
    a_headers, a = _v2_owner("tenant-a")
    _store_credentials(a)
    b_headers, b = _v2_owner("tenant-b")                       # no broker of its own
    assert client.get(URL, headers=a_headers, params={"symbol": name}).json()["state"] == "ok"
    body = client.get(URL, headers=b_headers, params={"symbol": name}).json()
    assert body["state"] == "no_broker" and body["analyses"] == []
    assert used == [a["tenant_id"]]                             # B never reached A's broker session
    _store_credentials(b)
    assert client.get(URL, headers=b_headers, params={"symbol": name}).json()["state"] == "ok"
    assert used == [a["tenant_id"], b["tenant_id"]]
    assert _usage(a["tenant_id"]) == 1 and _usage(b["tenant_id"]) == 1


def test_a_member_reads_through_the_organisation_session_and_the_flag_is_per_organisation(monkeypatch):
    from tests.test_z3_members import member
    name = _name()
    broker = _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=118.0, rows=FIVE.rows)})
    _install(monkeypatch, broker)
    headers, me = _v2_owner("member")
    _store_credentials(me)
    from tests.test_trading_worker import _upgrade_plan
    _upgrade_plan(me["tenant_id"])
    viewer = member({"headers": headers}, role="VIEWER")
    assert client.get(URL, headers=viewer["headers"], params={"symbol": name}).json()["state"] == "ok"
    other_headers, other = _owner(f"oc2-flagoff-{uuid.uuid4().hex[:8]}@example.com")      # flag on for the first organisation only
    _store_credentials(other)
    assert client.get(URL, headers=other_headers, params={"symbol": name}).status_code == 404


def test_own_broker_tier_reads_no_shared_history(monkeypatch):
    name = _name()
    broker = _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=118.0, rows=CURRENT.rows)})
    used: List[int] = []
    _install(monkeypatch, broker, used)
    headers, me = _v2_owner("byob")
    _set_flag("byob_data_tier", me["tenant_id"], on=True)
    try:
        refused = client.get(URL, headers=headers, params={"symbol": name}).json()
        assert refused["state"] == "no_broker" and refused["analyses"] == [] and broker.reads == []
        assert refused["message"]
        _store_credentials(me)                                   # the user's own session at a broker
        _store_snapshot(name, OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=116.0, rows=REFERENCE.rows),
                        datetime.now(UTC) - timedelta(minutes=10), me["tenant_id"])
        body = client.get(URL, headers=headers, params={"symbol": name}).json()
        assert body["state"] == "ok" and used == [me["tenant_id"]]
        assert body["session"]["snapshots"] == 0 and "own-broker" in body["session"]["note"]
        build = {a["key"]: a for a in body["analyses"]}["build_up"]
        assert build["state"] == "no_data" and build["sentences"] == [A.BU_HISTORY_OFF]       # the shared collector is not read
    finally:
        _set_flag("byob_data_tier", me["tenant_id"], on=False)
