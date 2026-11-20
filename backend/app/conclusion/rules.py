"""TERM-CON C1: what agrees, what conflicts, and the glance cue (plan sections 3.3 and 6A). Pure: readings in, words out.

A reading is one source's direction AS IS (`up` / `down` / `range` / `mixed` / `none`), one per source and per timeframe
(never merged, Z-1u). Nothing is weighted and no score is made (DECISIONS 20 Nov, TC-Q1):
* agrees: two or more readings with the same direction (up or down);
* conflicts: up readings and down readings at the same time;
* lean (TC-Q5): the majority of the up / down readings, always with its count ("3 of 4"); a tie, or fewer than two
  up / down readings, is sideways. It is a count of ATP's readings, never a recommendation;
* meter: how many readings are up / down / range or mixed / without a direction;
* line: one short sentence in structure words (it must pass the signal language, or it is left empty).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.signals import language as L

DIRECTIONAL = ("up", "down")
WORDS = {"up": "up", "down": "down", "range": "in a range", "mixed": "mixed", "none": "no direction"}
LEAN_WORDS = {"up": "Lean up", "down": "Lean down", "sideways": "Sideways"}


def label(reading: Mapping[str, Any]) -> str:
    """The reading's name in words a signal line may carry ("15-minute structure", "OI", "Your signals")."""
    if reading.get("source") == "chart":
        return f"{L.TIMEFRAMES.get(str(reading.get('tf')), str(reading.get('tf')))} structure"
    return str(reading.get("label") or reading.get("source"))


def _with(readings: Sequence[Mapping[str, Any]], direction: str) -> list[Mapping[str, Any]]:
    return [r for r in readings if r.get("direction") == direction]


def agrees(readings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for d in DIRECTIONAL:
        same = _with(readings, d)
        if len(same) >= 2:
            out.append({"direction": d, "text": " · ".join(f"{label(r)} {d}" for r in same),
                        "sources": [r.get("source") for r in same]})
    return out


def conflicts(readings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    up, down = _with(readings, "up"), _with(readings, "down")
    if not up or not down:
        return []
    return [{"text": " · ".join([*(f"{label(r)} up" for r in up), *(f"{label(r)} down" for r in down)]),
             "sources": [r.get("source") for r in (*up, *down)]}]


def meter(readings: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    count = {d: len(_with(readings, d)) for d in ("up", "down", "range", "mixed", "none")}
    return {"up": count["up"], "down": count["down"], "range": count["range"] + count["mixed"], "none": count["none"],
            "total": len(readings)}


def lean(readings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The majority of the up / down readings with its count; `of` counts every reading that has a direction or a range."""
    m = meter(readings)
    of = m["up"] + m["down"] + m["range"]
    if not of:
        return {"side": "sideways", "count": 0, "of": 0, "words": "No readings yet"}
    if m["up"] + m["down"] < 2 or m["up"] == m["down"]:
        return {"side": "sideways", "count": 0, "of": of, "words": f"Sideways · {m['up']} up, {m['down']} down of {of}"}
    side = "up" if m["up"] > m["down"] else "down"
    return {"side": side, "count": m[side], "of": of, "words": f"{LEAN_WORDS[side]} · {m[side]} of {of}"}


def line(readings: Sequence[Mapping[str, Any]], symbol: str, limit: int = 90) -> str:
    """One sentence of the readings in order ("15-minute structure up · OI up · Positions down"), cut to `limit`
    characters at a separator; empty when it would not pass the signal language."""
    parts = [f"{label(r)} {WORDS.get(str(r.get('direction')), 'no direction')}" for r in readings if r.get("direction") != "none"]
    text = ""
    for p in parts:
        nxt = p if not text else f"{text} · {p}"
        if len(nxt) > limit:
            break
        text = nxt
    return text if text and not L.check(text, "signal_own", own_symbols=[symbol]) else ""


def glance(readings: Sequence[Mapping[str, Any]], symbol: str) -> dict[str, Any]:
    return {"lean": lean(readings), "meter": meter(readings), "agrees": agrees(readings), "conflicts": conflicts(readings),
            "line": line(readings, symbol)}


__all__ = ["glance", "lean", "meter", "agrees", "conflicts", "line", "label"]
