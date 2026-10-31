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
from app.chart_analyze import contract, imaging, limits, share, storage
from app.chart_analyze import provider as engine_provider
from app.chart_analyze.engine import EngineUnavailable, ask
from app.db.models import ChartAnalysisRecord, Tenant, User

logger = logging.getLogger(__name__)

MODES = ("image_only", "hybrid")
READY, RUNNING, DONE, FAILED, DELETED = "ready", "running", "done", "failed", "deleted"
HISTORY_MAX = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def as_dict(row: ChartAnalysisRecord, *, with_result: bool = True, now: datetime | None = None) -> dict[str, Any]:
    """The owner's view of one item. The share link's state (stored with the result, share.py) is never part of
    `result`; the owner sees it as `share` (the live link's token and expiry, or None). A hybrid item (M59 CR-4) has
    no `result`: its reading is `hybrid` (once done) and the symbol / timeframe the user picked is `pick`."""
    parsed, state = share.split(row.result_json)
    if row.mode == "hybrid":
        return _hybrid_dict(row, parsed if row.status == DONE else None, with_result=with_result)
    summary = None
    if parsed is not None:
        try:
            summary = {"setup_status": parsed["setup"]["status"], "trend": parsed["trend"]["side"],
                       "symbol": (parsed.get("recognised") or {}).get("symbol")}
        except (KeyError, TypeError):
            summary = None
    link = share.owner_view(row.id, row.user_id, row.tenant_id, state, now or _utcnow()) if row.status == DONE else None
    return {"id": row.id, "mode": row.mode, "source": row.source, "source_host": row.source_host, "status": row.status,
            "image": {"width": row.image_width, "height": row.image_height, "url": f"/api/chart-analyze/items/{row.id}/image"},
            "error": row.error, "provider": row.provider, "model": row.model, "engine_version": row.engine_version,
            "billed_to_user_key": bool(row.billed_to_user_key), "cost_usd": round(float(row.cost_usd or 0.0), 6),
            "created_at": _iso(row.created_at), "analysed_at": _iso(row.analysed_at), "summary": summary,
            "result": parsed if with_result else None, "share": link,
            "label": contract.IMAGE_ONLY_LABEL if row.mode == "image_only" else None, "disclaimer": contract.DISCLAIMER}


def _hybrid_dict(row: ChartAnalysisRecord, parsed: dict[str, Any] | None, *, with_result: bool) -> dict[str, Any]:
    from app.chart_analyze import hybrid
    done = parsed if isinstance(parsed, dict) and parsed.get("mode") == "hybrid" else None
    return {"id": row.id, "mode": row.mode, "source": row.source, "source_host": row.source_host, "status": row.status,
            "image": {"width": row.image_width, "height": row.image_height, "url": f"/api/chart-analyze/items/{row.id}/image"},
            "reading_png": f"/api/chart-analyze/items/{row.id}/reading.png" if done else None,
            "error": row.error, "provider": row.provider, "model": row.model, "engine_version": row.engine_version,
            "billed_to_user_key": bool(row.billed_to_user_key), "cost_usd": round(float(row.cost_usd or 0.0), 6),
            "created_at": _iso(row.created_at), "analysed_at": _iso(row.analysed_at),
            "summary": hybrid.summary(done) if done else None, "result": None, "hybrid": done if with_result else None,
            "pick": hybrid.stored_pick(row.result_json), "share": None,
            "label": done["label"] if done else hybrid.LABEL_CODE_AI, "disclaimer": contract.DISCLAIMER}


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
                   mode: str, source: str, source_host: Optional[str], now: Optional[datetime] = None,
                   pick: dict[str, str] | None = None) -> ChartAnalysisRecord:
    """Stores the cleaned image and its row. The plan limit is checked first (nothing is written when it refuses).
    A hybrid item keeps the symbol / timeframe the user picked with the row (`_pick`, until its result replaces it)."""
    from app.chart_analyze import hybrid
    if mode == "hybrid" and pick is None:
        raise ValueError("a hybrid item needs the user's pick")
    now = now or _utcnow()
    await limits.check_can_add(session, user, tenant, settings, now, lock=True)     # serialised per user until the commit below
    name = storage.new_name(user.tenant_id)
    storage.write(name, clean.png)
    row = ChartAnalysisRecord(tenant_id=user.tenant_id, user_id=user.id, mode=mode, source=source, source_host=source_host, status=READY,
                              image_path=name, image_sha256=clean.sha256, image_width=clean.width, image_height=clean.height,
                              image_bytes=len(clean.png), created_at=now,
                              result_json=json.dumps({hybrid.PICK_KEY: pick}, separators=(",", ":")) if mode == "hybrid" else None)
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
    pick = None
    if row.mode == "hybrid":
        from app.chart_analyze import hybrid
        pick = hybrid.stored_pick(row.result_json)
        if pick is None and row.status != DONE:
            raise HTTPException(status_code=409, detail={"message": "Add the chart again and pick its symbol and timeframe.", "code": "pick_missing"})
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
        if pick is not None:
            return await _run_hybrid(session, user, tenant, settings, row.id, engine, item_id, pick, client=client, now=now)
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


async def _run_hybrid(session: AsyncSession, user: User, tenant: Tenant, settings: dict[str, Any], row_id: int, engine: Any, item_id: int,
                      pick: dict[str, str], *, client: Any | None, now: datetime) -> ChartAnalysisRecord:
    """M59 CR-4: ATP's own reading of the stored candles for the user's pick, drawn by code; then, only when the engine
    has the hybrid entry point, its AI part on those generic facts and ATP's rendered chart (never the user's picture,
    never a price read from it). Without the entry point - or when the AI part cannot run - the result is the code
    reading alone. The layer releases the session while it builds, so only ids are used after it (no lazy loads)."""
    from app.chart_analyze import hybrid, reading_png
    try:
        reading = await hybrid.read(session, user, pick, now=now)
        png = await anyio.to_thread.run_sync(reading_png.render, reading)
    except hybrid.ReadingUnavailable as exc:
        await _finish(session, row_id, now, FAILED, error=str(exc)[:300])
        return await own(session, user, item_id)
    except ValueError:
        await _finish(session, row_id, now, FAILED, error=f"ATP has no stored candles to draw for {pick['symbol']} on this timeframe yet."[:300])
        return await own(session, user, item_id)
    width, height = reading_png.WIDTH, reading_png.HEIGHT
    notes: list[str] = []
    ai: dict[str, Any] | None = None
    ai_version: dict[str, str] | None = None
    cost = 0.0
    values: dict[str, Any] = {"analysed_at": now, "engine_version": "ATP structure reading (code)"}
    if engine_provider.supports_hybrid(engine):
        facts = hybrid.facts(reading, width=width, height=height)
        answer = None
        try:
            instr = engine.hybrid_instructions(width=width, height=height, facts=facts)
            answer = await ask(session, user, tenant, instr, png, settings, now=now, client=client)
        except EngineUnavailable as exc:
            notes.append(f"The AI part did not run: {exc}")
        except HTTPException:
            await _finish(session, row_id, now, READY)
            raise
        except Exception as exc:  # noqa: BLE001 - the code reading still stands; the AI part is left out
            logger.warning("Chart analysis %s: the hybrid AI part failed (%s)", row_id, exc.__class__.__name__)
            notes.append("The AI part could not run this time; ATP's reading is shown on its own.")
        if answer is not None:
            cost = answer.cost_usd
            values.update(provider=answer.provider[:20], model=(answer.model or "")[:80] or None, billed_to_user_key=answer.billed_to_user_key,
                          engine_version=f"{engine.name} {engine.version} / {engine.method_version}"[:80])
            if answer.error is not None:
                notes.append(f"The AI could not answer: {answer.error}"[:300])
            else:
                try:
                    raw = engine.parse_hybrid(answer.text or "", facts=facts)
                    ai = contract.validate(raw, mode="hybrid", width=width, height=height)
                    ai_version = {"version": str(raw.get("version") or engine.version)[:40],
                                  "method_version": str(raw.get("method_version") or engine.method_version)[:64]}
                except Exception as exc:  # noqa: BLE001 - an unreadable reply leaves the AI part out, never a partial one
                    logger.info("Chart analysis %s: the hybrid reply did not fill the contract (%s)", row_id, exc.__class__.__name__)
                    notes.append("The AI's answer could not be read; ATP's reading is shown on its own.")
    result = hybrid.result(pick, reading, width=width, height=height, ai=ai, notes=notes, ai_version=ai_version)
    if await _finish(session, row_id, now, DONE, result=json.dumps(result, separators=(",", ":")), cost=cost, **values):
        await write_audit_log(session, user.tenant_id, user.id, "chart_analyze_done", f"#{row_id} hybrid {result['method']}")
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


# --- the share link (PR-A4; share.py) ------------------------------------------------------------------------------------

NOT_SHAREABLE = "Only a finished analysis can be shared."
LINK_GONE = "This link does not open: it was stopped, replaced by a newer link, or the analysis was deleted."
LINK_EXPIRED = "This link has expired. Ask the person who shared it for a new one."


async def _set_share_state(session: AsyncSession, row: ChartAnalysisRecord, state: Dict[str, Any]) -> None:
    """Writes the link state only while the row is still the finished analysis we read (a delete or a purge that ran
    meanwhile wins: no stored result is brought back)."""
    old = row.result_json
    if row.status != DONE or not old:
        raise HTTPException(status_code=409, detail={"message": NOT_SHAREABLE, "code": "not_analysed"})
    done = await session.execute(update(ChartAnalysisRecord).where(
        ChartAnalysisRecord.id == row.id, ChartAnalysisRecord.status == DONE, ChartAnalysisRecord.result_json == old)
        .values(result_json=share.with_state(old, state)).execution_options(synchronize_session=False))
    if getattr(done, "rowcount", 0) != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"message": "This analysis changed meanwhile. Open it again.", "code": "changed"})


async def start_sharing(session: AsyncSession, user: User, item_id: int, settings: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """A new organisation-only link for the caller's own finished analysis (any older link stops working)."""
    now = (now or _utcnow()).replace(microsecond=0)
    row = await own(session, user, item_id)
    if row.mode == "hybrid":
        from app.chart_analyze import hybrid
        raise HTTPException(status_code=409, detail={"message": hybrid.NOT_SHAREABLE, "code": "not_shareable"})
    _, state = share.split(row.result_json)
    await _set_share_state(session, row, share.new_state(state, now, int(settings["share_expiry_days"])))
    await write_audit_log(session, user.tenant_id, user.id, "chart_analyze_shared", f"#{item_id}")
    await session.commit()
    return as_dict(await own(session, user, item_id), now=now)


async def stop_sharing(session: AsyncSession, user: User, item_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    row = await own(session, user, item_id)
    _, state = share.split(row.result_json)
    if row.status == DONE and row.result_json and state:
        await _set_share_state(session, row, share.stopped_state(state))
        await write_audit_log(session, user.tenant_id, user.id, "chart_analyze_share_stopped", f"#{item_id}")
        await session.commit()
    return as_dict(await own(session, user, item_id), now=now)


async def open_shared(session: AsyncSession, viewer: User, token: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The structure-only copy behind a link, for a logged-in member of the owner's organisation. Another organisation,
    a forged, stopped or replaced link, and a deleted analysis all answer the same 404 (nothing about the item leaks)."""
    now = now or _utcnow()
    link = share.read_token(token)
    gone = HTTPException(status_code=404, detail={"message": LINK_GONE, "code": "link_gone"})
    if link is None:
        raise gone
    item_id, owner_id, tenant_id, version, expires_at = link
    if tenant_id != viewer.tenant_id:
        raise gone
    row = await session.scalar(select(ChartAnalysisRecord).where(
        ChartAnalysisRecord.id == item_id, ChartAnalysisRecord.tenant_id == tenant_id, ChartAnalysisRecord.user_id == owner_id,
        ChartAnalysisRecord.status == DONE))
    result, state = share.split(row.result_json if row is not None else None)
    if row is None or result is None or int(state.get("version") or 0) != version:
        raise gone
    if expires_at <= now:
        raise HTTPException(status_code=410, detail={"message": LINK_EXPIRED, "code": "link_expired"})
    if not share.opens(state, version, expires_at, now):
        raise gone
    # No item id, no picture address and no analysis date: the copy names nothing about the chart (share.py).
    return {"result": share.redact(result), "mode": row.mode, "expires_at": expires_at.isoformat(),
            "is_yours": viewer.id == owner_id, "disclaimer": contract.DISCLAIMER}


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
