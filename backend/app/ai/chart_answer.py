"""M72 PR-5 (CP-4, finding F9): the planner's chart reading, drawn by code - never by the model.

When the planner read a chart for its answer, the chat shows the same code-drawn parts as the chart reader's own answer:
* the zone layer (everyone; `get_chart_structure` + `get_market_zones`): the headline row with the annotated mini chart
  (the nearest area drawn as a band) and the structure card (`copilot_chat.chart_headline` / `structure_card`);
* the signal engine (only where `trade_engine` is on and the engine is installed; `get_engine_read`): the headline row
  with the nearest engine area drawn as a band - from the tool's PUBLIC result only (price ranges, side, state, the
  higher timeframe's side); no structure card (the engine's per-timeframe table is not public words).
Every number comes from that tool call's own result. And one wording guard for any answer that read a chart: no buy /
sell / target / stop words (`signals.language.call_words`) and no "invalidation" - an area is described as an area of
interest with its price range, never as a level to act on (CLAUDE.md section 3). Pure; reference only (ADR-0006).
"""
from __future__ import annotations

import re
from typing import Any

CHART_TOOLS = ("get_engine_read", "get_chart_structure", "get_market_zones")
_INVALIDATION = re.compile(r"\binvalidat\w*", re.IGNORECASE)


def wording_problem(text: str) -> str | None:
    """Why a chart answer's text cannot be shown (None = it can)."""
    from app.signals.language import call_words
    found = call_words(text or "")
    if _INVALIDATION.search(text or ""):
        found.append("invalidation")
    if found:
        return ("a chart answer describes areas of interest as price ranges, without " + ", ".join(dict.fromkeys(found)) +
                " wording")
    return None


def read_a_chart(calls: list[dict[str, Any]]) -> bool:
    return any(c.get("ok") and c.get("name") in CHART_TOOLS for c in calls)


def _last_ok(calls: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((c for c in reversed(calls) if c.get("ok") and c.get("name") == name), None)


def engine_headline(data: dict[str, Any]) -> dict[str, Any] | None:
    """The headline row of an engine reading (public words only): the last close, the higher timeframe's side, the
    nearest area as a band on the mini chart."""
    from app.ai.copilot_chat import CHART_TF, TF_LABELS, exchange_of, nearest_area
    from app.ai.tools.copilot import INDEX_NAMES
    price = data.get("last_close")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    bands = [{"low": a["low"], "high": a["high"], "role": "demand" if a.get("side") == "buyer" else "supply",
              "timeframe": a.get("timeframe"), "state": a.get("state")} for a in data.get("areas") or []]
    structure = data.get("structure") or {}
    side = structure.get("higher_side")
    tf = str(data.get("timeframe") or "")
    symbol = str(data.get("symbol") or "")
    return {"symbol": symbol, "price": price, "price_label": "Last close", "as_of": data.get("as_of"),
            "trend": side if side in ("up", "down", "sideways") else None,
            "timeframe": TF_LABELS.get(str(structure.get("higher_timeframe") or tf), str(structure.get("higher_timeframe") or tf)),
            "area": nearest_area(float(price), bands),
            "chart": {"symbol": INDEX_NAMES.get(symbol, symbol), "exchange": exchange_of(symbol), "timeframe": CHART_TF.get(tf, "15min" if tf in ("5m", "15m") else "day")}}


def chart_parts(calls: list[dict[str, Any]], results: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(headline, structure card) for the chart the planner read last - the engine's reading when it was read, else the
    zone layer's; (None, None) when no chart was read."""
    from app.ai import copilot_chat
    from app.ai.tools import ToolResult
    engine = _last_ok(calls, "get_engine_read")
    if engine is not None and isinstance(results.get(engine["id"]), dict):
        return engine_headline(results[engine["id"]]), None
    structure = _last_ok(calls, "get_chart_structure")
    if structure is None or not isinstance(results.get(structure["id"]), dict):
        return None, None
    data = results[structure["id"]]
    symbol = str(data.get("symbol") or (structure.get("arguments") or {}).get("symbol") or "").upper()
    tf = str((structure.get("arguments") or {}).get("timeframe") or "1d")
    res = ToolResult(True, data, structure.get("as_of"), "price_action_layer")
    zones_call = next((c for c in reversed(calls) if c.get("ok") and c.get("name") == "get_market_zones"
                       and str((c.get("arguments") or {}).get("symbol") or "").upper() == symbol), None)
    zones = (ToolResult(True, results[zones_call["id"]], zones_call.get("as_of"), "market_zones") if zones_call is not None
             else ToolResult(False, None, None, "market_zones", error="not read"))
    return copilot_chat.chart_headline(symbol, tf, res, zones), copilot_chat.structure_card(symbol, tf, res, zones)


__all__ = ["CHART_TOOLS", "wording_problem", "read_a_chart", "engine_headline", "chart_parts"]
