"""N3 S9 (spec §5 Copilot tools): the screener for the Copilot agent - build_screen, run_screen(as_of), explain_results
and propose_alert (the spec's create_alert: a proposal a person confirms). Flag `screener_copilot` (off by default; the
screener itself needs `screener_v2`); while it is off the tools are not offered to the model and do not run.

* The model writes ScreenQL; our validator, flag gates and runtime decide everything else - the same code the Screener
  page uses (`screener.routes`, `history_routes`, `evidence`), on the caller's own organisation and session.
* run_screen: live, on server bars through the organisation's (or under BT1b the caller's own) session - the run is
  stored like any Screener run, so a list someone acted on can be reproduced; with `as_of`, who passed on that past
  date from the shared end-of-day lake (S6, flag `screener_history`).
* explain_results: per symbol, each stage's pass / fail and each condition's own numbers ("close 1,842.00 > SMA(close,
  20) 1,830.40") from the evidence pack - the engine's numbers, never the model's.
* propose_alert: a PROPOSED `CREATE_ALERT` action for the person who asked (ADR-0006); the rule is created only when
  they (or the OWNER) approve it under AI Copilot, checked again then. An alert notifies; it never places an order.
Matches are what passed the filters on the data shown - never advice.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools import Tool, ToolContext, ToolResult, register

FLAG = "screener_copilot"
SCREENER_FLAG = "screener_v2"
TF = r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$"
EXCHANGE = r"^(NSE|BSE|NFO|BFO|MCX|CDS)$"
MAX_EXPLAIN = 10
MAX_ROWS_PER_SYMBOL = 12


class _Screen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=3, max_length=2000, description="The screen in ScreenQL, e.g. close > SMA(close, 50) AND RSI(14) > 55")
    base_tf: str = Field(default="1d", pattern=TF, description="The screen's timeframe")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict, description="Values for $parameters, if the text has any")


class _Run(_Screen):
    symbols: List[str] = Field(min_length=1, max_length=50, description="The symbols to run it on (at most 50)")
    exchange: str = Field(default="NSE", pattern=EXCHANGE)
    as_of: Optional[date] = Field(default=None, description="A past date: who passed on that day (daily screens, end-of-day data); omit for now")


class _Explain(_Screen):
    symbols: List[str] = Field(min_length=1, max_length=MAX_EXPLAIN, description="The symbols to explain (at most 10)")
    exchange: str = Field(default="NSE", pattern=EXCHANGE)


class _Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=300, description="Why, from the tool results - shown to the person who decides")
    quote: str = Field(min_length=2, max_length=300, description="The trader's own words that ask for the alert, copied exactly from their message")
    symbol: str = Field(pattern=r"^[A-Za-z0-9&._:-]{1,40}$", description="The instrument the condition watches (its exchange symbol)")
    condition: str = Field(min_length=3, max_length=2000, description="The condition in ScreenQL (a validated build_screen text)")
    base_tf: str = Field(default="1d", pattern=TF)
    exchange: str = Field(default="NSE", pattern=EXCHANGE)


def _now(ctx: ToolContext) -> str:
    return ctx.now.isoformat()


def _detail(exc: HTTPException) -> str:
    d = exc.detail
    if isinstance(d, dict):
        msgs = [p.get("message") for p in d.get("problems") or [] if isinstance(p, dict)]
        return "; ".join([str(d.get("message") or "")] + [m for m in msgs if m])[:400]
    return str(d)[:400]


async def _may_run(ctx: ToolContext, source: str) -> Optional[ToolResult]:
    """Review MED-1: a run for the caller passes the Screener route's own gate - the trading role and the shared
    per-user run limit (`routes.run_allowed`)."""
    from app.screener.routes import run_allowed
    try:
        await run_allowed(ctx.session, ctx.user)
    except HTTPException as exc:
        return ToolResult(False, None, None, source, error=_detail(exc))
    return None


def neutral(text: str, fallback: str) -> str:
    """Review MED-2: model-written words that reach a person (an alert's name, a proposal's note) pass the H-C1 c
    output filter; refused words are replaced by a neutral text built from the facts."""
    from app.ai import output_filter
    ok, shown, _ = output_filter.check(text, "en", where="screener_copilot")
    return shown.strip() if ok and shown and shown.strip() == text.strip() else fallback


FIXED_NAME = "Copilot alert"


def final_name(name: str) -> str:
    """Re-check (MED): the FINAL alert name - the model's or the fallback - passes the output filter, else fixed text."""
    return neutral(name.strip()[:120], FIXED_NAME) or FIXED_NAME


def free_text_literals(ast: Any) -> List[str]:
    """Re-check (MED): string literals in a condition other than a function's fixed choices ("promoter", "hammer"...).
    Free text would survive into the canonical condition and from there into the rule and the proposal."""
    from app.screener import nodes
    from app.screener.registry import FUNCTIONS
    allowed: set = set()
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None:
            for arg, value in zip(spec.args, node.args):
                if isinstance(value, nodes.Str) and arg.choices and value.value in arg.choices:
                    allowed.add(id(value))
        if isinstance(node, nodes.Compare):           # a classifier against one of its own values: ZoneRole() == "DEMAND"
            for side, other in ((node.left, node.right), (node.right, node.left)):
                ospec = FUNCTIONS.get(other.name) if isinstance(other, nodes.Call) else None
                if isinstance(side, nodes.Str) and ospec is not None and ospec.values and side.value in ospec.values:
                    allowed.add(id(side))
    return [n.value for n in nodes.walk(ast) if isinstance(n, nodes.Str) and id(n) not in allowed]


async def known_symbol(session: Any, symbol: str) -> bool:
    """Re-check (MED): an alert's symbol is a real instrument - in the instrument master (any broker), an index name,
    or the NSE listing as of today."""
    from sqlalchemy import or_, select

    from app.db.models import InstrumentRecord
    from app.instruments.master import INDEX_SYMBOLS
    sym = symbol.strip().upper()
    if sym in INDEX_SYMBOLS or sym in {v.upper() for v in INDEX_SYMBOLS.values()}:
        return True
    found = await session.scalar(select(InstrumentRecord.id).where(or_(InstrumentRecord.tradingsymbol == sym,
                                                                       InstrumentRecord.underlying == sym)).limit(1))
    if found is not None:
        return True
    from app.universe.asof import isin_for
    return await isin_for(session, sym, date.today()) is not None


async def _screener_on(ctx: ToolContext) -> Optional[ToolResult]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(ctx.session, SCREENER_FLAG, ctx.tenant_id):
        return ToolResult(False, None, None, "screener", error=f"the Screener is not enabled for this organisation (flag {SCREENER_FLAG})")
    return None


def _stages(ast: Any) -> List[str]:
    from app.screener import nodes
    items = list(ast.items) if isinstance(ast, nodes.Logic) and ast.op == "ALL" else [ast]
    return [nodes.to_text(i) for i in items]


async def build_screen(ctx: ToolContext, args: _Screen) -> ToolResult:
    """Check a ScreenQL screen: the canonical text, its stages, the problems (with positions) and the plan. Runs nothing."""
    off = await _screener_on(ctx)
    if off:
        return off
    from app.screener.routes import _compiled, _gate
    ast, validated = _compiled(args.text, args.base_tf, dict(args.params))
    await _gate(ctx.session, ctx.user, ast, validated, dict(args.params))
    from app.screener import nodes
    data = {"ok": validated.ok, "text": nodes.to_text(ast) if ast is not None else None, "stages": _stages(ast) if ast is not None else [],
            "problems": [p.as_dict() for p in validated.problems], "base_tf": args.base_tf,
            "plan": {"lookback": validated.lookback, "cost": round(validated.cost, 2), "cross_sectional": validated.cross_sectional}}
    return ToolResult(True, data, _now(ctx), "screenql_validator", {"validated_at": _now(ctx)})


async def run_screen(ctx: ToolContext, args: _Run) -> ToolResult:
    """Run a screen on up to 50 symbols: now (last closed bars, stored as a Screener run) or on a past date (as_of)."""
    off = await _screener_on(ctx) or await _may_run(ctx, "screener_run")
    if off:
        return off
    try:
        if args.as_of is not None:
            from app.screener.history_routes import AsOfBody, Universe, asof
            if args.base_tf != "1d":
                return ToolResult(False, None, None, "screen_history", error="as_of runs daily screens only (base_tf 1d)")
            out = await asof(AsOfBody(source=args.text, params=dict(args.params), universe=Universe(symbols=args.symbols), date=args.as_of),
                             user=ctx.user, session=ctx.session)
            data = {"as_of_date": out["date"], "text": out["text"], "matched": out["matched"], "universe": out["universe"],
                    "not_evaluated": dict(list(out["not_evaluated"].items())[:20]), "notes": out["notes"], "data": out["data"],
                    "disclaimer": out["disclaimer"]}
            return ToolResult(True, data, out["date"], "screen_history", {"bars_closed_by": out["date"]})
        from app.screener.routes import RunBody, run
        out = await run(RunBody(source=args.text, base_tf=args.base_tf, params=dict(args.params), symbols=args.symbols, exchange=args.exchange),
                        user=ctx.user, session=ctx.session)
    except HTTPException as exc:
        return ToolResult(False, None, None, "screener", error=_detail(exc))
    not_run = {r["symbol"]: r["reason"] for r in out["results"] if not r["matched"] and r.get("reason")}
    data = {"run_id": out["run_id"], "text": out["text"], "base_tf": out["base_tf"], "matched": out["matched"], "scanned": out["scanned"],
            "not_evaluated": dict(list(not_run.items())[:20]),
            "stages": [{"text": st["text"], "survivors": st["survivors"]} for st in out["funnel"]["stages"]],
            "data_source": out["data_source"], "disclaimer": out["disclaimer"]}
    stamps = {"run_at": _now(ctx), "bars": "the last closed bar of each symbol"}
    if out.get("data_tier"):
        stamps["data_tier"] = out["data_tier"]
    return ToolResult(True, data, _now(ctx), "screener_run", stamps)


async def explain_results(ctx: ToolContext, args: _Explain) -> ToolResult:
    """Why each symbol passed or not: every stage's pass / fail and every condition's own numbers on its last closed bar."""
    import asyncio

    off = await _screener_on(ctx) or await _may_run(ctx, "screen_evidence")
    if off:
        return off
    from app.screener import evidence
    from app.screener import ranking as RK
    from app.screener.routes import _compiled, _gate, load_universe, missing_data
    from app.screener.runtime import ScreenRuntimeError, _cross_sectional
    params = dict(args.params)
    ast, validated = _compiled(args.text, args.base_tf, params)
    await _gate(ctx.session, ctx.user, ast, validated, params, args.exchange)
    if not validated.ok or ast is None:
        return ToolResult(False, None, None, "screener", error="the screen did not pass validation: "
                          + "; ".join(p.message for p in validated.problems)[:300])
    missing = missing_data(ast)
    if missing:
        return ToolResult(False, None, None, "screener", error=f"{', '.join(m + '()' for m in missing)} need data the screener does not load yet")
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols if s.strip()))
    try:
        universe, problems, source, systems = await load_universe(ctx.session, ctx.user, ast, validated, params, symbols, args.exchange, args.base_tf)
    except HTTPException as exc:
        return ToolResult(False, None, None, "screener", error=_detail(exc))

    def packs() -> Dict[str, Any]:
        constants = (RK.within(systems, _cross_sectional, ast, universe, args.base_tf, params) if validated.cross_sectional
                     else {d.symbol: {} for d in universe})
        out: Dict[str, Any] = {}
        for d in universe:
            try:
                pack = evidence.build_pack(ast, validated, d, base_tf=args.base_tf, params=params, constants=constants[d.symbol],
                                           exchange=args.exchange, data_source=source)
            except ScreenRuntimeError as exc:
                out[d.symbol] = {"error": str(exc)[:200]}
                continue
            out[d.symbol] = {"matched": pack["matched"], "bar_close": pack["as_of"]["bar_close"],
                             "stages": [{"text": b["text"], "passed": b["passed"]} for b in pack["blocks"]],
                             "conditions": [{"verify": r["verify"], "passed": r["passed"]} for r in pack["rows"][:MAX_ROWS_PER_SYMBOL]]}
        return out
    explained = await asyncio.to_thread(packs)
    for sym, why in problems.items():
        explained[sym] = {"error": why}
    stamps = {s: v.get("bar_close") for s, v in explained.items() if v.get("bar_close")}
    from app.screener import nodes
    data = {"text": nodes.to_text(ast), "symbols": explained, "data_source": source,
            "note": "Each condition's numbers are the screen engine's own, on the last closed bar shown; a rank (if any) is across these symbols only."
            if validated.cross_sectional else "Each condition's numbers are the screen engine's own, on the last closed bar shown."}
    return ToolResult(True, data, max(stamps.values()) if stamps else _now(ctx), "screen_evidence", stamps)


async def propose_alert(ctx: ToolContext, args: _Alert) -> ToolResult:
    """Propose an alert rule on one instrument's condition (checked now). The person who asked approves or rejects it under
    AI Copilot; the rule exists only after approval. An alert notifies, it never places an order."""
    from app.ai import monitor
    from app.alerts.rules_routes import FLAG as ALERTS_FLAG
    from app.alerts.rules_routes import RuleBody, _checked
    from app.platform.controls import flag_enabled
    if not await flag_enabled(ctx.session, ALERTS_FLAG, ctx.tenant_id):
        return ToolResult(False, None, None, "ai_actions", error=f"alerts are not enabled for this organisation (flag {ALERTS_FLAG})")
    symbol = args.symbol.strip().upper()
    if not await known_symbol(ctx.session, symbol):
        return ToolResult(False, None, None, "ai_actions", error=f"{symbol} is not a known instrument (instrument master, index or NSE listing)")
    from app.screener import compile_screen
    parsed, _ = compile_screen(args.condition, base_tf=args.base_tf)
    texts = free_text_literals(parsed) if parsed is not None else []
    if texts:
        return ToolResult(False, None, None, "ai_actions", error="a Copilot alert condition takes no free-text literals (only a function's fixed choices)")
    body = RuleBody(name=f"{symbol} alert", kind="instrument", symbol=symbol, condition=args.condition, base_tf=args.base_tf,
                    exchange=args.exchange)
    try:
        target = await _checked(ctx.session, ctx.user, body)
    except HTTPException as exc:
        return ToolResult(False, None, None, "ai_actions", error=f"the alert would be refused: {_detail(exc)}")
    # Review MED-2: the name goes out in every fire's title and the note to the person deciding - filtered; refused words
    # give way to neutral text built from the facts
    # Re-check follow-up: the name is built, never the model's - "<symbol>: <condition>" (the condition is literal-free)
    name = final_name(f"{target['symbol']}: {target['condition_text']}")
    note = neutral(args.reason.strip(), "The trader asked for this alert.")
    rule = {**body.model_dump(mode="json", exclude_none=True), "name": name, "condition": target["condition_text"]}
    # review LOW-3: the asker is part of the key - two members proposing the same alert are two proposals
    key = hashlib.sha256(json.dumps({**rule, "for": ctx.user.id}, sort_keys=True).encode()).hexdigest()[:12]
    sentence = f"Create the alert \"{name}\": {target['condition_text']} on {target['symbol']} ({body.base_tf}, bar close)."
    reason = neutral(f"{sentence} Asked by the trader (\"{args.quote.strip()}\"). AI note: {note}",
                     f"Create a Copilot alert on {target['symbol']} ({body.base_tf}, bar close), as the trader asked.")
    evidence = {"source": "copilot_agent", "quote": args.quote.strip(), "agent_reason": note, "user_id": ctx.user.id, "rule": rule}
    created = await monitor.raise_proposals(ctx.session, ctx.tenant_id, [monitor.Proposal(None, None, "CREATE_ALERT", f"AGENT_ALERT:{key}", reason,
                                                                                         evidence, owner_user_id=ctx.user.id)], ctx.now)
    if not created:
        return ToolResult(True, {"created": False, "reason": "the same alert is already proposed, or was decided today"}, _now(ctx), "ai_actions")
    row = created[0]
    return ToolResult(True, {"created": True, "proposal_id": row.id, "action": "CREATE_ALERT", "status": row.status,
                             "note": "The person who asked must approve or reject this under AI Copilot; no alert exists until then."},
                      _now(ctx), "ai_actions")


for _tool in (
    Tool("build_screen", build_screen.__doc__ or "", _Screen, build_screen, flag=FLAG),
    Tool("run_screen", run_screen.__doc__ or "", _Run, run_screen, cost_units=3, timeout_seconds=45.0, flag=FLAG),
    Tool("explain_results", explain_results.__doc__ or "", _Explain, explain_results, cost_units=3, timeout_seconds=45.0, flag=FLAG),
    Tool("propose_alert", propose_alert.__doc__ or "", _Alert, propose_alert, kind="proposal", cost_units=2, flag=FLAG),
):
    register(_tool)


__all__ = ["FLAG", "build_screen", "run_screen", "explain_results", "propose_alert"]
