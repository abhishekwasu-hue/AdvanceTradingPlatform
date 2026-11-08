"""M88-R S5: the report as a PDF - the M77 study writer (`app.study.pages.Layout`, its print palette and
`app.study.pdf.build`: A4 page images with an invisible text layer). Rebuilt from the stored report on download (the
same report gives the same bytes); every page carries the information line in its footer.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image, ImageDraw

from app.study import pages as P
from app.study import pdf as study_pdf
from app.study.numfmt import NOT_AVAILABLE
from app.study.palette import palette

DIRECTION = {"up": "Up", "up (weak)": "Up, weakening", "down": "Down", "down (weak)": "Down, weakening", "range": "Range",
             "none": "Not formed"}
TITLES = {"index_read": "NIFTY index read", "breadth_read": "Breadth", "sectors_read": "Sector strength",
          "leaders_laggards": "Leaders and laggards", "notable_structures": "Notable structures", "conclusion": "Conclusion"}


def _num(x: Any, n: int = 2, sign: bool = False) -> str:
    if x is None:
        return NOT_AVAILABLE
    text = f"{float(x):,.{n}f}"
    return f"+{text}" if sign and float(x) > 0 else text


def direction(s: Mapping[str, Any] | None) -> str:
    return DIRECTION.get(str((s or {}).get("direction") or "none"), str((s or {}).get("direction")))


def _render(pages: Sequence[P.Page], pal: Any, foot: str) -> list[P.Rendered]:
    out: list[P.Rendered] = []
    n = len(pages)
    for i, page in enumerate(pages, start=1):
        img = Image.new("RGB", (P.PAGE_W, P.PAGE_H), pal.background)
        draw = ImageDraw.Draw(img)
        text: list[tuple[float, float, int, str]] = []
        ops = list(page.ops)
        ops.append(P.Op("rule", (P.MARGIN, P.FOOTER_TOP, P.PAGE_W - P.MARGIN, P.FOOTER_TOP + 1), pal.border))
        number = f"Page {i} of {n}"
        fy = float(P.FOOTER_TOP + 14)
        for line in P.wrap(foot, P.SIZE_SMALL, P.PAGE_W - 2 * P.MARGIN - P.width(number, P.SIZE_SMALL) - 24):
            ops.append(P.Op("text", (P.MARGIN, fy, P.SIZE_SMALL, 0), pal.text, line))
            fy += P.SIZE_SMALL * P.LINE_GAP
        ops.append(P.Op("text", (P.PAGE_W - P.MARGIN, P.FOOTER_TOP + 14, P.SIZE_SMALL, 0), pal.text_muted, number, align="right"))
        for op in ops:
            if op.kind == "text":
                P._draw_text(draw, op)  # noqa: SLF001 - the study writer's own text drawing
                x, y, size, _ = op.box
                text.append((x - P.width(op.text, int(size)) if op.align == "right" else x, y, int(size), op.text))
            else:
                x0, y0, x1, y1 = op.box
                draw.rectangle((math.floor(x0), math.floor(y0), math.ceil(x1) - 1, math.ceil(y1) - 1), fill=op.colour, outline=op.outline)
        out.append(P.Rendered(img, text))
    return out


def report_pdf(report: Mapping[str, Any]) -> bytes:
    pal = palette()
    lay = P.Layout(pal)
    day = str(report.get("trade_date") or "")
    sections = report.get("sections") or {}
    lay.title(f"Daily market report · last close {day}")
    lay.para(str(report.get("info_line") or ""), colour=pal.text_muted)
    for idx in report.get("index") or []:
        if idx.get("missing"):
            continue
        lay.heading(f"{idx['symbol']}")
        lay.kv([("Close", _num(idx.get("close"))), ("Change", f"{_num(idx.get('change_pct'), sign=True)} %"),
                ("Daily structure", direction(idx.get("structure_d"))), ("Weekly structure", direction(idx.get("structure_w"))),
                ("Areas", "; ".join(f"{a['kind'].capitalize()} {_num(a['low'])} - {_num(a['high'])} ({a['where']})"
                                    for a in idx.get("areas") or []) or NOT_AVAILABLE),
                ("Futures OI change", f"{_num(idx.get('fut_oi_change_pct'), sign=True)} %" if idx.get("fut_oi_change_pct") is not None else NOT_AVAILABLE),
                ("PCR", _num(idx.get("pcr")))])
        lay.end_section()
    for key in ("index_read", "breadth_read", "sectors_read"):
        lay.heading(TITLES[key])
        lay.para(str(sections.get(key) or NOT_AVAILABLE))
        if key == "breadth_read":
            b = report.get("breadth") or {}
            lay.kv([("Advances / declines", f"{b.get('advances', 0)} / {b.get('declines', 0)}"),
                    ("At a new recent high", f"{b.get('new_recent_high', 0)} ({_num(b.get('new_recent_high_pct'), 1)} %)"),
                    ("At a new recent low", f"{b.get('new_recent_low', 0)} ({_num(b.get('new_recent_low_pct'), 1)} %)")])
        if key == "sectors_read" and report.get("sectors"):
            lay.table([("Sector", 0.5, "left"), ("Stocks", 0.15, "right"), ("Avg change %", 0.2, "right"), ("Up / down", 0.15, "right")],
                      [[s["sector"], str(s["stocks"]), _num(s.get("avg_change_pct"), sign=True), f"{s['advances']} / {s['declines']}"]
                       for s in report["sectors"]])
        lay.end_section()
    lay.heading("Stocks")
    rows = [[s["symbol"], _num(s.get("change_pct"), sign=True), _num(s.get("volume_ratio")), f"{direction(s.get('structure_d'))} / {direction(s.get('structure_w'))}",
             _num(s.get("fut_oi_change_pct"), sign=True), str(s.get("read") or ("No close stored" if s.get("missing") else NOT_AVAILABLE))]
            for s in report.get("stocks") or []]
    lay.table([("Symbol", 0.14, "left"), ("Chg %", 0.09, "right"), ("Vol x avg", 0.1, "right"), ("Structure D / W", 0.2, "left"),
               ("OI chg %", 0.1, "right"), ("Read", 0.37, "left")], rows)
    lay.end_section()
    for key in ("leaders_laggards", "notable_structures", "conclusion"):
        lay.heading(TITLES[key])
        lay.para(str(sections.get(key) or NOT_AVAILABLE))
        lay.end_section()
    lay.pages[-1].end = lay.y
    return study_pdf.build(_render(lay.pages, pal, str(report.get("info_line") or "")), title=f"Daily market report {day}")
