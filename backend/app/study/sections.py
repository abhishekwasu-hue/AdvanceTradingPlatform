"""M77 SX-5: two more sections of a saved study, read by the server when the study is saved (PD-2.5, PD-2.6).

* AI explanation card (section 5): the card his Copilot chart workspace already read for this chart (M75 W-4,
  app/chart_analyze/workspace.py) - his own latest finished workspace item for the symbol and a studied timeframe, made
  at or before the study time, through the workspace's own `card_of` (grounded: a line with a number that is not in
  ATP's reading stays withheld). Saving a study never starts a model call. "How I worked this out": the reading's
  as-of time, when the card was made, and the card's own notes.
* Option context (section 6): the Option chain page's own read (`GET /api/option-chain/{underlying}/live`, his broker
  session, its entitlement and budget) for the default expiry - expiry, days to expiry, spot, ATM, PCR, max pain, ATM IV,
  the highest-OI strikes near spot (OI walls) and the ATM strike's OI / IV / Greeks. All as numbers, with the chain's
  as-of time. SX-6: the chosen strikes are the option legs of his open trade plan on the chain's expiry, with their OI /
  IV / Greeks from the same chain read; no plan, or legs on another expiry, print as not available with the reason.
Each read returns the section, or {"reason": <plain words>} - the study prints "Not available: <reason>", never a
blank or a guess. Only the caller's own rows; nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

AI_OFF = "the AI part is off for your account"
AI_NONE = "no AI card was read for this chart before the study was saved"
NOT_FNO = "this instrument has no option chain"
NO_CHAIN = "no option chain from your broker session"
NO_PLAN_STRIKES = "no trade plan was open, so there are no chosen strikes"
NO_OPTION_LEGS = "the trade plan has no option legs"
WALLS_EACH_SIDE = 3
CARD_TEXT_KEYS = ("story", "trend", "current_state", "rsi")
CARD_NAMES = {"story": "Story", "trend": "Trend", "current_state": "Current state", "rsi": "RSI"}
LEG_KEYS = ("oi", "iv", "delta", "gamma", "theta", "vega")


def _num(x: Any) -> float | None:
    from app.study.snapshot import _num as num
    return num(x)


# ---------------------------------------------------------------------------------------------------------------------
# Pure parts
# ---------------------------------------------------------------------------------------------------------------------
def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("text", "summary", "words", "condition"):
            if isinstance(value.get(key), str) and value[key].strip():
                return str(value[key]).strip()
    return None


def ai_card_part(answer: Mapping[str, Any], tf: str, made_at: Any) -> dict[str, Any]:
    """The study's copy of a workspace card answer (`workspace.card_of`): its lines as text, the evidence as present /
    not present, and "How I worked this out" (the reading's as-of, when the card was made, its notes)."""
    card = answer.get("card") if isinstance(answer.get("card"), Mapping) else None
    if answer.get("status") != "ready" or card is None:
        return {"reason": str(answer.get("message") or AI_NONE).rstrip(".")}
    lines = [{"name": CARD_NAMES[k], "text": t} for k in CARD_TEXT_KEYS if (t := _text(card.get(k)))]
    scen = card.get("scenarios") if isinstance(card.get("scenarios"), Mapping) else {}
    for key, name in (("primary", "First scenario"), ("alternative", "Other scenario")):
        if (t := _text(scen.get(key))) is not None:
            lines.append({"name": name, "text": t})
    if (t := _text(card.get("what_to_wait_for"))) is not None:
        lines.append({"name": "What to watch", "text": t})
    evidence = [{"name": str(e.get("name")), "present": bool(e.get("present"))} for e in card.get("evidence") or []
                if isinstance(e, Mapping) and e.get("name")]
    when = made_at.astimezone(UTC).isoformat() if isinstance(made_at, datetime) else made_at
    return {"timeframe": tf, "lines": lines, "evidence": evidence,
            "how": {"reading_as_of": answer.get("as_of"), "made_at": when,
                    "notes": [str(n) for n in answer.get("notes") or [] if isinstance(n, str)][:4]},
            "disclaimer": answer.get("disclaimer")}


def chosen_part(rows: Sequence[Mapping[str, Any]], expiry: Any, legs: Sequence[Mapping[str, Any]] | None) -> tuple[list[dict[str, Any]] | None, str | None]:
    """SX-6: (the plan's option legs on the chain's expiry with their OI / IV / Greeks, or None with the reason)."""
    if not legs:
        return None, NO_PLAN_STRIKES
    options = [leg for leg in legs if leg.get("right") in ("CE", "PE")]
    if not options:
        return None, NO_OPTION_LEGS
    same = [leg for leg in options if str(leg.get("expiry")) == str(expiry)]
    if not same:
        return None, f"the trade plan's option legs are on another expiry than the chain read ({expiry})"
    by_strike = {_num(r.get("strike")): r for r in rows}
    out = []
    for leg in same:
        row = by_strike.get(_num(leg.get("strike"))) or {}
        side = row.get("call" if leg["right"] == "CE" else "put") or {}
        out.append({"label": leg.get("label"), "strike": _num(leg.get("strike")), "right": leg["right"], "side": leg.get("side"),
                    "in_chain": bool(row), **{k: _num(side.get(k)) for k in LEG_KEYS}})
    return out, None


def option_context_part(view: Mapping[str, Any], legs: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The study's copy of the Option chain page's view (`chain_view.build_view` + the route's as-of): numbers only."""
    summary: Mapping[str, Any] = view["summary"] if isinstance(view.get("summary"), Mapping) else {}
    rows = [r for r in view.get("rows") or [] if isinstance(r, Mapping) and _num(r.get("strike")) is not None]
    spot = _num(summary.get("spot"))
    atm = _num(summary.get("atm_strike"))

    def oi(r: Mapping[str, Any], side: str) -> float:
        return _num((r.get(side) or {}).get("oi")) or 0.0

    walls: list[dict[str, Any]] = []
    if spot is not None:
        above = sorted((r for r in rows if float(r["strike"]) >= spot and oi(r, "call") > 0), key=lambda r: (-oi(r, "call"), r["strike"]))
        below = sorted((r for r in rows if float(r["strike"]) <= spot and oi(r, "put") > 0), key=lambda r: (-oi(r, "put"), -r["strike"]))
        walls = ([{"side": "call", "strike": _num(r["strike"]), "oi": oi(r, "call")} for r in above[:WALLS_EACH_SIDE]]
                 + [{"side": "put", "strike": _num(r["strike"]), "oi": oi(r, "put")} for r in below[:WALLS_EACH_SIDE]])
    atm_row = next((r for r in rows if atm is not None and _num(r.get("strike")) == atm), None)
    atm_legs = None if atm_row is None else {side: {k: _num((atm_row.get(side) or {}).get(k)) for k in LEG_KEYS} for side in ("call", "put")}
    chosen, chosen_reason = chosen_part(rows, view.get("expiry"), legs)
    return {"underlying": view.get("underlying"), "expiry": view.get("expiry"), "days_to_expiry": _num(summary.get("days_to_expiry")),
            "spot": spot, "spot_source": summary.get("spot_source"), "atm_strike": atm, "pcr_oi": _num(summary.get("pcr_oi")),
            "max_pain": _num(summary.get("max_pain")), "atm_iv": _num(summary.get("atm_iv")), "walls": walls,
            "atm_legs": atm_legs, "chosen_strikes": chosen, "chosen_reason": chosen_reason,
            "as_of": view.get("data_as_of") or view.get("fetched_at")}


def pick_card(rows: Sequence[Mapping[str, Any]], picks: Sequence[Mapping[str, str]]) -> tuple[Mapping[str, Any], str] | None:
    """The first pick (in the order given) with a finished workspace item: (that item, its timeframe). `rows` newest
    first, each {"pick": {...}, "row": <item>}."""
    for pick in picks:
        for r in rows:
            got = r.get("pick") or {}
            if got.get("symbol") == pick["symbol"] and got.get("timeframe") == pick["timeframe"]:
                return r["row"], pick["timeframe"]
    return None


# ---------------------------------------------------------------------------------------------------------------------
# The reads (the caller's own data only)
# ---------------------------------------------------------------------------------------------------------------------
async def ai_card_read(session: AsyncSession, user: User, symbol: str, tfs: Sequence[str], when: datetime) -> dict[str, Any]:
    from app.chart_analyze import FLAG, HYBRID_FLAG, hybrid, service
    from app.chart_analyze import workspace as W
    from app.db.models import ChartAnalysisRecord
    from app.platform.controls import flag_enabled
    for name in (W.WORKSPACE_FLAG, FLAG, HYBRID_FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            return {"reason": AI_OFF}
    picks = []
    for tf in sorted(tfs, key=lambda t: hybrid.TIMEFRAMES.index(t) if t in hybrid.TIMEFRAMES else 99):   # the smallest first
        try:
            picks.append(hybrid.check_pick(symbol, tf))
        except hybrid.PickError:
            continue
    if not picks:
        return {"reason": AI_NONE}
    found = (await session.execute(select(ChartAnalysisRecord).where(
        ChartAnalysisRecord.user_id == user.id, ChartAnalysisRecord.tenant_id == user.tenant_id, ChartAnalysisRecord.source == W.SOURCE,
        ChartAnalysisRecord.mode == "hybrid", ChartAnalysisRecord.status == service.DONE, ChartAnalysisRecord.created_at <= when)
        .order_by(ChartAnalysisRecord.id.desc()).limit(50))).scalars().all()
    rows = []
    for row in found:
        pick, _ = W._pick_and_as_of(row.result_json)
        if pick is not None:
            rows.append({"pick": pick, "row": row})
    hit = pick_card(rows, picks)
    if hit is None:
        return {"reason": AI_NONE}
    row, tf = hit
    item: Any = row
    return ai_card_part(W.card_of(service.as_dict(item), cached=True), tf, item.created_at)


async def option_context_read(session: AsyncSession, user: User, exchange: str, symbol: str,
                              legs: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.option_chain import chain_view
    from app.option_chain.routes import live
    if exchange.upper() not in ("NSE", "BSE", "NSE_INDEX", "BSE_INDEX"):
        return {"reason": NOT_FNO}
    try:
        name = chain_view.underlying_name(symbol)
    except ValueError:
        return {"reason": NOT_FNO}
    try:
        view = await live(underlying=name, expiry=None, user=user, session=session)
    except HTTPException as exc:
        return {"reason": NOT_FNO if exc.status_code == 422 else NO_CHAIN}
    except Exception:  # noqa: BLE001 - no broker session, a broker error: the chain is simply not available
        return {"reason": NO_CHAIN}
    if not view.get("rows"):
        return {"reason": NO_CHAIN}
    return option_context_part(view, legs)


__all__ = ["ai_card_part", "option_context_part", "pick_card", "ai_card_read", "option_context_read", "AI_OFF", "AI_NONE",
           "NOT_FNO", "NO_CHAIN", "NO_PLAN_STRIKES"]
