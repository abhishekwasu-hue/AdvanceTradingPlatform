"""Store-only, opt-in breakout scan on ATP's non-trading research worker."""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from app.db.models import User, UserPrefsRecord
from app.engine_provider import frames as frame_builder
from app.engine_provider import sources
from app.market_lake.own_bars import scope_for
from app.screener.breakouts.amw_breakout import Settings
from app.screener.breakouts.amw_preferences import NAMESPACE, BreakoutPreferences
from app.screener.breakouts.amw_status import owner_key, write_status
from app.screener.breakouts.session import calendar_for
from app.workers.amw_breakout_signals import ScanFacts, run_closed
from app.workers.engine_runner import due_bar

log = logging.getLogger(__name__)


def requested_timeframes(cfg: Settings) -> set[str]:
    rsi = set(cfg.rsi_tfs) if cfg.divergence_mode != "OFF" else set()
    return {cfg.breakout_tf, cfg.structure_tf, *cfg.demand_tfs, *cfg.supply_tfs, *cfg.htf_tfs} | rsi


def dataframe(frame, through: datetime) -> pd.DataFrame:
    result = pd.DataFrame(
        [(b.start, b.end, b.open, b.high, b.low, b.close, b.volume) for b in frame.bars],
        columns=["start", "bar_end", "open", "high", "low", "close", "volume"],
    ).set_index("start")
    if frame.futures_volume is not None:
        result["futures_volume"] = frame.futures_volume
    result.attrs["available_through"] = through
    return result


async def _scan_user(session, user, prefs: BreakoutPreferences, now: datetime) -> None:
    configs = [Settings(**entry) for entry in prefs.rules]
    if not configs:
        return
    # This scheduler supports NSE sessions only. Reuse ATP's holiday table and the
    # same real-close rules as its existing analytical bar-close jobs.
    first = now.date() - timedelta(days=90)
    cal = await sources.load_calendar(session, "NSE", first, now.date())
    holidays = cal.holidays
    days = [first + timedelta(days=offset) for offset in range((now.date() - first).days + 1)]
    days = [day for day in days if frame_builder.is_session_day(cal, day)]
    previous_sessions = dict(zip(days[1:], days))
    session_calendar = calendar_for("NSE", special={
        item.day: (cal.regular_open, item.close) for item in cal.early_closes
    })
    due = {}
    for cfg in configs:
        end = due_bar(cal, cfg.breakout_tf, now, 0)
        if end is not None and timedelta(0) <= now - end <= timedelta(minutes=2):
            due.setdefault((cfg.breakout_tf, end), []).append(cfg)
    if not due:
        return
    started = datetime.now(timezone.utc)
    counts = {"processed": 0, "missing_data": 0, "signals": 0, "duplicates": 0, "failed": 0, "cache_hits": 0}
    for key in prefs.instruments:
        try:
            scope = await scope_for(session, user, key)
            for (tf, end), group in due.items():
                timeframes = sorted(set().union(*(requested_timeframes(cfg) for cfg in group)))
                start_map = {source: now - timedelta(days=75 if source == "1d" else 14) for source in timeframes}
                data, _notes = await sources.collect(session, key=key, exchange="NSE", timeframes=timeframes,
                                                      starts=start_map, now=now, cal=cal, scope=scope,
                                                      lake_timeframes=timeframes)
                # Optional sources are excluded if unavailable. Required ones fail closed.
                built = frame_builder.build_request(data, timeframes, tf, now, start_map, cal)
                if built.request is None:
                    counts["missing_data"] += len(group)
                    continue
                unavailable = {gap.timeframe for gap in built.gaps}
                dataframes = {source: dataframe(frame, built.request.as_of)
                              for source, frame in built.request.frames.items() if source not in unavailable}
                if tf in dataframes:
                    dataframes[tf].attrs["previous_sessions"] = previous_sessions
                    dataframes[tf].attrs["session_calendar"] = session_calendar
                facts = ScanFacts(dataframes)
                for cfg in group:
                    result = await run_closed(session, user, dataframes, cfg, symbol=key.split(":", 1)[-1],
                                     instrument_key=key, now=now, holidays=holidays, enabled=True, facts=facts, scheduled_end=end,
                                     instrument_is_index=frame_builder.is_index_key(key))
                    counts["processed"] += 1
                    counts["missing_data"] += int(bool(result.reason) or any(d.state == "WAITING_FOR_DATA" for d in result.decisions))
                    counts["signals"] += len(result.published)
                    counts["duplicates"] += result.duplicates
                counts["cache_hits"] += facts.hits
        except Exception:
            counts["failed"] += 1
            await session.rollback()
            await session.refresh(user)
            log.exception("amw_breakout_instrument_failed", extra={"instrument": key})
    status = "ERROR" if counts["failed"] else "WAITING_FOR_DATA" if counts["missing_data"] else "ENABLED"
    await write_status(session, owner_key(user), {
        "status": status, "last_scan_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
        "config_versions": {rule.profile: rule.version for rule in configs},
        "counts": counts,
    })


async def run_opted_in(session_factory, now: datetime | None = None) -> None:
    """Read opted-in owner preferences. All scans are store-only and informational."""
    now = now if now is not None else datetime.now(timezone.utc)
    query = (
        select(UserPrefsRecord.user_id, UserPrefsRecord.tenant_id, UserPrefsRecord.value_json)
        .where(UserPrefsRecord.namespace == NAMESPACE)
        .order_by(UserPrefsRecord.id)
    )
    async with session_factory() as session:
        await write_status(session, "worker", {})
        rows = tuple((await session.execute(query)).all())
    for user_id, tenant_id, raw in rows:
        try:
            prefs = BreakoutPreferences.model_validate_json(raw)
            if not prefs.enabled:
                continue
            async with session_factory() as session:
                user = await session.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
                if user is not None and user.is_active:
                    await _scan_user(session, user, prefs, now)
        except (ValueError, TypeError):
            log.warning("amw_breakout_preferences_invalid", extra={"user_id": user_id})
        except Exception:
            log.exception("amw_breakout_scan_failed", extra={"user_id": user_id})
