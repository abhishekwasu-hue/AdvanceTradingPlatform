"""M77 SX-3: the page engine - a stored study snapshot -> A4 portrait pages drawn with Pillow (150 dpi, light,
print-safe), each with the information-only footer and "Page n of N".

* Sections drawn here: 1 cover, 2 charts (SX-4: one full-width chart per timeframe studied, app/study/chart.py), 3
  structure, 4 areas, 11 notes and checklist, 12 the footer on every page. Every other ticked section prints its title
  and one line "Not available: <plain reason>" (AI card, option context, trade plan, pre-trade check, scenarios,
  similar setups: their sources are not saved with a study yet).
* Every number comes from the snapshot (the server's own reads, app/study/snapshot.py) through the one Indian number
  formatter (app/study/numfmt.py); a missing value prints "not available", never a blank, a zero or a guess.
* Colours are the app's light tokens (app/study/palette.py): seller rows in the `down` tint, buyer rows in the `up`
  tint; `cvd` = his colour-vision setting. Text in the embedded Inter face; his own notes in Devanagari use the embedded
  Noto Sans Devanagari face (raqm shaping). Labels are English; his notes are his words, verbatim.
* Pure and deterministic: the same snapshot (and the same `cvd`) gives the same page pixels; nothing reads a clock.
Each page also returns its text lines and their positions, which app/study/pdf.py writes as an invisible text layer
(search / copy in a PDF viewer).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.charts.read_chart import TF_WORDS
from app.study import fonts
from app.study import snapshot as S
from app.study.chart import render_chart
from app.study.numfmt import NOT_AVAILABLE, format_number
from app.study.palette import Palette, Rgb, palette

DPI = 150
PAGE_W, PAGE_H = 1240, 1754                  # A4 portrait at 150 dpi (210 x 297 mm)
MARGIN = 96
FOOTER_TOP = PAGE_H - 150                    # the footer band: a rule, the information line, the sources, the page number
BODY_BOTTOM = FOOTER_TOP - 24
SIZE_TITLE, SIZE_HEAD, SIZE_BODY, SIZE_TABLE, SIZE_SMALL = 38, 28, 20, 18, 15
LINE_GAP = 1.38                              # line height as a multiple of the font size
CELL_PAD = 8
IST = timezone(timedelta(hours=5, minutes=30))
CHARTS_LATER = "the chart pictures are added to the study PDF in a later version"   # SX-3 wording, kept (SX-4 draws them)
CHART_CAPTION = "chart - closed bars up to"
ROLE_WORDS = {"supply": "Seller", "demand": "Buyer"}
DIRECTION_WORDS = {"up": "Up", "up (weak)": "Up, weakening", "down": "Down", "down (weak)": "Down, weakening", "range": "Range",
                   "none": "Not clear"}


# ---------------------------------------------------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------------------------------------------------
def ist(value: Any) -> str:
    """An ISO time (or epoch seconds) as "01 Nov 2026, 14:05 IST"; a date alone as "02 Jan 2026"; else "not available"."""
    if value is None or value == "":
        return NOT_AVAILABLE
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            when = datetime.fromtimestamp(float(value), tz=UTC)
        else:
            text = str(value)
            if len(text) == 10:
                return datetime.fromisoformat(text).strftime("%d %b %Y")
            when = datetime.fromisoformat(text)
            when = when if when.tzinfo else when.replace(tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        return NOT_AVAILABLE
    return when.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def price(value: Any) -> str:
    return format_number(value if isinstance(value, (int, float)) and not isinstance(value, bool) else None, 2)


def _runs(text: str) -> list[tuple[str, bool]]:
    """`text` split into runs of (Devanagari or not); a space stays with the run it is in."""
    out: list[tuple[str, bool]] = []
    for ch in text:
        dev = fonts.is_devanagari(ch) if not ch.isspace() else (out[-1][1] if out else False)
        if out and out[-1][1] == dev:
            out[-1] = (out[-1][0] + ch, dev)
        else:
            out.append((ch, dev))
    return out


def _font(size: int, dev: bool) -> ImageFont.FreeTypeFont:
    return fonts.devanagari(size) if dev else fonts.regular(size)


def width(text: str, size: int) -> float:
    return sum(_font(size, dev).getlength(run) for run, dev in _runs(text))


def wrap(text: str, size: int, max_w: float) -> list[str]:
    """Lines of `text` no wider than `max_w` (his own line breaks kept; a word longer than a line is cut)."""
    lines: list[str] = []
    for para in str(text).split("\n"):
        words = para.split(" ")
        line = ""
        for word in words:
            trial = word if not line else f"{line} {word}"
            if width(trial, size) <= max_w:
                line = trial
                continue
            if line:
                lines.append(line)
            while width(word, size) > max_w and len(word) > 1:
                cut = len(word)
                while cut > 1 and width(word[:cut], size) > max_w:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------------------------------------------------
# Draw operations and the layout
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Op:
    kind: str                                   # text | rect | rule | image
    box: tuple[float, float, float, float]      # text: (x, y, size, 0); rect / rule: (x0, y0, x1, y1)
    colour: Rgb
    text: str = ""
    outline: Rgb | None = None
    align: str = "left"                         # text: left | right (x is then the right edge)
    image: Any = None                           # image: the picture placed at (x0, y0)


@dataclass
class Page:
    ops: list[Op] = field(default_factory=list)


class Layout:
    """Places blocks top to bottom; a block that does not fit starts a new page (a table repeats its header)."""

    def __init__(self, pal: Palette) -> None:
        self.pal = pal
        self.pages: list[Page] = [Page()]
        self.y = float(MARGIN)

    @property
    def ops(self) -> list[Op]:
        return self.pages[-1].ops

    def new_page(self) -> None:
        self.pages.append(Page())
        self.y = float(MARGIN)

    def room(self, h: float) -> None:
        if self.y + h > BODY_BOTTOM and self.y > MARGIN:
            self.new_page()

    def text_line(self, x: float, text: str, size: int, colour: Rgb, align: str = "left") -> None:
        self.ops.append(Op("text", (x, self.y, size, 0), colour, text, align=align))

    def gap(self, h: float) -> None:
        self.y += h

    def title(self, text: str) -> None:
        self.room(SIZE_TITLE * LINE_GAP * 2)
        for line in wrap(text, SIZE_TITLE, PAGE_W - 2 * MARGIN):
            self.text_line(MARGIN, line, SIZE_TITLE, self.pal.text)
            self.y += SIZE_TITLE * LINE_GAP
        self.gap(8)

    def heading(self, text: str) -> None:
        self.room(SIZE_HEAD * LINE_GAP + SIZE_BODY * LINE_GAP * 3)          # never a heading alone at a page's foot
        self.gap(18)
        self.text_line(MARGIN, text, SIZE_HEAD, self.pal.text)
        self.y += SIZE_HEAD * LINE_GAP
        self.ops.append(Op("rule", (MARGIN, self.y - 6, PAGE_W - MARGIN, self.y - 4), self.pal.border))
        self.gap(10)

    def para(self, text: str, size: int = SIZE_BODY, colour: Rgb | None = None, indent: float = 0) -> None:
        for line in wrap(text, size, PAGE_W - 2 * MARGIN - indent):
            self.room(size * LINE_GAP)
            self.text_line(MARGIN + indent, line, size, colour or self.pal.text)
            self.y += size * LINE_GAP

    def not_available(self, reason: str) -> None:
        self.para(f"Not available: {reason}.", colour=self.pal.text_muted)

    def table(self, columns: Sequence[tuple[str, float, str]], rows: Sequence[Sequence[str]],
              tints: Sequence[Rgb | None] | None = None, size: int = SIZE_TABLE) -> None:
        """`columns`: (title, share of the width, "left" / "right"). Each cell wraps; a row never splits."""
        total = PAGE_W - 2 * MARGIN
        widths = [total * share for _, share, _ in columns]
        xs = [MARGIN + sum(widths[:i]) for i in range(len(columns))]
        line_h = size * LINE_GAP

        def cells(values: Sequence[str]) -> list[list[str]]:
            return [wrap(v, size, w - 2 * CELL_PAD) for v, w in zip(values, widths, strict=True)]

        def draw_row(values: Sequence[str], fill: Rgb | None, colour: Rgb) -> None:
            wrapped = cells(values)
            h = max(len(c) for c in wrapped) * line_h + 2 * CELL_PAD
            if fill is not None:
                self.ops.append(Op("rect", (MARGIN, self.y, MARGIN + total, self.y + h), fill))
            for (_, _, align), x, w, lines in zip(columns, xs, widths, wrapped, strict=True):
                for k, line in enumerate(lines):
                    tx = x + w - CELL_PAD if align == "right" else x + CELL_PAD
                    self.ops.append(Op("text", (tx, self.y + CELL_PAD + k * line_h, size, 0), colour, line, align=align))
            self.ops.append(Op("rule", (MARGIN, self.y + h - 1, MARGIN + total, self.y + h), self.pal.border))
            self.y += h

        def row_height(values: Sequence[str]) -> float:
            return max(len(c) for c in cells(values)) * line_h + 2 * CELL_PAD

        header = [t for t, _, _ in columns]
        self.room(row_height(header) + (row_height(rows[0]) if rows else 0))
        draw_row(header, self.pal.surface, self.pal.text_muted)
        for i, row in enumerate(rows):
            if self.y + row_height(row) > BODY_BOTTOM:
                self.new_page()
                draw_row(header, self.pal.surface, self.pal.text_muted)
            draw_row(row, (tints[i] if tints else None), self.pal.text)
        self.gap(10)

    def picture(self, img: Image.Image) -> None:
        """A picture (a chart) full width; it never splits across pages."""
        self.room(img.height + 10)
        self.ops.append(Op("image", (MARGIN, self.y, MARGIN + img.width, self.y + img.height), self.pal.border, image=img))
        self.y += img.height + 10

    def kv(self, rows: Sequence[tuple[str, str]]) -> None:
        self.table([("Item", 0.32, "left"), ("Value", 0.68, "left")], [list(r) for r in rows], size=SIZE_BODY)


# ---------------------------------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------------------------------
def _live(cover: Mapping[str, Any]) -> str:
    live = cover.get("live_price") or {}
    if live.get("value") is None:
        return f"{NOT_AVAILABLE} ({live.get('reason') or 'no live price'})"
    return f"{price(live['value'])} at {ist(live.get('as_of'))}"


def _reference(ref: Mapping[str, Any]) -> str:
    if not ref or ref.get("value") is None:
        return NOT_AVAILABLE
    return f"{price(ref['value'])} ({ref.get('source') or 'source not available'}, {ist(ref.get('as_of'))})"


def cover(lay: Layout, snap: Mapping[str, Any], fp: str) -> None:
    inst = snap.get("instrument") or {}
    data = (snap.get("data") or {}).get("cover") or {}
    lay.title(f"Study: {inst.get('symbol') or NOT_AVAILABLE}")
    if snap.get("title"):
        lay.para(str(snap["title"]), colour=lay.pal.text_muted)
        lay.gap(6)
    tf_words = [TF_WORDS.get(tf, tf) for tf in snap.get("timeframes") or []]
    lay.kv([("Symbol", str(inst.get("symbol") or NOT_AVAILABLE)), ("Exchange", str(inst.get("exchange") or NOT_AVAILABLE)),
            ("Instrument", str(inst.get("kind") or NOT_AVAILABLE)), ("Timeframes studied", ", ".join(tf_words) or NOT_AVAILABLE),
            ("Live price", _live(data)), ("Distances measured from", _reference(data.get("reference_price") or {})),
            ("Study time", ist(snap.get("created_at"))), ("App version", str(snap.get("app_version") or NOT_AVAILABLE)),
            ("Reading source", str(snap.get("reading_source") or NOT_AVAILABLE)), ("Study fingerprint", fp[:12])])
    sources = (snap.get("footer") or {}).get("sources") or []
    lay.para("Data as of, per source", size=SIZE_BODY, colour=lay.pal.text_muted)
    lay.gap(4)
    lay.table([("Source", 0.30, "left"), ("From", 0.40, "left"), ("As of", 0.30, "left")],
              [[str(s.get("name")), str(s.get("words") or NOT_AVAILABLE), ist(s.get("as_of"))] for s in sources])


def _area_ref(near: Mapping[str, Any] | None) -> str:
    if not near:
        return "none on this timeframe"
    return f"{near['label']} {price(near.get('low'))} – {price(near.get('high'))} ({format_number(near.get('distance_points'), 2)} pts)"


def structure(lay: Layout, snap: Mapping[str, Any]) -> None:
    lay.heading(S.SECTION_TITLES["structure"])
    data = (snap.get("data") or {}).get("structure") or {}
    rows, notes = [], []
    for r in data.get("timeframes") or []:
        if not r.get("available"):
            rows.append([str(r.get("tf_words")), f"{NOT_AVAILABLE} ({r.get('reason')})", "", "", "", ""])
            continue
        last = r.get("last_event") or {}
        event = NOT_AVAILABLE if not last else f"{last.get('words', '').capitalize()} {last.get('direction') or ''}".strip()
        if last:
            event += f" at {price(last.get('level'))}"
        prot = r.get("protected") or {}
        near = r.get("nearest") or {}
        rows.append([str(r.get("tf_words")), DIRECTION_WORDS.get(str(r.get("direction")), str(r.get("direction"))), event,
                     f"{price(prot.get('price'))} ({prot.get('side')})" if prot else NOT_AVAILABLE,
                     _area_ref(near.get("above")), _area_ref(near.get("below"))])
        if r.get("commitment"):
            notes.append(f"{r.get('tf_words')}: {r['commitment'].get('words')}")
    if not rows:
        lay.not_available("no timeframe was studied")
        return
    lay.table([("Timeframe", 0.13, "left"), ("Trend", 0.13, "left"), ("Last event", 0.20, "left"), ("Protected level", 0.16, "left"),
               ("Nearest area above", 0.19, "left"), ("Nearest area below", 0.19, "left")], rows)
    ref = data.get("reference_price") or {}
    lay.para(f"Distances in points from {_reference(ref)}.", size=SIZE_SMALL, colour=lay.pal.text_muted)
    daily = data.get("daily_trend") or {}
    lay.para(str(daily.get("words") or "The Daily trend is not available for this symbol yet."), size=SIZE_BODY)
    for line in notes:
        lay.para(line, size=SIZE_BODY)


def areas(lay: Layout, snap: Mapping[str, Any]) -> None:
    lay.heading(S.SECTION_TITLES["areas"])
    data = (snap.get("data") or {}).get("areas") or {}
    rows = data.get("rows") or []
    if not rows:
        lay.not_available(str(data.get("reason") or S.NO_AREAS))
        return
    pal = lay.pal
    lay.table([("Area", 0.07, "left"), ("TF", 0.11, "left"), ("Role", 0.09, "left"), ("Band", 0.20, "left"), ("State", 0.10, "left"),
               ("Tests", 0.07, "right"), ("Formed", 0.13, "left"), ("Last visit", 0.13, "left"), ("Distance", 0.10, "right")],
              [[str(a["label"]), str(a.get("tf_words")), ROLE_WORDS.get(str(a.get("role")), str(a.get("role"))),
                f"{price(a.get('low'))} – {price(a.get('high'))}", str(a.get("state") or NOT_AVAILABLE).capitalize(),
                format_number(a.get("touches")), ist(a.get("formed_at")), ist(a.get("last_visit")),
                format_number(a.get("distance_points"), 2)] for a in rows],
              tints=[pal.seller_tint if a.get("role") == "supply" else pal.buyer_tint for a in rows], size=SIZE_SMALL)
    lay.para(f"Areas as of {ist(data.get('as_of'))}; distance in points from {_reference(data.get('reference_price') or {})}.",
             size=SIZE_SMALL, colour=pal.text_muted)
    if data.get("settings_note"):
        lay.para(f"Areas from {data['settings_note']}.", size=SIZE_SMALL, colour=pal.text_muted)
    states = sorted({(str(a.get("state")), str(a.get("state_words"))) for a in rows})
    for state, words in states:
        lay.para(f"{state.capitalize()}: {words}.", size=SIZE_SMALL, colour=pal.text_muted)


def notes(lay: Layout, snap: Mapping[str, Any]) -> None:
    lay.heading(S.SECTION_TITLES["notes"])
    data = (snap.get("data") or {}).get("notes") or {}
    text = data.get("text")
    if text:
        lay.para(str(text))
    else:
        lay.para("No notes.", colour=lay.pal.text_muted)
    checklist = data.get("checklist") or []
    if checklist:
        lay.gap(6)
        lay.table([("Your checklist rule", 0.75, "left"), ("Your answer", 0.25, "left")],
                  [[str(c.get("rule")), str(c.get("answer")).capitalize()] for c in checklist])


def _na(value: Any, decimals: int = 2) -> str:
    return format_number(value if isinstance(value, (int, float)) and not isinstance(value, bool) else None, decimals)


def ai_card(lay: Layout, snap: Mapping[str, Any]) -> None:
    """SX-5: the AI explanation card as the workspace showed it, and "How I worked this out"."""
    lay.heading(S.SECTION_TITLES["ai_card"])
    card = (snap.get("data") or {}).get("ai_card")
    if not card:
        lay.not_available(str((snap.get("not_available") or {}).get("ai_card") or "no AI card was saved with this study"))
        return
    lay.para(f"{TF_WORDS.get(str(card.get('timeframe')), str(card.get('timeframe'))).capitalize()} chart", colour=lay.pal.text_muted)
    rows = [[str(line.get("name")), str(line.get("text"))] for line in card.get("lines") or []]
    if rows:
        lay.table([("Part", 0.26, "left"), ("What the card says", 0.74, "left")], rows)
    evidence = card.get("evidence") or []
    if evidence:
        lay.table([("Evidence", 0.74, "left"), ("On the chart", 0.26, "left")],
                  [[str(e.get("name")), "Present" if e.get("present") else "Not present"] for e in evidence])
    how = card.get("how") or {}
    lay.para("How I worked this out", size=SIZE_BODY)
    lay.kv([("Reading as of", ist(how.get("reading_as_of"))), ("Card made", ist(how.get("made_at"))),
            *[("Note", str(n)) for n in how.get("notes") or []]])
    if card.get("disclaimer"):
        lay.para(str(card["disclaimer"]), size=SIZE_SMALL, colour=lay.pal.text_muted)


def option_context(lay: Layout, snap: Mapping[str, Any]) -> None:
    """SX-5: the option chain at the study time, as numbers."""
    lay.heading(S.SECTION_TITLES["option_context"])
    oc = (snap.get("data") or {}).get("option_context")
    if not oc:
        lay.not_available(str((snap.get("not_available") or {}).get("option_context") or "no option chain was saved with this study"))
        return
    lay.kv([("Underlying", str(oc.get("underlying") or NOT_AVAILABLE)), ("Expiry", ist(oc.get("expiry"))),
            ("Trading days to expiry", _na(oc.get("days_to_expiry"), 0)), ("Spot", price(oc.get("spot"))),
            ("At the money strike", price(oc.get("atm_strike"))), ("Put / call ratio (OI)", _na(oc.get("pcr_oi"))),
            ("Max pain", price(oc.get("max_pain"))), ("ATM IV (%)", _na(oc.get("atm_iv"))), ("Chain as of", ist(oc.get("as_of")))])
    walls = oc.get("walls") or []
    if walls:
        lay.table([("Highest open interest near spot", 0.5, "left"), ("Strike", 0.25, "right"), ("Open interest", 0.25, "right")],
                  [["Call side" if w.get("side") == "call" else "Put side", price(w.get("strike")), _na(w.get("oi"), 0)] for w in walls])
    legs = oc.get("atm_legs")
    if legs:
        names = {"oi": "Open interest", "iv": "IV (%)", "delta": "Delta", "gamma": "Gamma", "theta": "Theta", "vega": "Vega"}
        lay.table([("At the money strike", 0.4, "left"), ("Call", 0.3, "right"), ("Put", 0.3, "right")],
                  [[label, _na((legs.get("call") or {}).get(k), 0 if k == "oi" else 4 if k == "gamma" else 2),
                    _na((legs.get("put") or {}).get(k), 0 if k == "oi" else 4 if k == "gamma" else 2)] for k, label in names.items()])
    lay.para(f"Chosen strikes: not available ({oc.get('chosen_reason') or 'no trade plan'}).", colour=lay.pal.text_muted)


CHART_H = 620


def chart_inputs(snap: Mapping[str, Any], tf: str) -> dict[str, Any]:
    """What app/study/chart.py draws for one timeframe of the snapshot: its chart, every area (each labelled with its
    own timeframe), his drawings, that timeframe's protected level and the reference price."""
    data = snap.get("data") or {}
    chart = next((c for c in data.get("charts") or [] if c.get("tf") == tf), {"tf": tf, "bars": [], "reason": R_NO_CHART})
    row: Mapping[str, Any] = next((r for r in (data.get("structure") or {}).get("timeframes") or [] if r.get("tf") == tf), {})
    ref = ((data.get("areas") or {}).get("reference_price") or (data.get("cover") or {}).get("reference_price") or {}).get("value")
    return {"chart": chart, "areas": list((data.get("areas") or {}).get("rows") or []), "drawings": list(data.get("drawings") or []),
            "protected": row.get("protected"), "reference": ref}


R_NO_CHART = "this timeframe has no chart in the study"


def charts(lay: Layout, snap: Mapping[str, Any]) -> None:
    lay.room(SIZE_HEAD * LINE_GAP + 28 + SIZE_BODY * LINE_GAP + CHART_H + 10)    # the heading starts with its first chart
    lay.heading(S.SECTION_TITLES["charts"])
    tfs = list(snap.get("timeframes") or [])
    if not tfs:
        lay.not_available(R_NO_CHART)
        return
    for tf in tfs:
        parts = chart_inputs(snap, tf)
        chart = parts["chart"]
        words = TF_WORDS.get(tf, tf).capitalize()
        if not chart.get("bars"):
            lay.not_available(f"{words} chart - {str(chart.get('reason') or R_NO_CHART).rstrip('.')}")
            continue
        lay.room(SIZE_BODY * LINE_GAP + CHART_H + 10)
        lay.para(f"{words} {CHART_CAPTION} {ist(chart.get('as_of'))}", colour=lay.pal.text_muted)
        img, _ = render_chart(chart, areas=parts["areas"], drawings=parts["drawings"], protected=parts["protected"],
                              reference=parts["reference"], pal=lay.pal, width=PAGE_W - 2 * MARGIN, height=CHART_H)
        lay.picture(img)


# ---------------------------------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------------------------------
def layout(snap: Mapping[str, Any], fp: str, *, cvd: bool = False) -> tuple[list[Page], Palette]:
    pal = palette(cvd=cvd)
    lay = Layout(pal)
    ticked = list(snap.get("sections") or [])
    unavailable = snap.get("not_available") or {}
    if "cover" in ticked:
        cover(lay, snap, fp)
    else:
        inst = snap.get("instrument") or {}
        lay.title(f"Study: {inst.get('symbol') or NOT_AVAILABLE}")
        lay.para(ist(snap.get("created_at")), colour=pal.text_muted)
    for section in ticked:
        if section == "cover":
            continue
        if section == "charts":
            charts(lay, snap)
        elif section == "structure":
            structure(lay, snap)
        elif section == "areas":
            areas(lay, snap)
        elif section == "notes":
            notes(lay, snap)
        elif section == "ai_card":
            ai_card(lay, snap)
        elif section == "option_context":
            option_context(lay, snap)
        else:
            lay.heading(S.SECTION_TITLES.get(section, section))
            lay.not_available(str(unavailable.get(section) or "not part of a saved study yet"))
    return lay.pages, pal


def footer_lines(snap: Mapping[str, Any]) -> list[str]:
    foot = snap.get("footer") or {}
    sources = "; ".join(f"{s.get('name')}: {s.get('words') or NOT_AVAILABLE}" for s in foot.get("sources") or [])
    return [str(foot.get("text") or S.INFO_LINE), f"Data sources - {sources}." if sources else "Data sources: not available."]


def _draw_text(draw: ImageDraw.ImageDraw, op: Op) -> None:
    x, y, size, _ = op.box
    size = int(size)
    if op.align == "right":
        x -= width(op.text, size)
    for run, dev in _runs(op.text):
        f = _font(size, dev)
        draw.text((x, y), run, font=f, fill=op.colour)
        x += f.getlength(run)


@dataclass
class Rendered:
    image: Image.Image
    text: list[tuple[float, float, int, str]]       # (x, y top, size, text) in pixels, for the invisible text layer


def render(snap: Mapping[str, Any], fp: str, *, cvd: bool = False) -> list[Rendered]:
    """Every page of the study as an RGB image, with its text lines."""
    pages, pal = layout(snap, fp, cvd=cvd)
    n = len(pages)
    foot = footer_lines(snap)
    out: list[Rendered] = []
    for i, page in enumerate(pages, start=1):
        img = Image.new("RGB", (PAGE_W, PAGE_H), pal.background)
        draw = ImageDraw.Draw(img)
        text: list[tuple[float, float, int, str]] = []
        ops = list(page.ops)
        ops.append(Op("rule", (MARGIN, FOOTER_TOP, PAGE_W - MARGIN, FOOTER_TOP + 1), pal.border))
        fy = float(FOOTER_TOP + 14)
        number = f"Page {i} of {n}"
        info = wrap(foot[0], SIZE_SMALL, PAGE_W - 2 * MARGIN - width(number, SIZE_SMALL) - 24)
        for line in info:
            ops.append(Op("text", (MARGIN, fy, SIZE_SMALL, 0), pal.text, line))
            fy += SIZE_SMALL * LINE_GAP
        ops.append(Op("text", (PAGE_W - MARGIN, FOOTER_TOP + 14, SIZE_SMALL, 0), pal.text_muted, number, align="right"))
        for line in wrap(foot[1], SIZE_SMALL - 2, PAGE_W - 2 * MARGIN)[:3]:
            ops.append(Op("text", (MARGIN, fy, SIZE_SMALL - 2, 0), pal.text_muted, line))
            fy += (SIZE_SMALL - 2) * LINE_GAP
        for op in ops:
            if op.kind == "text":
                _draw_text(draw, op)
                x, y, size, _ = op.box
                tx = x - width(op.text, int(size)) if op.align == "right" else x
                text.append((tx, y, int(size), op.text))
            elif op.kind == "image":
                img.paste(op.image, (math.floor(op.box[0]), math.floor(op.box[1])))
            else:
                x0, y0, x1, y1 = op.box
                draw.rectangle((math.floor(x0), math.floor(y0), math.ceil(x1) - 1, math.ceil(y1) - 1), fill=op.colour, outline=op.outline)
        out.append(Rendered(img, text))
    return out


def all_text(pages: Iterable[Rendered]) -> list[str]:
    return [t for p in pages for *_, t in p.text]


__all__ = ["DPI", "PAGE_W", "PAGE_H", "CHARTS_LATER", "CHART_CAPTION", "chart_inputs", "Rendered", "render", "layout", "ist", "price", "wrap", "width", "all_text"]
