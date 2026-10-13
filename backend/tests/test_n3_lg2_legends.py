"""U7 LG2 (docs/specs/ATP_LEGENDS_SCREENS_ADDENDUM.md §6 "LG2 remaining trend / pullback / options"): five more Legends
cards behind their own flag `screener_legends_lg2` (off by default, on top of `screener_legends`).

* the content lint of LG1 holds for them (tests/test_z8_lg1_legends.py runs it over the whole library); here: the slice,
  the bibliographic check of the new sources (Livermore's 1940 first edition is unverified, with the reason), no
  branded method name in a title (Turtle Soup), no advice wording through the API;
* each screen matches a positive bar fixture and not a negative one (`run_screen`, the screener's own path);
* the flag: off -> the LG2 cards are left out of GET /api/screener/legends, one LG2 card is 404 and the taxonomy hides
  the LG2 screens (and their counts); on -> they are listed with their disclaimer. LG2 alone (Legends off) shows nothing.
* no new ScreenQL function (plain registry functions only), so no new Z10-a causal case.
Synthetic bars only. Educational only - nothing here ranks, recommends or places anything.
"""
from typing import Callable, Dict, List

import numpy as np
import pandas as pd
import pytest

from app.platform import controls
from app.screener import compile_screen, nodes, parse
from app.screener import legends as lg
from app.screener import taxonomy as tx
from app.screener.registry import FUNCTIONS
from app.screener.runtime import SymbolData, run_screen
from tests.test_auth_api import client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z8_tx1_taxonomy import _set_flags

LG2_CARDS = ["legends.livermore_pivotal_breakout", "legends.wyckoff_range_spring", "legends.raschke_false_breakdown",
             "legends.crabel_opening_range_breakout", "legends.tudor_jones_200_day_regime"]


def test_the_lg2_slice_is_in_the_library_and_passes_lint():
    assert lg.lint() == [] and lg.link_problems() == []
    lib = lg.load()
    assert [c.id for c in lib.of(("lg2",))] == LG2_CARDS
    assert all(c.slice == "lg1" for c in lib.cards if c.id not in LG2_CARDS)
    for card in lib.of(("lg2",)):
        assert card.name.startswith(f"{card.short}-style ") and not lg._TRADEMARK.findall(card.name), card.name
    assert "Turtle Soup" in lg.TRADEMARK_TERMS                                            # the Raschke title stays descriptive
    assert lg.FLAG_LG2 in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[lg.FLAG_LG2]


def test_lg2_sources_checked_against_public_records():
    """Each new source lists its public record URLs; Livermore's 1940 edition is matched only to dealer listings and the
    name authority (no catalogue record could be opened), so it and its card stay unverified, with the reason."""
    lib = lg.load()
    new = {"livermore_1940", "pruden_2007", "robbins_2014"}
    assert new <= set(lib.sources)
    for ident in new:
        s = lib.sources[ident]
        assert s.kind == "book" and len(s.evidence) >= 2 and s.publisher and s.review_note, ident
    assert {i: lib.sources[i].review for i in new} == {"livermore_1940": "unverified", "pruden_2007": "verified", "robbins_2014": "verified"}
    assert "catalogue record" in lib.sources["livermore_1940"].review_note
    reviews = {c.id: c.review for c in lib.of(("lg2",))}
    assert {i for i, r in reviews.items() if r == "unverified"} == {"legends.livermore_pivotal_breakout"}
    assert "false 20-day breakout reversal" in lib.sources["raschke_connors_1995"].covers   # the source says what the new card rests on


def test_no_new_screenql_function():
    for card in lg.load().of(("lg2",)):
        calls = {node.name for node in nodes.walk(parse(card.source)) if isinstance(node, nodes.Call)}
        assert calls <= set(FUNCTIONS) and not any(FUNCTIONS[c].flag for c in calls), (card.id, calls)   # no flagged family either


# --- fixtures --------------------------------------------------------------------------------------------------------
def _frame(close, *, high=None, low=None, vol=None, start="2024-01-01", freq="1D"):
    c = np.asarray(close, dtype=float)
    idx = pd.date_range(start, periods=len(c), freq=freq, tz="UTC")
    return pd.DataFrame({"open": c - 0.2, "high": np.asarray(high if high is not None else c + 0.5, dtype=float),
                         "low": np.asarray(low if low is not None else c - 0.5, dtype=float), "close": c,
                         "volume": np.asarray(vol if vol is not None else [1000.0] * len(c), dtype=float)}, index=idx)


def _livermore():
    trend = list(np.linspace(120.0, 195.0, 250))
    base = [200.0 + (i % 5) * 2.0 for i in range(60)]                          # 60 bars in a 199.5-208.5 base
    close = trend + base + [215.0]
    vol = [1000.0] * (len(close) - 1)
    return [_frame(close, vol=vol + [3000.0])], [_frame(close, vol=vol + [1100.0])]


def _wyckoff():
    rng = [101.0 + (i % 9) for i in range(60)]                                 # range: lows 100.5, highs 109.5
    spring = _frame(rng + [101.5], low=[c - 0.5 for c in rng] + [99.0])         # under the range low, closes back inside
    breakdown = _frame(rng + [100.0], low=[c - 0.5 for c in rng] + [99.0])      # stays below it
    return [spring], [breakdown]


def _raschke():
    def lows(age):
        out = [100.0 + (i % 4) for i in range(30)]
        out[-age] = 95.0                                                       # the previous 20-day low, `age` sessions before today
        return out
    def frame(age):
        low = lows(age) + [94.0]
        high = [lw + 2.0 for lw in low[:-1]] + [96.0]                          # today: a new low, then back above 95
        return _frame([lw + 1.0 for lw in low[:-1]] + [95.5], high=high, low=low)
    return [frame(5)], [frame(2)]                                              # four or more sessions old / only two


def _crabel():
    start = "2024-01-02 03:45"                                                 # 09:15 IST
    def day(last_close):
        n = 40
        close = [100.0] * 6 + [100.5] * (n - 7) + [last_close]
        high = [101.0] * 6 + [100.8] * (n - 7) + [last_close + 0.2]
        low = [99.0] * 6 + [100.2] * (n - 7) + [last_close - 0.3]
        return _frame(close, high=high, low=low, start=start, freq="5min")
    return [day(101.5)], [day(100.9)]                                          # the 30-minute range high is 101


def _tudor_jones():
    up = list(100 * 1.002 ** np.arange(260))
    return [_frame(up)], [_frame(up[::-1])]


FIXTURES: Dict[str, Callable[[], tuple]] = {
    "legends.livermore_pivotal_breakout": _livermore, "legends.wyckoff_range_spring": _wyckoff,
    "legends.raschke_false_breakdown": _raschke, "legends.crabel_opening_range_breakout": _crabel,
    "legends.tudor_jones_200_day_regime": _tudor_jones,
}


def _run_card(card, frames: List[pd.DataFrame]):
    ast, validated = compile_screen(card.source, base_tf=card.base_tf, params=card.defaults())
    assert validated.ok, validated.problems
    universe = [SymbolData(f"S{i}", {card.base_tf: f}) for i, f in enumerate(frames)]
    return run_screen(ast, validated, universe, base_tf=card.base_tf, params=card.defaults())


def test_every_lg2_card_has_a_fixture():
    assert sorted(FIXTURES) == sorted(LG2_CARDS)


@pytest.mark.parametrize("card_id", LG2_CARDS)
def test_each_lg2_screen_matches_its_positive_fixture_and_not_its_negative(card_id):
    card = lg.load().card(card_id)
    positive, negative = FIXTURES[card_id]()
    hit = _run_card(card, positive)[0]
    assert hit.reason is None and hit.matched, (card_id, hit)
    miss = _run_card(card, negative)[0]
    assert miss.reason is None and not miss.matched, (card_id, miss)


# --- the flag --------------------------------------------------------------------------------------------------------
@pytest.fixture
def legends_on():
    _set_flags(screener_v2=True, screener_legends=True, screener_gallery=True)
    yield
    _set_flags(screener_v2=False, screener_legends=False, screener_gallery=False, screener_legends_lg2=False)


def test_lg2_cards_and_screens_stay_hidden_while_their_flag_is_off(legends_on):
    headers, _ = _owner("lg2-off@example.com")
    body = client.get("/api/screener/legends", headers=headers).json()
    assert not set(LG2_CARDS) & {c["id"] for c in body["cards"]} and len(body["cards"]) == 8
    assert client.get(f"/api/screener/legends/{LG2_CARDS[0]}", headers=headers).status_code == 404
    tax = client.get("/api/screener/taxonomy?method=legends", headers=headers).json()
    assert not set(LG2_CARDS) & {s["id"] for s in tax["screens"]} and sum(tax["matrix"]["legends"].values()) == 8


def test_lg2_cards_and_screens_show_with_the_flag(legends_on):
    headers, _ = _owner("lg2-on@example.com")
    _set_flags(screener_legends_lg2=True)
    body = client.get("/api/screener/legends", headers=headers).json()
    ids = [c["id"] for c in body["cards"]]
    assert ids[-5:] == LG2_CARDS and len(ids) == 13
    for card in body["cards"][-5:]:
        assert card["disclaimer"] == lg.DISCLAIMER.format(name=card["legend"])
        assert card["source_line"].startswith("Source: ") and card["sources"] and card["review"]["status"] in lg.REVIEW_STATUSES
        for text in (card["name"], card["approximation"], *(line["text"] for line in card["lines"].values())):
            assert lg.wording_problems("api", text) == [], text
    one = client.get(f"/api/screener/legends/{LG2_CARDS[3]}", headers=headers).json()
    assert one["screen"]["base_tf"] == "5m" and one["filing"]["tags"]["timeframe_family"] == ["intraday"]
    tax = client.get("/api/screener/taxonomy?method=legends", headers=headers).json()
    assert [s["id"] for s in tax["screens"]][-5:] == LG2_CARDS and sum(tax["matrix"]["legends"].values()) == 13


def test_lg2_without_the_legends_flag_shows_nothing():
    headers, _ = _owner("lg2-alone@example.com")
    _set_flags(screener_v2=True, screener_gallery=True, screener_legends_lg2=True)
    try:
        r = client.get("/api/screener/legends", headers=headers)
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_legends"
        tax = client.get("/api/screener/taxonomy", headers=headers).json()
        assert not [s for s in tax["screens"] if s["source"] == "legends"]
    finally:
        _set_flags(screener_v2=False, screener_gallery=False, screener_legends_lg2=False)


def test_the_taxonomy_hides_flagged_screens_by_id():
    import app.main  # noqa: F401 - registers every template source
    assert {i: f for i, f in tx.screen_flags().items() if i in LG2_CARDS} == {i: lg.FLAG_LG2 for i in LG2_CARDS}
    assert set(tx.default_hidden_ids()) >= set(LG2_CARDS)
    shown = tx.payload(tx.load(), hidden_ids=LG2_CARDS)
    assert not set(LG2_CARDS) & {s["id"] for s in shown["screens"]} and shown["total"] == len(tx.load().screens) - 5
