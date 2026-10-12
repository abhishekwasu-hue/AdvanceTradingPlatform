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
DATA_LATE = "DATA_LATE"                # given up for this bar after MAX_LAG_TRIES cycles of short data
TREND_PENDING = "TREND_PENDING"        # the Daily trend is being computed (the warm task); retried
MAX_LAG_TRIES = 3
COLD_FOLDS_PER_CYCLE = 0               # cold Daily folds the cycle itself may run: none, the warm-up task folds (re-review)
CONFLICT = "SKIPPED_GATE_CONFLICT"     # both sides confirmed on the same candle: neither is sent


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


@dataclass
class CycleState:
    """One run's shared limits (review of #214, H1): the time deadline (monotonic) and the cold Daily folds left."""
    deadline: float
    cold_left: int = 1

    def out_of_time(self) -> bool:
        return time_module.monotonic() >= self.deadline


def _load_status(tpl: ScanThenTemplateRecord) -> Dict[str, Any]:
    try:
        data = json.loads(tpl.last_status_json) if tpl.last_status_json else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _pick(entries: List[Tuple[Tuple[float, float], Dict[str, Any]]]) -> Tuple[Tuple[float, float], Dict[str, Any]]:
    """One setup per side and commitment candle (review of #214, M1): grade A first, then the larger reward:risk, then the
    tighter risk; the area stays in the evidence only."""
    def score(item: Tuple[Tuple[float, float], Dict[str, Any]]) -> Tuple[int, float, float]:
        r = item[1]
        risk = abs(float(r.get("entry_price") or 0) - float(r.get("sl") or 0))
        return (1 if r.get("grade") == "A" else 0, float(r.get("rr") or 0.0), -risk)
    return max(entries, key=score)


async def evaluate_template(session: AsyncSession, tpl: ScanThenTemplateRecord, rule: AlertRuleRecord, now: datetime, fetch: Any,
                            holidays: Set[date], per_user: bool, report: RunReport, cycle: Optional[CycleState] = None) -> None:
    """One template at `now`, once per closed confirmation bar per symbol. Per symbol the status keeps the bar it was
    evaluated on (`bar`) and how many cycles its data lagged (`tries`): only symbols not done for the current bar are
    fetched again, a symbol whose data is still short after `MAX_LAG_TRIES` cycles is given up for that bar (DATA_LATE),
    and the template's bar advances when every symbol is done (review of #214, M2). Out of time or out of cold folds:
    the rest wait for the next cycle (H1)."""
    from app.alerts.barclose import expected_bar
    from app.execution import approval
    from app.execution.core import intents
    from app.screener.zones import instrument_key
    cycle = cycle or CycleState(deadline=time_module.monotonic() + BUDGET_SECONDS)
    tf = tpl.confirm_tf if tpl.confirm_tf in T.CONFIRM_TFS else T.DEFAULT_TF
    minutes = T.CONFIRM_TFS[tf]
    bar = expected_bar(now, tf, holidays)
    if bar is None or (tpl.last_bar_at is not None and _utc(tpl.last_bar_at) >= bar):
        return
    bar_key = _utc(bar).isoformat()
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
    if not approval.can_trade(owner):                              # review L3: a trading role and trading:write
        tpl.last_problem = "The template's owner may not trade any more (role or scope) - nothing is watched"
        tpl.last_bar_at = bar
        return
    status = {k: v for k, v in _load_status(tpl).items() if k in symbols}
    pending = [sym for sym in symbols if (status.get(sym) or {}).get("bar") != bar_key]
    if not pending:
        tpl.last_bar_at = bar
        return
    exchange = rule.exchange or "NSE"
    try:
        # The trading worker's shared session: a failed read here rolls back this savepoint only (on Postgres it would
        # otherwise abort the whole cycle's transaction - other templates and the exits after them).
        async with session.begin_nested():
            universe, problems, _source = await fetch(session, tpl.tenant_id, pending, exchange, tf, {tf: BAR_LOOKBACK},
                                                      user_id=owner.id if per_user else None)
    except Exception as exc:  # noqa: BLE001 - no bars, no gate (retried on the next cycle)
        tpl.last_problem = f"bars: {type(exc).__name__}: {str(exc)[:150]}"
        return
    gs = T.settings_of(tpl)
    expected = _ist_naive(bar)
    problem: Optional[str] = None
    report.evaluated += 1
    by_symbol = {data.symbol: data for data in universe}

    def lagging(sym: str, line: str) -> None:
        prev = status.get(sym) or {}
        tries = int(prev.get("tries", 0)) + 1 if prev.get("lag_bar") == bar_key else 1
        if tries >= MAX_LAG_TRIES:
            status[sym] = {"code": DATA_LATE, "lines": [line], "bar": bar_key}
        else:
            status[sym] = {"code": WAITING_DATA, "lines": [line], "lag_bar": bar_key, "tries": tries, "bar": prev.get("bar")}

    for sym in pending:
        if cycle.out_of_time():
            report.deferred += 1
            break                                                  # the rest are retried next cycle (same bar)
        data = by_symbol.get(sym)
        frame = data.frames.get(tf) if data is not None else None
        bars = G.closed_bars(bars_frame(frame), minutes, now) if frame is not None and len(frame) else None
        if bars is None or not len(bars) or pd.Timestamp(bars["timestamp"].iloc[-1]) != expected:
            lagging(sym, str(problems.get(sym) or f"waiting for the closed {tf} bar"))
            continue                                               # never decided on data short of the expected bar
        key = instrument_key(sym, exchange)
        allow_cold = cycle.cold_left > 0
        if allow_cold and trend.is_cold(key, today):
            cycle.cold_left -= 1
        ctx = await trend.daily_context(session, key, today, gs, allow_cold=allow_cold)
        if ctx.get("pending"):
            status[sym] = {"code": TREND_PENDING, "lines": [str(ctx.get("reason"))], "bar": (status.get(sym) or {}).get("bar")}
            continue                                               # retried next cycle (the warm task computes it)
        areas = await A.areas_for(session, key, tpl.tenant_id, now)
        if not areas:
            status[sym] = {"code": NO_AREAS, "lines": ["no live market zone for the symbol (zones off or not computed)"],
                           "trend": ctx.get("reason"), "bar": bar_key}
            continue
        start = G.session_start_index(bars)
        bar_close = _utc(bar) + timedelta(minutes=minutes)
        lines: List[Tuple[int, str, str]] = []
        entries: Dict[str, List[Tuple[Tuple[float, float], Dict[str, Any]]]] = {}
        for area in areas:
            others = [a for a in areas if a != area]
            r = G.safe_evaluate(bars, area, ctx, others, start, gs)
            code = status_of(r)
            if r.get("entry"):
                entries.setdefault(str(r.get("side")), []).append((area, r))
            if code != "SKIPPED_GATE_NOT_AT_AREA":
                rank = 0 if code == CONFIRMED_ENTRY else (1 if r.get("state") == G.ARMED else 2)
                lines.append((rank, code, G.describe(r)))
        if len(entries) > 1:                                       # re-review: long and short on one candle - refuse both
            sides = " and ".join(sorted(entries))
            lines.insert(0, (0, CONFLICT, f"{sides} both confirmed on the same candle (conflicting areas) - no entry"))
            entries = {}
        for side, found in entries.items():
            area, r = _pick(found)
            evidence = ticket_of(r, ctx, tf, rule.id)
            try:
                out = await intents.submit(session, tenant_id=tpl.tenant_id, user_id=owner.id, source="scan",
                                           source_ref=f"rule:{rule.id}:symbol:{sym}",
                                           idempotency_key=f"{rule.id}:{sym}:{side}:{r.get('confirm_ts')}",
                                           mode=tpl.mode, signal=signal_of(rule, sym, r, ctx, tf, bar_close), exchange=exchange,
                                           evidence=evidence, now=now)
                report.submitted += int(out.created)
            except (intents.CoreDisabled, intents.IntentRejected) as exc:
                lines.insert(0, (0, "SKIPPED_INTENT_REFUSED", str(exc)[:200]))
                problem = str(exc)[:300]
        lines.sort(key=lambda x: x[0])
        status[sym] = {"code": lines[0][1] if lines else "SKIPPED_GATE_NOT_AT_AREA",
                       "lines": [f"{c}: {d}" for _, c, d in lines[:MAX_STATUS_LINES]], "trend": ctx.get("reason"), "bar": bar_key}
    tpl.last_status_json = json.dumps(status, default=str)
    tpl.last_problem = problem
    if all((status.get(sym) or {}).get("bar") == bar_key for sym in symbols):
        tpl.last_bar_at = bar                                      # every symbol done (or given up) for this bar


async def _enabled_templates(session: AsyncSession) -> List[ScanThenTemplateRecord]:
    """Enabled templates of organisations with `scan_then` and `execution_core` on (one flags read when off everywhere)."""
    from app.execution.core.intents import FLAG as CORE_FLAG
    from app.platform.controls import feature_flags, flag_enabled
    entry = (await feature_flags(session)).get(T.FLAG) or {}
    if not entry.get("on") and not entry.get("tenants"):
        return []
    rows = list(await session.scalars(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.enabled.is_(True))
                                      .order_by(ScanThenTemplateRecord.last_bar_at.is_not(None), ScanThenTemplateRecord.last_bar_at,
                                                ScanThenTemplateRecord.id)))
    on: Dict[Tuple[str, int], bool] = {}
    out = []
    for tpl in rows:
        for name in (T.FLAG, CORE_FLAG):
            if (name, tpl.tenant_id) not in on:
                on[(name, tpl.tenant_id)] = await flag_enabled(session, name, tpl.tenant_id)
        if on[(T.FLAG, tpl.tenant_id)] and on[(CORE_FLAG, tpl.tenant_id)]:
            out.append(tpl)
    return out


async def watched_keys(session: AsyncSession, now: datetime) -> List[str]:
    """The lake keys enabled templates may watch today, in warm-up order (re-review W2): every template's matches today
    first (those are the symbols the gate evaluates), then the rules' own symbols (matches still to come)."""
    from app.screener.zones import instrument_key
    today = _utc(now).astimezone(IST).date()
    matched: List[str] = []
    later: List[str] = []

    def add(into: List[str], sym: str, exchange: str) -> None:
        key = instrument_key(sym, exchange)
        if key and key not in matched and key not in into:
            into.append(key)
    for tpl in await _enabled_templates(session):
        rule = await session.get(AlertRuleRecord, tpl.rule_id)
        if rule is None or rule.tenant_id != tpl.tenant_id or rule.status != "active":
            continue
        exchange = rule.exchange or "NSE"
        for sym in await _symbols_today(session, rule, today):
            add(matched, sym, exchange)
        try:
            own = json.loads(rule.universe_json) if rule.universe_json else []
        except ValueError:
            own = []
        for sym in ([str(x) for x in own if isinstance(x, str)] + ([rule.symbol] if rule.symbol else []))[: MAX_SYMBOLS * 2]:
            add(later, sym, exchange)
    return matched + [k for k in later if k not in matched]


async def warm_due(session_factory: Any, now: Optional[datetime] = None, limit: int = trend.WARM_PER_RUN) -> "trend.WarmReport":
    """Review of #214 (H1): computes today's Daily contexts for the watched symbols not cached yet - run by the worker as a
    background task outside the trading cycle. Re-review: the key list is read with one short session (closed before any
    fold), each key with its own (W1); at most `limit` folds per run, the rest reported and left for the next run (W2)."""
    now = _utc(now or datetime.now(timezone.utc))
    async with session_factory() as session:
        keys = await watched_keys(session, now)
    report = await trend.warm(session_factory, keys, now.astimezone(IST).date(), limit=limit)
    if report.skipped:
        logger.warning("Scan Then warm-up: %d keys folded, %d left for the next run (limit %d): %s", report.folded,
                       len(report.skipped), limit, ", ".join(report.skipped[:10]))
    return report


async def run_due(session: AsyncSession, now: Optional[datetime] = None, fetch: Any = None,
                  budget_seconds: float = BUDGET_SECONDS, cold_folds: int = COLD_FOLDS_PER_CYCLE) -> RunReport:
    """Every enabled template of the organisations with `scan_then` (and `execution_core`) on, within the time budget
    (checked per symbol) and at most `cold_folds` cold Daily folds. Commits once at the end. Never raises for one template."""
    from app.platform.controls import flag_enabled
    report = RunReport()
    now = _utc(now or datetime.now(timezone.utc))
    templates = await _enabled_templates(session)
    if not templates:
        return report
    from app.alerts.barclose import server_fetch
    from app.market_data.calendar import load_holidays
    holidays = await load_holidays(session, "NSE")
    byob: Dict[int, bool] = {}
    cycle = CycleState(deadline=time_module.monotonic() + budget_seconds, cold_left=max(0, int(cold_folds)))
    for tpl in templates:
        report.templates += 1
        if cycle.out_of_time():
            report.deferred += 1
            continue
        rule = await session.get(AlertRuleRecord, tpl.rule_id)
        if rule is None or rule.tenant_id != tpl.tenant_id or rule.status != "active":
            continue
        tpl_id = tpl.id
        try:
            # One savepoint per template: its failure (a SQL error included) rolls back its own work only, and the cycle's
            # session stays usable for the templates after it and the commit below.
            async with session.begin_nested():
                if tpl.tenant_id not in byob:
                    byob[tpl.tenant_id] = await flag_enabled(session, "byob_data_tier", tpl.tenant_id)
                await evaluate_template(session, tpl, rule, now, fetch or server_fetch, holidays, byob[tpl.tenant_id], report, cycle)
        except Exception as exc:  # noqa: BLE001 - one template's problem stays on it
            logger.exception("Scan Then template %s failed", tpl_id)
            problem = f"{type(exc).__name__}: {str(exc)[:150]}"
            row = await session.get(ScanThenTemplateRecord, tpl_id, populate_existing=True)
            if row is not None:
                row.last_problem = problem
            report.problems.append(f"template {tpl_id}: {problem}")
    await session.commit()
    return report
