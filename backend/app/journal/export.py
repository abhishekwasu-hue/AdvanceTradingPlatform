"""M62 JN-3: the journal export - CSV (one row per entry) and a plain PDF (the totals, then the entries as a table).

The repository has CSV exports (app/exports, app/tax) but no PDF writer on the server, and none is added as a
dependency: the PDF here is written by hand with the PDF standard fonts (text only, A4 landscape). Those fonts have no
rupee sign, so the PDF writes "Rs" and Indian digit grouping; the CSV keeps plain numbers (a spreadsheet's own format).
Numbers only: a record of the trader's own entries, never a recommendation.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.journal.stats import IST
from app.study.numfmt import format_inr, format_number

CSV_COLUMNS = ("date_ist", "source", "mode", "symbol", "direction", "setup", "timeframe", "entry_price", "exit_price", "quantity",
               "charges", "net_pnl", "r_multiple", "outcome", "exit_reason", "detector_tags", "own_tags", "images", "notes")


def _ist(iso: str | None) -> str:
    if not iso:
        return ""
    when = datetime.fromisoformat(iso)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(IST).strftime("%Y-%m-%d %H:%M")


def _detector(row: dict[str, Any]) -> list[str]:
    return [t["tag"] for t in row["tags"] if t["status"] != "DISMISSED"]


def _source(row: dict[str, Any]) -> str:
    return "Outside ATP" if row["outside_atp"] else "ATP"


def to_csv(rows: Sequence[dict[str, Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([_ist(r["entry_time"]), _source(r), r["mode"] or "", r["symbol"], r["direction"], r["setup"] or "",
                         r["timeframe"] or "", r["entry_price"], "" if r["exit_price"] is None else r["exit_price"], r["quantity"],
                         "" if r["charges"] is None else r["charges"], "" if r["pnl"] is None else r["pnl"],
                         "" if r["r_multiple"] is None else r["r_multiple"], r["outcome"], r["exit_reason"] or "",
                         " ".join(_detector(r)), " ".join(r["user_tags"]), r["attachments"], (r["notes"] or "").strip()])
    return ("﻿" + out.getvalue()).encode("utf-8")      # the BOM: a spreadsheet reads the text as UTF-8


# --- numbers as the PDF shows them ------------------------------------------------------------------------------------

def indian(value: float | None, decimals: int = 2) -> str:
    """Indian digit grouping (12,34,567.80); a missing value is "-". A thin wrapper over app.study.numfmt (M77 SX-1):
    the same output as before, byte for byte (hyphen-minus, Python's own rounding)."""
    return format_number(value, decimals, minus="-", missing="-", rounding="half_even", finite_only=False)


def rupees(value: float | None, *, signed: bool = False) -> str:
    return format_inr(value, signed=signed, decimals=2, symbol="Rs ", minus="-", missing="-", rounding="half_even",
                      finite_only=False)


# --- a minimal PDF (text only, standard fonts) ------------------------------------------------------------------------

PAGE_W, PAGE_H = 842, 595            # A4 landscape, points
MARGIN = 36
LINE = 12


def _ascii(text: str) -> str:
    text = text.replace("₹", "Rs ").replace("−", "-").replace("–", "-").replace("—", "-").replace("·", "-")
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)


def _escape(text: str) -> str:
    return _ascii(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Pdf:
    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def text(self, x: float, s: str, *, font: str = "F1", size: float = 9) -> None:
        self.pages[-1].append(f"BT /{font} {size} Tf {x:.1f} {self.y:.1f} Td ({_escape(s)}) Tj ET")

    def line(self, s: str = "", *, font: str = "F1", size: float = 9, gap: float = LINE) -> None:
        if self.y - gap < MARGIN:
            self.new_page()
        self.y -= gap
        if s:
            self.text(MARGIN, s, font=font, size=size)

    def build(self) -> bytes:
        objects: list[bytes] = []
        n_pages = len(self.pages)
        # 1 catalog, 2 pages, 3 Helvetica, 4 Helvetica-Bold, 5 Courier, then (page, content) per page
        kids = " ".join(f"{6 + 2 * i} 0 R" for i in range(n_pages))
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
        for name in ("Helvetica", "Helvetica-Bold", "Courier"):
            objects.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode())
        for i, ops in enumerate(self.pages):
            footer = f"BT /F1 8 Tf {MARGIN} 20 Td (Page {i + 1} of {n_pages}) Tj ET"
            stream = "\n".join([*ops, footer]).encode("latin-1")
            objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Contents {7 + 2 * i} 0 R "
                            "/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> >>").encode())
            objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


TABLE = (("Date (IST)", 17), ("Source", 12), ("Symbol", 16), ("Side", 6), ("Setup", 18), ("TF", 6), ("Qty", 8),
         ("Net P&L", 17), ("R", 7), ("Tags", 30))


def _cell(text: str, width: int, *, right: bool = False) -> str:
    text = _ascii(text)
    text = text if len(text) <= width - 1 else text[: width - 2] + "~"
    return text.rjust(width - 1) + " " if right else text.ljust(width)


def _filters_text(filters: dict[str, Any]) -> str:
    parts = []
    if filters.get("date_from") or filters.get("date_to"):
        parts.append(f"dates {filters.get('date_from') or 'start'} to {filters.get('date_to') or 'today'}")
    for key in ("setup", "tag", "outcome"):
        if filters.get(key):
            parts.append(f"{key} {filters[key]}")
    parts.append("outside-ATP entries included" if filters.get("include_manual", True) else "ATP trades only")
    return "; ".join(parts)


def to_pdf(rows: Sequence[dict[str, Any]], totals: dict[str, Any], filters: dict[str, Any], *, now: datetime | None = None) -> bytes:
    pdf = _Pdf()
    stamp = (now or datetime.now(UTC)).astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    pdf.line("Trading journal", font="F2", size=14, gap=4)
    pdf.line(f"Exported {stamp} - {_filters_text(filters)}", size=8, gap=16)
    t = totals
    figures = [("Trades", str(t["trades"])), ("Closed", str(t["closed"])),
               ("Win rate", "-" if t["win_rate"] is None else f"{indian(t['win_rate'], 1)}%"),
               ("Net P&L", rupees(t["net_pnl"], signed=True)),
               ("Profit factor", "-" if t["profit_factor"] is None else indian(t["profit_factor"])),
               ("Expectancy", rupees(t["expectancy"], signed=True)), ("Average win", rupees(t["avg_win"])),
               ("Average loss", rupees(t["avg_loss"])), ("Average R", "-" if t["avg_r"] is None else f"{t['avg_r']:+.2f}R")]
    for i in range(0, len(figures), 5):
        pdf.line("    ".join(f"{k}: {v}" for k, v in figures[i:i + 5]), size=9, gap=13)
    if t.get("outside_atp"):
        pdf.line(f"{t['outside_atp']} of the trades were done outside ATP (entered by hand).", size=8, gap=12)
    pdf.line("", gap=8)
    header = "".join(_cell(name, w, right=name in ("Qty", "Net P&L", "R")) for name, w in TABLE)
    pdf.line(header, font="F3", size=8, gap=12)
    for r in rows:
        tags = ", ".join(_detector(r) + list(r["user_tags"]))
        cells = (_ist(r["entry_time"]), _source(r), r["symbol"], r["direction"], r["setup"] or "-", r["timeframe"] or "-",
                 indian(r["quantity"], 0) if float(r["quantity"]).is_integer() else indian(r["quantity"]),
                 "open" if r["pnl"] is None else rupees(r["pnl"], signed=True),
                 "-" if r["r_multiple"] is None else f"{r['r_multiple']:+.2f}", tags or "-")
        line = "".join(_cell(str(c), w, right=name in ("Qty", "Net P&L", "R")) for c, (name, w) in zip(cells, TABLE, strict=True))
        if pdf.y - 11 < MARGIN:
            pdf.new_page()
            pdf.line(header, font="F3", size=8, gap=12)
        pdf.line(line, font="F3", size=8, gap=11)
    if not rows:
        pdf.line("No entries match these filters.", size=9, gap=14)
    pdf.line("", gap=10)
    pdf.line("A record of your own trades and the numbers worked out from them; nothing here is a recommendation.", size=7, gap=10)
    return pdf.build()
