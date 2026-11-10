"""M95 PR-5: the guest snapshot's parts as pure functions (no database, no clock) - what `build.py` stores.

Every price becomes a distance: bars and areas in % from the day's close, strikes in steps from the at-the-money strike,
open interest in lakhs. `price_figures` is the last check before anything is stored: no number anywhere in the payload
(also inside a text) may fall in an index's price range.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.charts.read_chart import STATE_WORDS, TF_WORDS

CHART_BARS = 120                       # Daily bars drawn on the chart (about six months)
CHAIN_STEPS = 10                       # strikes each side of the at-the-money strike
LAKH = 100_000
PRICE_RANGE = (0.5, 1.5)               # a number between half and one and a half times an index's low / high is a price
ROLE_WORDS = {"demand": "Buyer area", "supply": "Seller area"}


def pct_from(value: float, base: float) -> float:
    """`value` as % from `base` (2 decimals)."""
    return round((float(value) / float(base) - 1.0) * 100.0, 2)


def lakhs(oi: int | float | None) -> float | None:
    return None if oi is None else round(float(oi) / LAKH, 2)


# ------------------------------------------------------------------------------------------------------------- chart

def chart_bars(bars: Sequence[tuple[float, float, float, float]], close: float, keep: int = CHART_BARS) -> list[list[float]]:
    """The newest `keep` (open, high, low, close) bars as [o, h, l, c] in % from `close` - no price on the axis."""
    return [[pct_from(o, close), pct_from(h, close), pct_from(lo, close), pct_from(c, close)] for o, h, lo, c in bars[-keep:]]


def area_of(band: Mapping[str, Any], close: float) -> dict[str, Any]:
    """One chart band (app.price_action.zones.chart.select_bands) without its prices: role, state and edges in %."""
    role = str(band.get("role") or "")
    state = str(band.get("state") or "")
    tf = str(band.get("timeframe") or "")
    name = ROLE_WORDS.get(role, "Area") if band.get("kind") != "line" else ("Support line" if role == "demand" else "Resistance line")
    touches = int(band.get("touches") or 0)
    words = f"{name} from the {TF_WORDS.get(tf, tf)} chart: {STATE_WORDS.get(state, state)}"
    if touches > 0:
        words += f" ({touches} {'touch' if touches == 1 else 'touches'})"
    return {"role": role, "kind": str(band.get("kind") or "band"), "state": state, "timeframe": tf, "touches": touches,
            "low_pct": pct_from(band["low"], close), "high_pct": pct_from(band["high"], close), "words": words + "."}


def nearest_area_words(areas: Sequence[Mapping[str, Any]]) -> list[str]:
    """Where the close sat against the areas drawn: inside one, and the nearest one below and above (in %)."""
    out: list[str] = []
    inside = [a for a in areas if a["low_pct"] <= 0 <= a["high_pct"]]
    below = sorted((a for a in areas if a["high_pct"] < 0), key=lambda a: -a["high_pct"])
    above = sorted((a for a in areas if a["low_pct"] > 0), key=lambda a: a["low_pct"])
    if inside:
        out.append(f"The close was inside a {_area_name(inside[0])}.")
    if below:
        out.append(f"Nearest {_area_name(below[0])} below: {abs(below[0]['high_pct']):.2f}% away.")
    if above:
        out.append(f"Nearest {_area_name(above[0])} above: {above[0]['low_pct']:.2f}% away.")
    return out


def _area_name(area: Mapping[str, Any]) -> str:
    if area.get("kind") == "line":
        return "support line" if area["role"] == "demand" else "resistance line"
    return ROLE_WORDS.get(str(area["role"]), "area").lower()


# ------------------------------------------------------------------------------------------------------------- chain

def strike_step(strikes: Iterable[float]) -> float | None:
    """The chain's strike interval: the most common gap between neighbouring strikes (ties: the smaller)."""
    ordered = sorted(set(float(s) for s in strikes))
    gaps = Counter(round(b - a, 4) for a, b in zip(ordered, ordered[1:]) if b > a)
    if not gaps:
        return None
    best = max(gaps.values())
    return min(g for g, n in gaps.items() if n == best)


def chain_view(rows: Mapping[float, Mapping[str, int | None]], spot: float, steps: int = CHAIN_STEPS) -> dict[str, Any] | None:
    """{strike: {"call": oi, "put": oi}} of one expiry -> open interest in lakhs by steps from the at-the-money strike
    (the strike nearest the index close). Strikes off the step grid are left out. None when the chain is empty."""
    step = strike_step(rows)
    if not rows or not step:
        return None
    atm = min(rows, key=lambda k: (abs(float(k) - spot), float(k)))
    out: list[dict[str, Any]] = []
    for strike in sorted(rows):
        distance = (float(strike) - float(atm)) / step
        if abs(distance - round(distance)) > 1e-6 or abs(distance) > steps:
            continue
        cell = rows[strike]
        out.append({"steps": int(round(distance)), "call_oi_lakh": lakhs(cell.get("call")), "put_oi_lakh": lakhs(cell.get("put"))})
    calls = sum(int(c.get("call") or 0) for c in rows.values())
    puts = sum(int(c.get("put") or 0) for c in rows.values())
    top_call = max(out, key=lambda r: r["call_oi_lakh"] or 0, default=None)
    top_put = max(out, key=lambda r: r["put_oi_lakh"] or 0, default=None)
    return {"rows": out, "pcr": round(puts / calls, 2) if calls else None,
            "most_call_oi_steps": top_call["steps"] if top_call and top_call["call_oi_lakh"] else None,
            "most_put_oi_steps": top_put["steps"] if top_put and top_put["put_oi_lakh"] else None}


def steps_words(steps: int | None) -> str:
    if steps is None:
        return "not available"
    if steps == 0:
        return "at the money"
    return f"{abs(steps)} {'step' if abs(steps) == 1 else 'steps'} {'above' if steps > 0 else 'below'}"


# ------------------------------------------------------------------------------------------------------- Today / report

def direction_word(change_pct: float | None) -> str:
    if change_pct is None:
        return "no change figure"
    return "higher" if change_pct > 0 else "lower" if change_pct < 0 else "unchanged"


def today_lines(name: str, facts: Mapping[str, Any]) -> list[str]:
    """The Today card's lines for one index (no price figure)."""
    lines: list[str] = []
    change = facts.get("change_pct")
    if change is not None:
        lines.append(f"{name} closed {abs(change):.2f}% {direction_word(change)} than the day before."
                     if change else f"{name} closed unchanged.")
    for key in ("structure_daily", "structure_weekly"):
        if facts.get(key):
            lines.append(str(facts[key]))
    if facts.get("range_position_pct") is not None:
        lines.append(f"The close sat {facts['range_position_pct']:.0f}% of the way up its recent range.")
    return lines


def report_lines(name: str, facts: Mapping[str, Any], areas: Sequence[Mapping[str, Any]], chain: Mapping[str, Any] | None,
                 fut_oi_change_pct: float | None) -> list[str]:
    """The daily report's paragraph for one index: Today's lines, the areas, then the open interest (no price figure)."""
    lines = today_lines(name, facts)
    for key, word in (("swing_high_pct", "high"), ("swing_low_pct", "low")):
        if facts.get(key) is not None:            # % from the close: above it when positive
            lines.append(f"The last confirmed swing {word} was {abs(facts[key]):.2f}% "
                         f"{'above' if facts[key] > 0 else 'below'} the close.")
    lines += nearest_area_words(areas)
    if chain is not None:
        if chain.get("pcr") is not None:
            lines.append(f"Put-call ratio by open interest, nearest expiry: {chain['pcr']:.2f}.")
        lines.append(f"Most call open interest: {steps_words(chain.get('most_call_oi_steps'))}; "
                     f"most put open interest: {steps_words(chain.get('most_put_oi_steps'))}.")
    if fut_oi_change_pct is not None:
        lines.append(f"Futures open interest changed {fut_oi_change_pct:+.2f}% from the day before.")
    return lines


# ------------------------------------------------------------------------------------------------------------- checks

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(value: Any) -> Iterable[float]:
    """Every number in a JSON-like value: the numbers themselves and every number written inside a text."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, str):
        for m in _NUMBER.findall(value):
            yield float(m.replace(",", ""))
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from numbers_in(k)
            yield from numbers_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from numbers_in(v)


def price_ranges(lows_highs: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """(lowest low, highest high) per index -> the band of numbers that would read as one of its prices."""
    return [(lo * PRICE_RANGE[0], hi * PRICE_RANGE[1]) for lo, hi in lows_highs]


def price_figures(payload: Any, ranges: Sequence[tuple[float, float]]) -> list[float]:
    """The numbers in `payload` that fall in a price range - must be empty before a snapshot is stored."""
    return sorted({x for x in numbers_in(payload) if any(lo <= abs(x) <= hi for lo, hi in ranges)})


def texts_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from texts_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from texts_in(v)


def advice_words(payload: Any) -> list[str]:
    """Buy / sell / target / stop-loss and claim words in the payload's texts (the education guard's word lists)."""
    from app.idea_library.content import FORBIDDEN_PHRASES, FORBIDDEN_WORDS
    found: set[str] = set()
    for text in texts_in(payload):
        lower = text.lower()
        found.update(w for w in re.findall(r"[a-z]+", lower) if w in FORBIDDEN_WORDS)
        found.update(p for p in FORBIDDEN_PHRASES if p in lower)
    return sorted(found)


__all__ = ["CHART_BARS", "CHAIN_STEPS", "advice_words", "area_of", "chain_view", "chart_bars", "lakhs", "nearest_area_words",
           "numbers_in", "pct_from", "price_figures", "price_ranges", "report_lines", "steps_words", "strike_step", "today_lines"]
