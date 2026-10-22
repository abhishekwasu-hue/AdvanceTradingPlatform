"""Analyze a chart: the items (one chart image each), the analysis, the history, delete and retention.

Every query is scoped to the caller's own rows - tenant id AND user id - so another member of the same organisation or
another organisation gets "not found" for an id that exists. No organisation admin reads another user's charts (ATP
does not offer that for personal AI records; BYOK reports work the same way).
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import anyio
from fastapi import HTTPException
from sqlalchemy import and_, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.chart_analyze import contract, imaging, limits, storage
from app.chart_analyze import provider as engine_provider
from app.chart_analyze.engine import EngineUnavailable, ask
from app.db.models import ChartAnalysisRecord, Tenant, User

logger = logging.getLogger(__name__)

MODES = ("image_only", "hybrid")
HYBRID_SOON = "Hybrid analysis is coming soon. Use Image only for now."
READY, RUNNING, DONE, FAILED, DELETED = "ready", "running", "done", "failed", "deleted"
HISTORY_MAX = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def as_dict(row: ChartAnalysisRecord, *, with_result: bool = True) -> Dict[str, Any]:
    result = None
    if with_result and row.result_json:
        try:
            result = json.loads(row.result_json)
        except ValueError:
            result = None
    summary = None
    if row.result_json:
        try:
            parsed = result if result is not None else json.loads(row.result_json)
            summary = {"setup_status": parsed["setup"]["status"], "trend": parsed["trend"]["side"],
                       "symbol": (parsed.get("recognised") or {}).get("symbol")}
        except (ValueError, KeyError, TypeError):
            summary = None
    return {"id": row.id, "mode": row.mode, "source": row.source, "source_host": row.source_host, "status": row.status,
            "image": {"width": row.image_width, "height": row.image_height, "url": f"/api/chart-analyze/items/{row.id}/image"},
            "error": row.error, "provider": row.provider, "model": row.model, "engine_version": row.engine_version,
            "billed_to_user_key": bool(row.billed_to_user_key), "cost_usd": round(float(row.cost_usd or 0.0), 6),
            "created_at": _iso(row.created_at), "analysed_at": _iso(row.analysed_at), "summary": summary,
            "result": result, "label": contract.IMAGE_ONLY_LABEL if row.mode == "image_only" else None,
            "disclaimer": contract.DISCLAIMER}


def ai_floor_minutes() -> int:
    """The longest one AI call of a run may take (the provider timeout with the largest thinking headroom, twice for the
    retry after a cut-off answer), in whole minutes plus one: a run is never called stale while its call can still run."""
    from app.ai import providers
    worst = providers.timeout_for(max(providers.THINKING_HEADROOM.values())) * 2
    return math.ceil(worst / 60) + 1


def running_timeout_minutes(settings: Dict[str, Any]) -> int:
    return max(int(settings["running_timeout_minutes"]), ai_floor_minutes())


def stale_before(settings: Dict[str, Any], now: datetime) -> datetime:
    return now - timedelta(minutes=running_timeout_minutes(settings))


def is_stale(row: ChartAnalysisRecord, settings: Dict[str, Any], now: datetime) -> bool:
    """A "running" row whose run started longer ago than `running_timeout_minutes` (or never recorded a start)."""
    if row.status != RUNNING:
        return False
    started = row.started_at
    if started is None:
        return True
    started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
    return started < stale_before(settings, now)


async def own(session: AsyncSession, user: User, item_id: int) -> ChartAnalysisRecord:
    row = await session.scalar(select(ChartAnalysisRecord).where(
        ChartAnalysisRecord.id == item_id, ChartAnalysisRecord.tenant_id == user.tenant_id, ChartAnalysisRecord.user_id == user.id,
        ChartAnalysisRecord.status != DELETED).execution_options(populate_existing=True))      # the row as stored now
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return row


async def add_item(session: AsyncSession, user: User, tenant: Tenant, settings: Dict[str, Any], clean: imaging.CleanImage, *,
                   mode: str, source: str, source_host: Optional[str], now: Optional[datetime] = None) -> ChartAnalysisRecord:
    """Stores the cleaned image and its row. The plan limit is checked first (nothing is written when it refuses)."""
    now = now or _utcnow()
    await limits.check_can_add(session, user, tenant, settings, now, lock=True)     # serialised per user until the commit below
    name = storage.new_name(user.tenant_id)
    storage.write(name, clean.png)
    row = ChartAnalysisRecord(tenant_id=user.tenant_id, user_id=user.id, mode=mode, source=source, source_host=source_host, status=READY,
                              image_path=name, image_sha256=clean.sha256, image_width=clean.width, image_height=clean.height,
                              image_bytes=len(clean.png), created_at=now)
    session.add(row)
    try:
        await session.flush()
        await write_audit_log(session, user.tenant_id, user.id, "chart_analyze_added", f"#{row.id} {source} {mode}")
        await session.commit()
    except Exception:
        storage.delete(name)
        raise
    await session.refresh(row)
    return row


async def analyze(session: AsyncSession, user: User, tenant: Tenant, settings: Dict[str, Any], item_id: int, *,
                  client: Optional[Any] = None, now: Optional[datetime] = None) -> ChartAnalysisRecord:
    now = now or _utcnow()
    row = await own(session, user, item_id)
    if row.mode == "hybrid":
        raise HTTPException(status_code=409, detail={"message": HYBRID_SOON, "code": "hybrid_soon"})
    if row.status == DONE:
        return row
    if row.status == RUNNING and not is_stale(row, settings, now):
        raise HTTPException(status_code=409, detail={"message": "This chart is being analysed already.", "code": "running"})
    engine = engine_provider.load()
    if engine is None:
        raise HTTPException(status_code=503, detail={"message": engine_provider.NOT_INSTALLED, "code": "engine_not_installed"})
    png = storage.read(row.image_path or "")
    if png is None:
        raise HTTPException(status_code=410, detail={"message": "The chart image is no longer stored. Add it again.", "code": "image_gone"})
    claimable = or_(ChartAnalysisRecord.status.in_((READY, FAILED)),
                    and_(ChartAnalysisRecord.status == RUNNING, or_(ChartAnalysisRecord.started_at.is_(None),
                                                                    ChartAnalysisRecord.started_at < stale_before(settings, now))))
    claimed = await session.execute(update(ChartAnalysisRecord).where(ChartAnalysisRecord.id == row.id, claimable)
                                    .values(status=RUNNING, started_at=now).execution_options(synchronize_session=False))
    if getattr(claimed, "rowcount", 0) != 1:
        raise HTTPException(status_code=409, detail={"message": "This chart is being analysed already.", "code": "running"})
    await session.commit()
    try:
        return await _run(session, user, tenant, settings, row, engine, png, item_id, client=client, now=now)
    except BaseException:
        # A cancelled request, a dropped connection or anything unexpected: the row never stays "running". The cleanup is
        # shielded, so the cancellation that brought us here cannot skip it; it ends only this run's claim (a crash that
        # skips even this leaves a row that turns stale after running_timeout_minutes).
        with anyio.CancelScope(shield=True):
            try:
                await session.rollback()
                await _finish(session, row.id, now, FAILED, error="The analysis was interrupted. Try again.")
            except BaseException:  # noqa: BLE001 - never mask the original error
                pass
        raise


async def _run(session: AsyncSession, user: User, tenant: Tenant, settings: Dict[str, Any], row: ChartAnalysisRecord, engine: Any, png: bytes,
               item_id: int, *, client: Optional[Any], now: datetime) -> ChartAnalysisRecord:
    try:
        instr = engine.image_only_instructions(width=row.image_width, height=row.image_height)
        answer = await ask(session, user, tenant, instr, png, settings, now=now, client=client)
    except EngineUnavailable as exc:
        await _finish(session, row.id, now, FAILED, error=str(exc)[:300])
        raise HTTPException(status_code=409, detail={"message": str(exc), "code": getattr(exc, "code", "no_ai")}) from None
    except HTTPException:
        await _finish(session, row.id, now, READY)
        raise
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never a raw error to the user
        logger.warning("Chart analysis %s failed: %s", row.id, exc.__class__.__name__)
        await _finish(session, row.id, now, FAILED, error=f"The analysis failed ({exc.__class__.__name__}). Try again.")
        return await own(session, user, item_id)
    values: Dict[str, Any] = {"provider": answer.provider[:20], "model": (answer.model or "")[:80] or None,
                              "engine_version": f"{engine.name} {engine.version} / {engine.method_version}"[:80],
                              "billed_to_user_key": answer.billed_to_user_key, "analysed_at": now}
    if answer.error is not None:
        await _finish(session, row.id, now, FAILED, error=f"The AI could not answer: {answer.error}"[:300], cost=answer.cost_usd, **values)
        return await own(session, user, item_id)
    try:
        raw = engine.parse_image_only(answer.text or "", width=row.image_width, height=row.image_height)
        result = contract.validate(raw, mode="image_only", width=row.image_width, height=row.image_height)
    except Exception as exc:  # noqa: BLE001 - a reply the engine cannot read is a failed analysis, never a partial one
        logger.info("Chart analysis %s: the reply did not fill the contract (%s)", row.id, exc.__class__.__name__)
        await _finish(session, row.id, now, FAILED, error="The AI's answer could not be read. Try again.", cost=answer.cost_usd, **values)
        return await own(session, user, item_id)
    if await _finish(session, row.id, now, DONE, result=json.dumps(result, separators=(",", ":")), cost=answer.cost_usd, **values):
        await write_audit_log(session, user.tenant_id, user.id, "chart_analyze_done", f"#{row.id} {answer.provider}")
        await session.commit()
    return await own(session, user, item_id)


async def _finish(session: AsyncSession, item_id: int, claimed_at: datetime, status: str, *, error: Optional[str] = None,
                  result: Optional[str] = None, cost: float = 0.0, **values: Any) -> bool:
    """Ends THIS run: one conditional UPDATE on the row only while it is still the run this request claimed (status
    running and the same started_at). A run that outlived running_timeout_minutes and was re-claimed by another request,
    or whose row was deleted or purged meanwhile, changes nothing (no double result, no double cost, no resurrected
    row). Returns whether it wrote."""
    fields: Dict[str, Any] = {"status": status, "error": error,
                              "cost_usd": ChartAnalysisRecord.cost_usd + float(cost or 0.0), **values}
    if result is not None:
        fields["result_json"] = result
    done = await session.execute(update(ChartAnalysisRecord).where(
        ChartAnalysisRecord.id == item_id, ChartAnalysisRecord.status == RUNNING, ChartAnalysisRecord.started_at == claimed_at)
        .values(**fields).execution_options(synchronize_session=False))
    owned = getattr(done, "rowcount", 0) == 1
    await session.commit()
    if not owned:
        logger.warning("Chart analysis %s: this run no longer owns the row (re-claimed, deleted or purged); its %s result is dropped",
                       item_id, status)
    return owned


async def history(session: AsyncSession, user: User, limit: int = HISTORY_MAX) -> List[Dict[str, Any]]:
    rows = await session.scalars(select(ChartAnalysisRecord).where(
        ChartAnalysisRecord.tenant_id == user.tenant_id, ChartAnalysisRecord.user_id == user.id, ChartAnalysisRecord.status != DELETED)
        .order_by(ChartAnalysisRecord.id.desc()).limit(max(1, min(limit, HISTORY_MAX))))
    return [as_dict(r, with_result=False) for r in rows]


def _tombstone(row: ChartAnalysisRecord, now: datetime) -> None:
    """The file and the result go; the counters (created_at, mode, cost) stay so the plan limits cannot be reset. When
    the file could not be removed, its path stays on the row so retention retries it (never an orphaned file)."""
    gone = storage.delete(row.image_path)
    row.status, row.result_json, row.error, row.source_host = DELETED, None, None, None
    if gone:
        row.image_path = None
    row.deleted_at = now


async def delete_item(session: AsyncSession, user: User, item_id: int, settings: Optional[Dict[str, Any]] = None) -> None:
    from app.chart_analyze import settings as settings_mod
    now = _utcnow()
    row = await own(session, user, item_id)
    if row.status == RUNNING and not is_stale(row, settings or await settings_mod.load(session), now):
        raise HTTPException(status_code=409, detail={"message": "Wait for the analysis to finish, then delete it.", "code": "running"})
    _tombstone(row, now)
    await write_audit_log(session, user.tenant_id, user.id, "chart_analyze_deleted", f"#{item_id}")
    await session.commit()


async def purge_expired(session: AsyncSession, now: Optional[datetime] = None, settings: Optional[Dict[str, Any]] = None,
                        batch: int = 500) -> int:
    """Retention (run by the daily retention job), in batches until nothing is left:
    1. images and results older than `retention_days` are deleted (the row becomes a counter) - a stale "running" row
       included;
    2. a deleted row whose file could not be removed earlier is retried;
    3. counter rows older than `counter_keep_days` are removed (only once their file is gone).
    Returns how many rows it changed."""
    from app.chart_analyze import settings as settings_mod
    now = now or _utcnow()
    settings = settings or await settings_mod.load(session)
    expired_before = now - timedelta(days=int(settings["retention_days"]))
    stuck: set = set()
    changed = 0
    while True:
        rows = list(await session.scalars(select(ChartAnalysisRecord).where(
            ChartAnalysisRecord.status != DELETED, ChartAnalysisRecord.created_at < expired_before,
            or_(ChartAnalysisRecord.status != RUNNING, ChartAnalysisRecord.started_at.is_(None),
                ChartAnalysisRecord.started_at < stale_before(settings, now)),
            ChartAnalysisRecord.id.notin_(stuck) if stuck else true()).order_by(ChartAnalysisRecord.id).limit(batch)))
        if not rows:
            break
        for row in rows:
            _tombstone(row, now)
            changed += 1
            if row.image_path:
                stuck.add(row.id)
        await session.flush()
    while True:
        rows = list(await session.scalars(select(ChartAnalysisRecord).where(
            ChartAnalysisRecord.status == DELETED, ChartAnalysisRecord.image_path.is_not(None),
            ChartAnalysisRecord.id.notin_(stuck) if stuck else true()).order_by(ChartAnalysisRecord.id).limit(batch)))
        if not rows:
            break
        for row in rows:
            if storage.delete(row.image_path):
                row.image_path = None
                changed += 1
            else:
                stuck.add(row.id)
        await session.flush()
    keep_before = now - timedelta(days=int(settings["counter_keep_days"]))
    while True:
        rows = list(await session.scalars(select(ChartAnalysisRecord).where(
            ChartAnalysisRecord.status == DELETED, ChartAnalysisRecord.image_path.is_(None),
            ChartAnalysisRecord.created_at < keep_before).order_by(ChartAnalysisRecord.id).limit(batch)))
        if not rows:
            break
        for row in rows:
            await session.delete(row)
            changed += 1
        await session.flush()
    if stuck:
        logger.warning("Chart analysis retention: %s image file(s) could not be removed; retried on the next run", len(stuck))
    return changed


async def erase_user(session: AsyncSession, user: User) -> None:
    """Account erasure: every image of the user goes with the account, and so do the rows."""
    rows = list(await session.scalars(select(ChartAnalysisRecord).where(ChartAnalysisRecord.user_id == user.id,
                                                                       ChartAnalysisRecord.tenant_id == user.tenant_id)))
    for row in rows:
        storage.delete(row.image_path)
        await session.delete(row)
