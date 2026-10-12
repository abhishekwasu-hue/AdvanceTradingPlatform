"""S4a (ADR-0021/0022): the bar-close engine - evaluates alert rules on CLOSED bars only and turns matches into
`alert_events` (S3a `record_event`: idempotent per rule, symbol, condition and bar). It never places or changes an
order, and never fires on a bar that is still forming.

Each worker cycle, for every active rule whose organisation has `screener_v2` on:
1. `expected_bar` - the start of the latest fully closed bar of the rule's timeframe on the exchange clock (NSE
   09:15-15:30 IST; intraday buckets start at the open and the day's last bucket may be short; a daily bar closes at
   15:30 on a trading day). Weekly / monthly rules wait for a later step (SC-10).
2. Already evaluated on that bar (`last_bar_at`) -> nothing to do. Looked less than `RETRY_SECONDS` ago -> wait.
3. Server bars through the organisation's broker session (`fetch`), trimmed to closed bars. A symbol whose data has
   not reached the expected bar is skipped this round (never evaluated on stale data); when no symbol is current the
   rule is retried later and `last_problem` says why.
4. The screen runs on the current symbols; each match is recorded with its trigger values (close, volume, the bar
   time as `as_of`).

BT1b: with `byob_data_tier` on, a rule's bars come only through the own broker session of the user who created it
(`fetch(..., user_id=rule.created_by)`), and the cycle cache is per user, so one member's feed never evaluates another
member's rule.

S4b-2: a rule with `fire_on = "intrabar"` (flag `screener_intrabar`, off by default) is evaluated on the bar still
forming instead: during the session only, at most every `RETRY_SECONDS`, firing at most once per bar and symbol (the
S3a idempotency key is per bar). Its events say `intrabar: true` - the bar had not closed, so the condition may no
longer hold at the close.

Z10-b (G5, flag `alerts_stale_guard`): before a match is recorded, each symbol's data passes the alert badge
(`app/alerts/staleness.py`, timed by the newest bar the rule reads); a symbol that fails is not evaluated, and a rule with
no fresh symbol is "suppressed" (its `last_problem` says why) and retried later. Today's daily bar for a bar-close rule
is only built from intraday bars that reach the session's last bucket.
"""
from __future__ import annotations

import json
import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import staleness
from app.db.models import AlertRuleRecord, ScreenRecord
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day
from app.screener import compile_screen, nodes
from app.screener.runtime import SymbolData, bar_close, closed_only, run_screen

logger = logging.getLogger(__name__)

FLAG = "screener_v2"
INTRABAR_FLAG = "screener_intrabar"
BREAKOUT_FLAG = "screener_breakouts"                 # U6 BK1: rules using breakout functions pause (stay active) while it is off
ZONES_FLAG = "market_zones"                    # Z7a-3: rules using market-zone functions pause (stay active) while it is off
RETRY_SECONDS = 60
TIME_BUDGET_SECONDS = 10.0                     # per worker cycle; rules left over are simply still due next cycle
MAX_SYMBOLS = 50
INTRADAY = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
SUPPORTED = set(INTRADAY) | {"1d"}

# fetch(session, tenant_id, symbols, exchange, base_tf, lookback[, forming=True]) -> (universe, per-symbol problems, data
# source); `forming` is passed only for intrabar rules, so a bar-close fetch keeps the six-argument shape.
Fetch = Callable[..., Awaitable[Tuple[List[SymbolData], Dict[str, str], str]]]


def _utc(dt: datetime) -> datetime:
    """Aware UTC - what the engine stores (a naive value read back from the database is UTC)."""
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _session_bounds(day: date) -> Tuple[datetime, datetime]:
    return datetime.combine(day, MARKET_OPEN, IST), datetime.combine(day, MARKET_CLOSE, IST)


def _last_intraday_bar(day: date, minutes: int, until: datetime) -> Optional[datetime]:
    """The start of the last bar of `day` that had closed by `until` (bars start at the open; the last may be short)."""
    open_, close = _session_bounds(day)
    step = timedelta(minutes=minutes)
    best: Optional[datetime] = None
    start = open_
    while start < close:
        if min(start + step, close) <= until:
            best = start
        else:
            break
        start += step
    return best


def expected_bar(now: datetime, tf: str, holidays: Iterable[date] = ()) -> Optional[datetime]:
    """Start (UTC) of the latest fully closed `tf` bar at `now`; a daily bar is labelled by its session open. None for an
    unsupported timeframe."""
    if tf not in SUPPORTED:
        return None
    hol = set(holidays)
    until = _utc(now).astimezone(IST)
    day = until.date()
    for _ in range(15):                                                    # walk back over weekends and holidays
        if is_trading_day(day, hol):
            if tf == "1d":
                open_, close = _session_bounds(day)
                if until >= close:
                    return open_.astimezone(timezone.utc)
            else:
                bar = _last_intraday_bar(day, INTRADAY[tf], until)
                if bar is not None:
                    return bar.astimezone(timezone.utc)
        day -= timedelta(days=1)
        until = datetime.combine(day, MARKET_CLOSE, IST)                   # an earlier day: all of it had closed
    return None


def forming_bar(now: datetime, tf: str, holidays: Iterable[date] = ()) -> Optional[datetime]:
    """Start (UTC) of the `tf` bar forming at `now` - only inside the session of a trading day; None otherwise (and for
    an unsupported timeframe). A daily bar is labelled by its session open, like `expected_bar`."""
    if tf not in SUPPORTED:
        return None
    at = _utc(now).astimezone(IST)
    open_, close = _session_bounds(at.date())
    if not is_trading_day(at.date(), set(holidays)) or not (open_ <= at < close):
        return None
    if tf == "1d":
        return open_.astimezone(timezone.utc)
    step = INTRADAY[tf] * 60
    start = open_ + timedelta(seconds=((at - open_).total_seconds() // step) * step)
    return start.astimezone(timezone.utc)


def closed_frame(df: pd.DataFrame, tf: str, expected: datetime) -> Optional[pd.DataFrame]:
    """`df` trimmed to bars that had closed by the expected bar; None when the data has not reached it. Daily bars are
    matched by IST date (brokers label them at midnight or at the open)."""
    if df is None or df.empty:
        return None
    if tf == "1d":
        want = expected.astimezone(IST).date()
        dates = pd.Index([ts.astimezone(IST).date() for ts in df.index])
        kept = df[dates <= want]
        return kept if len(kept) and kept.index[-1].astimezone(IST).date() == want else None
    kept = df[df.index <= pd.Timestamp(expected)]
    return kept if len(kept) and kept.index[-1] == pd.Timestamp(expected) else None


@dataclass
class CycleCache:
    """S4b-1: one worker cycle's shared work. Rules that need the same symbol, timeframe and bar fetch it once (a
    longer lookback than the cached one fetches again); the same screen over the same current symbols, bar and
    params is evaluated once. Lives for one cycle only, so nothing stale survives into the next bar."""
    # value: (the lookback the frames were fetched for, per timeframe; the frames). BK1 verification F1: an entry is reused
    # only when it covers EVERY timeframe asked for - a frame fetched for a plain 15m rule has no daily context.
    frames: Dict[Tuple[int, Optional[int], str, str, datetime, str, bool], Tuple[Dict[str, int], Optional[SymbolData]]] = field(default_factory=dict)
    results: Dict[Tuple[Any, ...], List[Any]] = field(default_factory=dict)
    fetched_symbols: int = 0
    reused_symbols: int = 0
    reused_results: int = 0
    # Z10-b: flag alerts_stale_guard per organisation for this cycle, and the symbols it held back.
    stale_guard: Dict[int, bool] = field(default_factory=dict)
    suppressed_symbols: int = 0


@dataclass
class Outcome:
    rules: int = 0
    evaluated: int = 0
    fired: int = 0
    waiting: int = 0
    deferred: int = 0
    cache: CycleCache = field(default_factory=CycleCache)
    problems: List[str] = field(default_factory=list)


async def _rule_source(session: AsyncSession, rule: AlertRuleRecord) -> Tuple[Any, Any, List[str], Dict[str, Any]]:
    if rule.kind == "screen":
        screen = await session.get(ScreenRecord, rule.screen_id) if rule.screen_id else None
        if screen is None or screen.archived or screen.tenant_id != rule.tenant_id:
            raise ValueError("the rule's screen is gone or archived")
        ast, validated = compile_screen(json.loads(screen.ast_json), base_tf=rule.base_tf)
        symbols = [str(s).upper() for s in json.loads(rule.universe_json or "[]")][:MAX_SYMBOLS]
        params = json.loads(screen.params_json or "{}")
    else:
        ast, validated = compile_screen(rule.condition_text or "", base_tf=rule.base_tf)
        symbols, params = ([rule.symbol.upper()] if rule.symbol else []), {}
    if not validated.ok or ast is None:
        raise ValueError("the rule's condition no longer validates")
    if not symbols:
        raise ValueError("the rule has no symbols")
    return ast, validated, symbols, params


def _values(frame: pd.DataFrame) -> Dict[str, Any]:
    last = frame.iloc[-1]
    out: Dict[str, Any] = {"as_of": frame.index[-1].isoformat()}
    for col in ("close", "volume", "oi"):
        if col in frame.columns and pd.notna(last[col]):
            out[col] = round(float(last[col]), 4)
    return out


async def _current_universe(session: AsyncSession, rule: AlertRuleRecord, symbols: List[str], bar: datetime, lookback: Dict[str, int],
                            fetch: Fetch, cache: CycleCache, forming: bool = False, per_user: bool = False) -> Tuple[List[SymbolData], Dict[str, str]]:
    """The symbols whose bars reach `bar` (closed, or forming for an intrabar rule), fetching only what the cycle cache
    does not already hold. `per_user` (BT1b): fetched through the rule creator's own session, cached per user."""
    exchange, tf = rule.exchange or "NSE", rule.base_tf
    owner = rule.created_by if per_user else None
    extra: Dict[str, Any] = {"user_id": owner} if per_user else {}
    key = lambda sym: (rule.tenant_id, owner, exchange, tf, bar, sym, forming)  # noqa: E731
    def covers(have: Dict[str, int]) -> bool:
        return all(have.get(t, 0) >= bars for t, bars in lookback.items())
    missing = [s for s in symbols if key(s) not in cache.frames or not covers(cache.frames[key(s)][0])]
    problems: Dict[str, str] = {}
    if missing:
        # one fetch that covers both the cached entries' needs and this rule's (later rules keep reusing it)
        merged = dict(lookback)
        for sym in missing:
            for t, bars in (cache.frames[key(sym)][0] if key(sym) in cache.frames else {}).items():
                merged[t] = max(merged.get(t, 0), bars)
        lookback = merged
        if forming:
            universe, problems, _source = await fetch(session, rule.tenant_id, missing, exchange, tf, lookback, forming=True, **extra)
        else:
            universe, problems, _source = await fetch(session, rule.tenant_id, missing, exchange, tf, lookback, **extra)
        cache.fetched_symbols += len(missing)
        got = {d.symbol: d for d in universe}
        for sym in missing:
            data = got.get(sym)
            frame = closed_frame(data.frames.get(tf, pd.DataFrame()), tf, bar) if data is not None else None
            kept: Dict[str, pd.DataFrame] = {tf: frame} if frame is not None else {}
            daily = data.frames.get("1d") if data is not None and tf != "1d" else None
            if frame is not None and daily is not None:               # U6 BK1: daily context, sessions closed by this bar's close
                kept["1d"] = closed_only(daily, "1d", bar_close(pd.DatetimeIndex([pd.Timestamp(bar)]), tf)[0])
            closed = SymbolData(sym, kept, data.sector, data.industry, data.mcap_bucket, data.is_fno) if data is not None and frame is not None else None
            cache.frames[key(sym)] = (dict(lookback), closed)
    cache.reused_symbols += len(symbols) - len(missing)
    return [d for s in symbols if (d := cache.frames[key(s)][1]) is not None], problems


def _fresh_only(current: List[SymbolData], tf: str, now: datetime, holidays: Iterable[date]) -> Tuple[List[SymbolData], Optional[str]]:
    """Z10-b: the symbols whose data passes the alert badge (`staleness.alert_badge`, timed by the newest bar of the
    frame the rule reads), and the first refusal reason. A frame without a readable bar time is refused.

    Defence in depth: today `closed_frame` already keeps only frames that reach the expected (latest closed) bar, so
    this refuses nothing on the current paths. It holds the line if a future fetch or cache path lets an old bar through
    (the test bypasses `closed_frame` to show it)."""
    kept: List[SymbolData] = []
    first: Optional[str] = None
    for data in current:
        frame = data.frames.get(tf)
        last = frame.index[-1] if frame is not None and len(frame) else None
        bar_start = last.to_pydatetime() if isinstance(last, pd.Timestamp) and not pd.isna(last) else None
        badge = staleness.alert_badge(bar_start, tf, now, holidays)
        if badge["alerts_suppressed"]:
            first = first or f"{data.symbol}: {badge['stale_reason']}"
            continue
        kept.append(data)
    staleness.count_suppressed("alert_record", len(current) - len(kept))
    return kept, first


async def evaluate_rule(session: AsyncSession, rule: AlertRuleRecord, now: datetime, fetch: Fetch,
                        holidays: Iterable[date] = (), cache: Optional[CycleCache] = None, intrabar_enabled: bool = False,
                        breakouts_enabled: bool = True, zones_enabled: bool = True, per_user: bool = False) -> Tuple[str, int]:
    """One rule at `now` -> (state, matches fired). States: done, waiting, skipped, unsupported, error."""
    from app.alerts.engine import record_event
    now = _utc(now)
    intrabar = rule.fire_on == "intrabar"
    if rule.base_tf not in SUPPORTED:
        rule.last_problem = f"{rule.base_tf} rules are not evaluated yet (weekly / monthly come later)"
        return "unsupported", 0
    if intrabar and not intrabar_enabled:
        rule.last_problem = "intrabar alerts are off for this organisation (flag screener_intrabar)"
        return "unsupported", 0
    bar = forming_bar(now, rule.base_tf, holidays) if intrabar else expected_bar(now, rule.base_tf, holidays)
    if bar is None:
        if intrabar:
            rule.last_problem = None                               # outside the session nothing is forming: not a problem
            return "skipped", 0
        rule.last_problem = f"no closed {rule.base_tf} bar found"
        return "unsupported", 0
    if not intrabar and rule.last_bar_at is not None and _utc(rule.last_bar_at) >= bar:
        return "skipped", 0
    if rule.last_checked_at is not None and now - _utc(rule.last_checked_at) < timedelta(seconds=RETRY_SECONDS):
        return "waiting", 0
    rule.last_checked_at = now
    cache = cache if cache is not None else CycleCache()
    try:
        ast, validated, symbols, params = await _rule_source(session, rule)
        from app.screener.breakouts.gate import flagged_calls
        blocked = flagged_calls(ast)
        if blocked and (not breakouts_enabled or intrabar):
            # U6 BK1: the rule stays active and resumes when the flag is back; breakout rules fire on bar close only
            rule.last_problem = (f"{', '.join(n + '()' for n in blocked)}: the breakout screens are off for this organisation (flag {BREAKOUT_FLAG})"
                                 if not breakouts_enabled else "breakout rules fire on bar close only, not intrabar")
            return "unsupported", 0
        from app.screener import zones as zone_gate
        zone_calls = zone_gate.flagged_calls(ast)
        if zone_calls and (not zones_enabled or intrabar):
            # Z7a-3: the rule stays active and resumes when the flag is back; zone rules fire on bar close only
            rule.last_problem = zone_gate.refusal(zone_calls) if not zones_enabled else zone_gate.intrabar_refusal(zone_calls)
            return "unsupported", 0
        current, problems = await _current_universe(session, rule, symbols, bar, validated.fetch_lookback, fetch, cache, forming=intrabar,
                                                    per_user=per_user)
        if zone_calls and current:
            # the tenant's zone snapshots as of each bar's close (a bar never sees a zone computed after it closed)
            await zone_gate.attach_zones(session, rule.tenant_id, rule.exchange or "NSE", current, now, validated.lookback)
    except Exception as exc:  # noqa: BLE001 - one rule's problem is recorded on the rule; the others go on
        rule.last_problem = f"{type(exc).__name__}: {str(exc)[:150]}"
        return "error", 0
    if current and await staleness.guard_on(session, rule.tenant_id, cache.stale_guard):
        fresh, stale = _fresh_only(current, rule.base_tf, now, holidays)
        cache.suppressed_symbols += len(current) - len(fresh)
        current = fresh
        if not current:
            rule.last_problem = f"alerts suppressed: {stale}"       # Z10-b: nothing recorded on stale data; retried later
            return "suppressed", 0
    if not current:
        rule.last_problem = f"waiting for the {'forming' if intrabar else 'closed'} bar from the broker" + (f" ({'; '.join(list(problems.values())[:2])})" if problems else "")
        return "waiting", 0
    fired = 0
    frames = {d.symbol: d.frames[rule.base_tf] for d in current}
    result_key = (rule.tenant_id, rule.created_by if per_user else None, json.dumps(nodes.to_json(ast), sort_keys=True), rule.base_tf, bar, tuple(frames),
                  json.dumps(params, sort_keys=True, default=str), intrabar)
    if result_key in cache.results:
        cache.reused_results += 1
    else:
        cache.results[result_key] = run_screen(ast, validated, current, base_tf=rule.base_tf, params=params)
    for match in cache.results[result_key]:
        values = _values(frames[match.symbol])
        if intrabar:
            values["intrabar"] = True                             # the bar had not closed; it may not hold at the close
        if match.matched and await record_event(session, rule, match.symbol, bar, values, now=now) is not None:
            fired += 1
    if not intrabar:
        rule.last_bar_at = bar                                    # intrabar: other symbols may still match later in the bar
    rule.last_problem = None
    return "done", fired


async def run_due(session: AsyncSession, now: Optional[datetime] = None, fetch: Optional[Fetch] = None,
                  holidays: Optional[Set[date]] = None, budget_seconds: float = TIME_BUDGET_SECONDS) -> Outcome:
    """Every active rule of every organisation with the flag on, oldest-evaluated first, within `budget_seconds` (the
    rest wait for the next cycle). Commits once at the end."""
    from app.platform.controls import flag_enabled
    now = _utc(now or datetime.now(timezone.utc))
    if holidays is None:
        from app.market_data.calendar import load_holidays
        holidays = await load_holidays(session, "NSE")
    out = Outcome()
    rules = list(await session.scalars(select(AlertRuleRecord).where(AlertRuleRecord.status == "active")
                                       .order_by(AlertRuleRecord.last_checked_at.is_not(None), AlertRuleRecord.last_checked_at, AlertRuleRecord.id)))
    enabled: Dict[int, bool] = {}
    intrabar_on: Dict[int, bool] = {}
    breakouts_on: Dict[int, bool] = {}
    zones_on: Dict[int, bool] = {}
    per_user: Dict[int, bool] = {}           # BT1b: byob_data_tier per organisation
    started = time_module.monotonic()
    for rule in rules:
        if rule.expires_at is not None and _utc(rule.expires_at) <= now:
            continue
        if rule.tenant_id not in enabled:
            enabled[rule.tenant_id] = await flag_enabled(session, FLAG, rule.tenant_id)
        if not enabled[rule.tenant_id]:
            continue
        out.rules += 1
        if time_module.monotonic() - started > budget_seconds:
            out.deferred += 1
            continue
        if rule.fire_on == "intrabar" and rule.tenant_id not in intrabar_on:
            intrabar_on[rule.tenant_id] = await flag_enabled(session, INTRABAR_FLAG, rule.tenant_id)
        if rule.tenant_id not in breakouts_on:
            breakouts_on[rule.tenant_id] = await flag_enabled(session, BREAKOUT_FLAG, rule.tenant_id)
        if rule.tenant_id not in zones_on:
            zones_on[rule.tenant_id] = await flag_enabled(session, ZONES_FLAG, rule.tenant_id)
        if rule.tenant_id not in per_user:
            per_user[rule.tenant_id] = await flag_enabled(session, "byob_data_tier", rule.tenant_id)
        state, fired = await evaluate_rule(session, rule, now, fetch or server_fetch, holidays, out.cache,
                                           intrabar_enabled=intrabar_on.get(rule.tenant_id, False),
                                           breakouts_enabled=breakouts_on[rule.tenant_id], zones_enabled=zones_on[rule.tenant_id],
                                           per_user=per_user[rule.tenant_id])
        out.evaluated += state == "done"
        out.waiting += state == "waiting"
        out.fired += fired
        if state == "error" and rule.last_problem:
            out.problems.append(f"rule {rule.id}: {rule.last_problem}")
    await session.commit()
    return out


async def server_fetch(session: AsyncSession, tenant_id: int, symbols: List[str], exchange: str, base_tf: str,
                       lookback: Dict[str, int], forming: bool = False, user_id: Optional[int] = None) -> Tuple[List[SymbolData], Dict[str, str], str]:
    """Server bars via the organisation's broker session (S1d `fetch_frames`). For a daily rule, today's bar is built
    from today's 15-minute bars (after the close; during the session for an intrabar rule, `forming`), since a
    broker's daily history starts at yesterday."""
    from app.screener.routes import fetch_frames
    universe, problems, source = await fetch_frames(session, tenant_id, symbols, exchange, base_tf, lookback, include_forming=forming, user_id=user_id)
    if base_tf != "1d" or not universe:
        return universe, problems, source
    intraday, _p, _s = await fetch_frames(session, tenant_id, [d.symbol for d in universe], exchange, "15m", {"15m": 30},
                                          include_forming=forming, user_id=user_id)
    today = datetime.now(timezone.utc).astimezone(IST).date()
    by_symbol = {d.symbol: d.frames.get("15m") for d in intraday}
    for data in universe:
        data.frames["1d"] = with_today(data.frames["1d"], by_symbol.get(data.symbol), today, complete_only=not forming)
    return universe, problems, source


def _reaches_close(part: pd.DataFrame, day: date) -> bool:
    """The intraday bars of `day` include the session's last bucket of their own timeframe (the step between bars)."""
    if len(part) < 2:
        step_minutes = 15
    else:
        step_minutes = max(1, int(min((b - a).total_seconds() for a, b in zip(part.index[:-1], part.index[1:])) // 60))
    last = _last_intraday_bar(day, step_minutes, datetime.combine(day, MARKET_CLOSE, IST))
    return last is not None and part.index[-1] >= pd.Timestamp(last)


def with_today(daily: pd.DataFrame, intraday: Optional[pd.DataFrame], today: date, *, complete_only: bool = False) -> pd.DataFrame:
    """Appends today's daily bar built from today's intraday bars (label: the session open), unless already present.
    `complete_only` (a bar-close rule; Z10-b fix): only when the intraday bars reach the session's last bucket - a feed
    that stopped at noon must not become "today's close" (the rule then waits for the data, never fires on part of a
    day); a candle for today already in the broker's daily history is dropped first (it may still be forming)."""
    if intraday is None or intraday.empty:
        return daily
    if complete_only and len(daily) and daily.index[-1].astimezone(IST).date() >= today:
        # Z10-b review: a broker's daily history may already end with today's candle while it is still forming. A
        # bar-close rule never takes it: today's bar comes only from intraday bars that reach the session's last bucket.
        daily = daily[[ts.astimezone(IST).date() < today for ts in daily.index]]
    if len(daily) and daily.index[-1].astimezone(IST).date() >= today:
        return daily
    mask = [ts.astimezone(IST).date() == today for ts in intraday.index]
    part = intraday[mask]
    if part.empty:
        return daily
    if complete_only and not _reaches_close(part, today):
        return daily
    row = {"open": part["open"].iloc[0], "high": part["high"].max(), "low": part["low"].min(), "close": part["close"].iloc[-1],
           "volume": part["volume"].sum()}
    if "oi" in part.columns:
        row["oi"] = part["oi"].iloc[-1]
    label = pd.Timestamp(datetime.combine(today, time(9, 15), IST)).tz_convert(daily.index.tz or timezone.utc) if len(daily) else \
        pd.Timestamp(datetime.combine(today, time(9, 15), IST))
    return pd.concat([daily, pd.DataFrame([row], index=pd.DatetimeIndex([label]))])


__all__ = ["CycleCache", "expected_bar", "forming_bar", "INTRABAR_FLAG", "closed_frame", "evaluate_rule", "run_due", "server_fetch", "with_today", "Outcome", "SUPPORTED", "RETRY_SECONDS"]
