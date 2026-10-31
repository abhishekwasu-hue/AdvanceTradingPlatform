"""M61 PCR-1 / PCR-2 / PCR-4 (owner, 30 Oct): only readings taken between the open and the close of a trading day are
session rows; pre-open / after-close readings are kept but labelled and left out of the headline; a reading with fewer
strikes than the session's usual count is flagged partial and left out of the headline; one path point per change, not
per poll. Plus the OI figure format (OI-1) and the no-verdict rule for every PCR text. Made-up chains only."""
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import analyses as A
from app.option_chain import session_rows as SR

IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY = "2026-10-27"
STRIKES = list(range(100, 250, 10))           # 15 strikes; spot 171 -> ATM 170, near ATM 120..220


def at(hh: int, mm: int, day: date = date(2026, 10, 21)) -> datetime:      # 21 Oct 2026 is a Wednesday
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).astimezone(UTC)


def chain(put_oi: float = 10, call_oi: float = 10, spot: float = 171.0, missing: tuple = ()) -> OptionChain:
    rows = [OptionChainRow(strike=s, call_oi=None if s in missing else call_oi, put_oi=None if s in missing else put_oi) for s in STRIKES]
    return OptionChain(underlying="DEMO", expiry=EXPIRY, underlying_ltp=spot, rows=rows)


# ---------------------------------------------------------------- PCR-1: the session phase (calendar-aware)

@pytest.mark.parametrize("hh,mm,expected", [(6, 45, SR.PRE_OPEN), (9, 14, SR.PRE_OPEN), (9, 15, SR.SESSION), (12, 0, SR.SESSION),
                                            (15, 30, SR.SESSION), (15, 31, SR.AFTER_CLOSE), (22, 35, SR.AFTER_CLOSE)])
def test_phase_by_the_exchange_clock(hh, mm, expected):
    assert SR.phase(at(hh, mm)) == expected


def test_phase_on_a_weekend_or_holiday_is_market_closed():
    assert SR.phase(at(11, 0, date(2026, 10, 24))) == SR.CLOSED_DAY                      # Saturday
    holiday = date(2026, 10, 21)
    assert SR.phase(at(11, 0), holidays={holiday}) == SR.CLOSED_DAY
    assert SR.phase(at(11, 0), holidays={date(2026, 10, 22)}) == SR.SESSION
    assert SR.phase(at(3, 0), exchange="CRYPTO") == SR.SESSION                           # a 24x7 venue has no outside


# ---------------------------------------------------------------- PCR-1: strike count and partial

def test_strike_count_counts_strikes_with_oi_on_both_sides():
    assert SR.strike_count(chain()) == 15
    assert SR.strike_count(chain(missing=(100, 110, 120))) == 12
    assert SR.strike_count(chain(put_oi=0, call_oi=0)) == 0


def test_partial_is_below_the_ratio_of_the_earlier_session_median():
    s, p = SR.SESSION, SR.PRE_OPEN
    # 15, 15, 12 (< 0.9 x 15 = 13.5 -> partial), 14 (median of 15, 15, 12 = 15 -> 14 >= 13.5, full); the pre-open 5 is never judged
    assert SR.partial_flags([5, 15, 15, 12, 14], [p, s, s, s, s], 0.9) == [False, False, False, True, False]
    assert SR.partial_flags([10], [s], 0.9) == [False]                                       # the first session row: no median yet
    assert SR.partial_flags([15, None], [s, s], 0.9) == [False, False]


# ---------------------------------------------------------------- the PCR tab

def test_pre_open_rows_are_labelled_and_left_out_of_the_headline():
    history = [A.Snapshot(at(6, 45), chain(put_oi=30)), A.Snapshot(at(6, 50), chain(put_oi=31)),     # yesterday's chain before the open
               A.Snapshot(at(9, 20), chain(put_oi=10))]
    out = A.pcr(chain(put_oi=12), history, at(10, 0))
    path = out["tables"][1]["rows"]
    assert [r["phase"] for r in path] == [SR.PRE_OPEN, SR.PRE_OPEN, SR.SESSION, SR.SESSION]
    assert [r["note"] for r in path] == ["Pre-open", "Pre-open", "", ""]
    assert out["sentences"][1] == "Since 09:20 IST the near-ATM PCR moved from 1.00 to 1.20."          # not from 3.00
    assert out["sentences"][2] == "Left out of the comparison: 2 readings outside the session."
    assert [p["phase"] for p in out["chart"]["series"][0]["points"]] == [SR.PRE_OPEN, SR.PRE_OPEN, SR.SESSION, SR.SESSION]


def test_a_partial_snapshot_is_flagged_drawn_hollow_and_left_out_of_the_headline():
    history = [A.Snapshot(at(9, 20), chain(put_oi=10)), A.Snapshot(at(9, 25), chain(put_oi=11)),
               A.Snapshot(at(11, 50), chain(put_oi=11, missing=(160, 170, 180))),                     # 12 of 15 strikes
               A.Snapshot(at(11, 55), chain(put_oi=12))]
    out = A.pcr(chain(put_oi=13), history, at(12, 0))
    path = out["tables"][1]["rows"]
    assert [r["partial"] for r in path] == [False, False, True, False, False]
    assert path[2]["note"] == "Partial (12 strikes)" and path[2]["strikes"] == 12
    assert [p["hollow"] for p in out["chart"]["series"][0]["points"]] == [False, False, True, False, False]
    assert out["sentences"][1] == "Since 09:20 IST the near-ATM PCR moved from 1.00 to 1.30."
    assert out["sentences"][2] == "Left out of the comparison: 1 partial reading."


def test_one_point_per_change_not_per_poll():
    same = [A.Snapshot(at(9, 20), chain(put_oi=10)), A.Snapshot(at(9, 25), chain(put_oi=10)), A.Snapshot(at(9, 30), chain(put_oi=10)),
            A.Snapshot(at(9, 35), chain(put_oi=11)), A.Snapshot(at(9, 40), chain(put_oi=11))]
    out = A.pcr(chain(put_oi=11), same, at(15, 45))                  # after the close the reading repeats the last one
    times = [r["time"] for r in out["tables"][1]["rows"]]
    assert times == [at(9, 20).isoformat(), at(9, 35).isoformat()]
    assert len(out["chart"]["series"][0]["points"]) == 2


def test_an_atm_change_is_marked_on_the_chart():
    history = [A.Snapshot(at(9, 20), chain(spot=171.0)), A.Snapshot(at(9, 25), chain(spot=181.0))]
    out = A.pcr(chain(spot=181.0, put_oi=12), history, at(9, 30))
    assert out["chart"]["x_marks"] == [{"x": at(9, 25).isoformat(), "label": "ATM 180"}]
    point = out["chart"]["series"][0]["points"][0]
    assert point["put_oi"] == 110 and point["call_oi"] == 110 and point["atm"] == 170      # hover: put / call OI near ATM


# ---------------------------------------------------------------- OI-1 in sentences, and no verdict words

@pytest.mark.parametrize("value,text", [(0, "0"), (99_999, "99,999"), (100_000, "1 L"), (1_235_000, "12.35 L"), (9_999_999, "99.99 L"),
                                        (10_000_000, "1 Cr"), (12_400_000, "1.24 Cr"), (230_440_340, "23.04 Cr"), (-2_160_470, "-21.6 L")])
def test_fmt_count_boundaries(value, text):
    assert A.fmt_count(value) == text


BANNED = ("buy", "sell", "bullish", "bearish", "overbought", "oversold", "target", "stop-loss")


def test_no_verdict_words_in_any_pcr_text():
    history = [A.Snapshot(at(6, 45), chain(put_oi=30)), A.Snapshot(at(9, 20), chain(put_oi=10)),
               A.Snapshot(at(11, 50), chain(missing=(160, 170, 180)))]
    for put in (5, 10, 30):
        out = A.pcr(chain(put_oi=put), history, at(12, 0))
        words = " ".join([out["title"], out["method"], *out["sentences"], *(t.get("help", "") for t in out["tables"]),
                          *(str(v) for t in out["tables"] for r in t["rows"] for v in r.values() if isinstance(v, str))]).lower()
        for w in BANNED:
            assert w not in words, w


def test_oi_history_slots_carry_phase_strike_count_and_partial():
    from app.option_chain.snapshots import SlotChain, session_marks
    slots = [SlotChain(at(6, 45), at(6, 45), chain()), SlotChain(at(9, 20), at(9, 20), chain()),
             SlotChain(at(9, 25), at(9, 25), chain(missing=(160, 170, 180))), SlotChain(at(15, 35), at(15, 35), chain())]
    marks = session_marks(slots, ratio=0.9)
    assert [marks[s.slot_start]["phase"] for s in slots] == [SR.PRE_OPEN, SR.SESSION, SR.SESSION, SR.AFTER_CLOSE]
    assert [marks[s.slot_start]["partial"] for s in slots] == [False, False, True, False]
    assert marks[slots[2].slot_start]["strikes"] == 12
