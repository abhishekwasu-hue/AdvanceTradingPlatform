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

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import global_cues, market_memory
from app.ai.interview import FAMILY_TEXT, PROFILES, regime_fit, tr
from app.brokers.token_lifecycle import token_is_usable
from app.core.models import RiskConfig
from app.db.models import (BrokerCredentialRecord, RiskSettingsRecord, StrategyDeploymentRecord, TradeRecord, User,
                           WorkerHeartbeatRecord)
from app.market_data.calendar import IST, load_holidays, session_status
from app.risk_engine import guardian

WORKER_STALE_MINUTES = 5
DAY_TYPES = ("TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "UNKNOWN")
REGIME_MR = {"TRENDING_UP": "वरचा trend", "TRENDING_DOWN": "खालचा trend", "RANGING": "sideways", "VOLATILE": "अस्थिर", "QUIET": "शांत", "UNKNOWN": "-"}


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


def game_plan(lang: str, dt: dict, memory: dict, experience: Optional[str], events: List[dict]) -> dict:
    kind = dt["kind"]
    headlines = {
        "TREND_UP": ("A trending-up day: trade with the trend, buy the pullbacks; shorts are against the tide.",
                     "वरच्या trend चा दिवस: trend सोबत trade, pullback वर खरेदी; short घेणे प्रवाहाविरुद्ध."),
        "TREND_DOWN": ("A trending-down day: sell the rallies or stay out; buying dips is catching a falling knife.",
                       "खालच्या trend चा दिवस: वर आलेल्या भावावर विक्री किंवा बाजूला थांबा; घसरणीत खरेदी म्हणजे पडणारी सुरी पकडणे."),
        "RANGE": ("A sideways day: trend strategies will mostly sit out (correctly); range edges and reversion setups fit better.",
                  "Sideways दिवस: trend strategies बहुतेक trade घेणार नाहीत (आणि ते बरोबर आहे); range च्या कडा आणि reversion setups जास्त जुळतात."),
        "VOLATILE": ("A volatile day: wide swings and gaps - trade small or just watch; stops get hit by noise.",
                     "अस्थिर दिवस: मोठे चढ-उतार आणि gap - लहान size किंवा फक्त निरीक्षण; गोंधळात stop लागतात."),
        "UNKNOWN": ("No market read yet - press \"Read now\" in Market memory (needs a broker session), or wait for the worker after 09:15.",
                    "अजून market वाचलेला नाही - Market memory मध्ये \"आत्ता वाचा\" दाबा (broker login लागतो), किंवा 09:15 नंतर worker ची वाट पाहा."),
    }
    lines: List[str] = []
    fam = _families(dt["regime"]) if kind != "UNKNOWN" else {"fit": [], "avoid": []}
    if fam["fit"]:
        lines.append(tr(lang, "Fits today: " + ", ".join(FAMILY_TEXT[f][0] for f in fam["fit"]) + ".",
                        "आज जुळणाऱ्या पद्धती: " + ", ".join(FAMILY_TEXT[f][1] for f in fam["fit"]) + "."))
    if fam["avoid"]:
        lines.append(tr(lang, "Leave alone today: " + ", ".join(FAMILY_TEXT[f][0] for f in fam["avoid"]) + " - their regime filter will keep them out.",
                        "आज टाळा: " + ", ".join(FAMILY_TEXT[f][1] for f in fam["avoid"]) + " - त्यांचा regime filter त्यांना बाहेरच ठेवेल."))
    vix = dt.get("vix")
    if vix is not None:
        lines.append(market_memory.vix_text(lang, vix) + ".")
        if vix >= 20 and experience in (None, "new", "learning"):
            lines.append(tr(lang, "With VIX at 20+, a beginner should paper-trade or watch today.", "VIX 20+ असताना नवशिक्याने आज PAPER वर किंवा फक्त निरीक्षण करावे."))
    glines = global_cues.view(lang, memory.get("globals", []))
    if glines:
        lines.append(glines[0])
    for e in events:
        what = e.get("description") or e.get("kind")
        if e.get("action") == "BLOCK":
            lines.append(tr(lang, f"Event today: {what} - new entries are blocked in its window.", f"आज event: {what} - त्या वेळेत नवीन entries बंद."))
        else:
            lines.append(tr(lang, f"Event today: {what} - position size is cut automatically.", f"आज event: {what} - position size आपोआप कमी होईल."))
    if experience == "new":
        lines.append(tr(lang, "Your rule as a beginner: one setup, small size, stop placed with the entry, done after two losses.",
                        "नवशिक्या म्हणून तुमचा नियम: एकच setup, लहान size, entry सोबतच stop, दोन तोट्यांनंतर दिवस संपला."))
    head = headlines[kind]
    return {"headline": tr(lang, head[0], head[1]), "lines": lines, "fit_families": fam["fit"], "avoid_families": fam["avoid"]}


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
        limit = round(cfg.capital * cfg.max_daily_loss_pct / 100.0, 2)
        streak = 0
        for t in sorted(closed_today, key=lambda t: _utc(t.exit_time), reverse=True):
            if t.pnl > 0:
                break
            streak += 1
        out[mode] = {"realised_pnl": pnl, "trades_today": len(entered_today), "max_trades": cfg.max_trades_per_day,
                     "open_positions": len(open_), "max_open": cfg.max_open_positions, "loss_limit": limit,
                     "loss_used": round(max(0.0, -pnl), 2), "loss_left": round(max(0.0, limit + min(pnl, 0.0)), 2),
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
        lines.append(tr(lang, f"Paused: {dep.pause_reason or 'by you'}.", f"थांबवलेली: {dep.pause_reason or 'तुम्ही थांबवली'}."))
    if not market_open and (dep.holding or "INTRADAY") != "SWING":
        state = "closed" if state == "ok" else state
        when = next_open.astimezone(IST).strftime("%a %d %b %H:%M") if next_open else "-"
        lines.append(tr(lang, f"Market closed - it trades again from {when} IST.", f"बाजार बंद - पुढचे trading {when} IST पासून."))
    if dep.last_error:
        state = "error"
        lines.append(tr(lang, f"Last error: {dep.last_error[:160]}", f"शेवटची त्रुटी: {dep.last_error[:160]}"))
    if market_open and dep.status == "ACTIVE" and (last_eval is None or now - last_eval > timedelta(minutes=WORKER_STALE_MINUTES)):
        state = "stale" if state == "ok" else state
        lines.append(tr(lang, "Not evaluated in the last few minutes - is the worker container running?",
                        "गेल्या काही मिनिटांत तपासणी झालेली नाही - worker container चालू आहे का?"))
    if profile and regime:
        fit = regime_fit(profile.family, regime)
        fam_en, fam_mr = FAMILY_TEXT[profile.family]
        if fit == 0:
            state = "regime" if state == "ok" else state
            lines.append(tr(lang, f"{dep.symbol} is {regime.lower().replace('_', ' ')}; a {fam_en} strategy stays out in this regime - no trade is the correct result.",
                            f"{dep.symbol} सध्या {REGIME_MR.get(regime, regime)}; या स्थितीत {fam_mr} strategy trade घेत नाही - trade न होणे हाच योग्य निकाल."))
        elif fit >= 2:
            lines.append(tr(lang, f"Today's {regime.lower().replace('_', ' ')} market suits this {fam_en} strategy - it trades when its exact rules line up.",
                            f"आजचा {REGIME_MR.get(regime, regime)} market या {fam_mr} strategy ला जुळतो - नियम पूर्ण झाले की trade होईल."))
    if last_sig and last_sig.astimezone(IST).date() == today:
        lines.append(tr(lang, f"Last signal today at {last_sig.astimezone(IST).strftime('%H:%M')}.", f"आज शेवटचा signal {last_sig.astimezone(IST).strftime('%H:%M')} ला."))
    elif state == "ok" and dep.status == "ACTIVE":
        lines.append(tr(lang, "Watching: no setup yet today. Most good strategies take 0-3 trades a day.",
                        "लक्ष ठेवून आहे: आज अजून setup आलेला नाही. चांगल्या strategies दिवसाला 0-3 trades घेतात."))
    return {"id": dep.id, "strategy_id": dep.strategy_id, "symbol": dep.symbol, "timeframe": dep.timeframe, "mode": dep.mode, "status": dep.status,
            "holding": dep.holding, "family": profile.family if profile else None, "regime": regime, "state": state,
            "last_evaluated_at": last_eval.isoformat() if last_eval else None, "last_signal_at": last_sig.isoformat() if last_sig else None, "why": lines}


async def build(session: AsyncSession, user: User, lang: str = "mr", now: Optional[datetime] = None) -> dict:
    from app.ai import advisor
    from app.risk_engine.routes import get_tenant_risk_config
    lang = "mr" if lang == "mr" else "en"
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    memory = await market_memory.latest(session, user.tenant_id, now=now)
    saved, _ = await advisor.load_profile(session, user)
    experience = (saved or {}).get("experience")
    cfg_saved = await get_tenant_risk_config(user.tenant_id, session)
    cfg = cfg_saved or RiskConfig()
    holidays = await load_holidays(session, "NSE")
    status = session_status(now, holidays, "NSE")
    upcoming = sorted(d for d in holidays if now_ist.date() <= d <= now_ist.date() + timedelta(days=7))
    events = [guardian.event_as_dict(e) for e in await guardian.events_on(session, user.tenant_id, now_ist.date())]

    dt = day_type(memory)
    plan = game_plan(lang, dt, memory, experience, events)
    mood = global_cues.mood(memory.get("globals", []), now) if memory.get("globals") else None
    day = await your_day(session, user, cfg, now)
    deps = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == user.tenant_id, StrategyDeploymentRecord.status.in_(["ACTIVE", "PAUSED"]))
        .order_by(StrategyDeploymentRecord.id)))
    deployments = [_why(lang, d, now, status.is_open, memory, status.next_open) for d in deps]

    creds = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)))
    broker_ok = any(token_is_usable(c, now) for c in creds)
    beats = list(await session.scalars(select(WorkerHeartbeatRecord)))
    worker_seen = max((_utc(b.last_seen_at) for b in beats if b.last_seen_at), default=None)
    worker_ok = worker_seen is not None and now - worker_seen <= timedelta(minutes=WORKER_STALE_MINUTES)
    budget_mode = "LIVE" if day["LIVE"]["active"] else "PAPER"
    checklist = [
        {"id": "broker", "ok": broker_ok, "text": tr(lang, "Broker session logged in today", "आजचा broker login झाला आहे") if broker_ok
         else tr(lang, "No usable broker session - log in under Settings > Brokers (needed for real candles and LIVE)",
                 "Broker session नाही - Settings > Brokers मध्ये login करा (खऱ्या candles आणि LIVE साठी लागतो)")},
        {"id": "risk", "ok": cfg_saved is not None, "text": tr(lang, "Your own risk settings are saved", "तुमचे स्वतःचे risk settings जतन आहेत") if cfg_saved is not None
         else tr(lang, "Risk settings are still the defaults - set capital and daily loss limit (Risk Management)",
                 "Risk settings अजून default आहेत - भांडवल आणि दैनिक तोटा मर्यादा ठरवा (Risk Management)")},
        {"id": "worker", "ok": worker_ok, "text": tr(lang, "Autopilot worker is running", "Autopilot worker चालू आहे") if worker_ok
         else tr(lang, "Worker not seen in the last minutes - deployments will not trade (check the worker container)",
                 "गेल्या काही मिनिटांत worker दिसला नाही - deployments trade करणार नाहीत (worker container तपासा)")},
        {"id": "loss_budget", "ok": day[budget_mode]["loss_left"] > 0,
         "text": tr(lang, f"Loss budget left today ({budget_mode}): {_money(day[budget_mode]['loss_left'])} of {_money(day[budget_mode]['loss_limit'])}",
                    f"आजचे उरलेले तोटा-बजेट ({budget_mode}): {_money(day[budget_mode]['loss_limit'])} पैकी {_money(day[budget_mode]['loss_left'])}")},
        {"id": "vix", "ok": None if dt["vix"] is None else dt["vix"] < 20,
         "text": tr(lang, "India VIX not read yet", "India VIX अजून वाचलेला नाही") if dt["vix"] is None else market_memory.vix_text(lang, dt["vix"])},
        {"id": "events", "ok": not any(e["action"] == "BLOCK" for e in events),
         "text": tr(lang, f"{len(events)} market event(s) today", f"आज {len(events)} market event") if events
         else tr(lang, "No market events today", "आज कोणतेही market event नाही")},
    ]
    session_text = tr(lang, "Market open now" if status.is_open else f"Market closed - opens {status.next_open.astimezone(IST).strftime('%a %d %b %H:%M') if status.next_open else '-'} IST",
                      "बाजार चालू आहे" if status.is_open else f"बाजार बंद - {status.next_open.astimezone(IST).strftime('%a %d %b %H:%M') if status.next_open else '-'} IST ला उघडेल")
    return {
        "as_of": now.isoformat(), "language": lang, "experience": experience,
        "session": {"open": status.is_open, "text": session_text, "next_open": status.next_open.isoformat() if status.next_open else None,
                    "holidays_next_7_days": [d.isoformat() for d in upcoming]},
        "day_type": dt, "plan": plan, "global_mood": mood, "market_updated_at": memory.get("updated_at"),
        "market": {"symbols": memory.get("symbols", [])[:4], "cues": memory.get("cues", []), "globals": memory.get("globals", [])},
        "events": events, "you": day, "deployments": deployments, "checklist": checklist,
    }


def summary_lines(lang: str, brief: dict) -> List[str]:
    """The briefing in a few sentences - for the copilot chat and the AI context."""
    lines = [brief["plan"]["headline"], brief["session"]["text"] + "."]
    lines += brief["plan"]["lines"][:4]
    mode = "LIVE" if brief["you"]["LIVE"]["active"] else "PAPER"
    y = brief["you"][mode]
    lines.append(tr(lang, f"You today ({mode}): P&L {_money(y['realised_pnl'])}, {y['trades_today']}/{y['max_trades']} trades, loss budget left {_money(y['loss_left'])}.",
                    f"तुमचा आजचा दिवस ({mode}): P&L {_money(y['realised_pnl'])}, {y['trades_today']}/{y['max_trades']} trades, उरलेले तोटा-बजेट {_money(y['loss_left'])}."))
    missing = [c["text"] for c in brief["checklist"] if c["ok"] is False]
    if missing:
        lines.append(tr(lang, "Fix first: ", "आधी हे करा: ") + missing[0])
    return lines


def deployment_lines(lang: str, brief: dict) -> List[str]:
    if not brief["deployments"]:
        return [tr(lang, "No active deployments. Build a strategy with the interview and deploy it in PAPER first.",
                   "एकही deployment चालू नाही. Strategy मुलाखतीतून strategy बनवा आणि आधी PAPER मध्ये deploy करा.")]
    return [f"#{d['id']} {d['strategy_id']} · {d['symbol']} ({d['mode']}): " + " ".join(d["why"]) for d in brief["deployments"]]


__all__ = ["build", "day_type", "game_plan", "summary_lines", "deployment_lines", "DAY_TYPES"]
