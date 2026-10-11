"""U8 EV1/EV2: the evidence pack and its image.

* only the screen's own conditions are drawn (nothing else: no user layers, no other indicators, no zone the screen did
  not read); every series drawn is one of the screen's operands;
* value parity: every number in the verify strip is the runtime's own value at the matched bar, and pass / fail is the
  runtime's; the pack's `matched` is `run_screen`'s answer;
* no look-ahead: bars (and zone snapshots) after the evidence time - appended or changed - leave the pack identical;
* breakouts: the box / level and the verify terms come from the breakout engine, and its signal is the runtime's;
* zone bands go through the Z7a-3 selector (at most six, only zones a zone condition read);
* the image is deterministic (pixel hash), carries the layer (pixel diff), has light and dark themes, is size-capped;
* the caption fits Telegram, keeps a Devanagari screen name intact, ends with the informational note, never advises.
"""
import hashlib
import json
import struct
import zlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.ai.wording import banned_terms
from app.price_action.zones.read import Snapshot, ZoneTimeline
from app.screener import compile_screen, parse
from app.screener import evidence as ev
from app.screener import evidence_png as epng
from app.screener import nodes as n
from app.screener.evidence_store import canonical, view
from app.screener.runtime import SymbolData, _last, evaluate, run_screen
from tests.breakout_fixtures import TRUE_BREAK, base_then


def _daily(count=160, start="2026-01-01", jump=0.0, volume_spike=1.0) -> pd.DataFrame:
    i = np.arange(count, dtype=float)
    close = 100 + 0.4 * i + 3 * np.sin(i / 5)
    close[-1] += jump
    vol = 1000 + 100 * np.cos(i / 3)
    vol[-1] *= volume_spike
    idx = pd.date_range(start, periods=count, freq="1D", tz="UTC")
    return pd.DataFrame({"open": close - 0.5, "high": close + 1.2, "low": close - 1.1, "close": close, "volume": vol}, index=idx)


def _pack(src, data, tf="1d", **kw):
    ast, validated = compile_screen(src, base_tf=tf)
    assert validated.ok, validated.problems
    return ast, validated, ev.build_pack(ast, validated, data, base_tf=tf, **kw)


SCREEN = ('close > SMA(close, 20) AND RSI(14) BETWEEN 40 AND 101 AND (volume > 1.5 * SMA(volume, 20) OR close > high[1]) '
          'AND CrossAbove(EMA(close, 5), EMA(close, 50)) == FALSE AND Pattern("doji") == FALSE')


# ------------------------------------------------------------------------------------------------ only the conditions
def test_the_pack_draws_only_the_screens_own_conditions():
    data = SymbolData("ABC", {"1d": _daily(jump=3, volume_spike=2.5)})
    ast, _v, pack = _pack(SCREEN, data, debug=True)
    assert pack["schema"] == "evidence/1" and pack["symbol"] == "ABC" and pack["matched_index"] == len(pack["bars"]["t"]) - 1
    operand_texts = {n.to_text(node) for node in n.walk(ast)}
    for d in pack["drawables"]:
        assert d["row"] in {r["id"] for r in pack["rows"]}                                # every drawing belongs to a condition
        for s in d.get("series", []):
            assert s["name"] in operand_texts, s["name"]                                    # ... and draws only that condition's operands
        for lv in d.get("levels", []):
            assert lv["label"] in operand_texts
        assert d["kind"] in ("overlay", "pane", "box", "band", "bars")
    # one block per top-level AND item, the OR group opened into its two conditions
    assert [b["text"] for b in pack["blocks"]] == [n.to_text(i) for i in ast.items]
    assert [r["text"] for r in pack["rows"]][2:4] == ["volume > 1.5 * SMA(volume, 20)", "close > high[1]"]
    assert len(pack["drawables"]) <= len(pack["rows"])                                    # at most one drawing per condition
    # a screen without zone functions draws no zone, even when the symbol has zones loaded
    zoned = SymbolData("ABC", {"1d": _daily()}, zones=_timeline(_daily()))
    assert not [d for d in _pack("close > SMA(close, 20)", zoned)[2]["drawables"] if d["kind"] == "band"]


def test_failed_conditions_are_drawn_only_in_debug():
    data = SymbolData("ABC", {"1d": _daily()})
    src = "close > SMA(close, 20) AND (RSI(14) > 99 OR close > 0)"
    _a, _v, full = _pack(src, data, debug=True)
    failed = [r for r in full["rows"] if r["passed"] is False]
    assert failed and all(full["drawables"][int(r["drawable"][1:]) - 1]["failed"] for r in failed)
    shown = view(full, debug=False)
    assert all(not d["failed"] for d in shown["drawables"]) and len(shown["drawables"]) < len(full["drawables"])
    assert not any("RSI(14)" in line for line in shown["caption"][2:])                   # hidden from the caption's strip too
    assert any("RSI(14)" in line and line.startswith("❌") for line in view(full, debug=True)["caption"])


# ------------------------------------------------------------------------------------------------------ value parity
@pytest.mark.parametrize("src", [SCREEN, "PctChange(close, 5) > 0 AND ZScore(close, 20) < 5", "Count(10, close > open) >= 0 AND ATR(14) > 0",
                                 "close > Max(high, 20)[1] OR close < Min(low, 20)[1]", "NOT (close < SMA(close, 50)) AND volume > 0"])
def test_verify_strip_numbers_are_the_engines(src):
    data = SymbolData("ABC", {"1d": _daily(jump=2)})
    ast, validated, pack = _pack(src, data)
    assert pack["matched"] == run_screen(ast, validated, [data], base_tf="1d")[0].matched
    by_text = {}
    for node in n.walk(ast):
        by_text.setdefault(n.to_text(node), node)
    for row in pack["rows"]:
        engine = _last(evaluate(by_text[row["text"]], data, base_tf="1d"))
        assert row["passed"] == bool(engine if not (isinstance(engine, float) and np.isnan(engine)) else False), row["text"]
        for term in row["terms"]:
            value = _last(evaluate(by_text[term["text"]], data, base_tf="1d"))
            if isinstance(term["value"], float):
                assert term["value"] == round(float(value), 4), (row["text"], term)
                assert term["value_text"] in row["verify"] or term["literal"]
    for block in pack["blocks"]:
        assert block["passed"] == bool(_last(evaluate(by_text[block["text"]], data, base_tf="1d")))


def test_drawn_series_are_the_engines_series():
    data = SymbolData("ABC", {"1d": _daily()})
    ast, _v, pack = _pack("close > EMA(close, 20) AND RSI(14) > 10", data, window=50)
    sma = next(d for d in pack["drawables"] if d["kind"] == "overlay")
    engine = evaluate(parse("EMA(close, 20)"), data, base_tf="1d").iloc[-50:]
    assert sma["series"][0]["values"] == [round(float(x), 4) for x in engine]
    rsi = next(d for d in pack["drawables"] if d["kind"] == "pane")
    assert rsi["pane"] == "oscillator" and rsi["levels"][0]["value"] == 10.0 and rsi["unit"] == "index"
    assert len(pack["bars"]["c"]) == 50 and pack["bars"]["c"][-1] == round(float(data.frames["1d"]["close"].iloc[-1]), 4)


# -------------------------------------------------------------------------------------------------------- look-ahead
def test_no_look_ahead_appending_or_changing_later_bars_leaves_the_pack_unchanged():
    base = _daily(160)
    as_of = (base.index[119] + pd.Timedelta(days=1)).to_pydatetime()                       # the close of bar 119
    data = SymbolData("ABC", {"1d": base}, zones=_timeline(base))
    _a, _v, at_t = _pack(SCREEN + " AND NearZone(\"any\", 20)", data, as_of=as_of, debug=True)
    assert at_t["as_of"]["bar_start"] == base.index[119].isoformat() and len(at_t["bars"]["t"]) == 120
    assert all(pd.Timestamp(t) <= base.index[119] for t in at_t["bars"]["t"])               # no bar after T
    mutated = base.copy()
    mutated.iloc[120:, :] = mutated.iloc[120:, :] * 3.0                                    # later bars changed wildly
    longer = pd.concat([mutated, _daily(30, start="2026-06-10") * 5.0])                     # and more bars appended
    later_zones = _timeline(base, extra=[(base.index[130], 400.0)])                         # and a later zone snapshot
    for frame, zones in ((mutated, data.zones), (longer, later_zones)):
        again = _pack(SCREEN + " AND NearZone(\"any\", 20)", SymbolData("ABC", {"1d": frame}, zones=zones), as_of=as_of, debug=True)[2]
        assert canonical(again) == canonical(at_t)
    # the same pack as a run made at T on the bars it had then (the evidence is what the engine used)
    cut = SymbolData("ABC", {"1d": base.iloc[:120]}, zones=data.zones)
    assert canonical(_pack(SCREEN + " AND NearZone(\"any\", 20)", cut, debug=True)[2]) == canonical(at_t)


def test_a_symbol_without_a_closed_bar_at_the_evidence_time_is_refused():
    from app.screener.runtime import ScreenRuntimeError
    data = SymbolData("ABC", {"1d": _daily(10, start="2026-05-01")})
    ast, validated = compile_screen("close > 1", base_tf="1d")
    with pytest.raises(ScreenRuntimeError):
        ev.build_pack(ast, validated, data, base_tf="1d", as_of=datetime(2026, 4, 1, tzinfo=timezone.utc))


# --------------------------------------------------------------------------------------------------------- breakouts
def test_breakout_box_and_terms_come_from_the_breakout_engine():
    f = base_then([TRUE_BREAK])
    data = SymbolData("BRK", {"1d": f})
    src = 'RangeBreakout("up")'
    ast, validated, pack = _pack(src, data)
    row = pack["rows"][0]
    engine = bool(_last(evaluate(parse(src), data, base_tf="1d")))
    assert row["engine_signal"] == engine == row["passed"] == pack["matched"] is True
    box = next(d for d in pack["drawables"] if d["row"] == row["id"])
    assert box["kind"] == "box" and box["from"] < box["to"] <= pack["matched_index"]
    assert box["level"] == row["terms"][1]["value"] and box["low"] <= box["level"] <= box["high"] + 1e-9
    from app.screener.breakouts import params as bp
    from app.screener.breakouts.screens import Bars, evaluate_screen
    p = bp.settings()
    out = evaluate_screen(f, "B1", p, sides=("up",))["up"]
    assert row["terms"][1]["value"] == round(float(out["level"].iloc[-1]), 4)
    b = Bars(f, p)
    assert row["terms"][3]["value"] == round(float(b.rvol[-1]), 4)
    assert row["terms"][2]["value_text"].endswith(" ATR") and "range high" in row["verify"]


@pytest.mark.parametrize("name", sorted(set(ev.BREAKOUT_ARGS) - {"SwingBreakType"}))
def test_every_breakout_function_keeps_signal_parity_with_the_runtime(name):
    f = base_then([TRUE_BREAK])
    data = SymbolData("BRK", {"1d": f})
    args = {"NRBreakout": '"up", "nr7", 0', "NewHighBreakout": '"up", 4, 0', "RangeExpansion": '"up", 1, 0', "VolumeBreakout": '"up", 1'}
    src = f"{name}({args.get(name, chr(34) + 'up' + chr(34))})"
    _a, _v, pack = _pack(src, data, debug=True)
    row = pack["rows"][0]
    assert row["engine_signal"] == bool(_last(evaluate(parse(src), data, base_tf="1d"))) == row["passed"], src


# ------------------------------------------------------------------------------------------------------------- zones
def _zone(key, low, high, ztype="DEMAND_ZONE", state="fresh", touches=0):
    return {"zone_key": key, "zone_type": ztype, "timeframe": "1d", "zone_low": low, "zone_high": high, "state": state, "touches": touches,
            "strength": None, "remembered": False}


def _timeline(df, extra=()):
    """One snapshot seen from the 20th bar's close (zones around the closes), plus optional later ones."""
    first = df.index[20] + pd.Timedelta(days=1)
    last = float(df["close"].iloc[-1])
    rows = [_zone(f"z{i}", round(last - 2 - 3 * i, 2), round(last - 1 - 3 * i, 2)) for i in range(5)]
    rows += [_zone(f"s{i}", round(last + 1 + 3 * i, 2), round(last + 2 + 3 * i, 2), "SUPPLY_ZONE", "tested", 2) for i in range(5)]
    times = [pd.Timestamp(first).value] + [pd.Timestamp(t + pd.Timedelta(days=1)).value for t, _p in extra]
    snaps = [Snapshot(1, first.to_pydatetime(), "platform", last, rows)]
    snaps += [Snapshot(2 + i, (t + pd.Timedelta(days=1)).to_pydatetime(), "platform", p, [_zone("late", p - 1, p + 1)]) for i, (t, p) in enumerate(extra)]
    return ZoneTimeline("NSE:ABC", np.array(times, dtype="int64"), snaps)


def test_zone_conditions_draw_the_zone_they_read_through_the_band_selector():
    df = _daily()
    data = SymbolData("ABC", {"1d": df}, zones=_timeline(df))
    src = 'NearZone("demand", 20) AND NearZone("supply", 20) AND ZoneStrength("demand", 20) > 0 AND ZoneState("supply", 20) == "TESTED"'
    ast, validated, pack = _pack(src, data)
    assert pack["matched"] is run_screen(ast, validated, [data], base_tf="1d")[0].matched is True
    bands = [d for d in pack["drawables"] if d["kind"] == "band"]
    assert 1 <= len(bands) <= ev.MAX_ZONE_BANDS and {b["role"] for b in bands} == {"demand", "supply"}
    assert {b["label"].split()[0] for b in bands} == {"demand", "supply"} and all("(" in b["label"] for b in bands)
    close = df["close"].iloc[-1]
    demand = next(b for b in bands if b["role"] == "demand")
    assert demand["high"] <= close and demand["high"] == round(float(close) - 1, 2)          # the nearest demand zone
    row = next(r for r in pack["rows"] if r["text"].startswith('ZoneStrength'))
    assert row["terms"][0]["value"] == round(float(_last(evaluate(parse('ZoneStrength("demand", 20)'), data, base_tf="1d"))), 4)
    assert row["zone_as_of"] is not None


def test_more_than_six_zones_are_capped_by_the_selector():
    df = _daily()
    data = SymbolData("ABC", {"1d": df}, zones=_timeline(df))
    conds = " AND ".join(f'NearZone("{role}", {tol}, "zones")' for role in ("demand", "supply") for tol in (1, 4, 7, 10, 13))
    _a, _v, pack = _pack(conds, data)
    assert len([d for d in pack["drawables"] if d["kind"] == "band"]) <= ev.MAX_ZONE_BANDS


# ------------------------------------------------------------------------------------------------------------- image
def _decode(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            size = struct.unpack(">II", data[:8])
        elif kind == b"IDAT":
            idat += data
        pos += 12 + length
    raw = zlib.decompress(idat)
    w, h = size
    return w, h, b"".join(raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)] for y in range(h))


def _fixture_pack():
    data = SymbolData("ABC", {"1d": _daily(jump=3, volume_spike=2.5)})
    return view(_pack('close > SMA(close, 20) AND RSI(14) > 50 AND volume > 1.5 * SMA(volume, 20)', data, window=80, debug=True)[2])


def test_the_image_is_deterministic_and_carries_the_layer():
    pack = _fixture_pack()
    png = epng.render(pack)
    assert png == epng.render(pack) and len(png) < epng.MAX_PNG_BYTES
    w, h, px = _decode(png)
    assert (w, h) == (epng.WIDTH, epng.HEIGHT) and px == epng.pixels(pack)
    # pinned: the raw pixels (independent of the zlib build). Change only with a deliberate renderer change.
    assert hashlib.sha256(epng.pixels(pack)).hexdigest() == PIXELS_SHA256
    bare = dict(pack, drawables=[])
    diff = sum(1 for a, b in zip(epng.pixels(pack), epng.pixels(bare)) if a != b)
    assert diff > 2000                                                                    # the evidence layer is in the image
    sma_colour = bytes(epng.PALETTE[0])
    assert sma_colour in _rgb_set(px) and sma_colour not in _rgb_set(epng.pixels(bare))
    assert epng.pixels(pack, theme="dark") != epng.pixels(pack) and epng.pixels(pack, theme="dark")[:3] == bytes(epng.DARK.bg)


PIXELS_SHA256 = "05f49e6477c861bdddb3ffb2a933693e1bad998c8fb0f1aa8d8bf47909d53b17"


def _rgb_set(px: bytes):
    return {px[i:i + 3] for i in range(0, len(px), 3)}


def test_the_image_draws_the_signal_blocks_first_and_one_lower_pane():
    pack = _fixture_pack()
    shown = epng.image_drawables(pack)
    assert len(shown) <= epng.IMAGE_MAX_DRAWABLES and sum(1 for d in shown if d["pane"] != "price") <= 1
    kinds = [d["kind"] for d in shown]
    assert kinds == sorted(kinds, key=lambda k: epng.KIND_RANK[k])
    with pytest.raises(ValueError):
        epng.render(dict(pack, bars={"t": [], "o": [], "h": [], "l": [], "c": []}))


def test_caption_fits_telegram_keeps_devanagari_and_never_advises():
    data = SymbolData("ABC", {"1d": _daily()})
    name = "मोमेंटम स्क्रीन"
    many = " AND ".join(f"close > SMA(close, {k})" for k in range(2, 40))
    _a, _v, pack = _pack(many, data, screen_name=name)
    text = ev.caption_text(pack)
    assert len(text) <= ev.CAPTION_MAX and text.endswith(ev.DISCLAIMER) and f"Screen: {name}" in text
    assert pack["summary"]["not_shown"] == 38 - ev.MAX_ROWS
    assert epng.render(pack)[:8] == b"\x89PNG\r\n\x1a\n"                                  # unknown glyphs draw as "?"
    for line in pack["caption"] + [r["verify"] for r in pack["rows"]]:
        assert not banned_terms(line), line
    assert "recommendation" in ev.DISCLAIMER.lower() and not banned_terms(ev.DISCLAIMER)


def test_pack_is_size_bounded_and_canonical():
    data = SymbolData("ABC", {"1d": _daily(400)})
    _a, _v, pack = _pack(SCREEN, data, window=ev.MAX_WINDOW + 500, debug=True)
    assert len(pack["bars"]["t"]) <= ev.MAX_WINDOW
    text, sha = canonical(pack)
    assert len(text) <= ev.MAX_PACK_BYTES and sha == hashlib.sha256(text.encode()).hexdigest()
    assert json.loads(text) == json.loads(json.dumps(pack))


def test_intraday_bar_time_reads_in_ist():
    idx = pd.date_range("2026-03-10 03:45", periods=40, freq="5min", tz="UTC")
    c = np.linspace(100, 110, 40)
    f = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0}, index=idx)
    _a, _v, pack = _pack("close > open[1]", SymbolData("X", {"5m": f}), tf="5m")
    assert pack["caption"][0] == "X · 5m · bar closed 10 Mar 2026 12:35 IST"         # the 12:30 bar closed at 12:35


# ---------------------------------------------------------------------------------------- the frontend's sample pack
SAMPLE = __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener" / "evidenceSample.json"


def _sample_pack():
    f = base_then([TRUE_BREAK])
    data = SymbolData("BRK", {"1d": f}, zones=_timeline(f))
    src = ('RangeBreakout("up") AND close > SMA(close, 10) AND RSI(14) > 50 AND (volume > 3 * SMA(volume, 20) OR close > open) '
           'AND NearZone("demand", 20)')
    _a, _v, pack = _pack(src, data, window=40, debug=True, data_source="broker:fake", run_id=7, screen_name="Range breakout with trend")
    return {**pack, "evidence_id": 11, "pack_sha256": "0" * 64, "can_send": True}


def test_the_frontend_sample_is_the_backend_pack():
    """frontend/src/screener/evidenceSample.json (the vitest fixture) is exactly what the builder makes, so the chart is
    tested on the real wire format. Regenerate with UPDATE_EVIDENCE_SAMPLE=1 pytest tests/test_u8_evidence.py."""
    import os
    text = json.dumps(_sample_pack(), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    if os.environ.get("UPDATE_EVIDENCE_SAMPLE"):
        SAMPLE.write_text(text, encoding="utf-8")
    assert SAMPLE.read_text(encoding="utf-8") == text
    pack = json.loads(text)
    assert {d["kind"] for d in pack["drawables"]} == {"box", "overlay", "pane", "band"} and any(d["failed"] for d in pack["drawables"])
