"""ADV-2 D (owner 4 Nov, docs/design/ADV2_SPEC.md): the depth of the hybrid reading - what a trader reads off the structure.

Every value comes from ATP's own price-action layer of the picked timeframe (app/charts/price_action_layer.py) and the
areas of the same read; every sentence is fixed wording here, filled with those values. Structure words only: swings,
the trend and its phase, the protected level, a close beyond it, the areas of interest. No buy / sell / target / stop
wording and no trading rule - each line passes Analyze a chart's signal-language check (signal_language.is_clean) or is
left out. The rules named in a sentence are the structure tracker's own (app/price_action/structure_tracker.py): a close
beyond the protected level is a break against the trend; after it a lower high (higher low) and a new low (high) turn
the structure; a close beyond a range edge followed by a higher low / lower high starts a trend.
All fields are optional on a stored result: an older result without them still renders.
"""
from __future__ import annotations

from typing import Any

from app.chart_analyze import signal_language

MAX_SWINGS = 6
MAX_WATCH = 3
SWING_LABELS = ("HH", "HL", "LH", "LL", "H", "L")
TREND_WORDS = {"up": "up", "down": "down", "range": "sideways"}
# structure_tracker states -> (direction, the phase's words)
PHASES = {"UPTREND": ("up", "Up - trend running"), "UPTREND_PULLBACK": ("up", "Up - pullback running"),
          "UPTREND_WEAK": ("up", "Up - weakening after a break against the trend"),
          "DOWNTREND": ("down", "Down - trend running"), "DOWNTREND_PULLBACK": ("down", "Down - pullback running"),
          "DOWNTREND_WEAK": ("down", "Down - weakening after a break against the trend"), "RANGE": ("range", "Sideways - range")}
HANDOVER = "handover - commitment"
CHANGES = "structure changes"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, 2) if number == number and abs(number) != float("inf") else None


def price(x: float) -> str:
    return f"{x:,.2f}"


def band(area: dict[str, Any]) -> str:
    return f"{price(area['low'])}–{price(area['high'])}"


def pct(distance: float | None, last_close: float | None) -> float | None:
    """distance / last close x 100, 2 decimals (None when either is unknown)."""
    if distance is None or last_close is None or not last_close > 0:
        return None
    return round(abs(distance) / last_close * 100.0, 2)


def _clean(text: str | None) -> str | None:
    return text if text and signal_language.is_clean(text) else None


def swings(layer: dict[str, Any]) -> list[dict[str, Any]]:
    """The last MAX_SWINGS confirmed swings of the chart's degree: label (HH / HL / LH / LL, or H / L for the first of a
    kind and an equal one), price, time."""
    out: list[dict[str, Any]] = []
    for sw in layer.get("swings") or []:
        if not isinstance(sw, dict) or sw.get("status") not in (None, "confirmed"):
            continue
        p = _num(sw.get("price"))
        kind = "H" if sw.get("kind") == "H" else "L" if sw.get("kind") == "L" else None
        if p is None or kind is None or not isinstance(sw.get("time"), (int, float)):
            continue
        label = str(sw.get("label") or kind)
        out.append({"label": label if label in SWING_LABELS else kind, "price": p, "time": int(sw["time"])})
    return out[-MAX_SWINGS:]


def state(layer: dict[str, Any]) -> dict[str, Any] | None:
    phase = str((layer.get("structure_state") or {}).get("state") or "")
    if phase not in PHASES:
        return None
    return {"phase": phase, "text": PHASES[phase][1]}


def higher_degree(layer: dict[str, Any], tf_word: str) -> dict[str, Any] | None:
    """The trend of the next larger swing degree the layer computed (Dow: the last two highs and lows)."""
    degree = layer.get("degree")
    if not isinstance(degree, int):
        return None
    row = next((t for t in layer.get("trend") or [] if isinstance(t, dict) and t.get("degree") == degree + 1), None)
    trend = TREND_WORDS.get(str((row or {}).get("trend") or ""))
    if row is None or trend is None:
        return None
    basis = str(row.get("basis") or "").replace(" + ", ", ")
    text = _clean(f"On the larger swings the {tf_word} structure is {trend}" + (f" ({basis})." if basis else "."))
    return {"trend": trend, "text": text} if text else None


def _nearest(areas: list[dict[str, Any]], where: str) -> dict[str, Any] | None:
    return next((a for a in areas if a.get("where") == where), None)


def _last(layer: dict[str, Any], kind: str) -> float | None:
    for sw in reversed(layer.get("swings") or []):
        if isinstance(sw, dict) and sw.get("kind") == kind and sw.get("status") in (None, "confirmed"):
            return _num(sw.get("price"))
    return None


def _focus_area(direction: str, phase: str, areas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The area the structure is working toward: the one at the price, else the nearest on the pullback's side (below in
    an up trend, above in a down trend), else the nearest."""
    inside = _nearest(areas, "inside")
    if inside is not None:
        return inside
    if phase.endswith("_WEAK") or direction == "range":
        return areas[0] if areas else None
    return _nearest(areas, "below" if direction == "up" else "above")


def scenarios(layer: dict[str, Any], protected: float | None, areas: list[dict[str, Any]]) -> dict[str, str] | None:
    """{main, other}: the structure holding, and the structure changing - two sentences from the state, the protected
    level and the nearest area."""
    st = layer.get("structure_state") or {}
    phase = str(st.get("state") or "")
    if phase not in PHASES:
        return None
    direction = PHASES[phase][0]
    focus = _focus_area(direction, phase, areas)
    near = f"; the nearest area of interest is {band(focus)}" if focus else ""
    p = f" {price(protected)}" if protected is not None else ""
    if direction == "range":
        hi, lo = _num(st.get("range_high")), _num(st.get("range_low"))
        main = f"Price is moving sideways between {price(lo)} and {price(hi)}{near}." if hi is not None and lo is not None else \
            f"Price is moving sideways{near}."
        other = "A close beyond an edge of the range, followed by a higher low or a lower high, would start a trend."
    elif phase.endswith("_WEAK"):
        if direction == "up":
            main = f"The up trend is weakening after a close below the protected low{p}{near}."
            other = "A lower high and then a new low would turn the structure down; a close above the trend's high would recover it."
        else:
            main = f"The down trend is weakening after a close above the protected high{p}{near}."
            other = "A higher low and then a new high would turn the structure up; a close below the trend's low would recover it."
    else:
        word, side, against = ("low", "above", "below") if direction == "up" else ("high", "below", "above")
        lead = f"A pullback is running in the {direction} trend; the structure stays {direction}" if phase.endswith("_PULLBACK") else \
            f"The {direction} trend is intact"
        hold = f" while price holds {side} the protected {word}{p}" if protected is not None else ""
        main = f"{lead}{hold}{near}."
        other = (f"A close {against} the protected {word}{p} would be a break against the {direction} trend (a change of character)."
                 if protected is not None else f"A close {against} the last swing {word} would be a break against the {direction} trend.")
    main_c, other_c = _clean(main), _clean(other)
    return {"main": main_c, "other": other_c} if main_c and other_c else None


def watch(layer: dict[str, Any], tf_word: str, protected: float | None, areas: list[dict[str, Any]],
          last_close: float | None) -> list[dict[str, str]]:
    """Up to MAX_WATCH {when, meaning}: the area the structure works toward, the close that changes the structure, the
    close that makes the next swing of the trend."""
    st = layer.get("structure_state") or {}
    phase = str(st.get("state") or "")
    if phase not in PHASES:
        return []
    direction = PHASES[phase][0]
    out: list[dict[str, str]] = []
    focus = _focus_area(direction, phase, areas)
    if focus is not None:
        out.append({"when": f"Price at {band(focus)}", "meaning": HANDOVER})
    if direction == "range":
        hi, lo = _num(st.get("range_high")), _num(st.get("range_low"))
        if hi is not None:
            out.append({"when": f"{tf_word} close above {price(hi)}", "meaning": "range edge"})
        if lo is not None:
            out.append({"when": f"{tf_word} close below {price(lo)}", "meaning": "range edge"})
    elif not phase.endswith("_WEAK"):
        if protected is not None:
            out.append({"when": f"{tf_word} close {'below' if direction == 'up' else 'above'} {price(protected)}", "meaning": CHANGES})
        extreme = _last(layer, "H" if direction == "up" else "L")
        if extreme is not None and last_close is not None and ((extreme > last_close) if direction == "up" else (extreme < last_close)):
            out.append({"when": f"{tf_word} close {'above' if direction == 'up' else 'below'} {price(extreme)}",
                        "meaning": "new HH" if direction == "up" else "new LL"})
    return [w for w in out if _clean(w["when"]) and _clean(w["meaning"])][:MAX_WATCH]


def depth(layer: dict[str, Any], tf_word: str, protected: float | None, areas: list[dict[str, Any]], last_close: float | None) -> dict[str, Any]:
    """The reading's ADV-2 D fields."""
    distance = None if protected is None or last_close is None else last_close - protected
    return {"swings": swings(layer), "state": state(layer), "protected_distance_pct": pct(distance, last_close),
            "higher_degree": higher_degree(layer, tf_word), "scenarios": scenarios(layer, protected, areas),
            "watch": watch(layer, tf_word, protected, areas, last_close)}


__all__ = ["depth", "swings", "state", "higher_degree", "scenarios", "watch", "pct", "MAX_SWINGS", "MAX_WATCH"]
