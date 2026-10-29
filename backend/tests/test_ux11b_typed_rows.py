"""UX-11b C1: the daily rhythm's sections carry `kind` and typed `rows` (numbers, signs, states) next to the same `lines`
(Telegram keeps the sentences). The rows hold no call word, the structure row names its states as keys the page colours
(trend UP / DOWN / SIDEWAYS, BOS / CHoCH by direction, the price's place against the areas), and the user's OWN brief
carries the buyer / seller area as a price range (the owner's default, shown inside Details)."""
import json
from datetime import date, timedelta

from app.ai import market_memory
from app.chart_analyze.signal_language import call_words
from app.db.models import MarketEventRecord
from app.rhythm import build, words
from tests.test_mo4_daily_rhythm import DAY, _add, _at, _build, _flags_on, _memory, _signal, _trade  # noqa: F401 - autouse
from tests.test_ui2_4a1_prefs import _who


def _by_id(brief):
    return {s["id"]: s for s in brief["sections"]}


def _no_call_words(brief):
    for s in brief["sections"]:
        assert s["kind"] and isinstance(s["rows"], list)
        text = json.dumps(s["rows"])
        assert call_words(text) == [], (s["id"], text)


def test_vix_bands_match_the_sentence():
    for v, level, word in ((11.9, "LOW", "very calm"), (12.0, "NORMAL", "normal"), (15.9, "NORMAL", "normal"),
                           (16.0, "ELEVATED", "elevated"), (19.99, "ELEVATED", "elevated"), (20.0, "HIGH", "high")):
        assert market_memory.vix_level(v) == level
        assert word in market_memory.vix_text("en", v)


def test_structure_keys_follow_the_words():
    assert words.trend_key("UPTREND") == "UP" and words.trend_key("downtrend") == "DOWN"
    assert words.trend_key("RANGE") == "SIDEWAYS" and words.trend_key(None) == "SIDEWAYS"
    assert words.event_key({"event": "CHoCH", "direction": "BEARISH"}) == {"kind": "CHOCH", "direction": "DOWN"}
    assert words.event_key({"event": "BOS", "direction": "BULLISH"}) == {"kind": "BOS", "direction": "UP"}
    assert words.event_key({"event": "SWEEP", "direction": "BULLISH"}) is None
    support, resistance = {"low": 90.0, "high": 95.0}, {"low": 110.0, "high": 115.0}
    for price, key in ((92.0, "IN_BUYER"), (112.0, "IN_SELLER"), (98.0, "NEARER_BUYER"), (106.0, "NEARER_SELLER")):
        assert words.area_state(price, support, resistance) == key
        assert words.area_words(price, support, resistance) == words.AREA_WORDS[key]
    assert words.area_state(100.0, support, None) == "ABOVE_BUYER" and words.area_state(100.0, None, resistance) == "BELOW_SELLER"
    assert words.area_state(None, support, resistance) is None and words.area_words(None, support, resistance) is None


def test_pre_market_rows(monkeypatch):
    who = _who("ux11b-pre")
    morning = _at(DAY, 8, 45)

    async def tile(ctx, name):
        return {"vix": {"status": "ok", "close": 17.2, "change_pct": 3.1, "percentile": 64.0, "as_of": DAY.isoformat()},
                "fii_dii": {"status": "ok", "net_fii_crore": -1250.0, "net_dii_crore": 980.0, "as_of": "2031-06-10"},
                "regime": {"status": "ok", "daily": {"trend": "UPTREND"}, "weekly": {"trend": "RANGE"}}}.get(name, {})
    monkeypatch.setattr(build, "_tile", tile)
    _add(*_memory(who, morning - timedelta(hours=15)),
         MarketEventRecord(tenant_id=who["tenant_id"], event_date=DAY, kind="RBI_POLICY", action="BLOCK", description="Policy announcement"),
         _signal(who, "UXTCS", _at(DAY - timedelta(days=1), 12), ["The pullback reached a buyer area"], direction="SHORT"),
         _trade(who, "MO4UXNEAR", entry=100.0, stop=90.0, mark=91.5))
    by_id = _by_id(_build(who, "pre_market", morning))
    assert [by_id[k]["kind"] for k in ("mood", "events", "structure", "setups", "positions", "budget")] == \
        ["mood", "events", "structure", "setups", "positions", "budget"]

    mood = {r["type"]: r for r in by_id["mood"]["rows"]}
    assert mood["vix"] == {"type": "vix", "value": 17.2, "change_pct": 3.1, "level": "ELEVATED", "percentile": 64.0, "as_of": None}
    assert mood["flows"] == {"type": "flows", "fii_crore": -1250.0, "dii_crore": 980.0, "as_of": "2031-06-10"}

    nifty, bank = by_id["structure"]["rows"]
    assert (nifty["label"], nifty["trend"], nifty["daily"], nifty["weekly"]) == ("NIFTY 50", "UP", "UP", "SIDEWAYS")
    assert nifty["event"] == {"kind": "BOS", "direction": "UP"} and nifty["location"] == "NEARER_BUYER"
    assert nifty["buyer_area"] == {"low": 22900.0, "high": 22950.0} and nifty["seller_area"] == {"low": 23300.0, "high": 23350.0}
    assert nifty["last_read"] == (DAY - timedelta(days=1)).isoformat()            # read the evening before: stale
    assert (bank["label"], bank["trend"], bank["event"], bank["location"]) == ("BANK NIFTY", "DOWN", {"kind": "CHOCH", "direction": "DOWN"},
                                                                               "IN_SELLER")
    assert "daily" not in bank

    event = by_id["events"]["rows"][0]
    assert event["label"] == "RBI policy" and event["detail"] == "Policy announcement" and event["pauses_entries"] is True
    setup = by_id["setups"]["rows"][0]
    assert (setup["symbol"], setup["side"], setup["source"]) == ("UXTCS", "SHORT", "strategy")
    assert words.numberless(setup["detail"])
    position = by_id["positions"]["rows"][0]
    assert position == {"type": "position", "symbol": "MO4UXNEAR", "side": "LONG", "pnl": -85.0, "near": True}
    assert by_id["budget"]["rows"] == []                                           # no risk settings: no made-up budget


def test_closing_and_weekly_rows(monkeypatch):
    who = _who("ux11b-close")

    async def closes(ctx, name, days=20):
        base = {"NIFTY 50": 23000.0, "NIFTY BANK": 49000.0, "NIFTY IT": 30000.0, "NIFTY METAL": 9000.0}.get(name)
        if base is None:
            return []
        step = {"NIFTY IT": 1.02, "NIFTY METAL": 0.985}.get(name, 1.005)
        return [(DAY - timedelta(days=i), base) for i in range(6, 0, -1)] + [(DAY, base * step)]

    async def tile(ctx, name):
        return {"status": "ok", "advances": 1200, "declines": 800, "counted": 2050, "as_of": DAY.isoformat()} if name == "breadth" else {}
    monkeypatch.setattr(build, "_closes", closes)
    monkeypatch.setattr(build, "_tile", tile)
    _add(_trade(who, "MO4UXWIN", entry=100.0, stop=95.0, mark=104.0, exit_at=_at(DAY, 14), pnl=400.0),
         _trade(who, "MO4UXOPEN", entry=100.0, stop=95.0, mark=102.0))
    by_id = _by_id(_build(who, "closing", _at(DAY, 16, 10)))
    market = by_id["market"]["rows"]
    assert market[0] == {"type": "index_move", "label": "NIFTY 50", "close": 23115.0, "change_pct": 0.5, "as_of": None, "sessions": 1}
    assert market[2] == {"type": "breadth", "advances": 1200, "declines": 800, "as_of": None}
    assert market[3] == {"type": "sectors", "strongest": {"label": "IT", "change_pct": 2.0}, "weakest": {"label": "Metal", "change_pct": -1.5}}
    day = by_id["day"]["rows"][0]
    assert day["booked"] == 400.0 and day["open_pnl"] == 20.0
    nxt = by_id["next"]["rows"]
    assert nxt[0] == {"type": "next_session", "date": "2031-06-13"} and {"type": "carry", "count": 1} in nxt
    assert any(r["type"] == "holiday" and r["date"] == "2031-06-12" for r in nxt)

    week = _by_id(_build(who, "weekly", _at(date(2031, 6, 14), 10)))
    assert week["market"]["rows"][0]["sessions"] == 5 and week["story"]["kind"] == "structure"
    record = next(r for r in week["you"]["rows"] if r["type"] == "record")
    assert (record["closed"], record["won"], record["lost"], record["net"]) == (1, 1, 0, 400.0)


def test_empty_brief_has_no_rows():
    who = _who("ux11b-empty")
    brief = _build(who, "pre_market", _at(DAY, 8, 45))
    for s in brief["sections"]:
        assert s["rows"] == [] and s["empty"]
    _no_call_words(brief)


def test_rows_never_carry_a_call_word(monkeypatch):
    who = _who("ux11b-words")
    _add(*_memory(who, _at(DAY, 8)), _signal(who, "UXINFY", _at(DAY - timedelta(days=1), 11), ["Buy above 1,500"]))
    _no_call_words(_build(who, "pre_market", _at(DAY, 8, 45)))


def test_every_row_type_and_kind_is_known_to_the_page(monkeypatch):
    """The frontend's SectionRow / SectionKind (frontend/src/rhythm/types.ts) name every row type and kind the briefs send."""
    import re
    from pathlib import Path
    types_ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "rhythm" / "types.ts").read_text()
    known_rows = set(re.findall(r'type: "(\w+)"', types_ts))
    kinds_block = types_ts[types_ts.index("export type SectionKind"):types_ts.index("export type Side")]
    known_kinds = set(re.findall(r'"(\w+)"', kinds_block))
    who = _who("ux11b-contract")

    async def closes(ctx, name, days=20):
        return [(DAY - timedelta(days=i), 100.0 + i) for i in range(6, -1, -1)] if name in ("NIFTY 50", "NIFTY IT", "NIFTY METAL") else []

    async def tile(ctx, name):
        return {"vix": {"status": "ok", "close": 13.0}, "breadth": {"status": "ok", "advances": 1, "declines": 2, "counted": 3,
                                                                     "as_of": DAY.isoformat()}}.get(name, {})
    monkeypatch.setattr(build, "_closes", closes)
    monkeypatch.setattr(build, "_tile", tile)
    _add(*_memory(who, _at(DAY, 8)), _trade(who, "MO4UXC", entry=100.0, stop=90.0, mark=91.0),
         _trade(who, "MO4UXC2", entry=100.0, stop=95.0, mark=104.0, exit_at=_at(DAY, 14), pnl=40.0))
    seen_rows, seen_kinds = set(), set()
    for kind, at in (("pre_market", _at(DAY, 8, 45)), ("mid_session", _at(DAY, 12)), ("closing", _at(DAY, 16)),
                     ("weekly", _at(date(2031, 6, 14), 10))):
        for s in _build(who, kind, at)["sections"]:
            seen_kinds.add(s["kind"])
            seen_rows |= {r["type"] for r in s["rows"]}
    assert seen_rows and seen_rows <= known_rows, seen_rows - known_rows
    assert seen_kinds <= known_kinds, seen_kinds - known_kinds
    from app.growth import dishes
    source = Path(dishes.__file__).read_text() + Path(build.__file__).read_text()
    emitted = set(re.findall(r'\.row\("(\w+)"', source)) | set(re.findall(r'"type": "(\w+)"', source)) | {"best", "worst"}
    assert emitted <= known_rows, emitted - known_rows
    assert set(re.findall(r'kind="(\w+)"', source)) <= known_kinds
