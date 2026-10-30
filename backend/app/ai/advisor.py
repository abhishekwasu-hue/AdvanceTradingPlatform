"""Phase AQ: the shopkeeper's round - options, "not this one, because...", and memory.

A good shopkeeper does not put one shirt on the counter. He asks who it is for and the budget
(Phase AP's interview), lays out a few choices, listens to "too loud", "too expensive", "something
simpler" and brings the next ones closer to what the customer wants - and remembers him the next
time he walks in. This module does that with trading plans:

* `build_options()` - three options from one market read and one ranking: three templates (**safe** = the calmer
  trend-following one, **balanced** = the best fit, **active** = the one with more signals), every one with the
  trader's OWN risk answers and no exit he did not set (DEF-0 / SB-UX SU4: no option tightens, loosens or adds a limit,
  a trades-a-day cap, a minimum reward:risk, a break-even move or a time exit). Each is a full plan
  (`interview.compose_plan`).
* `apply_feedback()` - the reasons a trader gives for turning an option down become
  `Preferences` (leanings that move the template ranking, strategies not wanted, style changes); the next round of
  options is built from them. They never change a number of his risk settings.
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
    "want_swing": ("I want swing - hold for days", "मला swing हवे - काही दिवस धरायचे"),
    "no_time": ("I don't have time to watch", "पाहायला वेळ नाही"),
    "dislike_strategy": ("Not this strategy", "ही strategy नको"),
}


OWN_RISK_EN = "Your risk per trade and daily loss limit stay your own answers - change them in the risk questions or in My limits."
OWN_RISK_MR = "एका trade चा risk आणि दिवसाची तोटा मर्यादा तुमचीच उत्तरे राहतात - ती risk च्या प्रश्नांत किंवा My limits मध्ये बदला."


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
            changes.append(tr(lang, OWN_RISK_EN, OWN_RISK_MR))
            if answers.vehicle == "option_sell":
                answers.vehicle = "option_buy"
                changes.append(tr(lang, "Option selling replaced by option buying (loss capped at the premium).", "Option selling ऐवजी option buying (तोटा premium इतकाच)."))
        elif code == "more_risk_ok":
            p.risk_bias = min(p.risk_bias + 1, 2)
            changes.append(tr(lang, OWN_RISK_EN, OWN_RISK_MR))
        elif code == "too_many_trades":
            p.trades_bias = max(p.trades_bias - 1, -3)
            changes.append(tr(lang, "Calmer templates with fewer signals preferred. A trades-a-day limit is yours to set in My limits.",
                              "कमी signals देणाऱ्या शांत templates ला प्राधान्य. दिवसाच्या trades ची मर्यादा तुम्ही My limits मध्ये ठेवू शकता."))
        elif code == "too_few_trades":
            p.trades_bias = min(p.trades_bias + 1, 3)
            changes.append(tr(lang, "Templates with more signals preferred.", "जास्त signals देणाऱ्या templates ला प्राधान्य."))
        elif code == "low_reward":
            p.reward_bias = min(p.reward_bias + 1, 3)
            changes.append(tr(lang, "Trend-following and breakout templates preferred. Your target and minimum reward:risk stay your own settings.",
                              "Trend सोबतच्या आणि breakout templates ला प्राधान्य. तुमचे target आणि किमान reward:risk तुमच्याच settings राहतात."))
        elif code == "not_understood":
            p.simplicity = min(p.simplicity + 1, 3)
            changes.append(tr(lang, "Simpler strategies first (one clear trend rule), with plainer explanations.", "आधी सोप्या strategies (एक स्पष्ट trend नियम), सोप्या शब्दांत."))
        elif code == "want_swing":
            answers.style = "swing"
            if answers.vehicle == "option_sell":
                answers.vehicle = "option_buy"
            changes.append(tr(lang, "Switched to swing trading on daily candles: fewer, bigger moves held for days (delivery / monthly options).",
                              "Daily candles वरच्या swing trading कडे वळवले: कमी पण मोठ्या moves, काही दिवस धरून (delivery / monthly options)."))
        elif code == "no_time":
            answers.time = "auto"
            p.trades_bias = max(p.trades_bias - 1, -3)
            changes.append(tr(lang, "Fully automatic, calmer templates preferred. A time exit is yours to set; none is added.",
                              "पूर्ण automatic, शांत templates ला प्राधान्य. वेळेवर exit तुम्ही ठेवू शकता; platform स्वतःहून ठेवत नाही."))
        elif code == "dislike_strategy" and strategy_id:
            if strategy_id not in p.rejected:
                p.rejected = (p.rejected + [strategy_id])[-20:]
            changes.append(tr(lang, f"{strategy_id} will not be offered again.", f"{strategy_id} पुन्हा सुचवली जाणार नाही."))
    p.feedback_log = (p.feedback_log + [{"at": datetime.now(timezone.utc).isoformat(), "option": option_id, "strategy_id": strategy_id,
                                         "codes": list(dict.fromkeys(codes))}])[-50:]
    return answers, p, changes


# --- what this trader wants --------------------------------------------------------------------------

def desired(a: InterviewAnswers, p: Preferences) -> dict:
    """What the trader set himself (DEF-0 / SB-UX SU4): his risk answer; no trades-a-day or reward:risk he did not state.
    Feedback leanings move the template ranking only, never these numbers."""
    return {"risk_pct": iv.RISK_PCT[a.risk] if a.risk is not None else None, "trades": None, "rr": None}


OPTION_TEXT = {      # SB-UX SU4: the options differ only in the template; the risk settings are the trader's own on every one
    "safe": (("Trend template", "A trend-following template, with your own risk settings"),
             ("Trend template", "Trend सोबत चालणारे template, तुमच्याच risk settings सोबत")),
    "balanced": (("Regime template", "The template whose rules suit these candles' regime, with your own risk settings"),
                 ("Market स्थितीचे template", "या candles च्या market स्थितीला जुळणाऱ्या नियमांचे template, तुमच्याच risk settings सोबत")),
    "active": (("More-signals template", "A template whose rules give more signals, with your own risk settings"),
               ("जास्त signals चे template", "जास्त signals देणाऱ्या नियमांचे template, तुमच्याच risk settings सोबत")),
}


def _tilted_config(a: InterviewAnswers, p: Preferences, tilt: str, ceilings: Optional[Dict[str, float]]) -> Tuple[RiskConfig, List[str], dict]:
    """The option's risk settings and exits: the trader's own for every option (DEF-0 / SB-UX SU4) - no tilt changes a
    limit, adds a trades-a-day cap or a minimum reward:risk, a break-even move or a time exit."""
    cfg, notes = iv.risk_plan(a, ceilings)
    return cfg, list(notes), iv.default_exit_rules(a)


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
    """False when a fact (a cash stock has no options, no overnight option writing) changed what the trader asked to trade."""
    kind, position = contract.get("instrument_kind"), contract.get("option_position")
    if a.vehicle == "option_sell":
        return kind == "OPTION" and position != "BUY"
    if a.vehicle == "option_buy":
        return kind == "OPTION" and position == "BUY"
    return kind in ("UNDERLYING", "FUTURE")


def _style_fit(a: InterviewAnswers, p: Preferences, family: str) -> float:
    fit = 1.0
    if family != "trend" and p.simplicity > 0:
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

    def closeness(have: float | None, target: float | None) -> float:
        if have is None or target is None:                 # DEF-0: not set on both sides is his own setting kept
            return 1.0 if have is None and target is None else 0.0
        return max(0.0, 1.0 - abs(have - target) / max(target, 1e-9))

    parts = {
        "risk": (0.25, closeness(cfg.risk_per_trade_pct, want["risk_pct"])),
        "trades": (0.20, closeness(cfg.max_trades_per_day, want["trades"])),
        "reward": (0.15, closeness(cfg.min_risk_reward or None, want["rr"])),
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
                  data_source: str = "sample", memory: Optional[dict] = None) -> dict:
    lang = a.language
    market = iv.analyse_market(df, base_tf, lang)
    # Phase AR: what the market memory adds - the fear gauge, the last session, the bias trail.
    from app.ai import market_memory
    background = market_memory.describe(lang, memory, a.symbol) if memory else []
    vix = next((c for c in (memory or {}).get("cues", []) if c["symbol"] == "INDIA VIX"), None)
    high_fear = bool(vix and vix.get("last_price") and float(vix["last_price"]) >= 20)
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
    options = []
    for oid in ("safe", "balanced", "active"):
        cfg, notes, exit_rules = _tilted_config(a, p, oid, ceilings)
        pick = picks[oid]
        plan = iv.compose_plan(a, market, ranked, pick, cfg, notes, data_source, exit_rules=exit_rules)
        if background:
            plan["sections"].insert(1, {"id": "background", "title": tr(lang, "Market background", "बाजाराची पार्श्वभूमी"), "title_mr": "बाजाराची पार्श्वभूमी", "lines": background})
        if high_fear:
            plan["warnings"].append(tr(lang, "India VIX is 20 or higher - fear is high and gaps are more frequent at such readings.",
                                       "India VIX 20 किंवा जास्त आहे - भीती जास्त; अशा वेळी gap जास्त वेळा येतात."))
        (en_label, en_sub), (mr_label, mr_sub) = OPTION_TEXT[oid]
        # P0.8-D: three templates the trader chooses between. No score of any kind - no "match %", no market-fit %, no
        # "closest to you", no best option. The one data fact is whether the template's own regime filter is open on
        # today's candles (the same yes/no the daily briefing shows). SB-UX SU4: no risk headline - every option carries
        # the trader's own settings (the plan's risk section states them), so no option shows a number of its own.
        filter_open = None if pick is None else bool(pick["regime_fit"] >= 2)
        plan["option"] = {"id": oid, "label": tr(lang, en_label, mr_label), "summary": tr(lang, en_sub, mr_sub), "label_mr": mr_label, "summary_mr": mr_sub,
                          "regime_filter_open": filter_open, "headline": None}
        options.append(plan)
    # The top level stays the balanced option (the trader's own answers) for older clients; the UI shows all three.
    result = dict(next(o for o in options if o["option"]["id"] == "balanced"))
    result.update({"options": options, "best_option": None, "preferences": p.model_dump(), "feedback_options": feedback_options(), "desired": desired(a, p)})
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

    from app.db.models import LlmCallRecord, TraderProfileRecord
    from app.retention.service import scrub_llm_text
    row = await session.scalar(select(TraderProfileRecord).where(TraderProfileRecord.user_id == user.id))
    # H-C1 e: "forget me" also clears the text of this trader's AI conversations (the audit rows and hashes stay).
    scrubbed = await scrub_llm_text(session, LlmCallRecord.user_id == user.id, "[erased]")
    if row is None:
        await session.commit()
        return scrubbed > 0
    await session.delete(row)
    await session.commit()
    return True
