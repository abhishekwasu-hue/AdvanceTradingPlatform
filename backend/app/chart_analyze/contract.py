"""Analyze a chart: the output contract every analysis is validated against before it is stored or shown.

The contract is generic: it names the blocks of the result screen, not the method. The analysis engine (the private
package) fills it; the names of the evidence items, the wording of the story and every note come from the engine at
run time and are only rendered here. The blocks (docs/design/CHART_ANALYZE.md §4):

- `story` - the chart's story from the start of the visible range to now, in plain words (paragraphs);
- `trend` - the trend side (up / down / sideways / unclear) with a short reason;
- `current_state` - what is happening now (pullback in progress, handover, commitment, ...) with a short reason;
- `rsi` - the RSI divergence read (bullish / bearish / none) with a short reason;
- `setup` - what the method sees: side (bull put / bear call / none), label A / B, status formed / forming / none,
  what it is waiting for, the trigger, the invalidation, the next areas - conditions, never an instruction;
- `evidence` - a list of {name, present, reason}; listed, never scored (a `score` anywhere is dropped);
- `scenarios` - the primary and the alternative, and where the primary is proven wrong (both sides);
- `what_to_wait_for` - when the setup is not formed: the condition that would form it, and, when it is a price
  condition, an alert the user can set with one click (above / below / between prices);
- `htf_context` - the higher timeframes' trend and nearest areas, when the engine returns them;
- `key_levels` - every area / line with its price range and state;
- `annotations` - what is drawn: swings (HH / HL / LH / LL), areas (buyer / seller, fresh / tested / broken /
  flipped), lines with their touch points, the RSI divergence on price and on the RSI pane, the commitment candle,
  the lower-timeframe change of character, the setup lines (trigger / invalidation / next area).
  Image-only: coordinates are pixels of the stored image (x right, y down) and every price is approximate.
  Hybrid (PR-B / PR-C): coordinates are time and price.

Safety applied here, whatever the engine returns:
- every text goes through the platform's AI output filter (`app.ai.output_filter`): a text that reads as a trade call,
  advice or a guarantee - blocked, or one the filter would only frame as a specific call - is withheld (replaced by a
  neutral line), never shown;
- lengths, list sizes, enums and coordinates are bounded; an out-of-image point is dropped, not drawn off-screen;
- no order field, no score, no confidence number survives (unknown keys are dropped);
- the image-only label and the educational disclaimer are set here, so no engine can leave them out.
"""
import math
import re
from typing import Any, Dict, List, Optional

IMAGE_ONLY_LABEL = "Image-only analysis: prices and volume are read from the picture and may be approximate"
DISCLAIMER = ("Educational only. This describes the situation on the chart and the possibilities, as the method reads "
              "them. It is not investment advice or a recommendation to buy or sell, and ATP places no order from it.")
WITHHELD = "(This text was withheld: it read as advice rather than a description.)"
CONTRACT_VERSION = 1

TEXT_MAX = 600
SHORT_MAX = 160
NAME_MAX = 80
MAX_PARAGRAPHS = 8
MAX_ITEMS = 40

TREND_SIDES = ("up", "down", "sideways", "unclear")
PHASES = ("pullback", "handover", "commitment", "trending", "range", "unclear")
DIVERGENCE = ("bullish", "bearish", "none")
SETUP_SIDES = ("bull_put", "bear_call", "none")
SETUP_LABELS = ("A", "B")
SETUP_STATUS = ("formed", "forming", "none")
SWING_LABELS = ("HH", "HL", "LH", "LL")
AREA_SIDES = ("buyer", "seller")
AREA_STATES = ("fresh", "tested", "broken", "flipped")
SETUP_LINE_KINDS = ("trigger", "invalidation", "next_area")
ALERT_KINDS = ("above", "below", "between")
LEVEL_KINDS = ("area", "line")
_TIMEFRAME = re.compile(r"^[0-9]{1,3}\s?(?:s|m|min|h|hr|d|w|mo|M|D|W|H)$|^(?:1D|1W|1M|day|week|month)$")


class ContractError(ValueError):
    """The engine's answer does not fill the contract (the analysis is marked failed, nothing partial is shown)."""


def _screen(text: str) -> str:
    from app.ai import output_filter
    from app.chart_analyze import signal_language
    checked = output_filter.screen(text, where="chart_analyze")
    # A blocked text AND a text the filter would only frame as a specific call (an action next to a price) are withheld:
    # this page describes conditions, it never carries a call, framed or not. Then the word-level signal-language check:
    # any buy / sell / target / stop word withholds the text too (structure words only).
    if not checked.ok or checked.framed:
        return WITHHELD
    return checked.text if signal_language.is_clean(checked.text) else WITHHELD


def text(value: Any, limit: int = TEXT_MAX) -> str:
    """One clean line of the engine's text (or ''), filtered for advice."""
    if not isinstance(value, str):
        return ""
    out = " ".join(value.replace("\x00", " ").split())
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "..."
    return _screen(out) if out else ""


def _enum(value: Any, allowed: tuple, default: Optional[str]) -> Optional[str]:
    if isinstance(value, str):
        v = value.strip()
        for a in allowed:
            if v.lower() == a.lower():
                return a
    return default


def price(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    except ValueError:
        return None
    if not math.isfinite(number) or number <= 0 or number > 1e9:
        return None
    return round(number, 4)


class _Space:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height

    def x(self, value: Any) -> Optional[float]:
        return self._coord(value, self.width)

    def y(self, value: Any) -> Optional[float]:
        return self._coord(value, self.height)

    @staticmethod
    def _coord(value: Any, bound: int) -> Optional[float]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number) or number < 0 or number > bound:
            return None
        return round(number, 1)

    def point(self, raw: Any) -> Optional[Dict[str, float]]:
        if not isinstance(raw, dict):
            return None
        x, y = self.x(raw.get("x")), self.y(raw.get("y"))
        return None if x is None or y is None else {"x": x, "y": y}

    def segment(self, raw: Any) -> Optional[Dict[str, float]]:
        if not isinstance(raw, dict):
            return None
        x0, y0, x1, y1 = self.x(raw.get("x0")), self.y(raw.get("y0")), self.x(raw.get("x1")), self.y(raw.get("y1"))
        if x0 is None or y0 is None or x1 is None or y1 is None:
            return None
        return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _obj(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> List[Any]:
    return list(value)[:MAX_ITEMS] if isinstance(value, list) else []


def _ref(raw: Any) -> Dict[str, Any]:
    """{text, price} - a condition with an optional (approximate) price."""
    raw = raw if isinstance(raw, dict) else {"text": raw}
    return {"text": text(raw.get("text"), SHORT_MAX * 2), "price": price(raw.get("price"))}


def _band(raw: Dict[str, Any]) -> Dict[str, Optional[float]]:
    low, high = price(raw.get("price_low")), price(raw.get("price_high"))
    if low is not None and high is not None and low > high:
        low, high = high, low
    return {"price_low": low, "price_high": high}


def _annotations(raw: Any, space: _Space) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    swings = []
    for s in _items(raw.get("swings")):
        point = space.point(s)
        label = _enum(s.get("label"), SWING_LABELS, None) if isinstance(s, dict) else None
        if point and label:
            swings.append({"label": label, **point, "price": price(s.get("price"))})
    areas = []
    for i, a in enumerate(_items(raw.get("areas"))):
        box = space.segment(a)
        side = _enum(a.get("side"), AREA_SIDES, None) if isinstance(a, dict) else None
        if box and side:
            areas.append({"id": text(a.get("id"), NAME_MAX) or f"area-{i + 1}", "side": side,
                          "state": _enum(a.get("state"), AREA_STATES, "fresh"), **box, **_band(a), "note": text(a.get("note"), SHORT_MAX)})
    lines = []
    for i, line in enumerate(_items(raw.get("lines"))):
        seg = space.segment(line)
        if seg:
            touches = [p for p in (space.point(t) for t in _items(line.get("touches"))) if p]
            lines.append({"id": text(line.get("id"), NAME_MAX) or f"line-{i + 1}", "name": text(line.get("name"), NAME_MAX), **seg,
                          "touches": touches, "note": text(line.get("note"), SHORT_MAX)})
    div = raw.get("rsi_divergence") if isinstance(raw.get("rsi_divergence"), dict) else None
    divergence = None
    if div is not None:
        on_price = space.segment(div.get("price"))
        kind = _enum(div.get("kind"), DIVERGENCE[:2], None)
        if on_price and kind:
            divergence = {"kind": kind, "price": on_price, "rsi": space.segment(div.get("rsi")), "note": text(div.get("note"), SHORT_MAX)}

    def mark(value: Any) -> Optional[Dict[str, Any]]:
        point = space.point(value)
        return {**point, "note": text(value.get("note"), SHORT_MAX)} if point else None

    setup_lines = []
    for s in _items(raw.get("setup_lines")):
        kind = _enum(s.get("kind"), SETUP_LINE_KINDS, None) if isinstance(s, dict) else None
        y = space.y(s.get("y")) if isinstance(s, dict) else None
        if kind and y is not None:
            setup_lines.append({"kind": kind, "y": y, "price": price(s.get("price")), "note": text(s.get("note"), SHORT_MAX)})
    return {"swings": swings, "areas": areas, "lines": lines, "rsi_divergence": divergence,
            "commitment_candle": mark(raw.get("commitment_candle")), "ltf_choch": mark(raw.get("ltf_choch")), "setup_lines": setup_lines}


def _alert(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = _enum(raw.get("kind"), ALERT_KINDS, None)
    if kind == "between":
        band = _band({"price_low": raw.get("low"), "price_high": raw.get("high")})
        if band["price_low"] is None or band["price_high"] is None:
            return None
        return {"kind": kind, "low": band["price_low"], "high": band["price_high"]}
    if kind in ("above", "below") and price(raw.get("price")) is not None:
        return {"kind": kind, "price": price(raw.get("price"))}
    return None


def _recognised(raw: Any) -> Dict[str, Optional[str]]:
    raw = raw if isinstance(raw, dict) else {}
    symbol = text(raw.get("symbol"), 40).upper()
    exchange = str(raw.get("exchange") or "").strip().upper()
    tf = text(raw.get("timeframe"), 12)
    return {"symbol": symbol if re.fullmatch(r"[A-Z0-9 &._:-]{1,40}", symbol) else None,
            "exchange": exchange if re.fullmatch(r"[A-Z]{2,10}", exchange) else None,
            "timeframe": tf if tf and _TIMEFRAME.match(tf) else None,
            "first_bar": text(raw.get("first_bar"), 40) or None, "last_bar": text(raw.get("last_bar"), 40) or None}


def validate(raw: Any, *, mode: str, width: int, height: int) -> Dict[str, Any]:
    """The engine's parsed answer, made safe and complete, or ContractError when a required block is missing."""
    if not isinstance(raw, dict):
        raise ContractError("the engine's answer is not an object")
    story = [p for p in (text(s) for s in _items(raw.get("story"))[:MAX_PARAGRAPHS]) if p]
    if not story:
        raise ContractError("the answer has no story")
    trend_raw = _obj(raw.get("trend"))
    if not isinstance(raw.get("setup"), dict) or not isinstance(raw.get("scenarios"), dict) or "side" not in trend_raw:
        raise ContractError("the answer is missing the trend side, the setup or the scenarios")
    setup_raw, scen_raw = _obj(raw.get("setup")), _obj(raw.get("scenarios"))
    status = _enum(setup_raw.get("status"), SETUP_STATUS, "none")
    setup = {
        "side": _enum(setup_raw.get("side"), SETUP_SIDES, "none"),
        "label": _enum(setup_raw.get("label"), SETUP_LABELS, None) if status != "none" else None,
        "status": status,
        "waiting_for": (text(setup_raw.get("waiting_for"), SHORT_MAX * 2) or None) if status == "forming" else None,
        "trigger": _ref(setup_raw.get("trigger")), "invalidation": _ref(setup_raw.get("invalidation")),
        "next_areas": [_ref(n) for n in _items(setup_raw.get("next_areas"))[:6]],
    }
    evidence = []
    for e in _items(raw.get("evidence")):
        if isinstance(e, dict) and text(e.get("name"), NAME_MAX) and isinstance(e.get("present"), bool):
            evidence.append({"name": text(e.get("name"), NAME_MAX), "present": e["present"], "reason": text(e.get("reason"), SHORT_MAX)})
    wait_raw = raw.get("what_to_wait_for") if isinstance(raw.get("what_to_wait_for"), dict) else None
    wait = None
    if wait_raw is not None and status != "formed" and text(wait_raw.get("condition"), SHORT_MAX * 2):
        wait = {"condition": text(wait_raw.get("condition"), SHORT_MAX * 2), "alert": _alert(wait_raw.get("alert"))}
    htf = []
    for h in _items(raw.get("htf_context"))[:6]:
        if isinstance(h, dict) and text(h.get("timeframe"), 12):
            htf.append({"timeframe": text(h.get("timeframe"), 12), "trend": _enum(h.get("trend"), TREND_SIDES, "unclear"),
                        "nearest_areas": [{"side": _enum(a.get("side"), AREA_SIDES, None), "state": _enum(a.get("state"), AREA_STATES, None),
                                           **_band(a)} for a in _items(h.get("nearest_areas"))[:4] if isinstance(a, dict)]})
    levels = []
    for lv in _items(raw.get("key_levels")):
        if isinstance(lv, dict) and _enum(lv.get("kind"), LEVEL_KINDS, None):
            band = _band(lv)
            if band["price_low"] is None and band["price_high"] is None:
                continue
            levels.append({"kind": _enum(lv.get("kind"), LEVEL_KINDS, None), "name": text(lv.get("name"), NAME_MAX),
                           "side": _enum(lv.get("side"), AREA_SIDES, None), "state": _enum(lv.get("state"), AREA_STATES, None), **band})
    rsi_raw = _obj(raw.get("rsi"))
    state_raw = _obj(raw.get("current_state"))
    space = _Space(width, height)
    result: Dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "mode": mode,
        "label": IMAGE_ONLY_LABEL if mode == "image_only" else None,
        "disclaimer": DISCLAIMER,
        "approximate_prices": mode == "image_only",
        "coordinate_space": "image_pixels" if mode == "image_only" else "time_price",
        "image": {"width": width, "height": height},
        "recognised": _recognised(raw.get("recognised")),
        "story": story,
        "trend": {"side": _enum(trend_raw.get("side"), TREND_SIDES, "unclear"), "text": text(trend_raw.get("text"), SHORT_MAX * 2)},
        "current_state": {"phase": _enum(state_raw.get("phase"), PHASES, "unclear"), "text": text(state_raw.get("text"), SHORT_MAX * 2)},
        "rsi": {"divergence": _enum(rsi_raw.get("divergence"), DIVERGENCE, "none"), "text": text(rsi_raw.get("text"), SHORT_MAX * 2)},
        "setup": setup,
        "evidence": evidence,
        "scenarios": {"primary": text(scen_raw.get("primary")), "alternative": text(scen_raw.get("alternative")),
                      "primary_invalidated_at": _ref(scen_raw.get("primary_invalidated_at"))},
        "what_to_wait_for": wait,
        "htf_context": htf,
        "key_levels": levels,
        "annotations": _annotations(raw.get("annotations"), space),
    }
    if not result["scenarios"]["primary"] or not result["scenarios"]["alternative"]:
        raise ContractError("the answer is missing a scenario")
    return result
