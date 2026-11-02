"""M77 SX-3: the study PDF - page engine (Pillow, A4 at 150 dpi) + ATP's own PDF writer (Flate page images, invisible
text layer, footer, "Page n of N") for sections 1 cover, 3 structure, 4 areas, 11 notes, 12 footer.

- every section on / off (parametrised); a section without a source prints "Not available: <reason>";
- the same snapshot gives the same bytes; the colour-vision setting changes only the colours;
- the information-only footer and "Page n of N" on every page; the PDF stays under its bound (plain refusal above);
- his own notes in Devanagari are drawn (not the missing-glyph box); no advice wording in what the PDF prints;
- the route: 404 while off, his own study only, same bytes twice.
CI: with STUDY_SAMPLE_PDF_DIR set, the sample PDF built from the synthetic fixture is written there (uploaded as a
CI artifact; never committed - docs/design/M77_REVIEW.md G5).
"""
import hashlib
import os
import re
import zlib
from pathlib import Path

import pytest

from app.study import fonts
from app.study import pages as P
from app.study import pdf as PDF
from app.study import snapshot as S
from tests.test_m77_sx2_studies import BODY, _inputs, _member, client, served  # noqa: F401 - the app client and the served fixture

ADVICE = re.compile(r"\b(buy|sell|target|stop[- ]loss|entry|recommend\w*|profit)\b", re.I)
NOTES = "Watch the area.\n\u0915\u093e\u0932 \u092a\u093e\u0939\u093f\u0932\u0947\u0932\u093e area"


def _snap(**over):
    return S.assemble(_inputs(**over))


def _text(snap) -> list[str]:
    return P.all_text(P.render(snap, S.fingerprint(S.canonical(snap))))


def _pages(data: bytes) -> int:
    return int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", data).group(1))


def test_same_snapshot_same_bytes_and_a_valid_pdf_shape():
    snap = _snap(notes=NOTES)
    a, b = PDF.study_pdf(snap), PDF.study_pdf(snap)
    assert a == b and a.startswith(b"%PDF-1.4") and a.rstrip().endswith(b"%%EOF")
    assert b"CreationDate" not in a and b"Producer" not in a                     # nothing that changes per build
    assert PDF.study_pdf(snap, cvd=True) != a                                     # his colour-vision setting: other colours
    n = _pages(a)
    assert n >= 1 and a.count(b"/Subtype /Image") == n and a.count(b"/Predictor 15") == n
    # the xref offsets point at their objects
    xref = int(re.search(rb"startxref\n(\d+)", a).group(1))
    offsets = [int(x) for x in re.findall(rb"(\d{10}) 00000 n", a[xref:])]
    assert all(re.match(rb"\d+ 0 obj", a[o:o + 12]) for o in offsets)


def test_page_images_are_lossless_a4_at_150_dpi():
    snap = _snap()
    rendered = P.render(snap, "f" * 64)
    data = PDF.build(rendered)
    m = re.search(rb"/Width (\d+) /Height (\d+) .*?/Length (\d+) >>\nstream\n", data, re.S)
    assert (int(m.group(1)), int(m.group(2))) == (P.PAGE_W, P.PAGE_H)
    raw = zlib.decompress(data[m.end():m.end() + int(m.group(3))])
    assert len(raw) == P.PAGE_H * (1 + 3 * P.PAGE_W)                              # one filter byte + RGB per row: no JPEG
    assert b"/DCTDecode" not in data


@pytest.mark.parametrize("sections", [[s] for s in S.SECTIONS] + [list(S.SECTIONS)])
def test_every_section_on_and_off(sections):
    snap = _snap(sections=sections)
    joined = " ".join(_text(snap))
    marker = {"cover": "Study fingerprint", "charts": P.CHART_CAPTION, "structure": "Nearest area above", "areas": "Last visit",
              "notes": "Your checklist rule", **{k: f"Not available: {v}." for k, v in S.NOT_BUILT.items()}}
    for s in S.SECTIONS:
        assert (marker[s] in joined) == (s in sections), (s, sections)
    assert PDF.study_pdf(snap)


def test_footer_and_page_numbers_on_every_page():
    snap = _snap(notes="\n".join(f"Line {i} of my own long notes about the structure" for i in range(120)))
    rendered = P.render(snap, "f" * 64)
    n = len(rendered)
    assert n >= 3
    for i, page in enumerate(rendered, start=1):
        lines = [t for *_, t in page.text]
        assert S.INFO_LINE in lines and f"Page {i} of {n}" in lines
        assert any(t.startswith("Data sources") for t in lines)


def test_missing_sources_print_not_available_never_blank():
    import pandas as pd

    from app.charts import price_action_layer as pal
    empty = pd.DataFrame(columns=pal.FRAME_COLS)
    snap = _snap(frames={"15m": (empty, None), "1h": (empty, None)}, zones=S.ZoneRead(reason=S.AREAS_OFF), daily_context=None,
                 notes=None, checklist_rules=[])
    joined = " ".join(_text(snap))
    assert f"Not available: {S.AREAS_OFF}." in joined
    assert "not available (No closed bars for this chart yet.)" in joined
    assert "not available (no live price from your broker session)" in joined
    assert "No notes." in joined
    assert "Not available: 15-minute chart - No closed bars for this chart yet." in joined     # SX-4: each chart says so
    assert "Not available: 1-hour chart - No closed bars for this chart yet." in joined
    for s in S.NOT_BUILT:
        assert f"Not available: {S.NOT_BUILT[s]}." in joined


def test_devanagari_notes_are_drawn_with_the_bundled_face():
    assert fonts.DEVANAGARI.is_file() and fonts.DEVANAGARI_LICENCE.is_file()
    assert hashlib.sha256(fonts.DEVANAGARI.read_bytes()).hexdigest() == fonts.DEVANAGARI_SHA256
    assert "SIL OPEN FONT LICENSE Version 1.1" in fonts.DEVANAGARI_LICENCE.read_text(encoding="utf-8")
    word = "\u0915\u093e\u0932"
    missing = fonts.regular(24).getmask(word)
    drawn = fonts.devanagari(24).getmask(word)
    assert bytes(drawn) != bytes(missing) and any(bytes(drawn))
    assert P._runs("ab \u0915\u093e\u0932 cd") == [("ab ", False), ("\u0915\u093e\u0932 ", True), ("cd", False)]


def test_numbers_are_indian_and_plain_structure_words_only():
    snap = _snap(notes=None, title=None, checklist_rules=[])
    texts = _text(snap)
    assert not [t for t in texts if ADVICE.search(t)]
    band = next(t for t in texts if " – " in t and t[0].isdigit())
    assert re.fullmatch(r"[\d,]+\.\d\d – [\d,]+\.\d\d", band)
    assert P.ist("2026-03-24T04:00:00+00:00") == "24 Mar 2026, 09:30 IST" and P.ist(None) == "not available"


def test_size_bound_refuses_with_a_plain_message():
    with pytest.raises(PDF.PdfTooLarge) as exc:
        PDF.study_pdf(_snap(), limit=1024 * 1024 // 64)
    assert "Save the study again with fewer sections or shorter notes." in str(exc.value)
    assert len(PDF.study_pdf(_snap(notes="x " * 2500))) < 8 * 1024 * 1024


def test_sample_pdf_for_the_ci_artifact():
    data = PDF.study_pdf(_snap(notes=NOTES))
    out = os.environ.get("STUDY_SAMPLE_PDF_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "study-sample.pdf").write_bytes(data)
    assert data


def test_pdf_route_own_study_only_same_bytes(served):  # noqa: F811 - the fixture
    a_headers, _, _ = _member("pdf-a")
    b_headers, _, _ = _member("pdf-b")
    sid = client.post("/api/studies", json={**BODY, "notes": NOTES}, headers=a_headers).json()["id"]
    first = client.get(f"/api/studies/{sid}/pdf", headers=a_headers)
    second = client.get(f"/api/studies/{sid}/pdf", headers=a_headers)
    assert first.status_code == 200 and first.headers["content-type"] == "application/pdf"
    assert first.content == second.content and first.content.startswith(b"%PDF")
    assert first.headers["content-disposition"].startswith('attachment; filename="study-nifty-bank-')
    assert client.get(f"/api/studies/{sid}/pdf", params={"cvd": "true"}, headers=a_headers).content != first.content
    assert client.get(f"/api/studies/{sid}/pdf", headers=b_headers).status_code == 404


def test_pdf_route_is_classified_for_tenancy():
    from tests.tenancy_route_table import ROUTE_TABLE
    assert ROUTE_TABLE[("GET", "/api/studies/{study_id}/pdf")].startswith("member")
