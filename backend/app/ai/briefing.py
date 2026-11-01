"""Phase AV: the Copilot's daily briefing - what an experienced trader tells you before the open.

`build()` puts on one page what a beginner otherwise has to collect from six screens, and says what
it means for them today:

* **Day type** from the market memory (NIFTY's regime and bias, India VIX, the global mood):
  trending up / down, sideways, volatile - with a headline and a game plan (which strategy families
  fit the day, which to leave alone, how big to trade, events that cut or block trading).
* **Session** - open or closed, the next open, holidays in the coming week.
* **Your day** - today's realised P&L, trades used against the daily limit, loss budget left
  against the daily loss limit, open positions, the guardian's drawdown state.
* **Your deployments** - every active / paused deployment with a plain "why no trade" reading:
  paused, market closed, errors, the worker not evaluating, the day's regime not suiting the
  strategy family (no trade is then the correct result), the last signal.
* **Pre-trade checklist** - broker session, risk settings saved, worker running, loss budget left,
  VIX, blocking events.

Deterministic; the copilot route can hand it to an AI provider to narrate. Education, not advice.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import global_cues, market_memory
from app.ai.interview import FAMILY_TEXT, PROFILES, regime_fit
from app.brokers.token_lifecycle import token_is_usable
from app.core.models import RiskConfig
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, TraderProfileRecord, User, WorkerHeartbeatRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, last_session_close, load_holidays, session_family, session_status
from app.risk_engine import guardian

WORKER_STALE_MINUTES = 5
# P0.10: the market memory behind the briefing is "stale" when older than three worker reads while the market is open,
# or older than the overnight gap while it is closed; its figures are then not shown as today's.
STALE_OPEN_MINUTES = 3 * market_memory.INTERVAL_MINUTES
STALE_CLOSED_MINUTES = 20 * 60


def data_freshness(memory: dict, now: datetime, market_open: bool) -> dict:
    """How old the market figures are and whether they may be shown. Figures are hidden when there are none, when they
    are stale, and when every symbol carries the same change (a placeholder, not a market)."""
    # The age is the newest SYMBOL / VIX read - not the memory's `updated_at`, which global cues (fetched without a broker
    # session, every 15 minutes) keep current while the index rows can be days old.
    reads = [s.get("captured_at") for s in memory.get("symbols", [])]
    reads += [c.get("captured_at") for c in memory.get("cues", []) if c.get("symbol") == "INDIA VIX"]
    stamps = [datetime.fromisoformat(str(r)) for r in reads if r]
    if not stamps:
        return {"state": "none", "updated_at": None, "age_minutes": None, "figures_shown": False}
    newest = max(stamps)
    updated = newest.isoformat()
    age = max((now - newest).total_seconds() / 60.0, 0.0)
    stale = age > (STALE_OPEN_MINUTES if market_open else STALE_CLOSED_MINUTES)
    changes = [round(float(s["change_pct"]), 2) for s in memory.get("symbols", []) if s.get("change_pct") is not None]
    identical = len(changes) >= 2 and len(set(changes)) == 1
    state = "stale" if stale else "suspect" if identical else "fresh"
    return {"state": state, "updated_at": updated, "age_minutes": round(age, 1), "figures_shown": state == "fresh"}
DAY_TYPES = ("TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "UNKNOWN")


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _money(v: float) -> str:
    return f"{'-' if v < 0 else ''}₹{abs(v):,.0f}"


def day_type(memory: dict) -> dict:
    """The day's character from NIFTY's read (NIFTY BANK when NIFTY is missing) and India VIX."""
    snaps = {s["symbol"]: s for s in memory.get("symbols", [])}
    lead = snaps.get("NIFTY 50") or snaps.get("NIFTY BANK")
    vix_row = next((c for c in memory.get("cues", []) if c["symbol"] == "INDIA VIX"), None)
    vix = float(vix_row["last_price"]) if vix_row and vix_row.get("last_price") else None
    regime = (lead or {}).get("regime") or "UNKNOWN"
    if vix is not None and vix >= 20:
        kind = "VOLATILE"
    elif regime == "TRENDING_UP":
        kind = "TREND_UP"
    elif regime == "TRENDING_DOWN":
        kind = "TREND_DOWN"
    elif regime in ("RANGING", "QUIET"):
        kind = "RANGE"
    elif regime == "VOLATILE":
        kind = "VOLATILE"
    else:
        kind = "UNKNOWN"
    return {"kind": kind, "regime": regime, "symbol": (lead or {}).get("symbol"), "bias": (lead or {}).get("bias"),
            "change_pct": (lead or {}).get("change_pct"), "higher_regime": (lead or {}).get("higher_regime"), "vix": vix}


def _families(regime: str) -> Dict[str, List[str]]:
    fits: Dict[str, float] = {fam: regime_fit(fam, regime) for fam in FAMILY_TEXT}
    return {"fit": [f for f, v in fits.items() if v >= 2], "avoid": [f for f, v in fits.items() if v == 0]}


def risk_line(lang: str, cfg: RiskConfig, saved: bool) -> str:
    """P0.10: the risk settings the trades will actually use. DEF-0: only the limits the trader set himself - there are no
    platform defaults; with none set, none applies."""
    parts = []
    if cfg.risk_per_trade_pct is not None:
        parts.append(f"{cfg.risk_per_trade_pct:g}% risk per trade")
    if cfg.max_daily_loss_pct is not None:
        parts.append(f"daily loss limit {cfg.max_daily_loss_pct:g}%")
    if cfg.max_consecutive_losses is not None:
        parts.append(f"pause after {cfg.max_consecutive_losses} losses in a row")
    if parts:
        return f"Your risk settings: {', '.join(parts)}."
    return "No limits of your own are set, so none applies. You can set them in My limits."


# Handover 11l (flag `signal_language_v2`): the two plan texts that used stop wording, in the signal language. The brief
# is a public-style surface (no security names, no prices, no buy / sell / target / stop wording); the risk line is the
# user's own settings record and is unchanged.
VOLATILE_HEADLINE_V2 = ("A volatile day on the data: wide swings and gaps, so the risk distances your settings work out are wider "
                        "and position sizes come out smaller.")
HIGH_VIX_LINE_V2 = "India VIX is in its high-fear band: gaps are more likely; your own risk settings size every position."


def game_plan(lang: str, dt: dict, memory: dict, experience: Optional[str], events: List[dict], *,
              cfg: Optional[RiskConfig] = None, cfg_saved: bool = False, freshness: Optional[dict] = None,
              signal_v2: bool = False, read_label: Optional[str] = None) -> dict:
    kind = dt["kind"]
    # P0.8-D: the day described by the data - never "buy the pullbacks", "sell the rallies" or "stay out".
    headlines = {
        "TREND_UP": "A trending-up day on the data: higher highs and higher lows; trend-family templates' filters are open, reversal ones mostly closed.",
        "TREND_DOWN": "A trending-down day on the data: lower highs and lower lows; trend-family templates' filters are open, reversal ones mostly closed.",
        "RANGE": "A sideways day on the data: trend-family templates fit it poorly; range and reversion templates' filters are open.",
        "VOLATILE": "A volatile day on the data: wide swings and gaps, so ATR-based stops are wider and position sizes come out smaller.",
        # M82 R3: never "needs a broker session" here - the brief replaces this with the exact reason (`read_state`).
        "UNKNOWN": "No market read stored yet.",
    }
    if signal_v2:
        headlines["VOLATILE"] = VOLATILE_HEADLINE_V2
    lines: List[str] = []
    fam = _families(dt["regime"]) if kind != "UNKNOWN" else {"fit": [], "avoid": []}
    if fam["fit"]:
        lines.append("Regime filters open today: " + ", ".join(FAMILY_TEXT[f][0] for f in fam["fit"]) + ".")
    if fam["avoid"]:
        lines.append("Regime filters closed today: " + ", ".join(FAMILY_TEXT[f][0] for f in fam["avoid"]) + " - a poor fit for these templates (a diagnostic only; it never blocks an entry).")
    # P0.10: when the market figures are not a current read, no VIX or global-cue numbers are repeated as today's.
    hidden = freshness is not None and not freshness.get("figures_shown", True)
    vix = None if hidden else dt.get("vix")
    if vix is not None:
        lines.append(market_memory.vix_text(lang, vix) + ".")
        if vix >= 20:
            lines.append(HIGH_VIX_LINE_V2 if signal_v2 else
                         "VIX is 20 or higher: fear is high and gaps are more likely; your risk settings size every trade from its stop.")
    glines = [] if hidden else global_cues.view(lang, memory.get("globals", []))
    if glines:
        lines.append(glines[0])
    for e in events:
        what = e.get("description") or e.get("kind")
        if e.get("action") == "BLOCK":
            lines.append(f"Event today: {what} - new entries are blocked in its window.")
        else:
            lines.append(f"Event today: {what} - position size is cut automatically.")
    lines.append(risk_line(lang, cfg or RiskConfig(), cfg_saved))
    headline = headlines[kind]
    if read_label:                       # M82 R2: a stored read of an earlier session, named by its session date
        headline = f"{read_label}: " + headline
    elif hidden and kind != "UNKNOWN" and freshness and freshness.get("age_minutes") is not None:
        hours = freshness["age_minutes"] / 60.0
        ago = f"{hours:.0f} h" if hours >= 1 else f"{freshness['age_minutes']:.0f} min"
        headline = f"Last market read ({ago} ago, not today's): " + headline
    return {"headline": headline, "lines": lines, "fit_families": fam["fit"], "avoid_families": fam["avoid"]}


async def your_day(session: AsyncSession, user: User, cfg: RiskConfig, now: datetime) -> dict:
    start_ist = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_ist.astimezone(timezone.utc)
    rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                                                (TradeRecord.entry_time >= start) | (TradeRecord.exit_time >= start) | TradeRecord.exit_time.is_(None))))
    out = {}
    for mode in ("PAPER", "LIVE"):
        mine = [t for t in rows if t.mode == mode]
        closed_today = [t for t in mine if t.exit_time is not None and _utc(t.exit_time) >= start and t.pnl is not None]
        entered_today = [t for t in mine if _utc(t.entry_time) >= start]
        open_ = [t for t in mine if t.exit_time is None]
        pnl = round(sum(t.pnl for t in closed_today), 2)
        daily = cfg.daily_loss_amount()                  # DEF-0: None = no daily loss limit set
        limit = round(daily, 2) if daily is not None else None
        streak = 0
        for t in sorted(closed_today, key=lambda t: _utc(t.exit_time), reverse=True):
            if t.pnl > 0:
                break
            streak += 1
        out[mode] = {"realised_pnl": pnl, "trades_today": len(entered_today), "max_trades": cfg.max_trades_per_day,
                     "open_positions": len(open_), "max_open": cfg.max_open_positions, "loss_limit": limit,
                     "loss_used": round(max(0.0, -pnl), 2), "loss_left": round(max(0.0, limit + min(pnl, 0.0)), 2) if limit is not None else None,
                     "consecutive_losses": streak, "max_consecutive_losses": cfg.max_consecutive_losses,
                     "active": bool(mine)}
    return out


def _why(lang: str, dep: StrategyDeploymentRecord, now: datetime, market_open: bool, memory: dict, next_open: Optional[datetime]) -> dict:
    lines: List[str] = []
    state = "ok"
    profile = PROFILES.get(dep.strategy_id)
    snap = next((s for s in memory.get("symbols", []) if s["symbol"] == (dep.symbol or "").upper()), None)
    regime = (snap or {}).get("regime")
    last_eval, last_sig = _utc(dep.last_evaluated_at), _utc(dep.last_signal_at)
    today = now.astimezone(IST).date()
    if dep.status == "PAUSED":
        state = "paused"
        lines.append(f"Paused: {dep.pause_reason or 'by you'}.")
    if not market_open and (dep.holding or "INTRADAY") != "SWING":
        state = "closed" if state == "ok" else state
        when = next_open.astimezone(IST).strftime("%a %d %b %H:%M") if next_open else "-"
        lines.append(f"Market closed - it trades again from {when} IST.")
    if dep.last_error:
        state = "error"
        lines.append(f"Last error: {dep.last_error[:160]}")
    if market_open and dep.status == "ACTIVE" and (last_eval is None or now - last_eval > timedelta(minutes=WORKER_STALE_MINUTES)):
        state = "stale" if state == "ok" else state
        lines.append("Not evaluated in the last few minutes - is the worker container running?")
    if profile and regime:
        fit = regime_fit(profile.family, regime)
        fam_en = FAMILY_TEXT[profile.family][0]
        if fit == 0:
            state = "regime" if state == "ok" else state
            lines.append(f"{dep.symbol} is {regime.lower().replace('_', ' ')}; a {fam_en} strategy stays out in this regime - no trade is the correct result.")
        elif fit >= 2:
            lines.append(f"Today's {regime.lower().replace('_', ' ')} market suits this {fam_en} strategy - it trades when its exact rules line up.")
    if last_sig and last_sig.astimezone(IST).date() == today:
        lines.append(f"Last signal today at {last_sig.astimezone(IST).strftime('%H:%M')}.")
    elif state == "ok" and dep.status == "ACTIVE":
        lines.append("Watching: no setup yet today. Most good strategies take 0-3 trades a day.")
    return {"id": dep.id, "strategy_id": dep.strategy_id, "symbol": dep.symbol, "timeframe": dep.timeframe, "mode": dep.mode, "status": dep.status,
            "holding": dep.holding, "family": profile.family if profile else None, "regime": regime, "state": state,
            "last_evaluated_at": last_eval.isoformat() if last_eval else None, "last_signal_at": last_sig.isoformat() if last_sig else None, "why": lines}


def day_label(day: date) -> str:
    """"Fri 30 Oct" - a session date as the card shows it."""
    return f"{day:%a} {day.day} {day:%b}"


# M82 R3: the one reason line when no market read is stored (exactly one of these).
REASON_NONE_EVER = "none_ever"
REASON_BROKER = "broker_expired"
REASON_WORKER = "worker_missed"
BROKER_TEXT = {
    "expired": "Your broker session has expired - reconnect it to read the market.",
    "none": "No broker is connected - connect your broker on the Broker page to read the market.",
    "teammate": "The broker in your organisation was linked by a teammate - link your own broker to read the market.",
    "shared": "Market memory is shared in your organisation, so it is not read through your own broker session.",
    "role": "Your role cannot start a market read.",
}


async def read_now_state(session: AsyncSession, user: User) -> dict:
    """M82 R4: whether "Read now" (POST /api/ai/market-memory/refresh) can read the market for this user, the same checks
    the endpoint makes: a trading role, not the bring-your-own-broker tier, and the user's OWN usable broker session
    (`credential_for_reader`, never a teammate's). `broker`: ok / expired / none / teammate / shared / role."""
    from fastapi import HTTPException

    from app.auth.dependencies import TRADING_ROLES
    from app.market_data import live_access
    from app.market_data.reader_credential import credential_for_reader
    if user.role not in TRADING_ROLES:
        return {"enabled": False, "broker": "role", "reason": BROKER_TEXT["role"]}
    if await live_access.byob_on(session, user.tenant_id):
        return {"enabled": False, "broker": "shared", "reason": BROKER_TEXT["shared"]}
    try:
        await credential_for_reader(session, user.tenant_id, user.id)
    except HTTPException:
        own = list(await session.scalars(select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.user_id == user.id)
            .order_by(BrokerCredentialRecord.id)))
        if own:
            return {"enabled": False, "broker": "expired", "reason": BROKER_TEXT["expired"],
                    "reconnect": {"broker": own[0].broker_name, "account_label": own[0].account_label or "primary"}}
        anyone = await session.scalar(select(BrokerCredentialRecord.id).where(BrokerCredentialRecord.tenant_id == user.tenant_id).limit(1))
        kind = "teammate" if anyone is not None else "none"
        return {"enabled": False, "broker": kind, "reason": BROKER_TEXT[kind]}
    return {"enabled": True, "broker": "ok", "reason": None}


def _worker_ran(beats: list[WorkerHeartbeatRecord], start: datetime, end: datetime) -> bool:
    """The trading worker was alive inside [start, end]: started before its end and seen after its start."""
    for b in beats:
        if b.worker_name != "trading_worker" or b.last_seen_at is None:
            continue
        started, seen = _utc(b.started_at), _utc(b.last_seen_at)
        if seen is not None and seen >= start and (started is None or started <= end):
            return True
    return False


async def read_state(session: AsyncSession, user: User, now: datetime, is_open: bool, holidays, symbols: list[dict],
                     beats: list[WorkerHeartbeatRecord], read_now: dict) -> dict:
    """M82 R2 / R3: which market read the card shows and, when there is none, the one reason why.
    state: today / last_session / older / none; label: "Today", "Last session, Fri 30 Oct", "Last read, Wed 28 Oct"."""
    now_ist = now.astimezone(IST)
    spec = EXCHANGE_SESSIONS[session_family("NSE")]
    if is_open:
        session_day = now_ist.date()
        window = (datetime.combine(session_day, spec.open, tzinfo=IST), now)
    else:
        close = last_session_close(now, holidays, "NSE")
        session_day = close.date() if close else now_ist.date()
        # the worker also takes the day's final read in the hour after the close
        window = (datetime.combine(session_day, spec.open, tzinfo=IST), (close or now) + timedelta(hours=1))
    stamps = [datetime.fromisoformat(str(s["captured_at"])) for s in symbols if s.get("captured_at")]
    out: dict = {"session_date": session_day.isoformat(), "session_label": day_label(session_day), "read_now": read_now}
    if stamps:
        read_day = max(stamps).astimezone(IST).date()
        if is_open and read_day == session_day:
            state, label = "today", "Today"
        elif read_day == session_day:
            state, label = "last_session", f"Last session, {day_label(read_day)}"
        else:
            state, label = "older", f"Last read, {day_label(read_day)}"
        return {**out, "state": state, "label": label, "read_date": read_day.isoformat(), "reason": None, "reason_text": None}
    if read_now.get("broker") in ("expired", "none", "teammate"):
        reason, text = REASON_BROKER, BROKER_TEXT[read_now["broker"]]
    else:
        profiled = await session.scalar(select(TraderProfileRecord.id).where(TraderProfileRecord.tenant_id == user.tenant_id).limit(1))
        if profiled is None or not _worker_ran(beats, *window):
            reason = REASON_WORKER
            text = (f"The automatic market read did not run {'today' if is_open else 'on the last session'} "
                    f"({day_label(session_day)}).")
        else:
            reason, text = REASON_NONE_EVER, "No market read stored yet."
    if read_now.get("enabled"):
        text += " Press Read now."
    return {**out, "state": "none", "label": None, "read_date": None, "reason": reason, "reason_text": f"No market read: {text}"
            if reason != REASON_NONE_EVER else text}


async def build(session: AsyncSession, user: User, lang: str = "en", now: Optional[datetime] = None) -> dict:
    from app.ai import advisor
    from app.risk_engine.routes import get_tenant_risk_config
    lang = "en"         # ATP is English only
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    memory = await market_memory.latest(session, user.tenant_id, now=now)
    if not memory.get("symbols"):
        # M82 R2: a stored read older than `latest()`'s 3-day window is still the last read - never "no read".
        memory = {**memory, "symbols": await market_memory.last_read(session, user.tenant_id)}
    saved, _ = await advisor.load_profile(session, user)
    experience = (saved or {}).get("experience")
    cfg_saved = await get_tenant_risk_config(user.tenant_id, session)
    cfg = cfg_saved or RiskConfig()
    holidays = await load_holidays(session, "NSE")
    status = session_status(now, holidays, "NSE")
    upcoming = sorted(d for d in holidays if now_ist.date() <= d <= now_ist.date() + timedelta(days=7))
    events = [guardian.event_as_dict(e) for e in await guardian.events_on(session, user.tenant_id, now_ist.date())]

    dt = day_type(memory)
    freshness = data_freshness(memory, now, status.is_open)
    beats = list(await session.scalars(select(WorkerHeartbeatRecord)))
    read = await read_state(session, user, now, status.is_open, holidays, memory.get("symbols", []), beats,
                            await read_now_state(session, user))
    from app.platform.controls import flag_enabled
    from app.signals.language import FLAG as SIGNAL_LANGUAGE_FLAG
    plan = game_plan(lang, dt, memory, experience, events, cfg=cfg, cfg_saved=cfg_saved is not None, freshness=freshness,
                     signal_v2=await flag_enabled(session, SIGNAL_LANGUAGE_FLAG, user.tenant_id),
                     read_label=read["label"] if read["state"] in ("last_session", "older") else None)
    if read["state"] == "none":
        plan["headline"] = read["reason_text"]
    elif dt["kind"] == "UNKNOWN":
        # a read is stored, but its candles were too few to name the day's character
        plan["headline"] = ((f"{read['label']}: " if read["state"] != "today" else "")
                            + "The stored read has too few candles to name the day's character.")
    mood = global_cues.mood(memory.get("globals", []), now) if memory.get("globals") else None
    from app.ai import sentiment as sentiment_mod  # Phase BC
    sentiment_read = memory.get("sentiment")
    sentiment_lines = sentiment_mod.view(lang, sentiment_read) if sentiment_read else []
    day = await your_day(session, user, cfg, now)
    deps = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == user.tenant_id, StrategyDeploymentRecord.status.in_(["ACTIVE", "PAUSED"]))
        .order_by(StrategyDeploymentRecord.id)))
    deployments = [_why(lang, d, now, status.is_open, memory, status.next_open) for d in deps]

    creds = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)))
    broker_ok = any(token_is_usable(c, now) for c in creds)
    worker_seen = max((_utc(b.last_seen_at) for b in beats if b.last_seen_at), default=None)
    worker_ok = worker_seen is not None and now - worker_seen <= timedelta(minutes=WORKER_STALE_MINUTES)
    budget_mode = "LIVE" if day["LIVE"]["active"] else "PAPER"
    checklist = [
        {"id": "broker", "ok": broker_ok, "text": "Broker session logged in today" if broker_ok
         else "No usable broker session - log in on the Broker page (needed for real candles and LIVE)"},
        # DEF-0: limits are optional - none set is not a problem (ok None = neutral), only an offer
        {"id": "risk", "ok": True if cfg_saved is not None else None, "text": "Your own limits are saved" if cfg_saved is not None
         else "No limits of your own are set, so none applies - set them in My limits if you want"},
        {"id": "worker", "ok": worker_ok, "text": "Autopilot worker is running" if worker_ok
         else "Worker not seen in the last minutes - deployments will not trade (check the worker container)"},
        {"id": "loss_budget", "ok": None if day[budget_mode]["loss_left"] is None else day[budget_mode]["loss_left"] > 0,
         "text": "No daily loss limit set" if day[budget_mode]["loss_left"] is None
         else f"Loss budget left today ({budget_mode}): {_money(day[budget_mode]['loss_left'])} of {_money(day[budget_mode]['loss_limit'])}"},
        {"id": "vix", "ok": None if dt["vix"] is None else dt["vix"] < 20,
         "text": "India VIX not read yet" if dt["vix"] is None else market_memory.vix_text(lang, dt["vix"])},
        {"id": "events", "ok": not any(e["action"] == "BLOCK" for e in events),
         "text": f"{len(events)} market event(s) today" if events
         else "No market events today"},
    ]
    session_text = "Market open now" if status.is_open else f"Market closed - opens {status.next_open.astimezone(IST).strftime('%a %d %b %H:%M') if status.next_open else '-'} IST"
    return {
        "as_of": now.isoformat(), "language": lang, "experience": experience,
        "session": {"open": status.is_open, "text": session_text, "next_open": status.next_open.isoformat() if status.next_open else None,
                    "holidays_next_7_days": [d.isoformat() for d in upcoming]},
        "day_type": dt, "plan": plan, "global_mood": mood, "sentiment": sentiment_read, "sentiment_view": sentiment_lines, "market_updated_at": memory.get("updated_at"),
        "market": {"symbols": memory.get("symbols", [])[:4], "cues": memory.get("cues", []), "globals": memory.get("globals", [])},
        "market_data": freshness, "read": read,
        "events": events, "you": day, "deployments": deployments, "checklist": checklist,
    }


def summary_lines(lang: str, brief: dict) -> List[str]:
    """The briefing in a few sentences - for the copilot chat and the AI context."""
    lines = [brief["plan"]["headline"], brief["session"]["text"] + "."]
    lines += brief["plan"]["lines"][:4]
    mode = "LIVE" if brief["you"]["LIVE"]["active"] else "PAPER"
    y = brief["you"][mode]
    trades = f"{y['trades_today']}/{y['max_trades']}" if y["max_trades"] is not None else f"{y['trades_today']}"
    budget = f", loss budget left {_money(y['loss_left'])}" if y["loss_left"] is not None else ""
    lines.append(f"You today ({mode}): P&L {_money(y['realised_pnl'])}, {trades} trades{budget}.")
    missing = [c["text"] for c in brief["checklist"] if c["ok"] is False]
    if missing:
        lines.append("Fix first: " + missing[0])
    return lines


def deployment_lines(lang: str, brief: dict) -> List[str]:
    if not brief["deployments"]:
        return ["No active deployments. Build a strategy with the interview and deploy it in PAPER first."]
    return [f"#{d['id']} {d['strategy_id']} · {d['symbol']} ({d['mode']}): " + " ".join(d["why"]) for d in brief["deployments"]]


__all__ = ["build", "day_type", "game_plan", "summary_lines", "deployment_lines", "DAY_TYPES"]
