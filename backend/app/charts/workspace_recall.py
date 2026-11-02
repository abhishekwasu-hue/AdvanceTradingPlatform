"""M76 MP-14 L-1 (owner order M76 L-1; docs/design/M76_REVIEW.md §5; flag `copilot_workspace`): "Similar past setups"
beside the Copilot workspace's chart - his OWN past trades taken at the same kind of area, with their outcomes.

The area context is captured at entry (the review's rule: no back-fill guess):
* `POST /api/charts/workspace/entry-context` - after a trade placed from the workspace fills, the page names his new
  trade(s) and the open chart; the SERVER reads that chart as of the trade's entry (the workspace's own reading,
  `read_chart.workspace_annotations`, closed bars only - no look-ahead) and, when the price was inside an area, records
  that area's context on each trade's journal entry (`plan_json.area_context`: the reading's source, the chart's and the
  area's timeframe, the area's side and state). Nothing the page says about the area is stored; the first record stays.
  The journal hook only (app/journal/capture.py `on_area_context`, flag `discipline_engine` like every capture hook) -
  no order path calls it, and it never changes a trade.
* `GET /api/charts/workspace/similar?source=&tf=&side=&state=` - the area the open chart's price is inside (the page
  passes the reading it shows: these four values are match keys over his own rows only). His closed trades whose
  recorded area context is the same on all four, closed strictly before now, grouped by the side he took (long / short),
  each group counted and summarised with the setup recall's own pieces, unchanged (`setup_recall.service.build_units`,
  `summary`, `match_dict`; its minimum sample and list size). Trades without a recorded area context never match.

Self-scoped (tenant AND user). Read-only towards trading: nothing here places, changes or blocks an order. Words carry no
buy / sell / target / stop wording (the recall's own words). Behind `ui_v2` + `copilot_workspace`: 404 while either is
off. No migration (the context lives in the journal entry's plan JSON).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import JournalEntryRecord, TradeRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/charts/workspace", tags=["charts"])
FLAG = "copilot_workspace"
UI_V2 = "ui_v2"
INSTRUMENT = r"^[A-Za-z]{2,10}:[A-Za-z0-9&_\- ]{1,40}$"
STATE = r"^[a-z_]{1,24}$"
TF = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
AREA_TF = r"^[0-9a-zA-Z]{1,6}$"
CONTEXT_KEYS = ("source", "tf", "side", "state")
SIDE_WORDS = {"LONG": "Long", "SHORT": "Short"}


class EntryContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_ids: list[int] = Field(min_length=1, max_length=20)
    instrument: str = Field(pattern=INSTRUMENT, max_length=52)
    tf: TF


def area_context(annotations: dict[str, Any] | None, chart_tf: str) -> dict[str, Any] | None:
    """The area the reading has the last close inside (None: inside no area, or no reading). Pure."""
    inside = ((annotations or {}).get("next_areas") or {}).get("inside")
    if not isinstance(inside, dict) or inside.get("side") not in ("buyer", "seller"):
        return None
    return {"source": str(annotations.get("source") or ""), "chart_tf": chart_tf, "tf": str(inside.get("timeframe") or ""),
            "side": inside["side"], "state": inside.get("state") or None}


def same_area(recorded: Any, subject: dict[str, Any]) -> bool:
    """True when a recorded area context matches the subject on every one of CONTEXT_KEYS. Pure."""
    return isinstance(recorded, dict) and all((recorded.get(k) or None) == (subject.get(k) or None) for k in CONTEXT_KEYS)


async def _on(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for flag in (UI_V2, FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


@router.post("/entry-context")
async def entry_context(body: EntryContextBody, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Records the entry's area context on his own new trades' journal entries (see the module docstring)."""
    await _on(session, user)
    from app.charts import read_chart as rc
    from app.journal import capture
    from app.price_action.zones.store import normalise_key
    ids = sorted(set(body.trade_ids))
    rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                                                TradeRecord.id.in_(ids))))
    if not rows:
        raise HTTPException(status_code=404, detail="No such trade")
    entered = [t.entry_time if t.entry_time.tzinfo else t.entry_time.replace(tzinfo=UTC) for t in rows if t.entry_time is not None]
    if not entered:
        return {"stored": 0, "area": None}
    try:
        key = normalise_key(body.instrument)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Not an instrument") from exc
    context = area_context(await rc.workspace_annotations(session, user, key, body.tf, min(entered)), body.tf)
    if context is None:
        return {"stored": 0, "area": None}
    await capture.on_area_context(session, int(user.tenant_id), [t.id for t in rows], context)
    return {"stored": len(rows), "area": context}


async def _matched_trades(session: AsyncSession, user: User, subject: dict[str, Any], cutoff: datetime) -> list[TradeRecord]:
    """His closed trades whose journal entry recorded the same area context, plus every leg of their structures."""
    from app.core import config
    from app.journal import capture
    own = (TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id)
    pairs = (await session.execute(select(TradeRecord, JournalEntryRecord.plan_json).join(JournalEntryRecord, JournalEntryRecord.trade_id == TradeRecord.id)
                                   .where(*own, JournalEntryRecord.user_id == user.id, JournalEntryRecord.tenant_id == user.tenant_id,
                                          TradeRecord.exit_time.is_not(None), TradeRecord.exit_time < cutoff,
                                          JournalEntryRecord.plan_json.contains("area_context"))
                                   .order_by(TradeRecord.exit_time.desc(), TradeRecord.id.desc()).limit(config.SETUP_RECALL_MAX_SCAN))).all()
    rows = [t for t, plan in pairs if same_area(capture._loads(plan, {}).get("area_context"), subject)]  # noqa: SLF001
    groups = {r.leg_group_id for r in rows if r.leg_group_id}
    if groups:
        have = {r.id for r in rows}
        rows += [r for r in await session.scalars(select(TradeRecord).where(*own, TradeRecord.leg_group_id.in_(groups))) if r.id not in have]
    return rows


def grouped(units: list[Any], symbols: dict[int, str], cutoff: datetime, min_sample: int, max_listed: int) -> list[dict[str, Any]]:
    """Per side he took: the recall's own summary and rows (closed strictly before `cutoff`, newest first). Pure."""
    from app.setup_recall import service
    out: list[dict[str, Any]] = []
    for side in ("LONG", "SHORT"):
        found = sorted((u for u in units if u.context.side == side and u.closed_at is not None and u.closed_at < cutoff),
                       key=lambda u: (u.closed_at, u.trade_id), reverse=True)
        if not found:
            continue
        rows = [{**service.match_dict(u), "symbol": symbols.get(u.trade_id)} for u in found[:max_listed]]
        out.append({"side": side, "side_words": SIDE_WORDS[side], "count": len(found), **service.summary(found, min_sample), "matches": rows})
    return out


@router.get("/similar")
async def similar(source: Literal["atp", "engine"] = Query(...), tf: str = Query(min_length=1, max_length=6, pattern=AREA_TF),
                  side: Literal["buyer", "seller"] = Query(...), state: str | None = Query(default=None, max_length=24, pattern=STATE),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """His own closed trades at the same kind of area as the open chart's price (see the module docstring)."""
    await _on(session, user)
    from app.core import config
    from app.setup_recall import service
    cutoff = datetime.now(UTC)
    subject = {"source": source, "tf": tf, "side": side, "state": state}
    trades = await _matched_trades(session, user, subject, cutoff)
    units = service.build_units(trades, {})
    groups = grouped(units, {t.id: t.symbol for t in trades}, cutoff, config.SETUP_RECALL_MIN_SAMPLE, config.SETUP_RECALL_MAX_LISTED)
    return {"context": subject, "fields": list(CONTEXT_KEYS), "as_of": cutoff.isoformat(), "count": sum(g["count"] for g in groups),
            "sides": groups, "line": None if groups else service.NO_MATCHES, "blocks": False}


__all__ = ["router", "area_context", "same_area", "grouped", "EntryContextBody"]
