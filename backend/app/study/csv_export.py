"""M77 SX-5: the study's tables as CSV (PD-1 "CSV (tables)"), rebuilt from the stored snapshot - same study = same bytes.

Tables (each only when the study holds it): `structure`, `areas`, `option_context` (the summary and the OI walls),
`ai_card`, `checklist`, and `bars-<tf>` per chart (the closed bars the chart drew). Plain numbers: no grouping, a dot
for decimals, a missing value is an empty cell with its row's `note` column saying "not available" where it matters;
times in IST ISO form. One table -> one CSV; all of them -> one ZIP (fixed file times, so the bytes repeat).
His own words (notes, checklist rules) are his data, written as they are; a cell that starts with = + - @ gets a
leading apostrophe so a spreadsheet never runs it as a formula.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class NoSuchTable(LookupError):
    """The study holds no such table."""


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _time(value: Any) -> str:
    """An epoch second or an ISO time as IST ISO ("2026-03-24T09:30:00+05:30"); a date stays a date."""
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            when = datetime.fromtimestamp(float(value), tz=UTC)
        else:
            text = str(value)
            if len(text) == 10:
                return text
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        return ""
    return when.astimezone(IST).isoformat()


def _csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for row in rows:
        w.writerow([_cell(v) for v in row])
    return buf.getvalue().encode("utf-8")


def tables(snap: Mapping[str, Any]) -> dict[str, bytes]:
    """Every table the study holds, name -> CSV bytes, in a fixed order."""
    data = snap.get("data") or {}
    out: dict[str, bytes] = {}
    structure = (data.get("structure") or {}).get("timeframes") or []
    if structure:
        rows = []
        for r in structure:
            last = r.get("last_event") or {}
            prot = r.get("protected") or {}
            near = r.get("nearest") or {}
            above, below = near.get("above") or {}, near.get("below") or {}
            rows.append([r.get("tf"), r.get("direction"), last.get("type"), last.get("level"), _time(last.get("time")), prot.get("price"),
                         above.get("label"), above.get("distance_points"), below.get("label"), below.get("distance_points"),
                         _time(r.get("as_of")), "" if r.get("available") else (r.get("reason") or "not available")])
        out["structure"] = _csv(["timeframe", "direction", "last_event", "last_event_level", "last_event_time", "protected_level",
                                 "nearest_above", "nearest_above_points", "nearest_below", "nearest_below_points", "as_of", "note"], rows)
    areas = data.get("areas") or {}
    if "areas" in data:
        out["areas"] = _csv(["label", "timeframe", "role", "low", "high", "state", "touches", "formed_at", "last_visit", "side",
                             "distance_points"],
                            [[a.get("label"), a.get("timeframe"), "seller" if a.get("role") == "supply" else "buyer" if a.get("role") == "demand" else a.get("role"),
                              a.get("low"), a.get("high"), a.get("state"), a.get("touches"), _time(a.get("formed_at")), _time(a.get("last_visit")),
                              a.get("side"), a.get("distance_points")] for a in areas.get("rows") or []])
    oc = data.get("option_context")
    if oc:
        rows = [["summary", k, oc.get(k)] for k in ("underlying", "expiry", "days_to_expiry", "spot", "atm_strike", "pcr_oi", "max_pain", "atm_iv")]
        rows += [["oi_wall", f"{w.get('side')} {_cell(w.get('strike'))}", w.get("oi")] for w in oc.get("walls") or []]
        for side, legs in (oc.get("atm_legs") or {}).items():
            rows += [["atm_" + side, k, v] for k, v in (legs or {}).items()]
        rows.append(["summary", "as_of", _time(oc.get("as_of"))])
        out["option_context"] = _csv(["part", "item", "value"], rows)
    card = data.get("ai_card")
    if card:
        rows = [[line.get("name"), line.get("text")] for line in card.get("lines") or []]
        rows += [[f"Evidence: {e.get('name')}", "present" if e.get("present") else "not present"] for e in card.get("evidence") or []]
        how = card.get("how") or {}
        rows += [["Reading as of", _time(how.get("reading_as_of"))], ["Card made", _time(how.get("made_at"))]]
        out["ai_card"] = _csv(["part", "text"], rows)
    notes = data.get("notes") or {}
    if notes.get("checklist"):
        out["checklist"] = _csv(["rule", "answer"], [[c.get("rule"), c.get("answer")] for c in notes["checklist"]])
    for chart in data.get("charts") or []:
        if chart.get("bars"):
            out[f"bars-{chart.get('tf')}"] = _csv(["time_ist", "open", "high", "low", "close", "volume"],
                                                  [[_time(b[0]), *b[1:6]] for b in chart["bars"]])
    return out


def table_csv(snap: Mapping[str, Any], name: str) -> bytes:
    found = tables(snap)
    if name not in found:
        raise NoSuchTable(name)
    return found[name]


def zip_all(snap: Mapping[str, Any], stem: str) -> bytes:
    """Every table as `<stem>-<table>.csv` in one ZIP (fixed times and order: the same study gives the same bytes)."""
    found = tables(snap)
    if not found:
        raise NoSuchTable("*")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in found.items():
            info = zipfile.ZipInfo(f"{stem}-{name}.csv", date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


__all__ = ["tables", "table_csv", "zip_all", "NoSuchTable"]
