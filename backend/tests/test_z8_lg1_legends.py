"""U7 LG1 (docs/specs/ATP_LEGENDS_SCREENS_ADDENDUM.md): the first eight Legends cards - the content lint (every line
cites a public source, at most five lines, no advice / performance / endorsement wording, no first person or quotation,
no trademarked title, English only), the screens (compile, parameters inside their documented ranges, a positive and a
negative fixture each), the link to the taxonomy (every card is a filed screen and every filed Legends screen has a
card) and the API (flag `screener_legends`, off by default; the Legends screens are hidden from the taxonomy while it
is off)."""
import copy
import json
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np
import pandas as pd
import pytest

from app.platform import controls
from app.screener import compile_screen
from app.screener import legends as lg
from app.screener import taxonomy as tx
from app.screener.runtime import SymbolData, run_screen
from tests.test_auth_api import client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z8_tx1_taxonomy import _set_flags

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener"
SAMPLE = FRONTEND / "legendsSample.json"
TAXONOMY_SAMPLE = FRONTEND / "taxonomyLegendsSample.json"
LG1_CARDS = ["legends.minervini_trend_template", "legends.darvas_box_breakout", "legends.weinstein_stage2_breakout",
             "legends.turtle_channel_breakout", "legends.raschke_adx_pullback", "legends.crabel_nr7",
             "legends.connors_rsi2_pullback", "legends.williams_volatility_breakout"]


def _cards():
    return json.loads(lg.DATA_FILE.read_text(encoding="utf-8"))


def _sources():
    return json.loads(lg.SOURCES_FILE.read_text(encoding="utf-8"))


@pytest.fixture
def legends_on():
    _set_flags(screener_v2=True, screener_legends=True)
    yield
    _set_flags(screener_v2=False, screener_legends=False, screener_gallery=False)


# --- the content lint ------------------------------------------------------------------------------------------------

def test_the_library_passes_every_lint_rule():
    assert lg.lint() == []
    lib = lg.load()
    assert [c.id for c in lib.of(("lg1",))] == LG1_CARDS                               # LG2 cards: tests/test_n3_lg2_legends.py
    for card in lib.cards:
        assert card.name.startswith(f"{card.short}-style ")
        assert len(lg.LINES) + 1 <= lg.MAX_CARD_LINES                                   # who, style, looks for, timeframe, source
        for key in lg.LINES:
            line = card.lines[key]
            assert line.text and "\n" not in line.text and len(line.text) <= lg.MAX_LINE_CHARS
            assert line.cite and set(line.cite) <= set(lib.sources), (card.id, key)
    assert {c for card in lib.cards for c in card.cited()} == set(lib.sources)          # no source left uncited


def test_every_source_is_a_published_work_with_its_details():
    for source in lg.load().sources.values():
        assert source.kind in lg.SOURCE_KINDS and source.title and source.authors and 1800 <= source.year <= 2100
        assert source.covers, source.id                                                  # what a reviewer checks the card against
        assert source.review in lg.REVIEW_STATUSES
        assert source.evidence and all(u.startswith("https://") for u in source.evidence), source.id
        assert source.publisher and source.review_note, source.id


def test_lg1_2_the_bibliographic_check_against_public_catalogues():
    """LG1-2: each source checked against catalogue / ISBN records (the URLs in `evidence`); only Connors and Alvarez's
    first-edition year is unsettled (2008 or 2009), so that source and its one card are unverified, with the reason."""
    lib = lg.load()
    lg1_sources = {c for card in lib.of(("lg1",)) for c in card.cited()}
    status = {s.id: s.review for s in lib.sources.values() if s.id in lg1_sources}
    assert status == {**{i: "verified" for i in status}, "connors_alvarez_2008": "unverified"}
    darvas = lib.sources["darvas_1960"]
    assert (darvas.publisher, darvas.year) == ("American Research Council", 1960)
    assert any("archive.org" in u for u in darvas.evidence)
    assert "2009" in lib.sources["connors_alvarez_2008"].review_note
    cards = {c.id: (c.review, c.review_note) for c in lib.of(("lg1",))}
    assert {i for i, (r, _) in cards.items() if r == "unverified"} == {"legends.connors_rsi2_pullback"}
    assert "connors_alvarez_2008" in cards["legends.connors_rsi2_pullback"][1]
    for card in lib.cards:                                     # verified = every source it cites is verified
        assert (card.review == "verified") == all(lib.sources[c].review == "verified" for c in card.cited()), card.id


def test_the_files_are_english_only():
    for path in (lg.DATA_FILE, lg.SOURCES_FILE):
        assert not any("\u0900" <= ch <= "\u097f" for ch in path.read_text(encoding="utf-8")), path.name


def _first(data):
    return data["cards"][0]


@pytest.mark.parametrize("change, expect", [
    (lambda c, s: _first(c)["style"].update(text="Stocks to buy when the trend is strong."), "advice wording is not allowed (buy)"),
    (lambda c, s: _first(c)["looks_for"].update(text="The best setups with a clear target."), "advice wording is not allowed"),
    (lambda c, s: _first(c)["style"].update(text="A guaranteed way to find leaders."), "advice wording is not allowed (guaranteed)"),
    (lambda c, s: _first(c)["style"].update(text="A profitable method with high returns."), "performance wording is not allowed (profitable, returns)"),
    (lambda c, s: _first(c)["who"].update(text="He made millions with it."), "performance wording is not allowed (millions)"),
    (lambda c, s: _first(c)["style"].update(text="Shown to outperform the index."), "performance wording is not allowed (outperform)"),
    (lambda c, s: _first(c)["who"].update(text="Trader who endorsed this screen."), "endorsement wording is not allowed (endorsed)"),
    (lambda c, s: _first(c)["who"].update(text="The official version of his checklist."), "endorsement wording is not allowed (official)"),
    (lambda c, s: _first(c).update(approximation="Approved by the author."), "endorsement wording is not allowed (approved)"),
    (lambda c, s: _first(c)["style"].update(text="I only trade leaders in an uptrend."), "first person is not allowed (I)"),
    (lambda c, s: _first(c)["style"].update(text="This is my checklist for leaders."), "first person is not allowed (my)"),
    (lambda c, s: _first(c)["style"].update(text='He called it "the template".'), "no quotation marks"),
    (lambda c, s: _first(c)["style"].update(text="Trend following in \u0936\u0947\u0905\u0930 markets."), "English only"),
    (lambda c, s: _first(c).update(name="Minervini-style SEPA template"), "no trademarked or branded method name in a title (SEPA)"),
    (lambda c, s: c["cards"][4].update(name="Raschke-style Holy Grail"), "no trademarked or branded method name in a title (Holy Grail)"),
    (lambda c, s: _first(c).update(name="Trend template"), "name must read 'Minervini-style <screen>'"),
    (lambda c, s: _first(c).update(short="Darvas"), "short name 'Darvas' must be part of the legend's name"),
    (lambda c, s: _first(c)["style"].update(cite=[]), "style: cite at least one source"),
    (lambda c, s: _first(c)["who"].update(cite=["a_blog_post"]), "cites 'a_blog_post', which is not in legends_sources.json"),
    (lambda c, s: _first(c).pop("timeframe"), "timeframe: empty"),
    (lambda c, s: _first(c)["looks_for"].update(text="x" * 161), "at most 160 characters on one line"),
    (lambda c, s: _first(c)["looks_for"].update(text="Line one\nline two"), "at most 160 characters on one line"),
    (lambda c, s: _first(c).update(extra="x"), "unknown fields extra"),
    (lambda c, s: _first(c).update(id="minervini"), "id must be legends.<lower_snake_case>"),
    (lambda c, s: c["cards"].append(copy.deepcopy(_first(c))), "duplicate id"),
    (lambda c, s: _first(c)["screen"].update(source="close > SMA(close, 50"), "the screen does not compile"),
    (lambda c, s: _first(c)["screen"].update(source="close > RSI(14)"), "the screen does not compile"),
    (lambda c, s: _first(c)["screen"]["params"].pop(), "parameter $rs_min has no value"),
    (lambda c, s: _first(c)["screen"]["params"].append({"name": "unused", "default": 1, "min": 0, "max": 2, "doc": "Not used."}),
     "unknown parameter(s): $unused"),
    (lambda c, s: _first(c)["screen"]["params"][0].update(default=1.5), "$near_high: default 1.5 must lie inside its documented range [0.5, 1]"),
    (lambda c, s: _first(c)["screen"]["params"][0].update(min=2), "must lie inside its documented range"),
    (lambda c, s: _first(c)["screen"]["params"][0].update(doc="Pick the best stocks to buy."), "$near_high doc: advice wording"),
    (lambda c, s: s["sources"][0].update(kind="blog"), "kind 'blog' is not a published public work"),
    (lambda c, s: s["sources"][0].pop("year"), "needs a publication year"),
    (lambda c, s: s["sources"][0].update(authors=[]), "needs its authors"),
    (lambda c, s: s["sources"][0].update(covers=""), "say what it covers"),
    (lambda c, s: s["sources"][0].update(title="\u0936\u0947\u0905\u0930 \u092c\u093e\u091c\u093e\u0930"), "English only"),
    (lambda c, s: s["sources"].append(dict(s["sources"][0], id="uncited_book")), "source 'uncited_book' is listed but no card cites it"),
    (lambda c, s: c.update(cards=[]), "cards: none defined"),
    (lambda c, s: s["sources"][0].update(review="pending"), "review must be one of verified, unverified ('pending' given)"),
    (lambda c, s: s["sources"][0].update(evidence=[]), "a verified source lists its evidence"),
    (lambda c, s: s["sources"][0].update(evidence=["see the library"]), "evidence must be a list of public https:// URLs"),
    (lambda c, s: s["sources"][1].update(publisher=""), "a verified source names its publisher"),
    (lambda c, s: s["sources"][0].update(review="unverified", review_note=""), "an unverified entry says why in review_note"),
    (lambda c, s: s["sources"][0].update(review="unverified"), "verified, but it cites unverified sources (minervini_2013)"),
    (lambda c, s: _first(c).pop("review"), "review must be one of verified, unverified"),
    (lambda c, s: _first(c).update(review="unverified"), "an unverified entry says why in review_note"),
    (lambda c, s: c["cards"][6].update(review="verified"), "verified, but it cites unverified sources (connors_alvarez_2008)"),
    (lambda c, s: _first(c).update(review_note="x" * 401), "review_note: at most 400 characters"),
    (lambda c, s: c["cards"][3].update(legend="Richard Dennis and the Turtle traders"),
     "names 'William Eckhardt', so the disclaimer must too"),
    (lambda c, s: c["cards"][4].update(legend="Linda Bradford Raschke"), "names 'Laurence Connors', so the disclaimer must too"),
    (lambda c, s: _first(c).update(approximation="Compared with the list Paul Tudor Jones describes."),
     "names 'Paul Tudor Jones', so the disclaimer must too"),
    (lambda c, s: c["cards"][4].update(legend="Linda Bradford Raschke", who={"text": "Futures trader; co-author with Connors of the 1995 book Street Smarts.", "cite": ["raschke_connors_1995"]}),
     "names 'Laurence A. Connors', so the disclaimer must too"),
])
def test_the_lint_rules_refuse_a_broken_library(change, expect):
    cards, sources = _cards(), _sources()
    change(cards, sources)
    _, problems = lg.parse(cards, sources)
    assert any(expect in p for p in problems), problems


def test_every_person_a_card_names_is_named_in_its_disclaimer():
    """L2: the disclaimer ("Not affiliated with, endorsed by, or representing <legend>") covers every person the card
    names - co-authors and the people a method is credited to - not just the card's main name."""
    lib = lg.load()
    for card in lib.cards:
        texts = [card.lines[k].text for k in lg.LINES] + [card.approximation] + [p.doc for p in card.params]
        named = lg.people_named(texts, [lib.sources[c] for c in card.cited()])
        disclaimer = lg.DISCLAIMER.format(name=card.legend)
        assert all(person in disclaimer for person in named), (card.id, named)
    turtles, raschke = lib.card("legends.turtle_channel_breakout"), lib.card("legends.raschke_adx_pullback")
    assert "William Eckhardt" in turtles.legend and "Richard Dennis" in turtles.legend
    assert "Laurence Connors" in raschke.legend
    street_smarts = [lib.sources["raschke_connors_1995"]]                  # a cited title is not a person
    assert lg.people_named(["Co-author with Laurence Connors of the 1995 book Street Smarts."], street_smarts) == ["Laurence Connors"]
    assert lg.people_named(["Co-author with Connors of Street Smarts."], street_smarts) == ["Laurence A. Connors"]
    assert lg.people_named(["Daily bars; System 1 used 20 days."], []) == []


def test_negated_and_neutral_wording_passes():
    assert lg.wording_problems("t", "Daily bars; a short-term setup lasting days.") == []
    assert lg.wording_problems("t", "US stock trader who set out his approach in a book.") == []    # "US" is not "us"
    assert lg.wording_problems("t", "Trades of one to a few days; the rules were symmetric for shorts.") == []
    assert lg.wording_problems("t", "We trade it") != []


def test_load_refuses_a_library_that_fails_lint(tmp_path):
    cards = _cards()
    cards["cards"][0]["style"]["text"] = "A sure-shot method."
    bad = tmp_path / "legends.json"
    bad.write_text(json.dumps(cards), encoding="utf-8")
    with pytest.raises(lg.LegendsError) as err:
        lg.load(bad, lg.SOURCES_FILE)
    assert any("advice wording" in p for p in err.value.problems)


# --- the screens: parameters and fixtures ----------------------------------------------------------------------------

def test_every_screen_compiles_uses_every_parameter_and_keeps_defaults_in_range():
    for card in lg.load().cards:
        _, validated = compile_screen(card.source, base_tf=card.base_tf, params=card.defaults())
        assert validated.ok, (card.id, validated.problems)
        assert validated.params_used == {p.name for p in card.params}, card.id
        for p in card.params:
            assert p.min <= p.default <= p.max, (card.id, p.name)
            for edge in (p.min, p.max):                                                    # the whole documented range compiles
                _, at_edge = compile_screen(card.source, base_tf=card.base_tf, params={**card.defaults(), p.name: edge})
                assert at_edge.ok, (card.id, p.name, edge, at_edge.problems)


def _frame(close, *, high=None, low=None, open_=None, vol=None, freq="1D"):
    c = np.asarray(close, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(c), freq=freq, tz="UTC")
    return pd.DataFrame({"open": np.asarray(open_ if open_ is not None else c - 0.2, dtype=float),
                         "high": np.asarray(high if high is not None else c + 0.5, dtype=float),
                         "low": np.asarray(low if low is not None else c - 0.5, dtype=float), "close": c,
                         "volume": np.asarray(vol if vol is not None else [1000.0] * len(c), dtype=float)}, index=idx)


def _uptrend(n=320, start=100.0, step=0.004):
    return list(start * (1 + step) ** np.arange(n))


def _minervini():
    up = _uptrend()
    broke = up[:-40] + list(np.linspace(up[-41], up[-41] * 0.7, 40))            # fell 30% in 40 bars: below its 50-day
    flat = [100.0 + (i % 5) * 0.1 for i in range(320)]
    return ([_frame(up), _frame(flat)], [_frame(broke), _frame(flat)])


def _darvas():
    base = _uptrend(270, 120.0, 0.002) + [200.0 + (i % 4) * 1.5 for i in range(29)]  # 29 bars in a 200-204.5 box
    close = base + [207.0]
    vol = [1000.0] * (len(close) - 1)
    return [_frame(close, vol=vol + [3000.0])], [_frame(close, vol=vol + [1000.0])]


def _weinstein():
    close = list(np.linspace(130, 100, 30)) + [100.0] * 40 + [108.0]
    vol = [1000.0] * 70
    weekly = dict(freq="7D")
    return [_frame(close, vol=vol + [3000.0], **weekly)], [_frame(close, vol=vol + [1100.0], **weekly)]


def _turtle():
    rng = [100.0 + (i % 3) for i in range(40)]
    return [_frame(rng + [105.0])], [_frame(rng + [101.0])]


def _raschke():
    trend = [100.0 + i for i in range(80)]
    ema = pd.Series(trend + [170.0]).ewm(span=20, adjust=False).mean().iloc[-1]
    touch = trend + [170.0]
    lows = [c - 0.5 for c in trend] + [ema - 1.0]                                 # the pullback bar's low reaches the EMA
    no_touch = trend + [180.0]
    return [_frame(touch, low=lows)], [_frame(no_touch)]


def _crabel():
    close = [100.0 + (i % 2) for i in range(30)]
    high, low = [c + 1.0 for c in close], [c - 1.0 for c in close]
    narrow = _frame(close + [100.5], high=high + [101.0], low=low + [100.0])        # range 1 after ranges of 2
    wide = _frame(close + [100.5], high=high + [102.0], low=low + [99.0])
    return [narrow], [wide]


def _connors():
    up = _uptrend(260, 100.0, 0.003)
    dip = up + [up[-1] * 0.98, up[-1] * 0.96, up[-1] * 0.94]                       # three down days, still above the 200-day
    return [_frame(dip)], [_frame(up + [up[-1] * 1.01])]


def _williams():
    close = [100.0] * 30
    high, low = [101.0] * 30, [99.0] * 30                                            # previous range 2
    reach = _frame(close + [101.2], open_=[99.8] * 30 + [100.0], high=high + [101.5], low=low + [99.9])
    short = _frame(close + [100.3], open_=[99.8] * 30 + [100.0], high=high + [100.5], low=low + [99.9])
    return [reach], [short]


def test_weinstein_needs_more_than_a_one_week_uptick_in_a_falling_average():
    """L3: a 30-week average that has fallen for months and ticks up for one week is not "flat or rising"."""
    card = lg.load().card("legends.weinstein_stage2_breakout")
    close = list(np.linspace(160, 100, 61)) + [131.0]                 # a long decline, then one strong week
    frame = _frame(close, vol=[1000.0] * 61 + [3000.0], freq="7D")
    sma = frame["close"].rolling(30).mean()
    assert sma.iloc[-1] >= sma.iloc[-2] and sma.iloc[-1] < sma.iloc[-5]  # the one-week uptick, still below 4 weeks ago
    one_week = card.source.replace(" AND SMA(close, 30) >= SMA(close, 30)[4]", "")
    assert one_week != card.source
    ast, validated = compile_screen(one_week, base_tf="1w", params=card.defaults())
    loose = run_screen(ast, validated, [SymbolData("S", {"1w": frame})], base_tf="1w", params=card.defaults())[0]
    assert loose.matched                                                    # the old rule let it through
    assert not _run_card(card, [frame])[0].matched


FIXTURES: Dict[str, Callable[[], tuple]] = {
    "legends.minervini_trend_template": _minervini, "legends.darvas_box_breakout": _darvas,
    "legends.weinstein_stage2_breakout": _weinstein, "legends.turtle_channel_breakout": _turtle,
    "legends.raschke_adx_pullback": _raschke, "legends.crabel_nr7": _crabel, "legends.connors_rsi2_pullback": _connors,
    "legends.williams_volatility_breakout": _williams,
}


def _run_card(card, frames: List[pd.DataFrame]):
    ast, validated = compile_screen(card.source, base_tf=card.base_tf, params=card.defaults())
    universe = [SymbolData(f"S{i}", {card.base_tf: f}) for i, f in enumerate(frames)]
    return run_screen(ast, validated, universe, base_tf=card.base_tf, params=card.defaults())


def test_every_card_has_a_fixture():
    assert sorted(FIXTURES) == sorted(c.id for c in lg.load().of(("lg1",)))


@pytest.mark.parametrize("card_id", LG1_CARDS)
def test_each_screen_matches_its_positive_fixture_and_not_its_negative(card_id):
    card = lg.load().card(card_id)
    positive, negative = FIXTURES[card_id]()
    first = _run_card(card, positive)[0]
    assert first.reason is None and first.matched, (card_id, first)
    miss = _run_card(card, negative)[0]
    assert miss.reason is None and not miss.matched, (card_id, miss)


# --- the link to the taxonomy ----------------------------------------------------------------------------------------

def test_every_card_links_to_a_filed_screen_and_every_filed_legends_screen_has_a_card():
    assert lg.link_problems() == []
    filed = {s.id: s for s in tx.load().screens if s.source == lg.SOURCE}
    lib = lg.load()
    assert sorted(filed) == sorted(lib.ids())
    for card in lib.cards:
        assert filed[card.id].method == "legends" and filed[card.id].definition == card.lines["looks_for"].text


def test_the_taxonomy_guard_knows_the_legends_source():
    import app.main  # noqa: F401 - registers every template source
    assert tx.sources()[lg.SOURCE].flag == lg.FLAG
    assert tx.coverage_problems() == []
    tax = tx.load()
    unfiled = tx.Taxonomy(tax.version, tax.methods, tax.setups, tax.tags, tuple(s for s in tax.screens if s.id != LG1_CARDS[0]))
    assert tx.coverage_problems(unfiled) == [f"template {LG1_CARDS[0]!r} from source 'legends' is not filed in the taxonomy "
                                             "(add it to app/screener/data/taxonomy.json)"]


def test_link_problems_catch_a_missing_renamed_or_misfiled_screen():
    tax, lib = tx.load(), lg.load()

    def with_screens(screens):
        return tx.Taxonomy(tax.version, tax.methods, tax.setups, tax.tags, tuple(screens))

    others = [s for s in tax.screens if s.id != LG1_CARDS[0]]
    first = next(s for s in tax.screens if s.id == LG1_CARDS[0])
    assert lg.link_problems(lib, with_screens(others)) == [f"card {LG1_CARDS[0]!r} links to a screen that is not filed in the taxonomy"]
    renamed = tx.Screen(**{**first.__dict__, "name": "Something else"})
    assert any("differs from the card's" in p for p in lg.link_problems(lib, with_screens(others + [renamed])))
    misfiled = tx.Screen(**{**first.__dict__, "method": "indicator"})
    assert any("must be filed with source and method 'legends'" in p for p in lg.link_problems(lib, with_screens(others + [misfiled])))
    orphan = tx.Screen(**{**first.__dict__, "id": "legends.nobody"})
    assert "taxonomy screen 'legends.nobody' has no Legends card" in lg.link_problems(lib, with_screens(list(tax.screens) + [orphan]))


# --- the API ---------------------------------------------------------------------------------------------------------

def test_the_api_is_off_by_default_and_needs_both_flags():
    headers, _ = _owner("lg1-off@example.com")
    assert "screener_legends" in controls.FEATURE_FLAGS and "screener_legends" in controls.DEFAULT_OFF_FLAGS
    r = client.get("/api/screener/legends", headers=headers)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_v2"
    _set_flags(screener_v2=True)
    try:
        r = client.get("/api/screener/legends", headers=headers)
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_legends"
        r = client.get(f"/api/screener/legends/{LG1_CARDS[0]}", headers=headers)
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_legends"
    finally:
        _set_flags(screener_v2=False)
    assert client.get("/api/screener/legends").status_code == 401


def test_the_api_shape_carries_the_disclaimer_the_sources_and_the_screen(legends_on):
    headers, _ = _owner("lg1-shape@example.com")
    body = client.get("/api/screener/legends", headers=headers).json()
    assert set(body) == {"version", "header", "note", "cards"}
    assert body["header"] == ("Inspired by publicly described methods. Not affiliated with, endorsed by, or representing the people "
                              "named. Educational tool, not investment advice.")
    assert [c["id"] for c in body["cards"]] == LG1_CARDS
    for card in body["cards"]:
        assert set(card) == {"id", "legend", "name", "lines", "source_line", "sources", "approximation", "disclaimer", "review", "screen",
                             "filing"}
        assert card["review"]["status"] in lg.REVIEW_STATUSES and (card["review"]["status"] == "verified" or card["review"]["note"])
        assert all(s["review"] in lg.REVIEW_STATUSES and s["evidence"] for s in card["sources"])
        assert card["disclaimer"] == (f"Inspired by publicly described methods. Not affiliated with, endorsed by, or representing "
                                      f"{card['legend']}. Educational tool, not investment advice.")
        assert list(card["lines"]) == list(lg.LINES) and len(card["lines"]) + 1 <= lg.MAX_CARD_LINES
        assert card["source_line"].startswith("Source: ") and card["sources"]
        assert all(s["title"] in card["source_line"] for s in card["sources"])
        assert set(card["screen"]) == {"id", "source", "base_tf", "params"} and card["screen"]["id"] == card["id"]
        assert card["filing"]["method"] == "legends"
        for text in (card["name"], card["approximation"], *(line["text"] for line in card["lines"].values())):
            assert lg.wording_problems("api", text) == [], text


def test_one_card_and_an_unknown_card(legends_on):
    headers, _ = _owner("lg1-one@example.com")
    one = client.get(f"/api/screener/legends/{LG1_CARDS[2]}", headers=headers)
    assert one.status_code == 200 and one.json()["name"] == "Weinstein-style stage 2 breakout" and one.json()["header"] == lg.HEADER
    assert one.json()["screen"]["base_tf"] == "1w"
    assert client.get("/api/screener/legends/legends.nobody", headers=headers).status_code == 404


def test_the_taxonomy_hides_the_legends_screens_while_the_flag_is_off():
    headers, _ = _owner("lg1-tax@example.com")
    _set_flags(screener_v2=True, screener_gallery=True)
    try:
        off = client.get("/api/screener/taxonomy", headers=headers).json()
        assert not [s for s in off["screens"] if s["source"] == "legends"] and sum(off["matrix"]["legends"].values()) == 0
        _set_flags(screener_legends=True)
        on = client.get("/api/screener/taxonomy?method=legends", headers=headers).json()
        assert [s["id"] for s in on["screens"]] == LG1_CARDS and sum(on["matrix"]["legends"].values()) == len(LG1_CARDS)
    finally:
        _set_flags(screener_v2=False, screener_gallery=False, screener_legends=False)


def test_the_legends_on_taxonomy_sample_is_what_the_route_serves():
    """Drift check through HTTP: with only screener_v2, screener_gallery and screener_legends on, GET
    /api/screener/taxonomy returns src/screener/taxonomyLegendsSample.json exactly."""
    headers, _ = _owner("lg1-taxsample@example.com")
    _set_flags(screener_v2=True, screener_gallery=True, screener_legends=True)
    try:
        served = client.get("/api/screener/taxonomy", headers=headers)
        assert served.status_code == 200
        assert served.json() == json.loads(TAXONOMY_SAMPLE.read_text(encoding="utf-8"))
    finally:
        _set_flags(screener_v2=False, screener_gallery=False, screener_legends=False)


def test_the_frontend_sample_is_the_server_payload():
    """src/screener/legendsSample.json feeds the frontend tests and stories; regenerate it with
    `python -m app.screener.legends > ../frontend/src/screener/legendsSample.json` after changing the data files."""
    assert json.loads(SAMPLE.read_text(encoding="utf-8")) == json.loads(json.dumps(lg.payload(lg.load())))


def test_the_frontend_taxonomy_sample_with_legends_on_is_the_server_payload():
    """src/screener/taxonomyLegendsSample.json is the taxonomy an organisation with `screener_legends` on sees (other
    default-off sources stay hidden); regenerate it with
    `python -m app.screener.legends taxonomy > ../frontend/src/screener/taxonomyLegendsSample.json`."""
    import app.main  # noqa: F401 - registers every template source
    hidden = tx.default_hidden()
    assert lg.SOURCE in hidden                                                           # off for a new organisation
    shown = [name for name in hidden if name != lg.SOURCE]
    sample = json.loads(TAXONOMY_SAMPLE.read_text(encoding="utf-8"))
    assert sample == json.loads(json.dumps(tx.payload(tx.load(), hidden_sources=shown, hidden_ids=tx.default_hidden_ids())))
    assert [s["id"] for s in sample["screens"] if s["source"] == lg.SOURCE] == LG1_CARDS
