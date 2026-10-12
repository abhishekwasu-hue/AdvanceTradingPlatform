"""U6 BK5: the nightly breakout-fitness pass - for every daily / weekly breakout template, the fitness of its past matches
(fitness.py) over the configured universe, from the SHARED lake's daily bars, stored as of the IST date.

Flag `screener_breakouts_fitness` (off by default; on for everyone or any tenant starts the pass - the rows are reference
data, the same for every organisation, like the market zones). Inputs: md_candles daily bars only (no tenant column; HB0's
md_tenant_candles and every user's broker data are never read), ended and ingested by the run time, at most
BREAKOUT_FITNESS_HISTORY_DAYS back. Universe: BREAKOUT_FITNESS_SYMBOLS (empty: MARKET_ZONES_SYMBOLS). Intraday templates
are not computed: there is no shared intraday history (open question BK5-2).

Never on the trading loop: the worker starts `run_nightly` as its own task once per IST day from BREAKOUT_FITNESS_HOUR_IST
(after HB1's EOD ingest), with its own sessions, a time budget and a Postgres advisory lock; the CPU work runs in a
thread. Resumable: a template already stored for the day is skipped; the day counts as done only when every template was
worked through. Places no orders.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time as clock
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import BreakoutFitnessRunRecord
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

FLAG = "screener_breakouts_fitness"
LOCK_KEY = 0x424B3505                                   # pg advisory lock id ("BK" 5 05)
EOD_TFS = ("1d", "1w")


def universe() -> List[str]:
    return list(config.BREAKOUT_FITNESS_SYMBOLS or config.MARKET_ZONES_SYMBOLS)


def eod_templates() -> List[Any]:
    from app.screener.breakouts.templates import TEMPLATES
    return [t for t in TEMPLATES if t.signal_tf in EOD_TFS]


def params_hash(template: Any) -> str:
    from app.screener.breakouts import params as bp
    from app.screener.breakouts.templates import template_params
    p = bp.settings(**template_params(template))
    return hashlib.sha256(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()


async def load_daily(session: AsyncSession, key: str, now: datetime, days: Optional[int] = None) -> pd.DataFrame:
    """The instrument's shared daily bars that ENDED and were ingested by `now`, start-labelled (index: the session
    open, UTC), columns open / high / low / close / volume - the frames the breakout engine reads."""
    from app.price_action.zones import job as zones_job
    sess = zones_job.exchange_session(key)
    start = now - timedelta(days=days or config.BREAKOUT_FITNESS_HISTORY_DAYS)
    rows: List[Any] = []
    for label in zones_job.LAKE_LABELS["1d"]:
        rows = await zones_job._fetch(session, key, label, start, now)
        if rows:
            break

    def build() -> pd.DataFrame:
        f = zones_job.raw_frame(rows, "1d", sess)
        if not len(f):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        f = f[pd.to_datetime(f["close_at"], utc=True) <= pd.Timestamp(now)]
        out = f[["open", "high", "low", "close", "volume"]].astype(float)
        out.index = pd.DatetimeIndex(pd.to_datetime(f["timestamp"], utc=True))
        return out
    return await asyncio.to_thread(build)


def compute(template: Any, daily: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """CPU part (a worker thread): the template's fitness over the symbols' daily frames (weekly / monthly frames built
    from them, as the breakout run does)."""
    from app.screener.breakouts import params as bp
    from app.screener.breakouts import style
    from app.screener.breakouts.fitness import template_fitness
    from app.screener.breakouts.templates import template_params
    params = template_params(template)
    p = bp.settings(**params)
    context = style.context_timeframes(template.signal_tf, p["context_ladder"])
    frames = {sym: {tf: style.resample(f, "1d", tf) for tf in (template.signal_tf, *context)} for sym, f in daily.items() if len(f)}
    return template_fitness(template.screen, template.signal_tf, frames, params)


async def any_enabled(session: AsyncSession) -> bool:
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    return bool(entry.get("on") or entry.get("tenants"))


async def _try_lock(session: AsyncSession) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}))


def ist_day(now: datetime) -> date:
    return now.astimezone(IST).date()


async def run_nightly(session_factory: Any, now: Optional[datetime] = None, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
    """One pass: load the universe's daily bars once, then each EOD template not yet stored for the day. `complete` only
    when every template was worked through (stored, already there or failed)."""
    now = now or datetime.now(timezone.utc)
    day = ist_day(now)
    budget = config.BREAKOUT_FITNESS_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = clock.monotonic() + budget
    report: Dict[str, Any] = {"stored": 0, "exists": 0, "failed": 0, "pending": 0, "no_data": 0, "symbols": 0, "complete": False,
                              "locked": False}
    symbols = universe()
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session):
            report["locked"] = True
            return report
        try:
            label = config.BREAKOUT_FITNESS_UNIVERSE
            async with session_factory() as session:
                done = set(await session.scalars(select(BreakoutFitnessRunRecord.template_id).where(
                    BreakoutFitnessRunRecord.universe == label, BreakoutFitnessRunRecord.as_of == day)))
            todo = [t for t in eod_templates() if t.id not in done]
            report["exists"] = len(eod_templates()) - len(todo)
            if not todo:
                report["complete"] = True
                return report
            daily: Dict[str, pd.DataFrame] = {}
            for key in symbols:
                async with session_factory() as session:
                    f = await load_daily(session, key, now)
                if len(f):
                    daily[key] = f
                else:
                    report["no_data"] += 1
            report["symbols"] = len(daily)
            for i, t in enumerate(todo):
                if clock.monotonic() > deadline:
                    report["pending"] = len(todo) - i
                    logger.warning("Breakout fitness: time budget (%ss) used - %s templates left", budget, report["pending"])
                    return report
                try:
                    stats = await asyncio.to_thread(compute, t, daily)
                    async with session_factory() as session:
                        session.add(BreakoutFitnessRunRecord(template_id=t.id, universe=label, as_of=day, signal_tf=t.signal_tf,
                                                             params_hash=params_hash(t), symbols=int(stats["symbols"]),
                                                             signals=int(stats["overall"]["signals"]), stats=json.dumps(stats, default=str),
                                                             computed_at=now))
                        await session.commit()
                    report["stored"] += 1
                except Exception:  # noqa: BLE001 - one template's problem never stops the others
                    logger.exception("Breakout fitness: %s failed", t.id)
                    report["failed"] += 1
            report["complete"] = True
            logger.info("Breakout fitness: %s", report)
            return report
        finally:
            await lock_session.rollback()


def nightly_due(now: datetime, last_day: Optional[date]) -> Optional[date]:
    """The IST date to run for, or None (before BREAKOUT_FITNESS_HOUR_IST, or that day is already done)."""
    local = now.astimezone(IST)
    if local.hour < config.BREAKOUT_FITNESS_HOUR_IST or last_day == local.date():
        return None
    return local.date()


async def latest(session: AsyncSession, template_ids: Optional[List[str]] = None, label: Optional[str] = None) -> List[Dict[str, Any]]:
    """The newest stored fitness per template (of the universe), as dicts."""
    label = label or config.BREAKOUT_FITNESS_UNIVERSE
    q = select(BreakoutFitnessRunRecord).where(BreakoutFitnessRunRecord.universe == label)
    if template_ids is not None:
        q = q.where(BreakoutFitnessRunRecord.template_id.in_(template_ids))
    newest: Dict[str, BreakoutFitnessRunRecord] = {}
    for row in await session.scalars(q.order_by(BreakoutFitnessRunRecord.as_of)):
        newest[row.template_id] = row
    return [{"template_id": r.template_id, "universe": r.universe, "as_of": r.as_of.isoformat(), "signal_tf": r.signal_tf,
             "symbols": r.symbols, "signals": r.signals, "params_hash": r.params_hash, "stats": json.loads(r.stats or "{}")}
            for r in sorted(newest.values(), key=lambda x: x.template_id)]


__all__ = ["FLAG", "EOD_TFS", "universe", "eod_templates", "load_daily", "compute", "any_enabled", "run_nightly", "nightly_due", "latest",
           "params_hash"]
