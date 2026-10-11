"""Phase AT: the guide - a bilingual concept library and questions answered from it.

A good guide knows every concept and answers in plain words; a good teacher also knows what the
market is doing today. This module is both, in English and Marathi:

* `CONCEPTS` - the trading concepts a beginner meets on this platform (trend, structure, support and
  resistance, the indicators the strategies use, stops, sizing, R:R and expectancy, drawdown,
  discipline, options, Greeks, VIX, intraday vs swing, paper trading...), each a short, accurate
  explanation with how this platform applies it, keywords in both scripts and related concepts.
* `answer()` - finds the concepts a question is about (keyword scoring over English and Devanagari
  tokens), and for a "what is the market doing" question about a watched symbol, answers from the
  market memory (Phase AR). Deterministic, no external call.
* `ai_answer()` - when the tenant has an external AI provider, the same retrieved concepts, the
  market memory and the trader's profile become the context of a short, grounded answer in the
  trader's language; the library answer is the fallback on any error.

Education, not advice: no answer tells anyone to buy or sell a particular security.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.ai.interview import tr
from app.ai import grounding, output_filter, prompt_versions, wording


@dataclass(frozen=True)
class Concept:
    id: str
    en: str
    mr: str
    keywords: Tuple[str, ...]
    body_en: str
    body_mr: str
    related: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self, lang: str) -> dict:
        return {"id": self.id, "title": tr(lang, self.en, self.mr), "body": tr(lang, self.body_en, self.body_mr), "related": list(self.related)}


# Marathi bodies of the Trade-port concept entries (same facts, sources and limits as the English text).
MR_BODIES: Dict[str, str] = {
    "candlestick_patterns": (
        "प्रत्येक candle म्हणजे buyers आणि sellers च्या एका लढाईचा निकाल: open = सुरुवात, high/low = कोणी किती ढकलले, close = शेवटी "
        "कोणाच्या हातात नियंत्रण. लांब खालची wick = sellers नी भाव खाली नेला आणि buyers नी परत आणला (rejection). पुरावा काय सांगतो: "
        "एकट्या pattern चे नाव जवळजवळ नाणेफेक आहे. Bulkowski च्या आकडेवारीत (US daily stocks, ~47 लाख candles, costs नाहीत, significance "
        "test नाही) बहुतेक single candles 50-60% - hammer ~60%, doji 50-52% - आणि काही 'classic' patterns उलटे वागतात (hanging man ~59% वेळा "
        "bullish continuation). Peer-reviewed अभ्यास मिश्र, costs आणि data-snooping correction नंतर नकाराकडे झुकलेले (Marshall, Young & Rose "
        "2006; Marshall et al. 2008 Tokyo; Duvinage et al. 2013 - 5-minute DJIA वर 83 पैकी फक्त 5 patterns costs नंतर टिकले, buy-and-hold ला "
        "कोणीही हरवले नाही); लहान positive निकाल मुख्यतः bullish patterns, योग्य trend आणि 2-3 दिवसांच्या holding सह (Caginalp & Laurent 1998; "
        "Lu 2014; Lu, Chen & Hsu 2015). Context मदत करतो: level वरचे location, मोठ्या trend मधला pullback, पुढच्या bar ची confirmation. "
        "RSI/stochastic filters ने SET50 अभ्यासात सुधारणा झाली नाही (Tharavanij et al. 2017). मर्यादा: NIFTY/SENSEX किंवा 15-minute bars वर "
        "एकही rigorous अभ्यास नाही - edge फक्त तुमच्या स्वतःच्या backtest, walk-forward आणि holdout मधूनच दिसू शकते."),
    "reversal_candle": (
        "प्रत्येक खऱ्या reversal मागे एकच order-flow कथा: उशिराचे traders move मध्ये शिरतात, level वर विस्तार नाकारला जातो, आणि दुसरी बाजू "
        "खोल आत close करते - उशिराचे traders अडकतात आणि त्यांचे stops वळण चालवतात. ही शेवटची पायरी नसलेले patterns (harami, matching low) "
        "चाचणीत random निघतात. Platform pattern च्या नावाशिवाय हे वाचतो: level ला touch झाल्यापासूनच्या candles एका composite candle मध्ये "
        "जोडल्या जातात (पहिला open, सर्वोच्च high, सर्वात कमी low, शेवटचा close - Nison चे 'blending'), त्यामुळे engulfing, piercing किंवा "
        "morning star सगळे hammer-सारखे दिसतात. मग touch, level चा reclaim, composite चा आकार अलीकडच्या bars च्या median range च्या "
        "तुलनेत (fixed points नाहीत), स्वतःच्या range मध्ये close कुठे झाला (मध्ये close = अनिश्चितता, follow-through bar हवा), wick आणि "
        "bounce किती वेगाने आला हे तपासले जाते. दोन modes: टप्प्याटप्प्याने composite तपासणी (default) आणि 0-100 score - दोन्ही फक्त बंद "
        "candles वापरतात. मर्यादा: score म्हणजे level वर काय घडले याचे वर्णन, अंदाज नाही; चांगल्या score ची candle NIFTY वर चांगला निकाल देते "
        "हे कोणत्याही स्रोताने दाखवलेले नाही - तुमच्या backtest मध्ये तपासायचा filter म्हणून वापरा."),
    "false_breakout": (
        "Level च्या थोडे पलीकडे stop orders जमा होतात. Sweep म्हणजे level मधून झटकन पलीकडे जाऊन ते stops लावणे आणि लगेच परत येणे - "
        "Wyckoff याला spring (support खाली) किंवा upthrust (resistance वर) म्हणतो; break वर विकणारे traders आता अडकले. खरा break वेगळा: "
        "भाव level पलीकडे अर्थपूर्ण अंतराने close होतो (अलीकडच्या median range किंवा ATR च्या पटीत मोजलेले, fixed points नाहीत), "
        "displacement दिसते - level सोडून जाणारे मोठे bodies - आणि retest वर तिथेच टिकतो (acceptance). Failed breakout उलटा: level पलीकडचा "
        "close काही bars मध्येच परत जातो. Platform हे सगळे फक्त बंद candles वरून ठरवतो, त्यामुळे label नंतर बदलत नाही. मर्यादा: 'stop-hunt' "
        "setups चे प्रसिद्ध win-rates vendors कडून, costs आणि samples शिवाय; एकच move 15 minutes वर sweep आणि daily वर खरा break असू शकतो, "
        "म्हणून timeframe नेहमी सांगा."),
    "gap_trading": (
        "Opening gap म्हणजे आजच्या 09:15 च्या open आणि कालच्या close (PDC) मधले अंतर; full gap मध्ये कालच्या high-low range शी अजिबात "
        "overlap नसतो. NSE वर open हा stocks आणि index futures च्या pre-open call auction मधून ठरतो - options auction मध्ये नसतात, त्यामुळे "
        "gap दिसण्याआधी short option मधून बाहेर पडता येत नाही; strike अंतर, spread width आणि size हेच संरक्षण. Gap चे 'प्रकार' (common, "
        "breakaway, runaway, exhaustion, island) बहुतेक नंतरच कळतात - Bulkowski: 'by the time you properly identify them, the move is nearly "
        "over'. आकडे काय सांगतात: size सर्वात महत्त्वाचा - SPY वर 0.2% पेक्षा लहान gaps त्याच दिवशी ~80-90% वेळा भरतात, 0.4% पेक्षा मोठे "
        "अर्ध्यापेक्षा खूप कमी (practitioner data, rolling windows जे 10-20 points हलतात). Peer-reviewed अभ्यासात equity index मध्ये टिकाऊ "
        "gap fade नाही (Caporale & Plastun 2017: Dow gaps 1-5 दिवसांत फक्त 27-39% भरले); खूप मोठे opening moves अंशतः परत येतात (Fung, Mok "
        "& Lam 2000). भारतात index चा बहुतेक परतावा overnight आला आहे (secondary स्रोत, Capitalmind analysis). NIFTY साठी प्रकाशित, तपासता "
        "येणारा fill तक्ता नाही: एक forum दावा (gap-up ~44%, gap-down ~24% same-day) आणि एक vendor page दिशेबद्दल असहमत; NIFTY 1-minute "
        "data च्या descriptive तपासणीत 0.30% किंवा मोठ्या gaps नी त्याच दिवशी PDC ला ~43-47% वेळा touch केले - हे वर्णन आहे, backtest नाही. "
        "Gap edge ला कमकुवत support/resistance माना आणि पहिल्या bars मध्ये acceptance की rejection ते पाहा."),
    "overfitting_checks": (
        "खूप parameter combinations वापरून पाहिली तर एखादे योगायोगाने छान दिसतेच. म्हणून optimizer combinations फक्त in-sample भागावर "
        "क्रमवारी लावतो, प्रत्येकाचा out-of-sample आकडा दाखवतो पण निवडीसाठी वापरत नाही, आणि दोन तपासण्या देतो. PBO (probability of "
        "backtest overfitting, Bailey, Borwein, Lopez de Prado & Zhu 2015) in-sample दिवसांचे blocks करून विचारतो की in-sample winner दुसऱ्या "
        "अर्ध्यात median च्या खाली किती वेळा येतो - 0.05 पेक्षा जास्त म्हणजे इशारा. Deflated Sharpe ratio (Bailey & Lopez de Prado 2014) "
        "किती combinations वापरली त्यानुसार winner च्या Sharpe मधून योगायोगाचा फायदा वजा करतो. Sealed holdout - operator ने ठरवलेला सर्वात "
        "अलीकडचा काळ - search ला कधीच दिसत नाही; तो एका निवडलेल्या configuration च्या एकाच अंतिम तपासणीसाठी. त्याच module मधली इतर साधने: "
        "त्याच setups मधला random-entry baseline, day-block bootstrap (एका दिवसाचे trades स्वतंत्र नसतात) आणि variants वर White चा Reality "
        "Check. मर्यादा: या तपासण्या पार केल्याने स्वतःला फसवण्याची शक्यता कमी होते; strategy फायदेशीर होत नाही."),
}

CONCEPTS: List[Concept] = [
    Concept("trend", "Trend", "Trend (कल)", ("trend", "uptrend", "downtrend", "ट्रेंड", "कल", "तेजी", "मंदी"),
            "A trend is the market's direction over time: higher highs and higher lows in an uptrend, lower highs and lower lows in a downtrend. "
            "Professionals trade with the trend of the timeframe above the one they trade; this platform reads it with EMA 20/50 and ADX.",
            "Trend म्हणजे market ची दिशा: uptrend मध्ये प्रत्येक high आणि low आधीच्यापेक्षा वर, downtrend मध्ये खाली. Professional trader ज्या timeframe वर trade करतो "
            "त्याच्या वरच्या timeframe च्या trend सोबतच trade घेतो; हा platform तो EMA 20/50 आणि ADX ने ओळखतो.", ("market_structure", "ema", "adx", "regime")),
    Concept("market_structure", "Market structure (HH/HL, BOS, CHoCH)", "Market structure (HH/HL, BOS, CHoCH)",
            ("structure", "bos", "choch", "higher high", "higher low", "स्ट्रक्चर", "structure"),
            "Structure is the sequence of swing highs and lows. HH/HL = uptrend, LH/LL = downtrend. A break of structure (BOS) continues the trend; "
            "a change of character (CHoCH) is the first break against it - an early warning that the trend may be turning.",
            "Structure म्हणजे swing high आणि low ची साखळी. HH/HL = uptrend, LH/LL = downtrend. BOS (break of structure) म्हणजे trend पुढे चालू; "
            "CHoCH (change of character) म्हणजे trend च्या विरुद्ध पहिला break - trend बदलू शकतो याचा पहिला इशारा.", ("trend", "support_resistance")),
    Concept("support_resistance", "Support and resistance", "Support आणि resistance",
            ("support", "resistance", "सपोर्ट", "रेझिस्टन्स", "level", "zone"),
            "Support is a price zone where buyers have repeatedly stepped in; resistance is where sellers have. They are zones, not exact lines. "
            "Stops go just beyond them, and a level that breaks often flips role (old resistance becomes support).",
            "Support म्हणजे जिथे खरेदीदार वारंवार आले तो भावाचा पट्टा; resistance म्हणजे जिथे विक्रेते आले. या रेषा नव्हेत, पट्टे (zone) आहेत. "
            "Stop त्यांच्या थोडा पलीकडे ठेवतात, आणि तुटलेला level अनेकदा भूमिका बदलतो (जुना resistance नवा support).", ("market_structure", "stop_loss", "breakout")),
    Concept("ema", "Moving averages (EMA)", "Moving average (EMA)", ("ema", "sma", "moving average", "मूव्हिंग", "सरासरी", "ईएमए"),
            "An exponential moving average is the average price weighted towards recent bars. Price above a rising EMA = uptrend; EMA 20 above EMA 50 = "
            "short-term trend agrees with the medium one. EMA 200 is the long-term trend filter several strategies here use.",
            "EMA म्हणजे अलीकडच्या candles ना जास्त महत्त्व देणारी सरासरी किंमत. वाढत्या EMA च्या वर भाव = uptrend; EMA 20 हा EMA 50 च्या वर = छोटा आणि मध्यम trend "
            "एकाच दिशेला. EMA 200 हा दीर्घ trend चा filter इथल्या काही strategies वापरतात.", ("trend", "macd", "pullback")),
    Concept("rsi", "RSI", "RSI", ("rsi", "आरएसआय", "overbought", "oversold"),
            "RSI (0-100) measures the strength of recent moves. Above 70 is stretched up (overbought), below 30 stretched down (oversold). "
            "In a strong trend RSI can stay high for long - it is a strength gauge, not a sell signal on its own.",
            "RSI (0-100) अलीकडच्या हालचालीची ताकद मोजतो. 70 च्या वर = जास्त ताणलेला (overbought), 30 च्या खाली = खूप खाली ताणलेला (oversold). "
            "मजबूत trend मध्ये RSI बराच काळ वर राहू शकतो - तो ताकद मोजणारा आहे, फक्त त्यावरून विक्रीचा signal नाही.", ("bollinger", "trend")),
    Concept("macd", "MACD", "MACD", ("macd", "मॅकडी"),
            "MACD is the gap between two EMAs (12 and 26) and its own 9-period average (the signal line). A cross above the signal shows momentum turning up. "
            "On its own it whipsaws; the MACD + EMA strategy here takes the cross only in the direction of the EMA 200 trend.",
            "MACD म्हणजे दोन EMA (12 आणि 26) मधले अंतर आणि त्याची 9-candles ची सरासरी (signal line). Signal च्या वर cross = momentum वर वळतो. "
            "एकट्याने तो खोटे signals देतो; इथली MACD + EMA strategy फक्त EMA 200 च्या trend च्या दिशेनेच cross घेते.", ("ema", "trend")),
    Concept("adx", "ADX", "ADX", ("adx", "di", "trend strength", "ताकद"),
            "ADX measures how strong a trend is, not its direction: below 20 = no trend (range), above 25 = trending. Trend strategies need it high; "
            "mean-reversion strategies prefer it low.",
            "ADX trend किती मजबूत आहे ते मोजतो, दिशा नाही: 20 च्या खाली = trend नाही (range), 25 च्या वर = trend. Trend strategies ना तो जास्त लागतो; "
            "reversion strategies ना कमी.", ("trend", "regime")),
    Concept("atr", "ATR (volatility)", "ATR (अस्थिरता)", ("atr", "volatility", "अस्थिरता", "व्होलॅटिलिटी"),
            "ATR is the average size of a bar's range - how much the market normally moves. Stops closer than about 1 ATR get hit by noise; "
            "this platform refuses them and sizes positions from the stop distance, so a wilder market means fewer shares, not more risk.",
            "ATR म्हणजे एका candle ची सरासरी हालचाल - market साधारण किती हलतो. 1 ATR पेक्षा जवळचा stop गोंगाटात (noise) लागतो; "
            "हा platform तो नाकारतो आणि stop च्या अंतरावरून quantity ठरवतो, म्हणून अस्थिर market मध्ये quantity कमी होते, risk वाढत नाही.", ("stop_loss", "position_sizing", "vix")),
    Concept("vwap", "VWAP", "VWAP", ("vwap", "व्हीवॅप"),
            "VWAP is the day's average price weighted by volume - where the day's business was done. Above VWAP buyers are in control, below it sellers. "
            "It restarts every session; indices have no volume, so here it falls back to the running average price.",
            "VWAP म्हणजे volume नुसार मोजलेली दिवसाची सरासरी किंमत - दिवसाचा व्यवहार कुठे झाला. VWAP च्या वर खरेदीदारांचे वर्चस्व, खाली विक्रेत्यांचे. "
            "तो रोज नव्याने सुरू होतो; index ला volume नसतो म्हणून इथे सरासरी किंमत वापरली जाते.", ("trend", "supertrend")),
    Concept("bollinger", "Bollinger bands", "Bollinger bands", ("bollinger", "बोलिंजर", "band"),
            "Bands two standard deviations around a 20-bar average. A close outside the band is a stretched move; the Bollinger + RSI strategy fades it "
            "only when RSI was also extreme and price closes back inside - a sideways-market tool, not a trend one.",
            "20-candles च्या सरासरीभोवती दोन standard deviation चे पट्टे. पट्ट्याबाहेरचा close म्हणजे ताणलेली हालचाल; Bollinger + RSI strategy RSI टोकाला असताना "
            "आणि भाव परत आत आल्यावरच उलट trade घेते - हे sideways market चे साधन आहे, trend चे नाही.", ("rsi", "regime")),
    Concept("supertrend", "Supertrend", "Supertrend", ("supertrend", "सुपरट्रेंड"),
            "Supertrend is an ATR-based trailing line: below price in an uptrend, above it in a downtrend; a flip changes the direction. "
            "Paired with VWAP here so both must agree before an entry.",
            "Supertrend ही ATR वर आधारित trailing रेषा: uptrend मध्ये भावाच्या खाली, downtrend मध्ये वर; ती पलटली की दिशा बदलते. "
            "इथे ती VWAP सोबत वापरली जाते - entry आधी दोन्ही सहमत हवेत.", ("atr", "vwap", "trend")),
    Concept("stop_loss", "Stop-loss", "Stop-loss", ("stop", "stoploss", "stop-loss", "sl", "स्टॉप", "स्टॉपलॉस"),
            "The price where the trade idea is proven wrong, decided before entry. Place it beyond the structure (swing low, support) and at least 1 ATR away, "
            "never move it against you, and let the platform keep it at the broker (SL-M) so it works even if your screen is off.",
            "Trade ची कल्पना चुकीची ठरते तो भाव - entry आधीच ठरवायचा. तो structure च्या (swing low, support) पलीकडे आणि किमान 1 ATR दूर ठेवा, "
            "विरुद्ध दिशेने कधीच सरकवू नका, आणि platform तो broker कडे (SL-M) ठेवतो त्यामुळे तुमची screen बंद असली तरी काम करतो.", ("position_sizing", "atr", "risk_reward")),
    Concept("position_sizing", "Position sizing", "Position sizing (quantity किती?)", ("size", "sizing", "quantity", "lot", "lots", "किती", "क्वांटिटी", "लॉट"),
            "Quantity = (capital x risk %) / (entry - stop). With 1 lakh at 1% and a 20-point stop, risk is 1,000, so 50 shares. The stop decides the size, "
            "not the conviction - this platform calculates it and floors it to whole lots.",
            "Quantity = (भांडवल x risk %) / (entry - stop). 1 लाख भांडवल, 1% risk आणि 20 points चा stop असेल तर risk ₹1,000, म्हणजे 50 shares. "
            "Size stop ठरवतो, खात्री नाही - हा platform ती मोजतो आणि पूर्ण lots मध्ये ठेवतो.", ("stop_loss", "risk_per_trade")),
    Concept("risk_per_trade", "Risk per trade and daily loss limit", "एका trade चा risk आणि दिवसाची तोटा मर्यादा",
            ("risk", "daily loss", "रिस्क", "जोखीम", "तोटा", "मर्यादा", "capital"),
            "Professionals risk 0.5-1% of capital per trade so a losing streak cannot sink them, and stop for the day after a fixed loss (2-3 losing trades). "
            "Here the daily limit engages the kill switch for the day, and the drawdown ladder halves size, then pauses entries.",
            "Professional एका trade मध्ये भांडवलाच्या 0.5-1% risk घेतात म्हणजे सलग तोटे झाले तरी खाते बुडत नाही, आणि ठराविक तोट्यानंतर (2-3 चुकलेले trades) "
            "त्या दिवशी थांबतात. इथे दिवसाची मर्यादा आली की kill switch लागतो, आणि drawdown ladder आधी size अर्धी करते, मग entries थांबवते.",
            ("position_sizing", "drawdown", "discipline")),
    Concept("risk_reward", "Risk : reward", "Risk : reward", ("risk reward", "r:r", "rr", "reward", "target", "टार्गेट", "रिवॉर्ड"),
            "How much a trade can make for each rupee it risks. At 1:2 you can be right only 34% of the time and still not lose. "
            "This platform skips trades below the minimum R:R and moves the stop to cost once the trade is 1-1.5R in profit.",
            "प्रत्येक रुपयाच्या risk मागे trade किती कमवू शकतो. 1:2 वर फक्त 34% वेळा बरोबर ठरलात तरी तोटा होत नाही. "
            "हा platform किमान R:R पेक्षा कमी trades घेत नाही, आणि trade 1-1.5R नफ्यात आला की stop खरेदी भावावर आणतो.", ("expectancy", "stop_loss")),
    Concept("expectancy", "Win rate, profit factor, expectancy", "Win rate, profit factor, expectancy",
            ("win rate", "profit factor", "expectancy", "विन रेट", "जिंक"),
            "Win rate alone means nothing: expectancy = win% x average win - loss% x average loss. Profit factor = gross profit / gross loss; above 1.3 after costs "
            "is a real edge. Judge any strategy on 30+ trades, never on a few.",
            "फक्त win rate काही सांगत नाही: expectancy = win% x सरासरी नफा - loss% x सरासरी तोटा. Profit factor = एकूण नफा / एकूण तोटा; खर्चानंतर 1.3 पेक्षा जास्त "
            "म्हणजे खरी धार. कोणतीही strategy 30+ trades वरच तपासा, थोड्यांवर नाही.", ("risk_reward", "paper_trading", "backtest")),
    Concept("drawdown", "Drawdown", "Drawdown", ("drawdown", "ड्रॉडाउन", "loss streak", "सलग तोटे"),
            "The fall from the account's peak. A 10% drawdown needs 11% to recover, 50% needs 100% - which is why size is cut as the drawdown grows. "
            "Here: 5% below the peak halves risk per trade, 10% pauses new entries until reviewed (tighter for beginners).",
            "खात्याच्या सर्वोच्च पातळीपासूनची घसरण. 10% drawdown भरून काढायला 11% लागतात, 50% साठी 100% - म्हणून drawdown वाढताना size कमी करतात. "
            "इथे: peak पासून 5% खाली = risk अर्धा, 10% खाली = तपासणी होईपर्यंत नवीन entries बंद (नवशिक्यासाठी याहून कडक).", ("risk_per_trade", "discipline")),
    Concept("discipline", "Discipline and revenge trading", "शिस्त आणि revenge trading",
            ("discipline", "revenge", "overtrading", "emotion", "fear", "greed", "शिस्त", "भीती", "लोभ", "रिव्हेंज", "भावना"),
            "Most beginner losses come from behaviour, not strategy: doubling size to win back a loss, trading out of boredom, moving stops. "
            "The platform enforces a cool-down after a stop, a daily loss limit and a losing-streak pause so the rules hold when emotions don't.",
            "नवशिक्यांचे बहुतेक तोटे strategy मुळे नाही तर वागण्यामुळे होतात: तोटा भरून काढायला size दुप्पट, कंटाळ्याने trades, stop सरकवणे. "
            "म्हणून platform stop नंतर थांबा (cool-down), दिवसाची तोटा मर्यादा आणि सलग तोट्यानंतरचा pause लागू करतो - भावना ढळल्या तरी नियम टिकतात.", ("risk_per_trade", "drawdown")),
    Concept("regime", "Market regime", "Market ची स्थिती (regime)", ("regime", "sideways", "range", "ranging", "choppy", "रेंज", "साईडवेज", "स्थिती"),
            "The market's mood: trending up/down, ranging (sideways), volatile or quiet. Trend strategies lose in ranges and reversion strategies lose in trends, "
            "so each deployment here can enter only in the regimes it suits - a day with no trade is often the right outcome.",
            "Market चा मूड: वर/खाली trend, sideways (range), अस्थिर किंवा शांत. Trend strategies range मध्ये हरतात आणि reversion strategies trend मध्ये, "
            "म्हणून इथे प्रत्येक deployment फक्त त्याला अनुकूल स्थितीतच entry घेते - trade न होणारा दिवस अनेकदा योग्यच असतो.", ("trend", "adx", "bollinger")),
    Concept("pullback", "Pullback", "Pullback", ("pullback", "dip", "पुलबॅक", "retracement"),
            "A temporary move against the trend. Buying a pullback in an uptrend gives a better price and a tighter, logical stop (below the pullback low) "
            "than chasing a breakout - the idea behind the MTF and swing EMA pullback strategies.",
            "Trend च्या विरुद्ध तात्पुरती हालचाल. Uptrend मधल्या pullback वर खरेदी केल्याने breakout मागे धावण्यापेक्षा चांगला भाव आणि जवळचा, तर्कशुद्ध stop "
            "(pullback च्या low खाली) मिळतो - MTF आणि swing EMA pullback strategies ची हीच कल्पना.", ("trend", "ema", "breakout")),
    Concept("breakout", "Breakout", "Breakout", ("breakout", "ब्रेकआउट", "orb", "opening range", "52 week"),
            "A close beyond a range or level that held before, ideally with rising volume and a trending ADX. Many breakouts fail (fakeouts), "
            "so wait for the close, confirm with volume and keep the stop back inside the old range.",
            "आधी टिकलेल्या range किंवा level च्या पलीकडचा close, शक्यतो वाढत्या volume आणि ADX सोबत. अनेक breakouts फसतात (fakeout), "
            "म्हणून close ची वाट पाहा, volume ने खात्री करा आणि stop जुन्या range मध्ये परत ठेवा.", ("support_resistance", "adx", "pullback")),
    Concept("options_basics", "Options: CE, PE, ITM / ATM / OTM", "Options: CE, PE, ITM / ATM / OTM",
            ("option", "options", "call", "put", "ce", "pe", "itm", "atm", "otm", "strike", "ऑप्शन", "कॉल", "पुट", "स्ट्राइक"),
            "A call (CE) gains when the underlying rises, a put (PE) when it falls. ATM = strike at the price, ITM = already in profit by its strike, "
            "OTM = not yet. Buying caps the loss at the premium paid; ITM options move closer to the underlying and decay slower than OTM ones.",
            "Call (CE) underlying वर गेला की फायदा, put (PE) खाली गेला की. ATM = सध्याच्या भावाचा strike, ITM = strike नुसार आधीच नफ्यात, OTM = अजून नाही. "
            "खरेदी केल्यावर तोटा भरलेल्या premium इतकाच; ITM options underlying च्या जवळून हलतात आणि OTM पेक्षा हळू झिजतात.", ("theta", "delta", "option_selling")),
    Concept("theta", "Theta (time decay)", "Theta (वेळेनुसार घट)", ("theta", "time decay", "थीटा", "decay", "expiry", "एक्सपायरी"),
            "An option loses value every day it is held, faster near expiry - theta. A bought weekly option on expiry day can lose most of its value in hours. "
            "That is why the interview picks the next weekly expiry for beginners and the monthly one for swing trades.",
            "Option दररोज मूल्य गमावतो, expiry जवळ आल्यावर जास्त वेगाने - हा theta. Expiry च्या दिवशी विकत घेतलेला weekly option काही तासांत बहुतेक मूल्य गमावू शकतो. "
            "म्हणूनच मुलाखत नवशिक्यासाठी पुढची weekly आणि swing साठी monthly expiry निवडते.", ("options_basics", "delta")),
    Concept("delta", "Delta and the Greeks", "Delta आणि Greeks", ("delta", "gamma", "vega", "greeks", "डेल्टा", "ग्रीक्स"),
            "Delta = how much the option moves per 1-point move of the underlying (ATM about 0.5, deep ITM near 1). Gamma = how fast delta changes, "
            "vega = sensitivity to implied volatility, theta = time decay. The Positions page shows them per option position.",
            "Delta = underlying 1 point हलल्यावर option किती हलतो (ATM सुमारे 0.5, खोल ITM जवळपास 1). Gamma = delta किती वेगाने बदलतो, "
            "vega = implied volatility चा परिणाम, theta = वेळेनुसार घट. Positions page प्रत्येक option position साठी हे दाखवतो.", ("theta", "iv")),
    Concept("iv", "Implied volatility (IV)", "Implied volatility (IV)", ("iv", "implied volatility", "आयव्ही", "premium expensive"),
            "IV is the volatility the option price assumes. High IV = expensive premiums (good for sellers, risky for buyers: IV can fall after the event and "
            "the option loses value even if you were right on direction).",
            "IV म्हणजे option च्या किमतीत गृहीत धरलेली अस्थिरता. जास्त IV = महाग premium (विक्रेत्यांना अनुकूल, खरेदीदारांना धोकादायक: event नंतर IV पडतो आणि "
            "दिशा बरोबर असली तरी option चे मूल्य घटू शकते).", ("vix", "options_basics", "delta")),
    Concept("option_selling", "Option selling and spreads", "Option selling आणि spreads", ("sell option", "option selling", "writing", "spread", "condor", "straddle", "strangle", "सेलिंग", "स्प्रेड"),
            "Selling an option earns the premium and theta but the loss is open-ended unless hedged. Spreads (bull put, bear call, iron condor) buy a further "
            "option to cap the worst case. The platform sizes structures on their maximum loss and does not offer naked selling to beginners.",
            "Option विकल्यावर premium आणि theta मिळतो पण hedge नसेल तर तोटा अमर्याद. Spreads (bull put, bear call, iron condor) पुढचा एक option विकत घेऊन "
            "सर्वात वाईट तोटा मर्यादित करतात. Platform structures चा size त्यांच्या कमाल तोट्यावरून ठरवतो आणि नवशिक्यांना naked selling देत नाही.", ("options_basics", "theta", "iv")),
    Concept("vix", "India VIX", "India VIX", ("vix", "india vix", "fear", "भीती", "व्हिक्स"),
            "India VIX is the market's fear gauge from NIFTY option prices: below 12 very calm, 12-16 normal, 16-20 elevated, above 20 high fear with big gaps likely. "
            "The market memory tracks it and the plan warns beginners to paper-trade or watch when it is 20+.",
            "India VIX हा NIFTY options वरून मोजलेला market चा भीतीमापक: 12 खाली खूप शांत, 12-16 सामान्य, 16-20 वाढलेला, 20 वर जास्त भीती आणि मोठे gap शक्य. "
            "Market चा साठा तो पाहत असतो आणि 20+ असताना plan नवशिक्यांना PAPER किंवा फक्त निरीक्षणाचा सल्ला देतो.", ("iv", "atr", "gap_risk")),
    Concept("intraday_swing", "Intraday vs swing (MIS, CNC, NRML)", "Intraday विरुद्ध swing (MIS, CNC, NRML)",
            ("intraday", "swing", "mis", "cnc", "nrml", "delivery", "overnight", "इंट्राडे", "स्विंग", "डिलिव्हरी", "रात्रभर"),
            "Intraday (MIS) positions are closed the same day, on leverage. Swing positions are held for days: CNC (delivery) for shares, NRML for F&O. "
            "Swing trades daily candles, takes fewer trades and carries gap risk, so its risk per trade is sized smaller here.",
            "Intraday (MIS) positions त्याच दिवशी बंद होतात, leverage सोबत. Swing positions काही दिवस धरल्या जातात: shares साठी CNC (delivery), F&O साठी NRML. "
            "Swing daily candles वर चालते, trades कमी आणि gap चा धोका असतो, म्हणून इथे त्याचा risk per trade कमी ठेवला जातो.", ("gap_risk", "position_sizing")),
    Concept("gap_risk", "Gap risk", "Gap चा धोका", ("gap", "gap up", "gap down", "गॅप"),
            "A gap is an open far from the previous close, usually after overnight news. A stop cannot fill inside a gap - the exit happens at the open, beyond the stop. "
            "Overnight positions therefore use smaller size, and results/events days are flagged in the event calendar.",
            "Gap म्हणजे आधीच्या close पासून खूप दूर उघडलेला भाव, सहसा रात्रीच्या बातमीनंतर. Gap मध्ये stop लागत नाही - exit open ला, stop च्या पलीकडे होतो. "
            "म्हणून रात्रभरच्या positions चा size कमी ठेवतात, आणि results/event चे दिवस event calendar मध्ये चिन्हांकित असतात.", ("intraday_swing", "vix")),
    Concept("paper_trading", "Paper trading", "Paper trading", ("paper", "demo", "practice", "पेपर", "सराव"),
            "Trading the strategy with simulated money on live data. It tests the rules and your discipline without risk; on this platform every new "
            "strategy goes backtest -> paper (2 weeks / 30+ trades) -> small LIVE through the Go-Live checklist.",
            "खऱ्या data वर खोट्या पैशाने strategy चालवणे. धोका न घेता नियम आणि तुमची शिस्त तपासली जाते; या platform वर प्रत्येक नवीन strategy "
            "backtest -> paper (2 आठवडे / 30+ trades) -> Go-Live checklist नंतर छोट्या रकमेने LIVE अशी जाते.", ("backtest", "expectancy")),
    Concept("backtest", "Backtesting", "Backtesting", ("backtest", "bactest", "बॅकटेस्ट", "history", "historical"),
            "Running the strategy's exact rules over past candles to see its trades. Useful to reject bad ideas; dangerous when over-tuned to the past "
            "(over-fitting). Walk-forward and Monte Carlo on the Backtest page check how robust a result is.",
            "Strategy चे नियम जुन्या candles वर चालवून trades पाहणे. वाईट कल्पना नाकारायला उपयोगी; भूतकाळाला जास्त जुळवले (over-fitting) तर धोकादायक. "
            "Backtest page वरचे walk-forward आणि Monte Carlo निकाल किती टिकाऊ आहे ते तपासतात.", ("paper_trading", "expectancy")),
    Concept("pcr_oi", "Open interest and PCR", "Open interest आणि PCR", ("oi", "open interest", "pcr", "put call ratio", "max pain", "ओपन इंटरेस्ट"),
            "Open interest = option contracts outstanding at a strike. Big call OI often marks resistance, big put OI support. PCR (put OI / call OI) above 1 "
            "leans bullish, below 0.7 bearish - a sentiment read, not a timing signal.",
            "Open interest = एखाद्या strike वर उघडे असलेले option contracts. मोठा call OI अनेकदा resistance, मोठा put OI support दाखवतो. PCR (put OI / call OI) "
            "1 च्या वर तेजीकडे, 0.7 च्या खाली मंदीकडे झुकतो - हे मनःस्थिती दाखवते, entry ची वेळ नाही.", ("support_resistance", "options_basics")),
    Concept("global_cues", "Global cues", "जागतिक संकेत",
            ("global", "global cues", "gift nifty", "sgx", "us market", "us futures", "america", "dow", "nasdaq", "s&p", "crude", "brent", "oil", "dollar",
             "rupee", "usdinr", "usd/inr", "fii", "asia", "nikkei", "जागतिक", "अमेरिका", "अमेरिकन", "कच्चे तेल", "क्रूड", "डॉलर", "रुपया", "गिफ्ट निफ्टी"),
            "What the world did overnight sets the mood of the Indian open: US index futures and Asian markets up or down usually show in the NIFTY open; "
            "costlier crude hurts India (it imports most of its oil) - oil marketers, paints, airlines and the rupee; a weaker rupee or a stronger dollar "
            "tends to make foreign investors (FII) sell. These are tendencies for the first minutes, not signals - the opening range still decides. "
            "The market memory reads them from free, delayed public data (GIFT Nifty has no free reliable source; US futures stand in for it).",
            "रात्री जगात काय घडले त्यावरून भारतीय बाजार उघडण्याचा मूड ठरतो: US index futures आणि आशियाई बाजार वर-खाली असतील तर NIFTY च्या open मध्ये ते सहसा दिसते; "
            "कच्चे तेल महाग झाले तर भारताला त्रास (भारत बहुतेक तेल आयात करतो) - OMC, paint, airline आणि रुपया; रुपया कमजोर किंवा डॉलर मजबूत झाला तर "
            "परदेशी गुंतवणूकदार (FII) विक्री करण्याची शक्यता. ही पहिल्या काही मिनिटांची प्रवृत्ती आहे, signal नाही - opening range च शेवटी ठरवते. "
            "Market चा साठा ही माहिती मोफत, उशिराच्या सार्वजनिक data वरून घेतो (GIFT Nifty साठी मोफत भरवशाचा source नाही; त्याऐवजी US futures).",
            ("gap_risk", "vix", "trend")),
    # Trade port (E): teaching entries from the "Candlestick patterns and psychology" and "Gap trading" research reports
    # (Oct 2026), written in English; the Marathi body (`MR_BODIES`) serves users whose AI answer language is Marathi.
    *[Concept(cid, title, title, kw, body, MR_BODIES[cid], rel) for cid, title, kw, body, rel in (
        ("candlestick_patterns", "Candlestick patterns: psychology and evidence",
         ("candlestick", "candle pattern", "hammer", "doji", "engulfing", "shooting star", "morning star", "evening star", "harami",
          "pin bar", "marubozu", "कँडल", "कॅण्डल", "पॅटर्न"),
         "Each candle is the result of one fight between buyers and sellers: the open is where it started, the high and low how far each "
         "side pushed, and the close who was in control at the end. A long lower wick means sellers pushed price down and buyers took it "
         "back - a rejection. What the evidence says: on its own a named pattern is close to a coin toss. In Bulkowski's practitioner data "
         "(US daily stocks, about 4.7 million candles, no costs, no significance tests) most single candles land at 50-60% - hammer about 60%, "
         "doji types 50-52% - and some 'classic' patterns behave the opposite way (hanging man is a bullish continuation about 59% of the "
         "time). Peer-reviewed studies are mixed and lean negative after costs and data-snooping corrections (Marshall, Young & Rose 2006 on "
         "the DJIA; Marshall et al. 2008 on Tokyo; Duvinage et al. 2013 on 5-minute DJIA bars, where only 5 of 83 patterns survived costs and "
         "none beat buy-and-hold); small positive results appear mostly for bullish patterns, with the right prior trend and 2-3 day holds "
         "(Caginalp & Laurent 1998; Lu 2014; Lu, Chen & Hsu 2015). Context helps: location at a level, a pullback inside a larger trend, "
         "confirmation by the next bar. RSI / stochastic filters did not help in the SET50 study (Tharavanij et al. 2017). Limits: no "
         "rigorous study exists for NIFTY / SENSEX or 15-minute bars - only your own backtest, walk-forward and holdout can show an edge.",
         ("reversal_candle", "support_resistance", "trend", "backtest")),
        ("reversal_candle", "Reversal candle (how this platform scores it)",
         ("reversal candle", "rejection", "reclaim", "wick", "blending", "composite candle", "follow-through", "close location", "trapped"),
         "Behind every real reversal pattern is one order-flow story: late traders join the move, the extension is rejected at a level, and "
         "the other side closes deep back inside, trapping the late traders whose stops then drive the turn. Patterns without that last "
         "step (harami, matching low) are the ones that test as random. The platform reads this without pattern names: the candles since "
         "the level was touched are blended into one composite candle (first open, highest high, lowest low, last close - Nison's "
         "'blending'), so an engulfing, a piercing line or a morning star all become a hammer-like shape. It then checks the touch, the "
         "reclaim of the level, the size of the composite against the median range of recent bars (no fixed points), where it closed inside "
         "its own range (a close in the middle is indecision and needs a follow-through bar), the wick, and whether the bounce came fast. "
         "Two modes exist - a step-by-step composite check (default) and a 0-100 score - and both only use closed candles. Limits: the score "
         "is a description of what happened at a level, not a forecast; no source has shown that a better-scored candle predicts better "
         "outcomes on NIFTY, so treat it as a filter to validate in your own backtest.",
         ("candlestick_patterns", "false_breakout", "support_resistance")),
        ("false_breakout", "False breakout, liquidity sweep and real break",
         ("false breakout", "false break", "fake breakout", "liquidity sweep", "stop hunt", "spring", "upthrust", "real break", "acceptance",
          "failed breakout", "स्वीप", "फेक"),
         "A level attracts stop orders just beyond it. A sweep is a quick push through the level that triggers those stops and comes straight "
         "back - Wyckoff calls it a spring (below support) or an upthrust (above resistance); the traders who sold the break are now trapped. "
         "A real break is different: price closes beyond the level by a meaningful distance (measured in multiples of the recent median range "
         "or ATR, not fixed points), shows displacement - large bodies leaving the level - and holds there on a retest (acceptance). A failed "
         "breakout is the opposite case: the close beyond the level is given back within a few bars. The platform labels each of these from "
         "closed candles only, so a label never changes after the fact. Limits: published win-rates for 'stop-hunt' setups come from vendors "
         "without costs or samples; the same move can be a sweep on 15 minutes and a real break on the daily chart, so name the timeframe.",
         ("support_resistance", "reversal_candle", "market_structure", "breakout")),
        ("gap_trading", "Opening gaps: types, fill statistics and limits",
         ("gap trading", "gap fill", "gap up", "gap down", "opening gap", "breakaway gap", "exhaustion gap", "runaway gap", "island reversal",
          "fvg", "fair value gap", "pdc", "गॅप"),
         "An opening gap is the distance between today's 09:15 open and yesterday's close (PDC); a full gap leaves no overlap with "
         "yesterday's high-low range. On NSE the open comes from a pre-open call auction for stocks and index futures - options are not in "
         "the auction, so a short option cannot be exited before the gap prints; strike distance, spread width and size are the only "
         "protection. Gap 'types' (common, breakaway, runaway, exhaustion, island) are mostly labelled in hindsight - Bulkowski: 'by the time "
         "you properly identify them, the move is nearly over'. What the numbers say: size matters most - on SPY gaps under 0.2% fill the same "
         "day about 80-90% of the time while gaps above 0.4% fill well under half (practitioner data, rolling windows that move 10-20 points). "
         "Peer-reviewed work finds no robust gap fade in equity indices (Caporale & Plastun 2017: Dow gaps filled within 1-5 days only 27-39% "
         "of the time); very large opening moves partly reverse (Fung, Mok & Lam 2000). In India most index return has come overnight "
         "(secondary source, Capitalmind analysis). For NIFTY there is no published, verifiable fill table: a forum claim (gap-up ~44%, "
         "gap-down ~24% same-day) and a vendor page disagree on direction; a descriptive check of NIFTY 1-minute data found that gaps of "
         "0.30% or more touched PDC the same day about 43-47% of the time - descriptive, not a backtest. Treat gap edges as weak "
         "support/resistance and wait for acceptance or rejection in the first bars.",
         ("gap_risk", "global_cues", "false_breakout", "intraday_swing")),
        ("overfitting_checks", "Overfitting checks: PBO, deflated Sharpe and the holdout",
         ("overfitting", "over-fitting", "pbo", "deflated sharpe", "dsr", "holdout", "out of sample", "out-of-sample", "data snooping",
          "reality check", "walk forward", "bootstrap", "random entry"),
         "Try enough parameter combinations and one will look great by chance. The optimizer therefore ranks combinations on the in-sample part "
         "only, shows each one's out-of-sample figure without using it to pick, and reports two checks. PBO (probability of backtest "
         "overfitting, Bailey, Borwein, Lopez de Prado & Zhu 2015) cuts the in-sample days into blocks and asks how often the in-sample winner "
         "ranks below the median on the other half - above 0.05 is a warning. The deflated Sharpe ratio (Bailey & Lopez de Prado 2014) "
         "discounts the winner's Sharpe for the number of combinations tried. A sealed holdout - the most recent period, set by the operator - "
         "is never seen by the search and is meant for one final check of one chosen configuration. Other tools in the same module: a "
         "random-entry baseline in the same setups, a day-block bootstrap (trades on one day are not independent) and White's Reality Check "
         "across variants. Limits: passing these checks lowers the chance of fooling yourself; it does not make a strategy profitable.",
         ("backtest", "expectancy", "paper_trading")),
    )],
]
BY_ID: Dict[str, Concept] = {c.id: c for c in CONCEPTS}

_TOKEN = re.compile(r"[a-z0-9:+\-]+|[ऀ-ॿ]+")
GLOBAL_WORDS = ("global", "gift", "sgx", "us market", "us futures", "america", "dow", "nasdaq", "s&p", "crude", "brent", "oil", "dollar", "rupee",
                "usdinr", "usd/inr", "fii", "nikkei", "hang seng", "asia", "जागतिक", "अमेरिका", "अमेरिकन", "कच्चे तेल", "क्रूड", "डॉलर", "रुपया", "गिफ्ट")
MARKET_WORDS = ("today", "now", "market", "trend", "आज", "आत्ता", "सध्या", "कल", "बाजार", "काय चाललंय", "why no trade", "trade का नाही", "का नाही")


def _score(question: str, concept: Concept) -> float:
    q = question.lower()
    tokens = set(_TOKEN.findall(q))
    score = 0.0
    for kw in concept.keywords:
        k = kw.lower()
        if " " in k or not k.isascii():
            if k in q:
                score += 2.0 + 0.1 * len(k)
        elif k in tokens:
            score += 2.0
    if concept.en.lower() in q or concept.mr.lower() in q:
        score += 1.5
    return score


def find_concepts(question: str, limit: int = 2) -> List[Concept]:
    scored = sorted(((s, c) for c in CONCEPTS if (s := _score(question, c)) > 0), key=lambda x: -x[0])
    if not scored:
        return []
    top = scored[0][0]
    return [c for s, c in scored[:limit] if s >= top * 0.6]


def _market_answer(lang: str, question: str, memory: Optional[dict]) -> Optional[str]:
    """A "what is X doing today" question about a symbol the market memory watches."""
    if not memory or not memory.get("symbols"):
        return None
    q = question.upper()
    q_low = question.lower()
    if not any(w in q_low for w in MARKET_WORDS):
        return None
    aliases = {"BANK NIFTY": "NIFTY BANK", "BANKNIFTY": "NIFTY BANK", "बँक निफ्टी": "NIFTY BANK", "निफ्टी": "NIFTY 50", "NIFTY": "NIFTY 50"}
    wanted = None
    for snap in memory["symbols"]:
        if snap["symbol"] in q:
            wanted = snap["symbol"]
            break
    if wanted is None:
        for alias, sym in aliases.items():
            if alias in q or alias in question:
                wanted = sym
                break
    snap = next((s for s in memory["symbols"] if s["symbol"] == wanted), None) if wanted else None
    if snap is None:
        return None
    from app.ai import market_memory
    bias = {"BULLISH": tr(lang, "bullish", "तेजीचा"), "BEARISH": tr(lang, "bearish", "मंदीचा"), "NEUTRAL": tr(lang, "neutral", "तटस्थ")}.get(snap["bias"] or "", "-")
    lines = [tr(lang, f"{wanted} right now: {bias} bias, regime {snap['regime']} (higher timeframe {snap['higher_regime']}), structure {snap['structure']}, "
                      f"{(snap['change_pct'] or 0):+.2f}% on the day at {snap['last_price']}.",
                f"{wanted} सध्या: कल {bias}, स्थिती {snap['regime']} (मोठा timeframe {snap['higher_regime']}), structure {snap['structure']}, "
                f"दिवसात {(snap['change_pct'] or 0):+.2f}%, भाव {snap['last_price']}.")]
    if snap["regime"] in ("RANGING", "QUIET"):
        lines.append(tr(lang, "In a sideways market trend strategies sit out on purpose - no trade is the disciplined result; reversion setups fit better.",
                        "Sideways market मध्ये trend strategies मुद्दाम trade घेत नाहीत - trade न होणे ही शिस्तच आहे; reversion setups जास्त जुळतात."))
    elif snap["regime"] == "VOLATILE":
        lines.append(tr(lang, "Volatile: wider stops mean smaller size; beginners do better watching than trading such days.",
                        "अस्थिर: stop मोठे म्हणजे size लहान; अशा दिवशी नवशिक्यांनी trade करण्यापेक्षा पाहणे चांगले."))
    lines += market_memory.describe(lang, memory, wanted)[:3]
    return " ".join(lines)


def _global_answer(lang: str, question: str, memory: Optional[dict]) -> Optional[str]:
    """A question about the world's markets, answered from the global cues in the market memory."""
    if not memory or not memory.get("globals"):
        return None
    q_low = question.lower()
    tokens = set(_TOKEN.findall(q_low))
    if not any((w in tokens) if w.isalpha() and w.isascii() else (w in q_low) for w in GLOBAL_WORDS):
        return None
    from app.ai import global_cues
    lines = global_cues.view(lang, memory["globals"])
    if not lines:
        return None
    quotes = ", ".join(f"{tr(lang, global_cues.BY_KEY[g['symbol']].en, global_cues.BY_KEY[g['symbol']].mr)} {g['change_pct']:+.2f}%"
                       for g in memory["globals"] if g["symbol"] in global_cues.BY_KEY and g.get("change_pct") is not None)
    if quotes:
        lines.append(tr(lang, f"Latest: {quotes}.", f"ताजे आकडे: {quotes}."))
    lines.append(tr(lang, f"({global_cues.SOURCE_NOTE_EN}; tendencies, not signals.)", f"({global_cues.SOURCE_NOTE_MR}; ही प्रवृत्ती आहे, signal नाही.)"))
    return " ".join(lines)


def answer(question: str, lang: str = "en", memory: Optional[dict] = None) -> dict:
    """The library's answer: market memory first for a market or world-markets question, then the
    matching concepts."""
    lang = "mr" if lang == "mr" else "en"
    concepts = find_concepts(question)
    market = " ".join(x for x in (_global_answer(lang, question, memory), _market_answer(lang, question, memory)) if x) or None
    parts: List[str] = []
    if market:
        parts.append(market)
    for c in concepts:
        parts.append(f"{tr(lang, c.en, c.mr)}: {tr(lang, c.body_en, c.body_mr)}")
    if not parts:
        parts.append(tr(lang, "I could not match that to a concept yet. Try a word like RSI, stop-loss, risk, theta, support, VIX, swing - or ask what a symbol is doing today.",
                        "हा प्रश्न अजून कोणत्याही संकल्पनेशी जुळवता आला नाही. RSI, stop-loss, risk, theta, support, VIX, swing असा शब्द वापरा - किंवा एखादा symbol आज काय करतो आहे ते विचारा."))
    related_ids = [r for c in concepts for r in c.related if r not in {x.id for x in concepts}]
    related = [{"id": r, "title": tr(lang, BY_ID[r].en, BY_ID[r].mr)} for r in dict.fromkeys(related_ids) if r in BY_ID][:5]
    return {"answer": "\n\n".join(parts), "source": "library", "concepts": [c.as_dict(lang) for c in concepts], "related": related,
            "used_market_memory": bool(market)}


GUIDE_PROMPT = """You are the trading guide inside AMW Algorithmic Trading - a patient, experienced Indian market professional teaching a
trader who may be a beginner. Answer in {language}, in plain words, in at most 180 words. Use the CONCEPT NOTES and MARKET MEMORY below as
your facts; if they do not cover the question, say what you can from general trading knowledge and keep it educational.
Rules: never tell the trader to buy or sell a specific security or promise profit; explain the why; always include the risk side; mention how
this platform handles it when the notes say so (sizing from the stop, daily loss limit, regime filter, paper first). Every number and every
symbol you write must come from the MARKET MEMORY, the CONCEPT NOTES or the question - no other prices, levels or tickers.
=== TRADER ===
{profile}
=== MARKET MEMORY ===
{memory}
=== CONCEPT NOTES ===
{notes}"""


PROMPT_VERSION = prompt_versions.version_of("guide", GUIDE_PROMPT)      # H-C1 f


def ai_context(lang: str, concepts: List[Concept], memory: Optional[dict], profile: Optional[dict]) -> str:
    notes = "\n".join(f"- {c.en}: {c.body_en}" for c in concepts) or "- (none matched)"
    mem_lines = []
    for s in (memory or {}).get("symbols", [])[:6]:
        mem_lines.append(f"- {s['symbol']}: bias {s['bias']}, regime {s['regime']}, higher {s['higher_regime']}, structure {s['structure']}, day {s['change_pct']}%")
    for c in (memory or {}).get("cues", [])[:4]:
        mem_lines.append(f"- {c['symbol']}: {c['last_price']} ({c['change_pct']}% on the day)")
    for g in (memory or {}).get("globals", [])[:11]:
        mem_lines.append(f"- global {g['symbol']}: {g['last_price']} ({g['change_pct']}% on the day; free delayed data)")
    prof = "unknown" if not profile else ", ".join(f"{k}={v}" for k, v in profile.items() if k in ("experience", "style", "risk", "vehicle", "symbol", "goal"))
    return GUIDE_PROMPT.format(language=tr(lang, "English", "Marathi (Devanagari script)"), profile=prof,
                               memory="\n".join(mem_lines) or "- (no market memory yet)", notes=notes)


async def ai_answer(provider, question: str, lang: str, memory: Optional[dict], profile: Optional[dict]) -> dict:
    """A grounded answer from the tenant's AI provider; the library answer on any failure."""
    base = answer(question, lang, memory)
    concepts = [BY_ID[c["id"]] for c in base["concepts"] if c["id"] in BY_ID]
    system = ai_context(lang, concepts, memory, profile)
    prompt_versions.stamp(provider, PROMPT_VERSION)
    try:
        text = (await provider.complete(system, f"QUESTION:\n{question.strip()}", max_tokens=1200)).strip()
    except Exception as exc:  # noqa: BLE001 - the library answer is always there
        base["note"] = f"AI provider unavailable ({type(exc).__name__}); answered from the concept library"
        return base
    if not text:
        return base
    # P0.8 / B2: numbers and symbols only from the memory, the concept notes and the question - else the library answer.
    trusted = system + "\n" + question
    allowed = grounding.numbers_in_values(memory or {}) | grounding.allowed_from_text(trusted)
    ok, bad = grounding.check_numbers(text, allowed)
    if ok:
        ok, bad = grounding.check_tickers(text, trusted)
    if ok:
        ok, bad = wording.check_wording(text)      # ATP review 11: banned words -> the library answer
    if not ok:
        base["note"] = f"AI answer failed the grounding check ({', '.join(bad[:5])}); answered from the concept library"
        return base
    ok, shown, why = output_filter.check(text, lang, where="knowledge")             # H-C1 c
    if not ok:
        base["note"] = f"AI answer not used ({why}); answered from the concept library"
        return base
    return {**base, "answer": shown, "source": "ai"}


def catalogue(lang: str) -> List[dict]:
    return [{"id": c.id, "title": tr(lang, c.en, c.mr)} for c in CONCEPTS]


__all__ = ["CONCEPTS", "answer", "ai_answer", "find_concepts", "catalogue"]
