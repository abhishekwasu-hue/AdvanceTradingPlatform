"""N1 X1: the scan Then runner - the trading worker calls `run_due` every cycle (market open or not; it only acts on a new
closed confirmation bar of the session).

For each enabled template of an organisation with `scan_then` and `execution_core` on, once per new closed `confirm_tf`
bar: the symbols its rule matched today (alert events since today's open), their closed `confirm_tf` bars through the
same server path the rule uses (BT1b: the template owner's own session when `byob_data_tier` is on), the Daily context
(`trend`), the zone areas (`areas`), then `level_gate.safe_evaluate` per area with the template's gate settings. A live
entry (the commitment candle is the latest closed bar) is submitted as an intent with the ticket as evidence; every
other result leaves its reason code (Trade's `SKIPPED_GATE_*`) on the template's status. Data that has not reached the
expected bar is never evaluated (the bar is retried next cycle). Fail-closed throughout; time-boxed per cycle.
"""
from __future__ import annotations

import json
import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import AlertEventRecord, AlertRuleRecord, ScanThenTemplateRecord, User
from app.market_data.calendar import IST
from app.price_action.level_gate import gate as G
from app.price_action.level_gate.settings import TRADE_REVISION
from app.price_action.level_gate.status import CONFIRMED_ENTRY, status_of
from app.screener.then import areas as A
from app.screener.then import templates as T
from app.screener.then import trend

logger = logging.getLogger(__name__)

BUDGET_SECONDS = 8.0                  # per worker cycle; templates left over wait for the next cycle
MAX_SYMBOLS = 20                      # symbols one template watches in a session (the rule's earliest matches)
BAR_LOOKBACK = 150                    # confirm-TF bars read: today's session plus the median-range warm-up
MAX_STATUS_LINES = 5                  # per symbol on the template's status
NO_AREAS = "SKIPPED_GATE_NO_AREAS"
WAITING_DATA = "WAITING_DATA"


@dataclass
class RunReport:
    templates: int = 0
    evaluated: int = 0
    submitted: int = 0
    deferred: int = 0
    problems: List[str] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _ist_naive(t: datetime) -> pd.Timestamp:
    return pd.Timestamp(_utc(t)).tz_convert(IST).tz_localize(None)


def bars_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """A screener frame (index = bar start) -> the gate's columns (timestamp, open, high, low, close[, volume])."""
    df = frame.reset_index(drop=True).copy()
    df.insert(0, "timestamp", pd.to_datetime(frame.index))
    cols = ["timestamp", "open", "high", "low", "close"] + (["volume"] if "volume" in df.columns else [])
    return df[cols]


def signal_of(rule: AlertRuleRecord, symbol: str, r: Dict[str, Any], ctx: Dict[str, Any], tf: str, bar_close: datetime) -> Signal:
    long = r.get("side") == G.BULL
    grade_a = r.get("grade") == "A"
    return Signal(symbol=symbol, strategy_id=f"scan_then:{rule.id}", strategy_name=f"Scan: {rule.name}"[:120],
                  direction=SignalDirection.LONG if long else SignalDirection.SHORT, timestamp=bar_close,
                  entry=r.get("entry_price"), stop_loss=r.get("sl"), target1=r.get("target"), risk_reward=r.get("rr"),
                  score=80 if grade_a else 60, grade=SignalGrade.HIGH_QUALITY if grade_a else SignalGrade.VALID,
                  reasons=[str(r.get("study") or ""), str(r.get("commit_line") or ""), str(ctx.get("reason") or ""), G.describe(r)],
                  timeframe_combo=tf)


def ticket_of(r: Dict[str, Any], ctx: Dict[str, Any], tf: str, rule_id: int) -> Dict[str, Any]:
    keys = ("entry_price", "sl", "target", "rr", "form", "commit_grade", "grade", "evidence", "confirm_bar", "confirm_ts", "study", "commit_line")
    return {"gate_status": CONFIRMED_ENTRY, "rule_id": rule_id, "confirm_tf": tf, "area": list(r.get("area") or ()),
            "side": r.get("side"), "trend": {"trend": ctx.get("trend"), "reason": ctx.get("reason"), "asof": ctx.get("asof")},
            "ticket": {k: r.get(k) for k in keys}, "line": G.describe(r), "trade_revision": TRADE_REVISION}


async def _symbols_today(session: AsyncSession, rule: AlertRuleRecord, today: date) -> List[str]:
    start = datetime.combine(today, dtime(9, 15), IST).astimezone(timezone.utc)
    rows = await session.execute(select(AlertEventRecord.symbol, AlertEventRecord.id)
                                 .where(AlertEventRecord.rule_id == rule.id, AlertEventRecord.tenant_id == rule.tenant_id,
                                        AlertEventRecord.bar_time >= start).order_by(AlertEventRecord.id))
    out: List[str] = []
    for symbol, _id in rows.all():
        if symbol not in out:
            out.append(symbol)
        if len(out) >= MAX_SYMBOLS:
            break
    return out


async def evaluate_template(session: AsyncSession, tpl: ScanThenTemplateRecord, rule: AlertRuleRecord, now: datetime, fetch: Any,
                            holidays: Set[date], per_user: bool, report: RunReport) -> None:
    """One template at `now` (at most once per closed confirmation bar)."""
    from app.alerts.barclose import expected_bar
    from app.execution.core import intents
    from app.screener.zones import instrument_key
    tf = tpl.confirm_tf if tpl.confirm_tf in T.CONFIRM_TFS else T.DEFAULT_TF
    minutes = T.CONFIRM_TFS[tf]
    bar = expected_bar(now, tf, holidays)
    if bar is None or (tpl.last_bar_at is not None and _utc(tpl.last_bar_at) >= bar):
        return
    today = _utc(bar).astimezone(IST).date()
    symbols = await _symbols_today(session, rule, today)
    if not symbols:
        tpl.last_bar_at, tpl.last_status_json, tpl.last_problem = bar, json.dumps({}), None
        return
    owner = await session.get(User, tpl.user_id) if tpl.user_id is not None else None
    if owner is None or owner.tenant_id != tpl.tenant_id or not owner.is_active:
        tpl.last_problem = "The template's owner is no longer an active member - nothing is watched"
        tpl.last_bar_at = bar
        return
    exchange = rule.exchange or "NSE"
    try:
        universe, problems, _source = await fetch(session, tpl.tenant_id, symbols, exchange, tf, {tf: BAR_LOOKBACK},
                                                  user_id=owner.id if per_user else None)
    except Exception as exc:  # noqa: BLE001 - no bars, no gate (retried on the next cycle)
        tpl.last_problem = f"bars: {type(exc).__name__}: {str(exc)[:150]}"
        return
    gs = T.settings_of(tpl)
    expected = _ist_naive(bar)
    status: Dict[str, Any] = {}
    waiting = False
    problem: Optional[str] = None
    report.evaluated += 1
    for data in universe:
        frame = data.frames.get(tf)
        bars = G.closed_bars(bars_frame(frame), minutes, now) if frame is not None and len(frame) else None
        if bars is None or not len(bars) or pd.Timestamp(bars["timestamp"].iloc[-1]) != expected:
            status[data.symbol] = {"code": WAITING_DATA, "lines": [f"waiting for the closed {tf} bar"]}
            waiting = True                                         # never decided on data short of the expected bar
            continue
        key = instrument_key(data.symbol, exchange)
        ctx = await trend.daily_context(session, key, today, gs)
        areas = await A.areas_for(session, key, tpl.tenant_id, now)
        if not areas:
            status[data.symbol] = {"code": NO_AREAS, "lines": ["no live market zone for the symbol (zones off or not computed)"],
                                   "trend": ctx.get("reason")}
            continue
        start = G.session_start_index(bars)
        bar_close = _utc(bar) + timedelta(minutes=minutes)
        lines: List[Tuple[int, str, str]] = []
        for area in areas:
            others = [a for a in areas if a != area]
            r = G.safe_evaluate(bars, area, ctx, others, start, gs)
            code = status_of(r)
            if r.get("entry"):
                lo, hi = area
                evidence = ticket_of(r, ctx, tf, rule.id)
                try:
                    out = await intents.submit(session, tenant_id=tpl.tenant_id, user_id=owner.id, source="scan",
                                               source_ref=f"rule:{rule.id}:symbol:{data.symbol}",
                                               idempotency_key=f"{rule.id}:{data.symbol}:{lo:.2f}-{hi:.2f}:{r.get('confirm_ts')}",
                                               mode=tpl.mode, signal=signal_of(rule, data.symbol, r, ctx, tf, bar_close), exchange=exchange,
                                               evidence=evidence, now=now)
                    report.submitted += int(out.created)
                except (intents.CoreDisabled, intents.IntentRejected) as exc:
                    code = "SKIPPED_INTENT_REFUSED"
                    problem = str(exc)[:300]
            if code != "SKIPPED_GATE_NOT_AT_AREA":
                rank = 0 if code == CONFIRMED_ENTRY else (1 if r.get("state") == G.ARMED else 2)
                lines.append((rank, code, G.describe(r)))
        lines.sort(key=lambda x: x[0])
        status[data.symbol] = {"code": lines[0][1] if lines else "SKIPPED_GATE_NOT_AT_AREA",
                               "lines": [f"{c}: {d}" for _, c, d in lines[:MAX_STATUS_LINES]], "trend": ctx.get("reason")}
    for symbol in symbols:
        if symbol not in status:
            status[symbol] = {"code": WAITING_DATA, "lines": [str(problems.get(symbol) or "no bars from the broker")]}
            waiting = True
    tpl.last_status_json = json.dumps(status, default=str)
    tpl.last_problem = problem
    if not waiting:
        tpl.last_bar_at = bar                                      # a lagging symbol: the same bar again next cycle


async def run_due(session: AsyncSession, now: Optional[datetime] = None, fetch: Any = None,
                  budget_seconds: float = BUDGET_SECONDS) -> RunReport:
    """Every enabled template of the organisations with `scan_then` (and `execution_core`) on, within the time budget.
    Commits once at the end. Never raises for one template."""
    from app.execution.core.intents import FLAG as CORE_FLAG
    from app.platform.controls import feature_flags, flag_enabled
    report = RunReport()
    now = _utc(now or datetime.now(timezone.utc))
    entry = (await feature_flags(session)).get(T.FLAG) or {}
    if not entry.get("on") and not entry.get("tenants"):
        return report                                              # off everywhere: one flags read
    templates = list(await session.scalars(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.enabled.is_(True))
                                           .order_by(ScanThenTemplateRecord.last_bar_at.is_not(None), ScanThenTemplateRecord.last_bar_at,
                                                     ScanThenTemplateRecord.id)))
    if not templates:
        return report
    from app.alerts.barclose import server_fetch
    from app.market_data.calendar import load_holidays
    holidays = await load_holidays(session, "NSE")
    on: Dict[Tuple[str, int], bool] = {}

    async def flag(name: str, tenant_id: int) -> bool:
        if (name, tenant_id) not in on:
            on[(name, tenant_id)] = await flag_enabled(session, name, tenant_id)
        return on[(name, tenant_id)]
    started = time_module.monotonic()
    for tpl in templates:
        if not (await flag(T.FLAG, tpl.tenant_id) and await flag(CORE_FLAG, tpl.tenant_id)):
            continue
        report.templates += 1
        if time_module.monotonic() - started > budget_seconds:
            report.deferred += 1
            continue
        rule = await session.get(AlertRuleRecord, tpl.rule_id)
        if rule is None or rule.tenant_id != tpl.tenant_id or rule.status != "active":
            continue
        try:
            await evaluate_template(session, tpl, rule, now, fetch or server_fetch, holidays,
                                    await flag("byob_data_tier", tpl.tenant_id), report)
        except Exception as exc:  # noqa: BLE001 - one template's problem stays on it
            logger.exception("Scan Then template %s failed", tpl.id)
            tpl.last_problem = f"{type(exc).__name__}: {str(exc)[:150]}"
            report.problems.append(f"template {tpl.id}: {tpl.last_problem}")
    await session.commit()
    return report
