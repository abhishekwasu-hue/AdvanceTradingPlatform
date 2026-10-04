"""Phase AP: the strategy interview - the Copilot asks before it answers.

A beginner who types "give me a trading strategy" (or "ट्रेडिंग स्ट्रॅटेजी सांगा") should not get a
list of everything the platform has. A professional would first ask who they are talking to: how
much capital, how much loss they can carry, how they want to trade, what they want to trade and
how much time they have. Then the professional reads the market - today's trend, the higher-
timeframe trend, market structure, support and resistance, volatility - and only then picks a
method, sizes the risk, allocates capital and fixes the risk:reward.

This module is that professional, deterministically:

* `QUESTIONS` - the interview, every question and option in English and Marathi, with a line on
  *why* it is asked (a beginner learns what matters while answering).
* `start()` - reads a free-text request: does it already describe rules (then the ordinary AI
  generator can take it) or is it vague (then interview)? Answers the text already gives are
  pre-filled (language, symbol, style, vehicle, capital, experience) so they are not asked again.
* `analyse_market()` - the market read on the candles the page sends (sample or broker).
* `build_plan()` - strategy choice (fit to style, regime and goal, plus evidence from walking
  each candidate over the same candles), the risk plan as a `RiskConfig`, capital allocation,
  R:R, what contract to trade, a PAPER deployment ready to create, warnings, and a prompt the
  external AI can use for a custom rule set. Every text is in the language the trader chose.

Nothing here places, changes or deploys anything: the page shows the plan and the trader acts on
it (apply risk settings, deploy in PAPER). LIVE stays behind the Go-Live checklist.
"""
from __future__ import annotations

import copy
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Dict, List, Literal, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field

from app.ai.regime import Regime, classify_regime
from app.core.models import RiskConfig
from app.core.resampling import resample_ohlc
from app.instruments import master as instrument_master
from app.price_action.market_structure import analyze_market_structure
from app.strategy_engine.chart_routes import evaluate_on_candles, timeframe_minutes
from app.strategy_engine.combo_strategies import session_vwap_or_mean
from app.strategy_engine.registry import registry
from app.support_resistance.engine import SupportResistanceEngine

Lang = Literal["en", "mr"]
DISCLAIMER = {
    "en": "Educational decision support, not investment advice. Past results on these candles do not promise future profit. Paper-trade first.",
    "mr": "हे शिक्षण आणि निर्णयासाठी मदत आहे, गुंतवणूक सल्ला नाही. या candles वरचे जुने निकाल पुढच्या नफ्याची हमी देत नाहीत. आधी PAPER मध्ये चालवा.",
}


def tr(lang: str, en: str, mr: str) -> str:
    return mr if lang == "mr" else en


# --- the interview ---------------------------------------------------------------------------------

@dataclass
class Option:
    value: str
    en: str
    mr: str


@dataclass
class Question:
    id: str
    kind: str                      # choice | number | symbol
    en: str
    mr: str
    why_en: str
    why_mr: str
    options: List[Option] = field(default_factory=list)
    default: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


QUESTIONS: List[Question] = [
    Question("language", "choice", "Which language should I talk in?", "मी कोणत्या भाषेत बोलू?",
             "Everything after this - questions and your plan - comes in this language.",
             "यापुढचे सगळे प्रश्न आणि तुमचा plan याच भाषेत येईल.",
             [Option("mr", "मराठी", "मराठी"), Option("en", "English", "English")], "mr"),
    Question("experience", "choice", "How long have you been trading?", "तुम्ही किती दिवसांपासून trading करत आहात?",
             "A beginner gets smaller risk, one position at a time and PAPER first - the way every professional started.",
             "नवशिक्याला कमी risk, एका वेळी एकच position आणि आधी PAPER - प्रत्येक professional ने अशीच सुरुवात केली.",
             [Option("new", "New - under 6 months", "नवीन - 6 महिन्यांपेक्षा कमी"),
              Option("learning", "Learning - 6 months to 2 years", "शिकत आहे - 6 महिने ते 2 वर्षे"),
              Option("experienced", "Experienced - over 2 years", "अनुभवी - 2 वर्षांपेक्षा जास्त")], "new"),
    Question("capital", "number", "How much capital do you have for trading (₹)?", "Trading साठी तुमच्याकडे किती भांडवल आहे (₹)?",
             "Position size is calculated from capital and risk, never guessed. Only money you can afford to lose belongs here.",
             "Quantity भांडवल आणि risk वरून मोजली जाते, अंदाजाने नाही. जे पैसे गमावले तरी चालतील तेवढेच इथे लिहा.",
             [], "100000"),
    Question("risk", "choice", "If a trade goes wrong, how much of your capital can you lose on it?", "एखादा trade चुकला तर त्यात भांडवलाच्या किती टक्के तोटा सहन करू शकता?",
             "Professionals risk 0.5-1% per trade, so ten losses in a row still leave 90%+ of the account.",
             "Professional trader एका trade मध्ये 0.5-1% risk घेतात - सलग 10 trades चुकले तरी 90% पेक्षा जास्त भांडवल शिल्लक राहते.",
             [Option("conservative", "0.5% - careful", "0.5% - सावध"),
              Option("moderate", "1% - balanced", "1% - मध्यम"),
              Option("aggressive", "1.5% - aggressive", "1.5% - आक्रमक")], "conservative"),
    Question("daily_loss", "choice", "What is the most you can lose in one day before stopping?", "एका दिवसात जास्तीत जास्त किती तोटा झाला की थांबायचे?",
             "When the day's loss reaches this, the platform stops new trades for the day - no revenge trading.",
             "दिवसाचा तोटा इथपर्यंत आला की platform त्या दिवशी नवीन trades बंद करतो - revenge trading होत नाही.",
             [Option("1", "1% of capital", "भांडवलाच्या 1%"), Option("2", "2% of capital", "भांडवलाच्या 2%"), Option("3", "3% of capital", "भांडवलाच्या 3%")], "2"),
    Question("style", "choice", "How do you want to trade?", "तुम्हाला कसे trading करायचे आहे?",
             "This decides the candle timeframe and how many trades a day are reasonable.",
             "यावरून candle चा timeframe आणि दिवसाला किती trades योग्य ते ठरते.",
             [Option("scalping", "Scalping - minutes, many small trades", "Scalping - काही मिनिटे, अनेक छोटे trades"),
              Option("intraday", "Intraday - hours, closed the same day", "Intraday - काही तास, त्याच दिवशी बंद"),
              Option("positional", "Calmer intraday - follow the bigger trend, few trades", "शांत intraday - मोठ्या trend सोबत, कमी trades"),
              Option("swing", "Swing - days to weeks, held overnight", "Swing - काही दिवस ते आठवडे (position रात्रभर)")], "intraday"),
    Question("instrument", "choice", "What do you want to trade?", "तुम्हाला काय trade करायचे आहे?",
             "Indices move smoothly and are liquid; a stock can gap on news.",
             "Index सहज हलतो आणि liquidity जास्त असते; stock बातमीवर अचानक gap करू शकतो.",
             [Option("index", "Index - NIFTY / BANK NIFTY", "Index - NIFTY / BANK NIFTY"),
              Option("fno_stock", "An F&O stock", "F&O stock"),
              Option("stock", "A cash-market stock", "Cash market stock")], "index"),
    Question("symbol", "symbol", "Which symbol?", "कोणता symbol?",
             "The market read and the backtest run on this symbol's candles.",
             "Market चे विश्लेषण आणि backtest याच symbol च्या candles वर होईल.",
             [], "NIFTY 50"),
    Question("vehicle", "choice", "How do you want to take the trade?", "Trade कशा प्रकारे घ्यायचा?",
             "Option buying caps the loss at the premium; option selling earns time decay but needs a hedge and experience.",
             "Option buying मध्ये तोटा premium इतकाच मर्यादित; option selling मध्ये time decay मिळतो पण hedge आणि अनुभव लागतो.",
             [Option("underlying", "The stock itself / futures", "Stock स्वतः / futures"),
              Option("option_buy", "Buy options (CE/PE)", "Option buy (CE/PE)"),
              Option("option_sell", "Sell options (hedged spreads)", "Option sell (hedge सोबत spread)")], "option_buy"),
    Question("time", "choice", "How much time can you give during market hours?", "Market चालू असताना तुम्ही किती वेळ देऊ शकता?",
             "The platform trades for you either way; this decides how many alerts and how much manual checking to plan for.",
             "Platform दोन्ही प्रकारे trade करतो; यावरून किती alerts आणि किती वेळा तपासायचे ते ठरते.",
             [Option("active", "I watch the screen all day", "दिवसभर screen समोर असतो"),
              Option("few_checks", "I check a few times a day", "दिवसातून काही वेळा पाहतो"),
              Option("auto", "Fully automatic - alerts only", "पूर्ण automatic - फक्त alerts")], "auto"),
    Question("goal", "choice", "What do you want most right now?", "सध्या तुम्हाला सगळ्यात जास्त काय हवे आहे?",
             "Honest goals pick honest methods: catching trends and steady small gains need different strategies.",
             "प्रामाणिक ध्येय = योग्य पद्धत: मोठे trend पकडणे आणि रोज छोटा नफा यासाठी वेगळ्या strategies लागतात.",
             [Option("learn", "Learn properly first", "आधी नीट शिकायचे आहे"),
              Option("steady", "Steady small gains", "रोज थोडा पण स्थिर नफा"),
              Option("big_trends", "Catch big trend moves", "मोठे trend moves पकडायचे")], "learn"),
    Question("view", "choice", "What do you think the market will do?", "Market कुठे जाईल असे तुम्हाला वाटते?",
             "Your view is compared with what the market is actually doing; a professional does not fight the trend.",
             "तुमचे मत market प्रत्यक्षात काय करतो आहे त्याच्याशी तुलना केली जाते; professional trend च्या विरुद्ध जात नाही.",
             [Option("bullish", "Up", "वर"), Option("bearish", "Down", "खाली"), Option("neutral", "Sideways", "एका पट्ट्यात"),
              Option("unsure", "Don't know - you read it", "माहीत नाही - तुम्हीच ठरवा")], "unsure"),
]
QUESTION_IDS = [q.id for q in QUESTIONS]


class InterviewAnswers(BaseModel):
    language: Lang = "mr"
    experience: Literal["new", "learning", "experienced"] = "new"
    capital: float = Field(default=100_000.0, ge=5_000, le=10_000_000_000)
    risk: Literal["conservative", "moderate", "aggressive"] = "conservative"
    daily_loss: float = Field(default=2.0, ge=0.5, le=10.0)
    style: Literal["scalping", "intraday", "positional", "swing"] = "intraday"
    instrument: Literal["index", "fno_stock", "stock"] = "index"
    symbol: str = Field(default="NIFTY 50", min_length=1, max_length=50)
    vehicle: Literal["underlying", "option_buy", "option_sell"] = "option_buy"
    time: Literal["active", "few_checks", "auto"] = "auto"
    goal: Literal["learn", "steady", "big_trends"] = "learn"
    view: Literal["bullish", "bearish", "neutral", "unsure"] = "unsure"


# --- reading the free-text request -------------------------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_RULE_WORDS = re.compile(
    r"\b(ema|sma|rsi|macd|vwap|supertrend|adx|bollinger|atr|stochastic|crosses?|crossover|moving average|di\+|di-)\b"
    r"|ईएमए|आरएसआय|मॅकडी|सुपरट्रेंड|मूव्हिंग",
    re.I,
)
_PREFILL_RULES: List[Tuple[str, str, str]] = [
    # (answer, value, regex)
    ("symbol", "NIFTY BANK", r"bank\s*nifty|banknifty|nifty\s*bank|बँक\s*निफ्टी|बँकनिफ्टी"),
    ("symbol", "NIFTY FIN SERVICE", r"fin\s*nifty|finnifty|nifty\s*fin|फिन\s*निफ्टी"),
    ("symbol", "NIFTY 50", r"\bnifty\b|निफ्टी"),
    ("style", "scalping", r"scalp|स्कॅल्प|स्काल्प"),
    ("style", "intraday", r"intra\s*-?day|इंट्राडे|इंट्रा\s*डे"),
    ("style", "swing", r"swing|positional|पोझिशनल|स्विंग|delivery|डिलिव्हरी"),
    ("vehicle", "option_sell", r"option\s*(sell|writ)|selling|सेलिंग|ऑप्शन\s*सेल|स्प्रेड|spread|iron\s*condor"),
    ("vehicle", "option_buy", r"option\s*buy|buy(ing)?\s*(options?|ce|pe|calls?|puts?)|ऑप्शन\s*(बाय|खरेदी)|कॉल\s*बाय|पुट\s*बाय"),
    ("vehicle", "underlying", r"futures?|फ्युचर|equity|cash\s*market|डिलिव्हरी"),
    ("experience", "new", r"beginner|new\s*trader|\bnew\b|नवशिक|नवीन|नव\s*शिक"),
    ("experience", "experienced", r"experienced|professional|अनुभवी"),
    ("goal", "learn", r"\blearn|शिक"),
    ("view", "bullish", r"bullish|तेजी"),
    ("view", "bearish", r"bearish|मंदी"),
]
_CAPITAL = re.compile(r"(?:₹|rs\.?|inr|capital|भांडवल|कॅपिटल)?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac|लाख|k|हजार|thousand|cr|crore|कोटी)?", re.I)


def is_vague(prompt: str) -> bool:
    """True when the request names no rule (no indicator, no crossover): the interview should
    run before any strategy is proposed."""
    text = (prompt or "").strip()
    return len(text) < 10 or not _RULE_WORDS.search(text)


def detect_language(prompt: str) -> Lang:
    return "mr" if _DEVANAGARI.search(prompt or "") else "en"


def _capital_from(text: str) -> Optional[float]:
    best: Optional[float] = None
    for m in _CAPITAL.finditer(text):
        raw, unit = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        if not raw or not re.search(r"\d", raw):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        value *= {"lakh": 1e5, "lac": 1e5, "लाख": 1e5, "k": 1e3, "हजार": 1e3, "thousand": 1e3, "cr": 1e7, "crore": 1e7, "कोटी": 1e7}.get(unit, 1.0)
        if 5_000 <= value <= 10_000_000_000 and (best is None or value > best):
            best = value
    return best


def prefill_from_prompt(prompt: str) -> Dict[str, str]:
    text = (prompt or "").lower()
    out: Dict[str, str] = {}
    if prompt and prompt.strip():
        out["language"] = detect_language(prompt)
    for key, value, pattern in _PREFILL_RULES:
        if key not in out and re.search(pattern, text, re.I):
            out[key] = value
    if out.get("symbol"):
        out["instrument"] = "index"
    capital = _capital_from(text)
    if capital:
        out["capital"] = str(int(capital))
    return out


def start(prompt: str) -> dict:
    prefill = prefill_from_prompt(prompt)
    language = prefill.get("language", "mr")
    return {
        "needs_interview": is_vague(prompt),
        "language": language,
        "prefill": prefill,
        "remaining": [q for q in QUESTION_IDS if q not in prefill],
        "questions": [q.as_dict() for q in QUESTIONS],
        "intro": tr(language,
                    "Before I suggest any strategy I need to know you - a professional never gives one strategy to everybody. "
                    "A few quick questions (I've skipped what you already told me).",
                    "कोणतीही strategy सांगण्याआधी मला तुम्हाला ओळखायचे आहे - professional trader सगळ्यांना एकच strategy देत नाही. "
                    "काही छोटे प्रश्न विचारतो (तुम्ही आधीच सांगितलेले प्रश्न वगळले आहेत)."),
    }


# --- the market read ---------------------------------------------------------------------------------

MIN_BARS = 60
MAX_BARS = 3000
_IST = "Asia/Kolkata"


def _frame_at(df: pd.DataFrame, base_tf: str, tf: str) -> pd.DataFrame:
    if tf == base_tf:
        return df
    return resample_ohlc(df, tf).dropna(subset=["close"])


def _regime_text(lang: str, r: Regime) -> str:
    names = {
        "TRENDING_UP": ("uptrend", "तेजीचा trend (वर)"), "TRENDING_DOWN": ("downtrend", "मंदीचा trend (खाली)"),
        "RANGING": ("sideways range", "एका पट्ट्यात (sideways)"), "VOLATILE": ("volatile - wild swings", "अस्थिर - मोठे चढ-उतार"),
        "QUIET": ("quiet - compressed", "शांत - दबलेला range"), "UNKNOWN": ("not enough data", "पुरेसा data नाही"),
    }
    en, mr = names.get(r.kind, (r.kind, r.kind))
    return tr(lang, en, mr)


def analyse_market(df: pd.DataFrame, base_tf: str, lang: str = "en") -> dict:
    """The professional's pre-market read, from the candles given. Returns plain numbers plus
    sentences in `lang`."""
    df = df.sort_index()
    last = float(df["close"].iloc[-1])
    idx = df.index.tz_convert(_IST) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(_IST)
    today = idx[-1].date()
    session = df[[d == today for d in idx.date]]
    first_open = float(session["open"].iloc[0]) if len(session) else float(df["open"].iloc[0])
    change_pct = (last / first_open - 1.0) * 100.0 if first_open else 0.0
    vwap = float(session_vwap_or_mean(df).iloc[-1])
    today_trend = "UP" if change_pct >= 0.3 else "DOWN" if change_pct <= -0.3 else "SIDEWAYS"

    work_tf = "5min" if (timeframe_minutes(base_tf) or 5) < 5 else base_tf
    work = _frame_at(df, base_tf, work_tf)
    base_regime = classify_regime(work)
    htf, htf_regime = work_tf, base_regime
    for tf in ("60min", "30min", "15min"):
        if (timeframe_minutes(tf) or 0) <= (timeframe_minutes(work_tf) or 5):
            continue
        frame = _frame_at(df, base_tf, tf)
        if len(frame) >= MIN_BARS:
            htf, htf_regime = tf, classify_regime(frame)
            break

    structure = analyze_market_structure(work.iloc[-600:])
    last_event = structure.events[-1] if structure.events else None
    try:
        zones = SupportResistanceEngine().build_zones(work.iloc[-600:], work_tf)
    except Exception:  # an S/R source needing more history than given must not sink the read
        zones = []
    below = [z for z in zones if z.upper < last]
    above = [z for z in zones if z.lower > last]
    support = max(below, key=lambda z: (z.mid, z.strength_score)) if below else None
    resistance = min(above, key=lambda z: (z.mid, -z.strength_score)) if above else None
    atr_pct = base_regime.atr_pct

    score, reasons = 0, []

    def vote(points: int, en: str, mr: str) -> None:
        nonlocal score
        score += points
        reasons.append(tr(lang, en, mr))

    if htf_regime.kind == "TRENDING_UP":
        vote(1, f"{htf} trend is up", f"{htf} चा trend वर आहे")
    elif htf_regime.kind == "TRENDING_DOWN":
        vote(-1, f"{htf} trend is down", f"{htf} चा trend खाली आहे")
    if base_regime.kind == "TRENDING_UP":
        vote(1, f"{work_tf} trend is up", f"{work_tf} चा trend वर आहे")
    elif base_regime.kind == "TRENDING_DOWN":
        vote(-1, f"{work_tf} trend is down", f"{work_tf} चा trend खाली आहे")
    if structure.trend.value == "UPTREND":
        vote(1, "structure makes higher highs and higher lows", "structure मध्ये higher high / higher low बनत आहेत")
    elif structure.trend.value == "DOWNTREND":
        vote(-1, "structure makes lower highs and lower lows", "structure मध्ये lower high / lower low बनत आहेत")
    if today_trend == "UP" and last > vwap:
        vote(1, "today is up and price holds above VWAP", "आज market वर आहे आणि भाव VWAP च्या वर टिकून आहे")
    elif today_trend == "DOWN" and last < vwap:
        vote(-1, "today is down and price is below VWAP", "आज market खाली आहे आणि भाव VWAP च्या खाली आहे")
    bias = "BULLISH" if score >= 2 else "BEARISH" if score <= -2 else "NEUTRAL"

    return {
        "last_price": round(last, 2), "base_timeframe": base_tf, "work_timeframe": work_tf,
        "today": {"date": today.isoformat(), "change_pct": round(change_pct, 2), "trend": today_trend, "vwap": round(vwap, 2),
                  "above_vwap": last > vwap, "bars": int(len(session))},
        "regime": base_regime.to_dict(), "higher_timeframe": htf, "higher_regime": htf_regime.to_dict(),
        "structure": {"trend": structure.trend.value,
                      "last_event": None if last_event is None else {"event": last_event.event, "direction": last_event.direction,
                                                                     "level": round(last_event.level, 2)}},
        "support": None if support is None else {"low": round(support.lower, 2), "high": round(support.upper, 2), "source": support.source},
        "resistance": None if resistance is None else {"low": round(resistance.lower, 2), "high": round(resistance.upper, 2), "source": resistance.source},
        "atr_pct": atr_pct, "volatile": base_regime.kind == "VOLATILE",
        "bias": bias, "bias_score": score, "bias_reasons": reasons,
        "regime_text": _regime_text(lang, base_regime), "higher_regime_text": _regime_text(lang, htf_regime),
    }


# --- the strategy catalogue -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Profile:
    family: str                 # trend | momentum | breakout | reversion
    styles: Tuple[str, ...]
    en: str
    mr: str


PROFILES: Dict[str, Profile] = {
    "mtf_1m_5m_trend_pullback": Profile("trend", ("scalping",), "Finds the 5-minute trend, enters on a 1-minute pullback in its direction.",
                                        "5 मिनिटांचा trend ओळखतो आणि 1 मिनिटाच्या pullback वर त्याच दिशेने entry घेतो."),
    "mtf_1m_15m_trend_pullback": Profile("trend", ("scalping",), "Follows the 15-minute trend, enters on a 1-minute pullback.",
                                         "15 मिनिटांच्या trend सोबत, 1 मिनिटाच्या pullback वर entry."),
    "ema_rsi_scalper_1m": Profile("trend", ("scalping",), "Fast EMA trend with an RSI pullback trigger on 1-minute candles.",
                                  "1 मिनिटाच्या candles वर EMA trend आणि RSI pullback वर entry."),
    "supertrend_adx_scalper_1m": Profile("trend", ("scalping",), "Supertrend direction, only when ADX says the trend is strong.",
                                         "Supertrend ची दिशा, आणि ADX trend मजबूत सांगतो तेव्हाच entry."),
    "rsi_adx_momentum_5m": Profile("momentum", ("intraday", "scalping"), "Momentum bursts: RSI strength confirmed by a rising ADX on 5-minute candles.",
                                   "Momentum: 5 मिनिटांच्या candles वर RSI ची ताकद आणि वाढता ADX."),
    "macd_ema_trend_5m": Profile("trend", ("intraday", "positional"), "Trades only with the EMA200 trend; MACD crossover times the entry.",
                                 "फक्त EMA200 च्या trend सोबत trade; entry ची वेळ MACD crossover ठरवतो."),
    "vwap_supertrend_5m": Profile("trend", ("intraday",), "Above VWAP with Supertrend up = long; below with Supertrend down = short.",
                                  "VWAP च्या वर आणि Supertrend वर = long; खाली आणि Supertrend खाली = short."),
    "orb_15m_5m": Profile("breakout", ("intraday",), "Marks the first 15 minutes' range and trades the first close outside it.",
                          "पहिल्या 15 मिनिटांची range ठरवतो आणि त्याबाहेरच्या पहिल्या close वर trade घेतो."),
    "bb_rsi_reversion_5m": Profile("reversion", ("intraday", "scalping"), "Fades stretched moves: outside the Bollinger band with RSI extreme, back to the mean.",
                                   "जास्त ताणलेली move उलटवतो: Bollinger band बाहेर आणि RSI टोकाला असेल तर सरासरीकडे परत."),
    "mtf_5m_30m_trend_pullback": Profile("trend", ("intraday", "positional"), "Follows the 30-minute trend, enters on a 5-minute pullback.",
                                         "30 मिनिटांच्या trend सोबत, 5 मिनिटाच्या pullback वर entry."),
    "mtf_5m_60m_trend_pullback": Profile("trend", ("positional",), "Follows the hourly trend, enters on a 5-minute pullback - few, calmer trades.",
                                         "तासाच्या trend सोबत, 5 मिनिटाच्या pullback वर entry - कमी आणि शांत trades."),
    # Phase AS: daily strategies, held overnight.
    "swing_ema_pullback_d": Profile("trend", ("swing",), "Daily uptrend: buys the pullback to the 20-day average when it turns back up; holds for days.",
                                    "Daily uptrend मध्ये 20-दिवसांच्या सरासरीपर्यंत आलेला pullback पुन्हा वर वळला की खरेदी; काही दिवस धरून ठेवतो."),
    "swing_breakout_d": Profile("breakout", ("swing",), "Buys a close above the 20-day high with a trending ADX and volume; holds for days.",
                                "20 दिवसांच्या high वरचा close, ADX trend आणि volume सोबत - खरेदी करून काही दिवस धरतो."),
}
FAMILY_TEXT = {
    "trend": ("trend following", "trend च्या सोबत"), "momentum": ("momentum", "momentum"),
    "breakout": ("breakout", "breakout"), "reversion": ("mean reversion", "सरासरीकडे परत (reversion)"),
}
REGIME_FILTERS = {
    "trend": ["TRENDING_UP", "TRENDING_DOWN"], "momentum": ["TRENDING_UP", "TRENDING_DOWN"],
    "reversion": ["RANGING", "QUIET"], "breakout": ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "QUIET"],
}


def regime_fit(family: str, regime: str, confidence: float = 1.0) -> float:
    """0-3: how well a strategy family suits the regime."""
    table = {
        "trend": {"TRENDING_UP": 3, "TRENDING_DOWN": 3, "VOLATILE": 1, "RANGING": 0, "QUIET": 0},
        "momentum": {"TRENDING_UP": 3, "TRENDING_DOWN": 3, "VOLATILE": 1.5, "RANGING": 0.5, "QUIET": 0},
        "breakout": {"TRENDING_UP": 2, "TRENDING_DOWN": 2, "VOLATILE": 1, "RANGING": 1, "QUIET": 3},
        "reversion": {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "VOLATILE": 0, "RANGING": 3, "QUIET": 2},
    }
    fit = float(table.get(family, {}).get(regime, 1.0))
    if family in ("trend", "momentum") and regime.startswith("TRENDING") and confidence < 0.4:
        fit -= 1.0   # weak/transitional trend
    return max(fit, 0.0)


# --- risk, capital, contract ---------------------------------------------------------------------------

RISK_PCT = {"conservative": 0.5, "moderate": 1.0, "aggressive": 1.5}
RISK_CAP_BY_EXPERIENCE = {"new": 0.5, "learning": 1.0, "experienced": 2.0}
ALLOCATION = {"new": 0.25, "learning": 0.5, "experienced": 0.8}
TRADES_PER_DAY = {"scalping": 6, "intraday": 3, "positional": 2, "swing": 1}
SWING_GAP_FACTOR = 0.75   # Phase AS: an overnight gap can jump the stop - swing risk is sized a quarter smaller
OPEN_POSITIONS = {"new": 1, "learning": 2, "experienced": 3}


def risk_plan(a: InterviewAnswers, ceilings: Optional[Dict[str, float]] = None) -> Tuple[RiskConfig, List[str]]:
    """The tenant risk settings the plan proposes, plus notes on what was capped and why."""
    ceilings = ceilings or {}
    lang = a.language
    notes: List[str] = []
    wanted = RISK_PCT[a.risk]
    risk_pct = min(wanted, RISK_CAP_BY_EXPERIENCE[a.experience], float(ceilings.get("risk_per_trade_pct", 2.0)))
    if risk_pct < wanted:
        notes.append(tr(lang, f"Risk per trade capped at {risk_pct:g}% (you chose {wanted:g}%) - a {a.experience} trader starts small; it can rise once the paper record earns it.",
                        f"एका trade चा risk {risk_pct:g}% वर मर्यादित केला (तुम्ही {wanted:g}% निवडले होते) - सुरुवात छोट्या risk ने; PAPER चे निकाल चांगले आले की वाढवता येईल."))
    if a.style == "swing":
        risk_pct = round(risk_pct * SWING_GAP_FACTOR, 2)
        notes.append(tr(lang, f"Swing risk per trade {risk_pct:g}% - a quarter smaller, because an overnight gap can open beyond the stop.",
                        f"Swing मध्ये एका trade चा risk {risk_pct:g}% - रात्रभरच्या gap मुळे भाव stop च्या पलीकडे उघडू शकतो, म्हणून एक चतुर्थांश कमी."))
    daily = min(a.daily_loss, round(risk_pct * 3, 2), float(ceilings.get("max_daily_loss_pct", 5.0)))
    if daily < risk_pct * 2:
        risk_pct = round(daily / 2, 2)
        notes.append(tr(lang, f"Risk per trade lowered to {risk_pct:g}% so the daily limit allows at least two losing trades.",
                        f"दिवसाच्या मर्यादेत किमान दोन चुकलेले trades बसावेत म्हणून एका trade चा risk {risk_pct:g}% केला."))
    elif daily < a.daily_loss:
        notes.append(tr(lang, f"Daily loss limit set to {daily:g}% (3 losing trades) rather than {a.daily_loss:g}% - three losses in a day is the professional's stop signal.",
                        f"दिवसाची तोटा मर्यादा {a.daily_loss:g}% ऐवजी {daily:g}% (3 चुकलेले trades) ठेवली - दिवसात 3 तोटे म्हणजे professional साठी थांबण्याचा संकेत."))
    trades = TRADES_PER_DAY[a.style] if a.experience != "new" else min(TRADES_PER_DAY[a.style], 3)
    trading_capital = round(a.capital * ALLOCATION[a.experience], 0)
    cfg = RiskConfig(
        capital=trading_capital, risk_per_trade_pct=risk_pct, max_daily_loss_pct=daily,
        max_trades_per_day=trades, max_open_positions=OPEN_POSITIONS[a.experience],
        max_consecutive_losses=2 if a.experience == "new" else 3,
        min_risk_reward=2.0 if a.vehicle == "option_buy" else 1.5,
        max_portfolio_risk_pct=min(round(risk_pct * 3, 2), float(ceilings.get("max_portfolio_risk_pct", 10.0))),
        stop_cooldown_minutes=45 if a.experience == "new" else 30,
        dd_level_1_pct=3.0 if a.experience == "new" else 5.0, dd_level_2_pct=6.0 if a.experience == "new" else 10.0,
    )
    return cfg, notes


def contract_plan(a: InterviewAnswers, bias: str) -> Tuple[dict, List[str], str]:
    """(contract fields for the deployment, notes, one-line description)."""
    lang = a.language
    notes: List[str] = []
    underlying = instrument_master.underlying_of(a.symbol)
    is_index = underlying in instrument_master.INDEX_SYMBOLS or a.instrument == "index"
    vehicle = a.vehicle
    if vehicle == "option_sell" and a.experience == "new":
        vehicle = "option_buy"
        notes.append(tr(lang, "Option selling is not suggested for a new trader: one bad day can wipe out weeks of premium. You get option buying (loss capped at the premium) until your paper record shows consistency.",
                        "नवशिक्यासाठी option selling सुचवत नाही: एका वाईट दिवसात अनेक आठवड्यांचा premium जाऊ शकतो. PAPER मध्ये सातत्य दिसेपर्यंत option buying (तोटा premium इतकाच) दिला आहे."))
    if a.instrument == "stock" and vehicle != "underlying":
        vehicle = "underlying"
        notes.append(tr(lang, "A cash-market stock has no options - the plan trades the stock itself.",
                        "Cash market stock ला options नसतात - plan मध्ये stock स्वतःच trade होईल."))
    if is_index and vehicle == "underlying":
        if a.experience == "new":
            vehicle = "option_buy"
            notes.append(tr(lang, "An index cannot be bought directly and a futures lot moves too much money for a first account - the plan buys an in-the-money option instead.",
                            "Index थेट विकत घेता येत नाही आणि futures चा एक lot नवशिक्याच्या खात्यासाठी खूप मोठा असतो - त्यामुळे plan मध्ये ITM option buy केला आहे."))
        else:
            return ({"instrument_kind": "FUTURE", "expiry_rule": "NEAREST"}, notes,
                    tr(lang, f"{underlying} futures, nearest expiry", f"{underlying} futures, जवळची expiry"))
    swing = a.style == "swing"
    if swing and vehicle == "option_sell":
        vehicle = "option_buy"
        notes.append(tr(lang, "Options are never written overnight here - a swing in options is a bought option.",
                        "इथे options रात्रभर विकले (write) जात नाहीत - options मधला swing म्हणजे option buy."))
    if vehicle == "underlying":
        if swing:
            return ({"instrument_kind": "UNDERLYING"}, notes + [tr(lang, "Bought for delivery (CNC) and held until the stop or target; long only - cash shares cannot be held short overnight.",
                                                                    "Delivery (CNC) ने खरेदी, stop किंवा target पर्यंत धरून; फक्त long - cash shares रात्रभर short ठेवता येत नाहीत.")],
                    tr(lang, f"{a.symbol.upper()} shares, delivery (swing)", f"{a.symbol.upper()} shares, delivery (swing)"))
        return ({"instrument_kind": "UNDERLYING"}, notes, tr(lang, f"{a.symbol.upper()} shares (intraday)", f"{a.symbol.upper()} shares (intraday)"))
    if vehicle == "option_buy" and swing:
        notes.append(tr(lang, "Monthly expiry for a swing: weekly options lose too much value (theta) while you wait days for the move.",
                        "Swing साठी monthly expiry: काही दिवस वाट पाहताना weekly option चे मूल्य (theta) खूप घटते."))
        return ({"instrument_kind": "OPTION", "option_position": "BUY", "expiry_rule": "MONTHLY", "strike_rule": "ITM", "strike_offset": 1}, notes,
                tr(lang, f"Buy one-step in-the-money {underlying} options, monthly expiry, held for days",
                   f"{underlying} चा एक step ITM option buy, monthly expiry, काही दिवस धरून"))
    if vehicle == "option_buy":
        expiry = "NEXT" if is_index and a.experience != "experienced" else "NEAREST"
        if expiry == "NEXT":
            notes.append(tr(lang, "The next weekly expiry is used, not the nearest: expiry-day options lose value very fast (theta) and swing wildly.",
                            "जवळची नव्हे तर पुढची weekly expiry वापरली आहे: expiry च्या दिवशी option चे मूल्य वेगाने घटते (theta) आणि भाव खूप उड्या मारतो."))
        return ({"instrument_kind": "OPTION", "option_position": "BUY", "expiry_rule": expiry, "strike_rule": "ITM", "strike_offset": 1}, notes,
                tr(lang, f"Buy one-step in-the-money {underlying} options (CE on a long signal, PE on a short), {expiry.lower()} expiry",
                   f"{underlying} चा एक step ITM option buy (long signal ला CE, short ला PE), {'पुढची' if expiry == 'NEXT' else 'जवळची'} expiry"))
    structure = {"BULLISH": "BULL_PUT_SPREAD", "BEARISH": "BEAR_CALL_SPREAD"}.get(bias, "IRON_CONDOR")
    notes.append(tr(lang, f"Option selling only as a hedged {structure.replace('_', ' ').lower()} - the bought wing caps the worst case.",
                    f"Option selling फक्त hedge सोबत ({structure.replace('_', ' ').lower()}) - विकत घेतलेला wing जास्तीत जास्त तोटा मर्यादित ठेवतो."))
    return ({"instrument_kind": "OPTION", "option_strategy": structure, "expiry_rule": "NEAREST", "spread_width": 2,
             "target_credit_pct": 50.0, "stop_credit_pct": 150.0}, notes,
            tr(lang, f"{structure.replace('_', ' ').title()} on {underlying}, take profit at 50% of the credit, stop at 150%",
               f"{underlying} वर {structure.replace('_', ' ').title()}, credit च्या 50% वर नफा, 150% वर stop"))


# --- the plan ---------------------------------------------------------------------------------------------

STRUCTURE_MR = {"UPTREND": "वरचा कल (higher high / higher low)", "DOWNTREND": "खालचा कल (lower high / lower low)", "RANGE": "range - स्पष्ट कल नाही"}
STYLE_EN = {"scalping": "scalping", "intraday": "intraday", "positional": "calmer trend-following intraday", "swing": "swing (overnight)"}
STYLE_MR = {"scalping": "scalping", "intraday": "intraday", "positional": "शांत, trend सोबतच्या intraday", "swing": "swing (रात्रभर)"}


def _money(v: float) -> str:
    return f"₹{v:,.0f}"


EVIDENCE_CANDIDATES = 3
EVIDENCE_BARS = {1: 1500, 3: 800}      # base minutes -> candles walked for evidence (default 500)


def _compatible(strategy, base_tf: str) -> bool:
    base = timeframe_minutes(base_tf) or 0
    needed = [timeframe_minutes(tf) for tf in strategy.timeframes]
    return bool(base) and all(n is not None and n >= base and n % base == 0 for n in needed)


# Evidence is measured on one fixed book (1 lakh, 1% risk) so it does not depend on the option
# being shown, and cached: a refinement round re-uses the walks it already did on the same candles.
EVIDENCE_RISK = RiskConfig(capital=100_000.0, risk_per_trade_pct=1.0, max_trades_per_day=20, max_consecutive_losses=100)
_EVIDENCE_CACHE: "OrderedDict[tuple, Tuple[float, dict, Optional[dict]]]" = OrderedDict()
_EVIDENCE_CACHE_SIZE = 256
_EVIDENCE_LOCK = Lock()


def _evidence_key(strategy_id: str, df: pd.DataFrame, base_tf: str, symbol: str, lang: str) -> tuple:
    return (strategy_id, base_tf, symbol.upper(), lang, len(df), str(df.index[0]), str(df.index[-1]), round(float(df["close"].iloc[-1]), 4))


def _evidence(lang: str, strategy, df: pd.DataFrame, base_tf: str, symbol: str, cfg: Optional[RiskConfig] = None) -> Tuple[float, dict, Optional[dict]]:
    key = _evidence_key(strategy.id, df, base_tf, symbol, lang)
    with _EVIDENCE_LOCK:
        if key in _EVIDENCE_CACHE:
            _EVIDENCE_CACHE.move_to_end(key)
            return _EVIDENCE_CACHE[key]
    bars = EVIDENCE_BARS.get(timeframe_minutes(base_tf) or 5, 500)
    run = evaluate_on_candles(strategy, df.iloc[-bars:], base_tf, symbol, EVIDENCE_RISK)
    score = 0.0
    if run.total_trades >= 5:
        pf = run.profit_factor if run.profit_factor is not None else (3.0 if run.net_pnl > 0 else 0.0)
        score = min(float(pf), 3.0) + (1.0 if run.net_pnl > 0 else -1.0)
        text = tr(lang, f"{run.total_trades} trades on recent candles, win rate {run.win_rate:.0f}%, net {_money(run.net_pnl)} (on ₹1 lakh at 1% risk)",
                  f"अलीकडच्या candles वर {run.total_trades} trades, win rate {run.win_rate:.0f}%, निव्वळ {_money(run.net_pnl)} (₹1 लाख, 1% risk वर)")
    elif run.reason and not run.total_trades:
        text = tr(lang, "not enough candles to test it here", "इथे तपासायला पुरेसे candles नाहीत")
    else:
        score = 0.25 if run.net_pnl > 0 else -0.25 if run.total_trades else 0.0
        text = tr(lang, f"only {run.total_trades} trade(s) on recent candles - too few to judge", f"अलीकडच्या candles वर फक्त {run.total_trades} trade - ठरवायला खूप कमी")
    evidence = {"tested": True, "total_trades": run.total_trades, "win_rate": run.win_rate, "net_pnl": run.net_pnl,
                "profit_factor": run.profit_factor, "text": text}
    out = (score, evidence, None if run.last_signal is None else run.last_signal.model_dump(mode="json"))
    with _EVIDENCE_LOCK:
        _EVIDENCE_CACHE[key] = out
        while len(_EVIDENCE_CACHE) > _EVIDENCE_CACHE_SIZE:
            _EVIDENCE_CACHE.popitem(last=False)
    return out


def _rank(a: InterviewAnswers, market: dict, df: pd.DataFrame, base_tf: str, cfg: RiskConfig, *,
          exclude: Tuple[str, ...] = (), family_bonus: Optional[Dict[str, float]] = None) -> List[dict]:
    """Fit first (style, regime, goal) for every candidate; the best few are then walked over
    the recent candles for evidence - a backtest per strategy is the slow part."""
    regime = market["regime"]["kind"]
    conf = float(market["regime"]["confidence"] or 0)
    lang = a.language
    ranked: List[dict] = []
    for sid, p in PROFILES.items():
        if a.style not in p.styles or sid in exclude:
            continue
        try:
            strategy = copy.copy(registry.get(sid))
        except KeyError:
            continue
        if not _compatible(strategy, base_tf):
            continue
        fit = regime_fit(p.family, regime, conf)
        if market["volatile"] and a.experience == "new":
            fit = max(fit - 0.5, 0.0)
        goal = 1.0 if (a.goal == "big_trends" and p.family in ("trend", "breakout")) or (a.goal == "steady" and p.family in ("reversion", "momentum")) else 0.0
        goal += (family_bonus or {}).get(p.family, 0.0)
        ranked.append({
            "strategy_id": sid, "name": strategy.name, "family": p.family, "timeframes": list(strategy.timeframes),
            "description": tr(lang, p.en, p.mr), "regime_fit": fit, "score": round(fit * 2 + goal, 2),
            "evidence": {"tested": False, "total_trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": None,
                         "text": tr(lang, "not tested - it fits this market less well", "तपासली नाही - या market ला कमी जुळते")},
            "last_signal": None, "_strategy": strategy,
        })
    ranked.sort(key=lambda r: r["score"], reverse=True)
    top = ranked[:EVIDENCE_CANDIDATES]
    with ThreadPoolExecutor(max_workers=EVIDENCE_CANDIDATES) as pool:
        results = list(pool.map(lambda r: _evidence(lang, r["_strategy"], df, base_tf, a.symbol), top))
    for r, (score, evidence, last_signal) in zip(top, results):
        r["score"] = round(r["score"] + score, 2)
        r["evidence"], r["last_signal"] = evidence, last_signal
    for r in ranked:
        r.pop("_strategy", None)
    ranked.sort(key=lambda r: (r["score"], r["evidence"]["tested"], r["evidence"]["net_pnl"], r["evidence"]["total_trades"]), reverse=True)
    return ranked


def build_plan(a: InterviewAnswers, df: pd.DataFrame, base_tf: str, *, ceilings: Optional[Dict[str, float]] = None,
               data_source: str = "sample") -> dict:
    market = analyse_market(df, base_tf, a.language)
    cfg, risk_notes = risk_plan(a, ceilings)
    ranked = _rank(a, market, df, base_tf, cfg)
    return compose_plan(a, market, ranked, ranked[0] if ranked else None, cfg, risk_notes, data_source)


def default_exit_rules(a: InterviewAnswers) -> dict:
    return {"break_even_at_r": 1.0 if a.experience == "new" else 1.5,
            "time_exit_at": "15:10" if a.style in ("scalping", "intraday") else None}


def compose_plan(a: InterviewAnswers, market: dict, ranked: List[dict], pick: Optional[dict], cfg: RiskConfig, risk_notes: List[str],
                 data_source: str, *, exit_rules: Optional[dict] = None, alternatives: Optional[List[dict]] = None,
                 extra_contract_notes: Optional[List[str]] = None) -> dict:
    """Every sentence of a plan for one strategy pick and one risk configuration (Phase AQ builds
    three of these, one per option)."""
    lang = a.language
    warnings: List[str] = []
    if not ranked:
        warnings.append(tr(lang, "No strategy could be tested on these candles - load more history (a longer lookback) and try again.",
                           "या candles वर एकही strategy तपासता आली नाही - जास्त history (मोठा lookback) घेऊन पुन्हा प्रयत्न करा."))
    alternatives = [r for r in ranked if pick is None or r["strategy_id"] != pick["strategy_id"]][:2] if alternatives is None else alternatives
    contract, contract_notes, contract_text = contract_plan(a, market["bias"])
    contract_notes = contract_notes + (extra_contract_notes or [])

    # Market sentences.
    t = market["today"]
    s = market["structure"]
    m_lines = [
        tr(lang, f"Today: {t['change_pct']:+.2f}% from the open ({ {'UP': 'up', 'DOWN': 'down', 'SIDEWAYS': 'flat'}[t['trend']] }), price {'above' if t['above_vwap'] else 'below'} VWAP {t['vwap']:,}.",
           f"आज: open पासून {t['change_pct']:+.2f}% ({ {'UP': 'वर', 'DOWN': 'खाली', 'SIDEWAYS': 'सपाट'}[t['trend']] }), भाव VWAP {t['vwap']:,} च्या {'वर' if t['above_vwap'] else 'खाली'}."),
        tr(lang, f"Latest trend ({market['higher_timeframe']}): {market['higher_regime_text']}.", f"मोठा trend ({market['higher_timeframe']}): {market['higher_regime_text']}."),
        tr(lang, f"Current regime ({market['work_timeframe']}): {market['regime_text']} - {'; '.join(market['regime']['reasons'][:2])}.",
           f"सध्याची स्थिती ({market['work_timeframe']}): {market['regime_text']} - {'; '.join(market['regime']['reasons'][:2])}."),
        tr(lang, f"Market structure: {s['trend'].lower()}" + (f", last {s['last_event']['event']} {s['last_event']['direction'].lower()} at {s['last_event']['level']:,}" if s["last_event"] else "") + ".",
           f"Market structure: {STRUCTURE_MR.get(s['trend'], s['trend'])}" + (f", शेवटचा {s['last_event']['event']} ({s['last_event']['direction'].lower()}) {s['last_event']['level']:,} वर" if s["last_event"] else "") + "."),
    ]
    if market["support"] or market["resistance"]:
        sup = f"{market['support']['low']:,}-{market['support']['high']:,}" if market["support"] else "-"
        res = f"{market['resistance']['low']:,}-{market['resistance']['high']:,}" if market["resistance"] else "-"
        m_lines.append(tr(lang, f"Nearest support {sup}, resistance {res} (last price {market['last_price']:,}).",
                          f"जवळचा support {sup}, resistance {res} (सध्याचा भाव {market['last_price']:,})."))
    bias_word = {"BULLISH": tr(lang, "BULLISH (up)", "तेजी (BULLISH)"), "BEARISH": tr(lang, "BEARISH (down)", "मंदी (BEARISH)"),
                 "NEUTRAL": tr(lang, "NEUTRAL - no clear side", "NEUTRAL - स्पष्ट दिशा नाही")}[market["bias"]]
    m_lines.append(tr(lang, f"Overall bias: {bias_word}" + (f" because {', '.join(market['bias_reasons'])}" if market["bias_reasons"] else "") + ".",
                      f"एकूण कल: {bias_word}" + (f" कारण {', '.join(market['bias_reasons'])}" if market["bias_reasons"] else "") + "."))
    view_map = {"bullish": "BULLISH", "bearish": "BEARISH", "neutral": "NEUTRAL"}
    if a.view in ("bullish", "bearish") and market["bias"] not in ("NEUTRAL", view_map[a.view]):
        warnings.append(tr(lang, f"Your view is {a.view} but the market reads {market['bias'].lower()}. A professional trades what the market does, not what they expect - the plan follows the market and the strategy only takes signals the trend confirms.",
                           f"तुमचे मत {'तेजीचे' if a.view == 'bullish' else 'मंदीचे'} आहे पण market {'तेजी' if market['bias'] == 'BULLISH' else 'मंदी'} दाखवतो. Professional अंदाजावर नाही तर market जे करतो त्यावर trade करतो - plan market च्या सोबत आहे."))
    if market["volatile"]:
        warnings.append(tr(lang, "The market is volatile right now: stops are wider in rupees, so the engine buys fewer lots. Consider waiting for it to settle.",
                           "सध्या market अस्थिर आहे: stop रुपयांत मोठा असतो त्यामुळे engine कमी lots घेईल. शांत होईपर्यंत थांबणे चांगले."))
    if data_source == "sample":
        warnings.append(tr(lang, "This read used SAMPLE candles. Switch Data to broker candles for the real market today.",
                           "हे विश्लेषण SAMPLE candles वर आहे. आजच्या खऱ्या market साठी Data मध्ये broker candles निवडा."))

    # Strategy sentences.
    s_lines: List[str] = []
    regime_filter: List[str] = []
    if pick:
        fam_en, fam_mr = FAMILY_TEXT[pick["family"]]
        regime_filter = REGIME_FILTERS[pick["family"]]
        s_lines += [
            tr(lang, f"Recommended: {pick['name']} ({fam_en}, {'/'.join(pick['timeframes'])}).", f"शिफारस: {pick['name']} ({fam_mr}, {'/'.join(pick['timeframes'])})."),
            pick["description"],
            tr(lang, f"Why: it suits the current market ({market['regime_text']}) and your {STYLE_EN[a.style]} style; evidence - {pick['evidence']['text']}.",
               f"का: {market['regime_text']} market आणि तुमच्या {STYLE_MR[a.style]} पद्धतीला ही योग्य आहे; पुरावा - {pick['evidence']['text']}."),
            tr(lang, f"It only enters in these regimes: {', '.join(regime_filter)} - on other days it sits out, and that is part of the edge.",
               f"ही फक्त या स्थितीत entry घेते: {', '.join(regime_filter)} - इतर दिवशी trade घेत नाही, आणि हेच शिस्तीचे बळ आहे."),
        ]
        if pick["regime_fit"] < 2:
            warnings.append(tr(lang, "Even the best match does not fit today's market well - expect few or no trades today, which is the right outcome.",
                               "सर्वात योग्य strategy सुद्धा आजच्या market ला नीट जुळत नाही - आज कमी किंवा शून्य trades होतील, आणि तेच योग्य आहे."))
        if pick["evidence"]["total_trades"] >= 5 and pick["evidence"]["net_pnl"] <= 0:
            warnings.append(tr(lang, "On these candles even the recommended strategy lost money. Paper-trade it and judge it on 30+ trades before any live money.",
                               "या candles वर शिफारस केलेल्या strategy नेही तोटा दाखवला. PAPER मध्ये 30+ trades पाहूनच निर्णय घ्या."))
        for alt in alternatives:
            s_lines.append(tr(lang, f"Alternative: {alt['name']} - {alt['evidence']['text']}.", f"पर्याय: {alt['name']} - {alt['evidence']['text']}."))

    # Risk and capital sentences.
    per_trade = cfg.capital * cfg.risk_per_trade_pct / 100
    per_day = cfg.capital * cfg.max_daily_loss_pct / 100
    r_lines = [
        tr(lang, f"Risk per trade: {cfg.risk_per_trade_pct:g}% of trading capital = {_money(per_trade)}. The quantity is calculated from this and the stop distance - never chosen by feel.",
           f"एका trade चा risk: trading भांडवलाच्या {cfg.risk_per_trade_pct:g}% = {_money(per_trade)}. Quantity यावरून आणि stop च्या अंतरावरून मोजली जाते - मनाने नाही."),
        tr(lang, f"Daily loss limit: {cfg.max_daily_loss_pct:g}% = {_money(per_day)} - then no new trades that day.",
           f"दिवसाची तोटा मर्यादा: {cfg.max_daily_loss_pct:g}% = {_money(per_day)} - त्यानंतर त्या दिवशी नवीन trade नाही."),
        tr(lang, f"At most {cfg.max_trades_per_day} trades a day, {cfg.max_open_positions} open position(s) at a time, pause after {cfg.max_consecutive_losses} losses in a row.",
           f"दिवसाला जास्तीत जास्त {cfg.max_trades_per_day} trades, एका वेळी {cfg.max_open_positions} position, सलग {cfg.max_consecutive_losses} तोटे झाले की थांबा."),
        tr(lang, f"After a stop-loss: {cfg.stop_cooldown_minutes} minutes cool-down on that symbol. Drawdown {cfg.dd_level_1_pct:g}% halves the size; {cfg.dd_level_2_pct:g}% pauses new entries.",
           f"Stop-loss लागल्यावर त्या symbol वर {cfg.stop_cooldown_minutes} मिनिटे थांबा. भांडवल {cfg.dd_level_1_pct:g}% खाली गेले की size अर्धी; {cfg.dd_level_2_pct:g}% खाली गेले की नवीन entry बंद."),
    ] + risk_notes
    reserve = a.capital - cfg.capital
    c_lines = [
        tr(lang, f"Total capital {_money(a.capital)}: trade with {_money(cfg.capital)} ({ALLOCATION[a.experience] * 100:.0f}%), keep {_money(reserve)} untouched as reserve.",
           f"एकूण भांडवल {_money(a.capital)}: {_money(cfg.capital)} ({ALLOCATION[a.experience] * 100:.0f}%) ने trading, {_money(reserve)} राखीव - हात लावायचा नाही."),
        tr(lang, f"Open risk across all positions stays under {cfg.max_portfolio_risk_pct:g}% of trading capital; NIFTY/BANK NIFTY/FIN NIFTY count as one bucket.",
           f"सगळ्या positions चा एकत्रित risk trading भांडवलाच्या {cfg.max_portfolio_risk_pct:g}% पेक्षा कमी; NIFTY/BANK NIFTY/FIN NIFTY एकच गट मानले जातात."),
        tr(lang, "Raise the allocation only after 30+ paper trades with a positive expectancy - never to win back a loss.",
           "30+ PAPER trades मध्ये सरासरी नफा दिसल्यानंतरच allocation वाढवा - तोटा भरून काढण्यासाठी कधीच नाही."),
    ]
    rr = cfg.min_risk_reward
    breakeven_win = 100.0 / (1.0 + rr)
    exit_rules = exit_rules or default_exit_rules(a)
    rr_lines = [
        tr(lang, f"Minimum reward:risk 1:{rr:g} - a trade that cannot make {rr:g}x its risk is skipped. At 1:{rr:g} you stay profitable even winning only {breakeven_win:.0f}% of trades.",
           f"किमान reward:risk 1:{rr:g} - risk च्या {rr:g} पट नफा शक्य नसेल तर trade घेत नाही. 1:{rr:g} वर फक्त {breakeven_win:.0f}% trades जिंकले तरी तोटा होत नाही."),
        tr(lang, f"Stop moves to cost once the trade is {exit_rules['break_even_at_r']:g}R in profit; it never moves against you.",
           f"Trade {exit_rules['break_even_at_r']:g}R नफ्यात आला की stop खरेदी भावावर येतो; stop कधीच विरुद्ध दिशेने सरकत नाही."),
    ]
    if exit_rules["time_exit_at"]:
        rr_lines.append(tr(lang, "Everything still open is closed at 15:10 - no overnight risk.", "15:10 ला उघडे असलेले सगळे trades बंद - रात्रीचा risk नाही."))
    elif a.style == "swing":
        rr_lines.append(tr(lang, "Held overnight: the stop sits with the broker (re-armed every morning) and the trade runs for days until the stop or a target.",
                           "रात्रभर धरले जाते: stop broker कडे असतो (रोज सकाळी पुन्हा लावला जातो) आणि trade stop किंवा target पर्यंत काही दिवस चालतो."))
    k_lines = [contract_text] + contract_notes
    checklist = [
        tr(lang, "Trend and regime agree with the strategy (regime filter)", "Trend आणि market ची स्थिती strategy ला अनुकूल आहे (regime filter)"),
        tr(lang, "Stop-loss defined before entry, at least 1 ATR away", "Entry आधीच stop-loss ठरलेला, किमान 1 ATR अंतरावर"),
        tr(lang, f"Reward at least {rr:g}x the risk", f"नफा risk च्या किमान {rr:g} पट"),
        tr(lang, "Size from risk %, never from feel; never averaging a loser", "Size risk % वरून, मनाने नाही; तोट्यातल्या trade मध्ये भर नाही"),
        tr(lang, "Daily loss limit and losing-streak pause respected", "दिवसाची तोटा मर्यादा आणि सलग तोट्यानंतरचा थांबा पाळला"),
        tr(lang, "No entries on blocked event days (results, policy)", "मोठ्या घटनांच्या दिवशी (results, policy) entry नाही"),
    ]
    steps = [
        tr(lang, "Read this plan and apply the risk settings.", "हा plan वाचा आणि risk settings लागू करा."),
        tr(lang, "Deploy in PAPER - the worker trades it with no real money.", "PAPER मध्ये deploy करा - worker खऱ्या पैशाशिवाय trade करेल."),
        tr(lang, "Review after 2 weeks or 30 trades: win rate, average win vs loss, rule breaks.", "2 आठवडे किंवा 30 trades नंतर तपासा: win rate, सरासरी नफा विरुद्ध तोटा, नियम मोडले का."),
        tr(lang, "Only then go LIVE small through the Go-Live checklist.", "त्यानंतरच Go-Live checklist पूर्ण करून छोट्या रकमेने LIVE."),
    ]
    sections = [
        {"id": "market", "title": tr(lang, "Market view", "Market चे विश्लेषण"), "lines": m_lines},
        {"id": "strategy", "title": tr(lang, "Strategy", "Strategy"), "lines": s_lines},
        {"id": "risk", "title": tr(lang, "Risk management", "Risk management"), "lines": r_lines},
        {"id": "capital", "title": tr(lang, "Capital allocation", "भांडवलाचे नियोजन"), "lines": c_lines},
        {"id": "rr", "title": tr(lang, "Risk : reward and exits", "Risk : reward आणि exit"), "lines": rr_lines},
        {"id": "contract", "title": tr(lang, "What will be traded", "काय trade होईल"), "lines": k_lines},
        {"id": "checklist", "title": tr(lang, "Checked before every trade", "प्रत्येक trade आधी तपासले जाते"), "lines": checklist},
        {"id": "steps", "title": tr(lang, "Your next steps", "पुढचे टप्पे"), "lines": steps},
    ]
    deployment = None
    if pick:
        swing = a.style == "swing"
        deployment = {"strategy_id": pick["strategy_id"], "symbol": a.symbol.upper(), "exchange": "NSE", "timeframe": "day" if swing else "1min",
                      "mode": "PAPER", **contract, "exit_rules": exit_rules, "regime_filter": regime_filter,
                      "holding": "SWING" if swing else "INTRADAY"}
    return {
        "language": lang, "answers": a.model_dump(), "market": market, "recommended": pick, "alternatives": alternatives,
        "ranking": [{"strategy_id": r["strategy_id"], "name": r["name"], "score": r["score"]} for r in ranked],
        "risk_config": cfg.model_dump(), "deployment": deployment, "sections": sections, "warnings": warnings,
        "ai_prompt": ai_prompt(a, market, cfg, pick), "disclaimer": DISCLAIMER[lang],
    }


def ai_prompt(a: InterviewAnswers, market: dict, cfg: RiskConfig, pick: Optional[dict]) -> str:
    """A complete request for the external AI generator, so a custom rule set starts from the
    same interview and market read instead of a one-line wish."""
    sup, res = market.get("support"), market.get("resistance")
    parts = [
        f"I am a {a.experience} trader. Capital for trading {cfg.capital:,.0f} INR (of {a.capital:,.0f}); risk {cfg.risk_per_trade_pct:g}% per trade, "
        f"daily loss limit {cfg.max_daily_loss_pct:g}%, at most {cfg.max_trades_per_day} trades a day.",
        f"Style: {a.style}. Instrument: {a.symbol.upper()} via {a.vehicle.replace('_', ' ')}. Goal: {a.goal.replace('_', ' ')}. Time: {a.time.replace('_', ' ')}.",
        f"Market now: today {market['today']['change_pct']:+.2f}% ({market['today']['trend'].lower()}), price {'above' if market['today']['above_vwap'] else 'below'} VWAP; "
        f"{market['work_timeframe']} regime {market['regime']['kind']}, {market['higher_timeframe']} regime {market['higher_regime']['kind']}; "
        f"structure {market['structure']['trend']}; overall bias {market['bias']}"
        + (f"; support {sup['low']}-{sup['high']}" if sup else "") + (f"; resistance {res['low']}-{res['high']}" if res else "") + ".",
        f"Build one rule-based strategy that fits this regime, with a stop at least 1 ATR away and reward:risk of at least 1:{cfg.min_risk_reward:g}.",
    ]
    if pick:
        parts.append(f"For reference the platform's best inbuilt match is {pick['name']}; improve on it or explain why not.")
    return " ".join(parts)


def frame_from_candles(candles) -> pd.DataFrame:
    from app.core.models import bars_to_dataframe
    df = bars_to_dataframe(candles)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


__all__ = ["QUESTIONS", "InterviewAnswers", "is_vague", "prefill_from_prompt", "start", "analyse_market", "risk_plan",
           "contract_plan", "build_plan", "regime_fit", "frame_from_candles"]
