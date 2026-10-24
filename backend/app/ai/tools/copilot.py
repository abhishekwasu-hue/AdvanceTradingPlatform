"""Item 5 (owner's handover, AI Copilot as a trader's brain, first slice): the read tools behind the chat-first Copilot's
jobs 1 (pre-market brief), 2 (chart reader) and 7 (option chain and risk).

Each tool wraps a read the platform already serves to the same user, so the Copilot sees exactly what the page would:

* `get_market_context`   - the market context panel (`GET /api/context/panel`, app/context_panel/service.py): the NIFTY 50
                           Dow structure on daily / weekly bars, breadth, India VIX with its rank, FII / DII, global cues
                           (US markets, the US 10-year yield, the dollar) and this week's events. Flag `context_panel`.
* `get_chart_structure`  - ATP's price-action layer (`GET /api/charts/layers/price-action`, app/charts/price_action_layer.py):
                           the structure state per timeframe (W / D / 1H / 15m), the protected level, the last BOS / CHoCH
                           and the nearest unbroken swing levels. Shared lake bars only. Flag `chart_price_action`.
* `get_market_zones`     - ATP's market-zones layer (`GET /api/charts/layers/market-zones`, app/charts/zones_layer.py): the
                           stored demand / supply, order-block, S/R and gap zones nearest the price. Flag `market_zones`.
* `get_option_chain_analyses` - the six built-in option chain analyses (`GET /api/option-chain/analyses`,
                           app/option_chain/analyses_routes.py) on the user's own broker chain. Flag `ui_v2`.

Until the Trade reader is ported, the chart reading is "structure reading from ATP's zone layers" - the answer says so.
All four are `read` tools: none writes, none places / blocks / changes an order (ADR-0006). The outputs stay small.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools import Tool, ToolContext, ToolResult, register

SYMBOL = Field(default="NIFTY", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 &._:-]+$",
               description="An index (NIFTY, BANKNIFTY, FINNIFTY) or an NSE stock symbol")
MAX_EVENTS = 8
MAX_LEVELS = 6
MAX_BANDS = 6
STRUCTURE_MIN_BARS = 30    # DH5: bars a timeframe's structure read needs before the Copilot states it
TF_WORDS = {"1w": "weekly", "1d": "daily", "1h": "1-hour", "15m": "15-minute"}
# The NSE index names the shared lake keys indices by (app/context_panel/service.index_key).
INDEX_NAMES = {"NIFTY": "NIFTY 50", "NIFTY 50": "NIFTY 50", "NIFTY50": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "NIFTY BANK": "NIFTY BANK",
               "NIFTYBANK": "NIFTY BANK", "FINNIFTY": "NIFTY FINANCIAL SERVICES", "NIFTY FIN SERVICE": "NIFTY FINANCIAL SERVICES",
               "MIDCPNIFTY": "NIFTY MIDCAP SELECT", "INDIA VIX": "INDIA VIX"}


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ChartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = SYMBOL
    timeframe: Literal["15m", "1h", "1d"] = Field(default="1d", description="The chart timeframe the levels are read on")


class _ZoneArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = SYMBOL
    price: Optional[float] = Field(default=None, gt=0, description="The reference price; omit for the snapshot's last close")


class _ChainArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = SYMBOL
    expiry: Optional[date] = Field(default=None, description="YYYY-MM-DD; omit for the nearest expiry")


def lake_key(symbol: str) -> str:
    """The shared lake's instrument key for an index name or an NSE stock symbol ("NIFTY" -> "NSE_INDEX:NIFTY50"); a
    BSE index (SENSEX, BANKEX) -> "BSE_INDEX:SENSEX" (DATA-H: the key its history is stored under)."""
    from app.context_panel.service import index_key
    from app.instruments.master import INDEX_EXCHANGE
    value = " ".join(symbol.strip().upper().split())
    if ":" in value:
        return value
    if INDEX_EXCHANGE.get(value) == "BSE":
        return f"BSE_INDEX:{value}"
    if value in INDEX_NAMES:
        return index_key(INDEX_NAMES[value])
    return f"NSE:{value.replace(' ', '')}"


def _refused(source: str, exc: HTTPException) -> ToolResult:
    return ToolResult(False, None, None, source, error=str(exc.detail)[:300])


# --- job 1: the market context panel ------------------------------------------------------------------------------------

def _tile(tiles: Dict[str, Any], key: str) -> Dict[str, Any]:
    tile = tiles.get(key) or {}
    return tile if isinstance(tile, dict) else {}


def summarise_context(panel: Dict[str, Any]) -> Dict[str, Any]:
    """The panel's tiles, trimmed for the model: every number kept, the method lines dropped."""
    tiles = panel.get("tiles") or {}
    regime, vix, flows, glob, events, breadth = (_tile(tiles, k) for k in ("regime", "vix", "fii_dii", "global", "events", "breadth"))
    out: Dict[str, Any] = {"as_of": panel.get("as_of")}
    out["structure"] = ({"status": regime.get("status"), "market": regime.get("market"), "close": regime.get("close"), "as_of": regime.get("as_of"),
                         "daily": (regime.get("daily") or {}).get("trend"), "weekly": (regime.get("weekly") or {}).get("trend")}
                        if regime.get("status") != "na" else {"status": "na", "reason": regime.get("reason")})
    out["vix"] = ({"status": vix.get("status"), "close": vix.get("close"), "change_pct": vix.get("change_pct"), "percentile": vix.get("percentile"),
                   "as_of": vix.get("as_of")} if vix.get("status") != "na" else {"status": "na", "reason": vix.get("reason")})
    out["fii_dii"] = ({k: v for k, v in flows.items() if k not in ("source",)} if flows.get("status") != "na"
                      else {"status": "na", "reason": flows.get("reason")})
    out["breadth"] = ({k: breadth.get(k) for k in ("status", "advances", "declines", "unchanged", "ratio", "as_of")} if breadth.get("status") != "na"
                      else {"status": "na", "reason": breadth.get("reason")})
    out["global"] = ({"status": glob.get("status"), "as_of": glob.get("as_of"),
                      "markets": [{k: m.get(k) for k in ("key", "name", "group", "last", "change_pct")} for m in (glob.get("markets") or [])]}
                     if glob.get("status") != "na" else {"status": "na", "reason": glob.get("reason")})
    if events.get("status") == "ok":
        items = list(events.get("today") or []) + list(events.get("this_week") or [])
        out["events"] = {"status": "ok", "count": len(items),
                         "items": [{k: e.get(k) for k in ("date", "start_time", "kind", "scope", "description", "today")} for e in items[:MAX_EVENTS]]}
    else:
        out["events"] = {"status": "na", "reason": events.get("reason")}
    return out


async def get_market_context(ctx: ToolContext, args: _NoArgs) -> ToolResult:
    """The market context panel: NIFTY 50 structure (daily / weekly), India VIX and its percentile, FII / DII flows,
    breadth, global cues (US markets, US 10-year, dollar, crude) and this week's market events, each with its own date."""
    from app.context_panel import service
    panel = await service.panel(ctx.session, ctx.user, now=ctx.now)
    data = summarise_context(panel)
    return ToolResult(True, data, panel.get("as_of"), "context_panel", {"read_at": panel.get("as_of")})


# --- job 2: the price-action layer and the market zones -------------------------------------------------------------------

def summarise_structure(layer: Dict[str, Any]) -> Dict[str, Any]:
    """Per timeframe the structure direction, the protected level and the last BOS / CHoCH; the nearest live levels.
    DH5: a timeframe with fewer than STRUCTURE_MIN_BARS bars is marked `short` (its direction is not a reading)."""
    from app.context_panel.service import short_history
    rows: List[Dict[str, Any]] = []
    for row in layer.get("mtf") or []:
        last = row.get("last_event") or {}
        bars = int(row.get("bars") or 0)
        short = bars < STRUCTURE_MIN_BARS
        note = short_history(bars, STRUCTURE_MIN_BARS, f"{TF_WORDS.get(str(row.get('tf')), row.get('tf'))} bars",
                             str(layer.get("instrument") or "")) if short else None
        rows.append({"tf": row.get("tf"), "direction": row.get("direction"), "protected": row.get("protected"), "bars": bars,
                     "needed": STRUCTURE_MIN_BARS, "short": short, "note": note, "source": row.get("source"),
                     "as_of": row.get("as_of"), "last_event": last.get("name"), "last_event_price": last.get("price")})
    state = layer.get("structure_state") or {}
    levels = [{"kind": lv.get("kind"), "price": lv.get("price"), "degree": lv.get("degree"), "status": lv.get("status")}
              for lv in (layer.get("levels") or [])[:MAX_LEVELS]]
    return {"instrument": layer.get("instrument"), "tf": layer.get("tf"), "as_of": layer.get("as_of"), "bars": layer.get("bars"),
            "last_close": layer.get("last_close"), "reason": layer.get("reason"),
            "chart_state": {"direction": state.get("direction"), "protected": state.get("protected"),
                            "range_high": state.get("range_high"), "range_low": state.get("range_low"),
                            "last_event": (state.get("last_event") or {}).get("name"),
                            "last_event_price": (state.get("last_event") or {}).get("price")} if state else None,
            "timeframes": rows, "levels": levels}


async def get_chart_structure(ctx: ToolContext, args: _ChartArgs) -> ToolResult:
    """ATP's price-action layer on shared lake bars (and the reader's own stored history): the structure per timeframe
    (weekly, daily, 1-hour, 15-minute) with the protected level and the last break of structure, the nearest unbroken
    swing levels above and below, and the official last close with its date and source."""
    from app.charts import price_action_layer as pal
    from app.context_panel.service import short_history
    from app.market_lake import own_bars
    key = lake_key(args.symbol)
    try:
        layer = await pal.price_action_layer(instrument=key, tf=args.timeframe, at=None, start=None, degree=pal.DEFAULT_DEGREE, debug=False,
                                             mtf=True, user=ctx.user, session=ctx.session)
    except HTTPException as exc:
        return _refused("price_action_layer", exc)
    data = summarise_structure(layer)
    # DH6: the last close the Copilot states is the session's official close from the stored Daily bars, labelled -
    # never a strike, a chain's spot or a placeholder.
    data["official_close"] = await own_bars.official_close(ctx.session, key, await own_bars.scope_for(ctx.session, ctx.user, key))
    if not data["bars"]:
        return ToolResult(False, None, None, "price_action_layer",
                          error=short_history(0, STRUCTURE_MIN_BARS, f"{TF_WORDS.get(args.timeframe, args.timeframe)} bars", key))
    return ToolResult(True, {"symbol": args.symbol.upper(), **data}, data["as_of"], f"price_action_layer:{layer.get('source') or 'lake'}",
                      {"last_bar": data["as_of"]})


def summarise_zones(layer: Dict[str, Any]) -> Dict[str, Any]:
    bands = [{"role": b.get("role"), "zone_type": b.get("zone_type"), "family": b.get("family"), "low": b.get("low"), "high": b.get("high"),
              "state": b.get("state"), "touches": b.get("touches"), "distance_pct": b.get("distance_pct"), "timeframe": b.get("timeframe")}
             for b in (layer.get("bands") or [])[:MAX_BANDS]]
    return {"instrument": layer.get("instrument"), "as_of": layer.get("as_of"), "price": layer.get("price"), "live_count": layer.get("live_count"),
            "fallback": layer.get("fallback"), "reason": layer.get("reason"), "bands": bands}


async def get_market_zones(ctx: ToolContext, args: _ZoneArgs) -> ToolResult:
    """ATP's stored market zones (demand / supply, order blocks, S/R, gaps) nearest the price, with their state and touches."""
    from app.charts import zones_layer as zl
    from app.price_action.zones import chart
    key = lake_key(args.symbol)
    try:
        layer = await zl.market_zones_layer(instrument=key, at=None, price=args.price, family="all", max_bands=chart.MAX_BANDS,
                                            user=ctx.user, session=ctx.session)
    except HTTPException as exc:
        return _refused("market_zones", exc)
    data = summarise_zones(layer)
    if layer.get("as_of") is None:
        return ToolResult(False, None, None, "market_zones", error=layer.get("reason") or "no zones for this instrument")
    return ToolResult(True, {"symbol": args.symbol.upper(), **data}, data["as_of"], "market_zones", {"snapshot": data["as_of"]})


# --- job 7: the option chain analyses ------------------------------------------------------------------------------------

def summarise_analyses(out: Dict[str, Any]) -> Dict[str, Any]:
    """The analyses' values, sentences and tables / charts (kept for the page; the model reads values and sentences)."""
    return {"symbol": out.get("symbol"), "expiry": out.get("expiry"), "state": out.get("state"), "message": out.get("message"),
            "spot": out.get("spot"), "data_as_of": out.get("data_as_of"),
            "analyses": [{"key": a.get("key"), "title": a.get("title"), "state": a.get("state"), "values": a.get("values") or {},
                          "sentences": a.get("sentences") or [], "method": a.get("method"), "tables": a.get("tables") or [],
                          "chart": a.get("chart")} for a in out.get("analyses") or []]}


async def get_option_chain_analyses(ctx: ToolContext, args: _ChainArgs) -> ToolResult:
    """The six option chain analyses on the user's own broker chain: support / resistance from OI, OI build-up, PCR,
    max pain, the expected move (ATM straddle) and the IV skew, each with its numbers and fixed sentences."""
    from app.option_chain import analyses_routes as oc
    try:
        out = await oc.option_chain_analyses(symbol=args.symbol, expiry=args.expiry, user=ctx.user, session=ctx.session)
    except HTTPException as exc:
        return _refused("option_chain_analyses", exc)
    data = summarise_analyses(out)
    if data["state"] != "ok":
        return ToolResult(False, data, None, "option_chain_analyses", error=data["message"] or data["state"])
    return ToolResult(True, data, data["data_as_of"], f"option_chain_analyses:broker:{(out.get('source') or {}).get('broker', '')}",
                      {"read_at": data["data_as_of"], "expiry": data["expiry"]})


for _tool in (
    Tool("get_market_context", get_market_context.__doc__ or "", _NoArgs, get_market_context, cost_units=2, timeout_seconds=20,
         flag="context_panel"),
    Tool("get_chart_structure", get_chart_structure.__doc__ or "", _ChartArgs, get_chart_structure, cost_units=2, timeout_seconds=20,
         flag="chart_price_action"),
    Tool("get_market_zones", get_market_zones.__doc__ or "", _ZoneArgs, get_market_zones, cost_units=1, timeout_seconds=10, flag="market_zones"),
    Tool("get_option_chain_analyses", get_option_chain_analyses.__doc__ or "", _ChainArgs, get_option_chain_analyses, cost_units=3,
         timeout_seconds=20, flag="ui_v2"),
):
    register(_tool)

__all__ = ["get_market_context", "get_chart_structure", "get_market_zones", "get_option_chain_analyses", "lake_key",
           "summarise_context", "summarise_structure", "summarise_zones", "summarise_analyses"]
