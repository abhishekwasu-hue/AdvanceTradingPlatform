"""Phase AQ: the shopkeeper's round - options, "not this one, because...", and memory.

A good shopkeeper does not put one shirt on the counter. He asks who it is for and the budget
(Phase AP's interview), lays out a few choices, listens to "too loud", "too expensive", "something
simpler" and brings the next ones closer to what the customer wants - and remembers him the next
time he walks in. This module does that with trading plans:

* `build_options()` - three options from one market read and one ranking: **safe** (less risk,
  fewer trades, more reward per trade), **balanced** (the trader's own answers, adjusted by what
  they told us) and **active** (more opportunities, still inside the beginner guard rails). Each
  is a full plan (`interview.compose_plan`) with a **match %** - how close it is to what this
  trader asked for, the market and the evidence - and the reasons behind the number.
* `apply_feedback()` - the reasons a trader gives for turning an option down become
  `Preferences` (risk / trades / reward / simplicity leanings, strategies not wanted, style
  changes); the next round of options is built from them, so the match climbs.
* `Preferences` and the answers live in `trader_profiles` (one row per user), so the next
  interview can start from them.

The match is never 100%: nobody can promise the market will fit a plan. Nothing here applies or
deploys anything - the trader chooses, then presses the buttons.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field

from app.ai import interview as iv
from app.ai.interview import InterviewAnswers, tr
from app.core.models import RiskConfig

MAX_MATCH = 97
OptionId = Literal["safe", "balanced", "active"]
FEEDBACK_CODES = ("too_risky", "too_many_trades", "too_few_trades", "low_reward", "not_understood", "want_swing",
                  "no_time", "dislike_strategy", "more_risk_ok")


class Preferences(BaseModel):
    """What the trader's feedback has taught the Copilot. Biases are steps: -2..+2."""
    risk_bias: int = Field(default=0, ge=-3, le=2)
    trades_bias: int = Field(default=0, ge=-3, le=3)
    reward_bias: int = Field(default=0, ge=0, le=3)
    simplicity: int = Field(default=0, ge=0, le=3)
    rejected: List[str] = Field(default_factory=list, max_length=20)
    chosen: List[dict] = Field(default_factory=list, max_length=20)
    feedback_log: List[dict] = Field(default_factory=list, max_length=50)
    match_history: List[int] = Field(default_factory=list, max_length=50)


FEEDBACK_LABELS: Dict[str, Tuple[str, str]] = {
    "too_risky": ("Too risky for me", "मला risk जास्त वाटतो"),
    "more_risk_ok": ("I can take a little more risk", "थोडा जास्त risk चालेल"),
    "too_many_trades": ("Too many trades", "खूप trades होतील"),
    "too_few_trades": ("Too few opportunities", "संधी खूप कमी"),
    "low_reward": ("Reward is too small", "नफा कमी वाटतो"),
    "not_understood": ("I don't understand this strategy", "ही strategy समजली नाही"),
    "want_swing": ("I want calmer, bigger moves", "मला शांत, मोठ्या moves हव्या"),
    "no_time": ("I don't have time to watch", "पाहायला वेळ नाही"),
    "dislike_strategy": ("Not this strategy", "ही strategy नको"),
}


def feedback_options() -> List[dict]:
    return [{"code": c, "en": en, "mr": mr} for c, (en, mr) in FEEDBACK_LABELS.items()]


def apply_feedback(a: InterviewAnswers, prefs: Preferences, codes: List[str], strategy_id: Optional[str] = None,
                   option_id: Optional[str] = None) -> Tuple[InterviewAnswers, Preferences, List[str]]:
    """(answers, preferences, what changed - in the trader's language)."""
    lang = a.language
    answers = a.model_copy()
    p = prefs.model_copy(deep=True)
    changes: List[str] = []
    for code in dict.fromkeys(codes):   # de-duplicated, in order
        if code == "too_risky":
            p.risk_bias = max(p.risk_bias - 1, -3)
            changes.append(tr(lang, "Smaller risk per trade and a tighter daily limit.", "एका trade चा risk आणि दिवसाची मर्यादा कमी केली."))
            if answers.vehicle == "option_sell":
                answers.vehicle = "option_buy"
                changes.append(tr(lang, "Option selling replaced by option buying (loss capped at the premium).", "Option selling ऐवजी option buying (तोटा premium इतकाच)."))
        elif code == "more_risk_ok":
            p.risk_bias = min(p.risk_bias + 1, 2)
            changes.append(tr(lang, "A little more risk per trade - still inside your experience cap.", "एका trade चा risk थोडा वाढवला - तरीही अनुभवाच्या मर्यादेत."))
        elif code == "too_many_trades":
            p.trades_bias = max(p.trades_bias - 1, -3)
            changes.append(tr(lang, "Fewer trades a day; calmer strategies preferred.", "दिवसाला कमी trades; शांत strategies ला प्राधान्य."))
        elif code == "too_few_trades":
            p.trades_bias = min(p.trades_bias + 1, 3)
            changes.append(tr(lang, "More trades a day allowed; strategies with more signals preferred.", "दिवसाला जास्त trades; जास्त signals देणाऱ्या strategies ला प्राधान्य."))
        elif code == "low_reward":
            p.reward_bias = min(p.reward_bias + 1, 3)
            changes.append(tr(lang, "Bigger targets: higher minimum reward:risk, trend-following preferred.", "मोठे target: किमान reward:risk वाढवला, trend सोबतच्या strategies ला प्राधान्य."))
        elif code == "not_understood":
            p.simplicity = min(p.simplicity + 1, 3)
            changes.append(tr(lang, "Simpler strategies first (one clear trend rule), with plainer explanations.", "आधी सोप्या strategies (एक स्पष्ट trend नियम), सोप्या शब्दांत."))
        elif code == "want_swing":
            if answers.style != "positional":
                answers.style = "positional"
            p.trades_bias = max(p.trades_bias - 1, -3)
            changes.append(tr(lang, "Switched to calmer trend-following on the bigger timeframe. True overnight swing trading comes in a later phase.",
                              "मोठ्या timeframe वरच्या शांत trend-following कडे वळवले. रात्रभर ठेवायचे खरे swing trading पुढच्या टप्प्यात येईल."))
        elif code == "no_time":
            answers.time = "auto"
            p.trades_bias = max(p.trades_bias - 1, -3)
            changes.append(tr(lang, "Fully automatic, fewer trades, everything closed by 15:10.", "पूर्ण automatic, कमी trades, 15:10 पर्यंत सगळे बंद."))
        elif code == "dislike_strategy" and strategy_id:
            if strategy_id not in p.rejected:
                p.rejected = (p.rejected + [strategy_id])[-20:]
            changes.append(tr(lang, f"{strategy_id} will not be offered again.", f"{strategy_id} पुन्हा सुचवली जाणार नाही."))
    p.feedback_log = (p.feedback_log + [{"at": datetime.now(timezone.utc).isoformat(), "option": option_id, "strategy_id": strategy_id,
                                         "codes": list(dict.fromkeys(codes))}])[-50:]
    return answers, p, changes


# --- what this trader wants --------------------------------------------------------------------------

def desired(a: InterviewAnswers, p: Preferences) -> dict:
    base_risk = iv.RISK_PCT[a.risk]
    risk = min(max(base_risk * (1 + 0.25 * p.risk_bias), 0.25), iv.RISK_CAP_BY_EXPERIENCE[a.experience])
    trades = iv.TRADES_PER_DAY[a.style] - (1 if a.time == "few_checks" else 0) + p.trades_bias
    if a.experience == "new":
        trades = min(trades, 3)
    rr = (2.0 if a.vehicle == "option_buy" else 1.5) + 0.5 * p.reward_bias
    return {"risk_pct": round(risk, 2), "trades": max(int(trades), 1), "rr": round(rr, 2)}


TILTS = {
    # risk multiplier on the desired risk, trades/day delta, extra R:R, break-even R
    "safe": {"risk": 0.6, "trades": -1, "rr": 0.5, "be": 1.0},
    "balanced": {"risk": 1.0, "trades": 0, "rr": 0.0, "be": None},
    "active": {"risk": 1.0, "trades": 2, "rr": -0.25, "be": 1.5},
}
OPTION_TEXT = {
    "safe": (("Safe", "Less risk, fewer trades, bigger reward per trade"), ("सावध", "कमी risk, कमी trades, प्रत्येक trade मध्ये मोठे target")),
    "balanced": (("Balanced", "Built from your own answers"), ("संतुलित", "तुमच्या उत्तरांवरून बनवलेला")),
    "active": (("Active", "More opportunities, same safety rules"), ("सक्रिय", "जास्त संधी, सुरक्षेचे नियम तेच")),
}


def _tilted_config(a: InterviewAnswers, p: Preferences, tilt: str, ceilings: Optional[Dict[str, float]]) -> Tuple[RiskConfig, List[str], dict]:
    cfg, base_notes = iv.risk_plan(a, ceilings)
    base_risk = cfg.risk_per_trade_pct
    want = desired(a, p)
    t = TILTS[tilt]
    cap = min(iv.RISK_CAP_BY_EXPERIENCE[a.experience], float((ceilings or {}).get("risk_per_trade_pct", 2.0)))
    risk = round(min(max(want["risk_pct"] * t["risk"], 0.25), cap), 2)
    trades = max(1, want["trades"] + t["trades"])
    if a.experience == "new":
        trades = min(trades, 4)
    rr = round(max(want["rr"] + t["rr"], 1.5), 2)
    daily = round(min(max(cfg.max_daily_loss_pct, risk * 2), risk * 3, float((ceilings or {}).get("max_daily_loss_pct", 5.0))), 2)
    cfg = cfg.model_copy(update={
        "risk_per_trade_pct": risk, "max_trades_per_day": trades, "min_risk_reward": rr, "max_daily_loss_pct": daily,
        "max_portfolio_risk_pct": min(round(risk * 3, 2), float((ceilings or {}).get("max_portfolio_risk_pct", 10.0))),
    })
    # risk_plan's notes describe its own numbers: kept only while they are still this option's.
    lang = a.language
    notes = list(base_notes) if risk == base_risk else []
    if tilt == "safe":
        notes.append(tr(lang, f"Safe option: {risk:g}% risk per trade, at most {trades} trade(s) a day, reward at least 1:{rr:g} - fewer, better trades.",
                        f"सावध पर्याय: एका trade चा risk {risk:g}%, दिवसाला जास्तीत जास्त {trades} trade, नफा किमान 1:{rr:g} - कमी पण चांगले trades."))
    elif tilt == "active":
        notes.append(tr(lang, f"Active option: up to {trades} trades a day at {risk:g}% risk each, reward at least 1:{rr:g}; the daily loss limit still stops the day.",
                        f"सक्रिय पर्याय: दिवसाला {trades} पर्यंत trades, प्रत्येकी {risk:g}% risk, नफा किमान 1:{rr:g}; दिवसाची तोटा मर्यादा तरीही दिवस थांबवते."))
    exit_rules = iv.default_exit_rules(a)
    if t["be"] is not None:
        exit_rules["break_even_at_r"] = t["be"]
    if a.time == "auto" and a.style != "positional":
        exit_rules["time_exit_at"] = "15:10"
    return cfg, notes, exit_rules


def _assign(ranked: List[dict], p: Preferences) -> Dict[str, Optional[dict]]:
    """Balanced gets the best fit; safe the calmest of the next ones (trend, best market fit);
    active the remaining one (momentum / breakout / reversion give more signals)."""
    top = ranked[:3]
    if not top:
        return {"safe": None, "balanced": None, "active": None}
    balanced, rest = top[0], top[1:]
    if not rest:
        return {"safe": balanced, "balanced": balanced, "active": balanced}
    safe = max(rest, key=lambda r: (r["family"] == "trend", r["regime_fit"], r["score"]))
    others = [r for r in rest if r is not safe]
    active = max(others, key=lambda r: (r["family"] != "trend", r["evidence"]["total_trades"], r["score"])) if others else balanced
    return {"safe": safe, "balanced": balanced, "active": active}


def _contract_honoured(a: InterviewAnswers, contract: dict) -> bool:
    """False when the beginner guard rails had to change what the trader asked to trade."""
    kind, position = contract.get("instrument_kind"), contract.get("option_position")
    if a.vehicle == "option_sell":
        return kind == "OPTION" and position != "BUY"
    if a.vehicle == "option_buy":
        return kind == "OPTION" and position == "BUY"
    return kind in ("UNDERLYING", "FUTURE")


def _style_fit(a: InterviewAnswers, p: Preferences, family: str) -> float:
    fit = 1.0
    if family != "trend" and (p.simplicity > 0 or a.experience == "new"):
        fit -= 0.25 + 0.1 * p.simplicity
    if a.goal == "big_trends" and family in ("reversion", "momentum"):
        fit -= 0.3
    if p.trades_bias < 0 and family in ("momentum", "reversion"):
        fit -= 0.15 * -p.trades_bias
    if p.trades_bias > 0 and family == "trend":
        fit -= 0.1 * p.trades_bias
    if p.reward_bias > 0 and family == "reversion":
        fit -= 0.2 * p.reward_bias
    return max(fit, 0.0)


def match_score(a: InterviewAnswers, p: Preferences, market: dict, pick: Optional[dict], cfg: RiskConfig,
                contract_honoured: bool = True) -> Tuple[int, int, List[str]]:
    """(match with what this trader asked for 0-97, fit with today's market 0-100, reasons).

    The two are kept apart on purpose: feedback moves the first; the second is the market's
    honest say - a calm strategy the trader wants may simply not suit a choppy day."""
    lang = a.language
    if pick is None:
        return 0, 0, [tr(lang, "no strategy could be tested", "एकही strategy तपासता आली नाही")]
    want = desired(a, p)

    def closeness(have: float, target: float) -> float:
        return max(0.0, 1.0 - abs(have - target) / max(target, 1e-9))

    parts = {
        "risk": (0.25, closeness(cfg.risk_per_trade_pct, want["risk_pct"])),
        "trades": (0.20, closeness(cfg.max_trades_per_day, want["trades"])),
        "reward": (0.15, closeness(cfg.min_risk_reward, want["rr"])),
        "style": (0.25, _style_fit(a, p, pick["family"])),
        "contract": (0.15, 1.0 if contract_honoured else 0.6),
    }
    score = sum(w * v for w, v in parts.values()) * 100
    if pick["strategy_id"] in p.rejected:
        score *= 0.5
    if any(c.get("strategy_id") == pick["strategy_id"] for c in p.chosen):
        score += 3
    match = int(round(min(score, MAX_MATCH)))

    ev = pick["evidence"]
    if ev.get("tested") and ev["total_trades"] >= 5:
        pf = ev["profit_factor"] if ev["profit_factor"] is not None else (3.0 if ev["net_pnl"] > 0 else 0.0)
        evidence = min(max((float(pf) - 0.5) / 1.5, 0.0), 1.0)
    else:
        evidence = 0.4
    market_fit = int(round((0.6 * pick["regime_fit"] / 3.0 + 0.4 * evidence) * 100))

    names = {"risk": ("risk", "risk"), "trades": ("trades a day", "दिवसाचे trades"), "reward": ("reward per trade", "प्रत्येक trade चा नफा"),
             "style": ("style", "पद्धत"), "contract": ("what is traded", "काय trade होते")}
    reasons = [f"{tr(lang, *names[k])} {round(v * 100)}%" for k, (_, v) in parts.items()]
    return match, market_fit, reasons


def build_options(a: InterviewAnswers, p: Preferences, df: pd.DataFrame, base_tf: str, *, ceilings: Optional[Dict[str, float]] = None,
                  data_source: str = "sample") -> dict:
    lang = a.language
    market = iv.analyse_market(df, base_tf, lang)
    # What the trader told us moves the ranking towards the families they prefer.
    bonus: Dict[str, float] = {}

    def add(fam: str, v: float) -> None:
        bonus[fam] = bonus.get(fam, 0.0) + v

    if p.simplicity:
        add("trend", 1.0 * p.simplicity)
    if p.reward_bias:
        add("trend", 0.5 * p.reward_bias)
        add("breakout", 0.5 * p.reward_bias)
    if p.trades_bias < 0:
        add("trend", 0.5 * -p.trades_bias)
    if p.trades_bias > 0:
        add("momentum", 0.5 * p.trades_bias)
        add("reversion", 0.5 * p.trades_bias)
    base_cfg, _ = iv.risk_plan(a, ceilings)
    ranked = iv._rank(a, market, df, base_tf, base_cfg, exclude=tuple(p.rejected), family_bonus=bonus)
    if not ranked and p.rejected:          # everything that fits was turned down: offer them again, with a note
        ranked = iv._rank(a, market, df, base_tf, base_cfg, family_bonus=bonus)
    picks = _assign(ranked, p)
    honoured = _contract_honoured(a, iv.contract_plan(a, market["bias"])[0])
    options = []
    for oid in ("safe", "balanced", "active"):
        cfg, notes, exit_rules = _tilted_config(a, p, oid, ceilings)
        pick = picks[oid]
        plan = iv.compose_plan(a, market, ranked, pick, cfg, notes, data_source, exit_rules=exit_rules)
        match, market_fit, reasons = match_score(a, p, market, pick, cfg, honoured)
        (en_label, en_sub), (mr_label, mr_sub) = OPTION_TEXT[oid]
        plan["option"] = {"id": oid, "label": tr(lang, en_label, mr_label), "summary": tr(lang, en_sub, mr_sub), "match": match,
                          "market_fit": market_fit, "match_reasons": reasons,
                          "headline": None if pick is None else {"strategy": pick["name"], "risk_pct": cfg.risk_per_trade_pct,
                                                                 "trades_per_day": cfg.max_trades_per_day, "min_rr": cfg.min_risk_reward}}
        options.append(plan)
    best = max(options, key=lambda o: 0.7 * o["option"]["match"] + 0.3 * o["option"]["market_fit"])
    history = (p.match_history + [best["option"]["match"]])[-50:]
    # The top level stays the balanced option (the trader's own answers) for older clients; the
    # UI shows all three and marks `best_option`.
    result = dict(next(o for o in options if o["option"]["id"] == "balanced"))
    result.update({"options": options, "best_option": best["option"]["id"], "preferences": p.model_copy(update={"match_history": history}).model_dump(),
                   "feedback_options": feedback_options(), "desired": desired(a, p)})
    return result


# --- the profile ---------------------------------------------------------------------------------------

async def load_profile(session, user) -> Tuple[Optional[dict], Preferences]:
    """(saved answers or None, preferences) for this user."""
    import json
    from sqlalchemy import select
    from app.db.models import TraderProfileRecord
    row = await session.scalar(select(TraderProfileRecord).where(TraderProfileRecord.user_id == user.id))
    if row is None:
        return None, Preferences()
    try:
        prefs = Preferences.model_validate(json.loads(row.preferences_json or "{}"))
    except ValueError:
        prefs = Preferences()
    answers = json.loads(row.answers_json or "{}") or None
    return answers, prefs


async def save_profile(session, user, answers: InterviewAnswers, prefs: Preferences) -> None:
    import json
    from sqlalchemy import select
    from app.db.models import TraderProfileRecord
    row = await session.scalar(select(TraderProfileRecord).where(TraderProfileRecord.user_id == user.id))
    if row is None:
        row = TraderProfileRecord(tenant_id=user.tenant_id, user_id=user.id)
        session.add(row)
    row.answers_json = answers.model_dump_json()
    row.preferences_json = prefs.model_dump_json()
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def delete_profile(session, user) -> bool:
    from sqlalchemy import select
    from app.db.models import TraderProfileRecord
    row = await session.scalar(select(TraderProfileRecord).where(TraderProfileRecord.user_id == user.id))
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
