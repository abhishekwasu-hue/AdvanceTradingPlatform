"""M77 SX-4: the study chart renderer (PD-2.2), "Save as image" of one chart and the phone single-column PNG.

- band labels: the same placement as the Charts tab (shared fixture with frontend placeBandLabels), never on the price
  axis inset, never over another label (bands, swings, events);
- closed bars only: a chart draws exactly the snapshot's bars (no bar ending after the study time);
- every drawing kind of `drawing/1` is drawn at its anchors;
- PDF: one chart picture per timeframe; a timeframe without bars says "Not available";
- PNG: same snapshot = same bytes, no time chunk, bounded; the route: 404 while off, his own study only, a timeframe
  the study does not hold -> 404 with a plain sentence.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image

from app.charts import drawings as DR
from app.study import chart as C
from app.study import pages as P
from app.study import png as PNG
from app.study import snapshot as S
from app.study.palette import palette
from tests.test_m77_sx2_studies import BODY, _flags, _inputs, _member, client, served  # noqa: F401 - the app client and the served fixture

FIXTURE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "charting" / "bandLabelsFixture.json"


def _snap(**over):
    return S.assemble(_inputs(**over))


def _all_drawings(bars):
    iso = lambda i: datetime.fromtimestamp(bars[i][0], tz=UTC).isoformat()   # noqa: E731
    lo, hi = min(b[3] for b in bars), max(b[2] for b in bars)
    mid = (lo + hi) / 2
    tp = lambda i, p: {"t": iso(i), "p": p}                                     # noqa: E731
    return [
        {"kind": "trendline", "anchors": [tp(5, lo), tp(50, mid)], "style": {"color": "#7b1fa2"}},
        {"kind": "ray", "anchors": [tp(10, mid), tp(20, hi)], "style": {}},
        {"kind": "measure", "anchors": [tp(30, lo), tp(60, mid)], "style": {}},
        {"kind": "rectangle", "anchors": [tp(70, mid), tp(90, hi)], "style": {}},
        {"kind": "fib_retracement", "anchors": [tp(100, lo), tp(140, hi)], "style": {"color": "#ef6c00"}},
        {"kind": "fib_extension", "anchors": [tp(100, lo), tp(140, hi), tp(160, mid)], "style": {}},
        {"kind": "channel", "anchors": [tp(150, lo), tp(200, mid), tp(160, mid)], "style": {}},
        {"kind": "hline", "anchors": [{"p": mid}], "style": {"line": "dashed"}},
        {"kind": "vline", "anchors": [{"t": iso(250)}], "style": {"line": "dotted"}},
        {"kind": "text", "anchors": [tp(260, hi)], "text": "my own words", "style": {}},
        {"kind": "long_position", "anchors": [tp(300, mid), {"p": mid - 100}, {"p": mid + 200}], "style": {}},
        {"kind": "short_position", "anchors": [tp(320, mid), {"p": mid + 100}, {"p": mid - 200}], "style": {}},
    ]


def test_label_placement_matches_the_charts_tab_fixture():
    for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]:
        avoid = [C.Rect(**a) for a in case["avoid"]]
        got = C.place_band_labels(case["items"], case["width"], case["height"], avoid=avoid, font_px=case["fontPx"])
        assert [{"index": i, "x": r.x, "y": r.y, "w": r.w, "h": r.h} for r, i in got] == case["expected"], case["name"]


def test_every_drawing_kind_is_drawn():
    assert set(DR.ANCHORS) == {d["kind"] for d in _all_drawings([[0, 1, 2, 0.5, 1]] * 400)}
    snap = _snap()
    parts = P.chart_inputs(snap, "15m")
    drawings = _all_drawings(parts["chart"]["bars"])
    _, info = C.render_chart(parts["chart"], areas=parts["areas"], drawings=drawings, protected=parts["protected"],
                             reference=parts["reference"], pal=palette(), width=1048, height=620)
    assert sorted(info.drawings) == sorted(DR.ANCHORS)
    # an anchor that cannot be placed (no time on a trendline) is skipped, never guessed
    _, info = C.render_chart(parts["chart"], areas=[], drawings=[{"kind": "trendline", "anchors": [{"p": 1}, {"p": 2}]}],
                             protected=None, reference=None, pal=palette(), width=600, height=300)
    assert info.drawings == []


@pytest.mark.parametrize("size", [(1048, 620), (1016, 760), (600, 320)])
def test_no_label_on_the_axis_or_over_another_label(size):
    snap = _snap()
    for tf in snap["timeframes"]:
        parts = P.chart_inputs(snap, tf)
        _, info = C.render_chart(parts["chart"], areas=parts["areas"], drawings=parts["drawings"], protected=parts["protected"],
                                 reference=parts["reference"], pal=palette(), width=size[0], height=size[1])
        assert info.labels, tf
        rects = [r for r, _ in info.labels]
        for i, a in enumerate(rects):
            assert a.x >= 0 and a.x + a.w <= info.pane.w and a.y >= 0 and a.y + a.h <= info.pane.h   # inside the pane, off the axis
            assert not any(C.overlaps(a, b) for b in rects[i + 1:])
        texts = [t for _, t in info.labels]
        assert any(t.startswith("S1 · Seller area · 15m") for t in texts)                         # each area with its own timeframe


def test_closed_bars_only():
    snap = _snap()
    created = datetime.fromisoformat(snap["created_at"]).timestamp()
    for chart in snap["data"]["charts"]:
        assert chart["bars"] and datetime.fromisoformat(chart["as_of"]).timestamp() <= created
        parts = P.chart_inputs(snap, chart["tf"])
        _, info = C.render_chart(parts["chart"], areas=[], drawings=[], protected=None, reference=None, pal=palette(), width=800, height=400)
        assert info.candles == len(chart["bars"])


def test_pdf_holds_one_chart_picture_per_timeframe():
    snap = _snap(sections=["charts"])
    pages, _ = P.layout(snap, "f" * 64)
    images = [op for p in pages for op in p.ops if op.kind == "image"]
    assert len(images) == len(snap["timeframes"]) == 2
    text = " ".join(op.text for p in pages for op in p.ops if op.kind == "text")
    assert "15-minute chart - closed bars up to" in text and "1-hour chart - closed bars up to" in text
    empty = pd.DataFrame(columns=["timestamp", "bar_end", "open", "high", "low", "close", "volume"])
    gone = _snap(sections=["charts"], frames={"15m": (empty, None), "1h": (empty, None)})
    pages, _ = P.layout(gone, "f" * 64)
    assert not [op for p in pages for op in p.ops if op.kind == "image"]


def test_pngs_are_deterministic_bounded_and_carry_no_time():
    snap = _snap()
    one, column = PNG.chart_png(snap, "15m"), PNG.study_png(snap)
    assert one == PNG.chart_png(snap, "15m") and column == PNG.study_png(snap)
    assert PNG.chart_png(snap, "15m", cvd=True) != one
    for data in (one, column):
        assert data.startswith(b"\x89PNG") and b"tIME" not in data and b"tEXt" not in data and len(data) < 4 * 1024 * 1024
    import io
    assert Image.open(io.BytesIO(one)).size[0] == PNG.ONE_W + 2 * PNG.PAD
    assert Image.open(io.BytesIO(column)).size[0] == PNG.PHONE_W                                 # one phone-width column
    with pytest.raises(PNG.NoSuchChart):
        PNG.chart_png(snap, "5m")
    with pytest.raises(PNG.NoSuchChart):
        PNG.study_png(_snap(sections=["notes"]))
    with pytest.raises(PNG.PngTooLarge):
        PNG.study_png(snap, limit=1024)


def test_png_route(served):  # noqa: F811 - the fixture
    headers, _, _ = _member("png")
    _flags(ui_v2=True, study_export=True)
    sid = client.post("/api/studies", json=BODY, headers=headers).json()["id"]
    a = client.get(f"/api/studies/{sid}/png", headers=headers)
    assert a.status_code == 200 and a.headers["content-type"] == "image/png" and a.content.startswith(b"\x89PNG")
    assert client.get(f"/api/studies/{sid}/png", headers=headers).content == a.content
    one = client.get(f"/api/studies/{sid}/png?tf=15m", headers=headers)
    assert one.status_code == 200 and "-15m.png" in one.headers["content-disposition"]
    missing = client.get(f"/api/studies/{sid}/png?tf=5m", headers=headers)
    assert missing.status_code == 404 and "no chart for that timeframe" in missing.json()["detail"]
    other, _, _ = _member("png-other")
    assert client.get(f"/api/studies/{sid}/png", headers=other).status_code == 404               # never another member's
    _flags(ui_v2=True, study_export=False)
    assert client.get(f"/api/studies/{sid}/png", headers=headers).status_code == 404
