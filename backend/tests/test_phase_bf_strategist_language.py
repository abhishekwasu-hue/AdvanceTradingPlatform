"""Phase BF: the strategist in words - rules in words, direction and timeframe words, a one-line summary, and plain-words
requests parsed into symbol / style / direction (API: /strategist/parse, and `request` on /study and /build).

English only (the owner's ruling): the words are English; a request or a `language` asking for Marathi still gets English.
Non-English text here is written with \\u escapes (no Devanagari in the source - scripts/check_english_only.py)."""
import re

from app.ai import market_study as ms
from app.ai import strategist as st
from app.ai.strategist import cond, ind, val
from tests.test_auth_api import _register, client
from tests.test_phase_ap_interview import _sessions
from tests.test_phase_aw_strategist import _bars

DEVANAGARI = re.compile("[\u0900-\u097f]")
MR_BANK_NIFTY_LONG = "\u092c\u0901\u0915 \u0928\u093f\u092b\u094d\u091f\u0940 \u092b\u0915\u094d\u0924 long scalping"   # "bank nifty only long scalping"


def test_rules_in_words_cover_every_operand_family():
    c1 = cond(ind("CLOSE"), "CROSSES_ABOVE", ind("OR_HIGH", 15))
    assert st.rule_words("en", c1) == "close crosses above opening-range high (15 min)"
    assert st.rule_words("mr", c1) == st.rule_words("en", c1)                   # an older "mr" caller gets English
    c2 = cond(ind("EMA", 9), "GT", ind("EMA", 50, tf="15min"))
    assert st.rule_words("en", c2) == "EMA(9) above EMA(50) on 15-minute candles"
    c3 = cond(ind("LOW"), "LT", ind("BB_LOWER", 20, mult=2.5))
    assert "lower Bollinger band (20, 2.5σ)" in st.rule_words("en", c3)
    c4 = cond(ind("RSI", 14), "CROSSES_BELOW", val(55))
    assert st.rule_words("en", c4) == "RSI(14) crosses below 55"
    c5 = cond(ind("CLOSE"), "GTE", ind("PDH"))
    assert st.rule_words("en", c5) == "close at or above yesterday's high"
    c6 = cond(ind("CLOSE"), "LTE", ind("SUPERTREND", 10, mult=3.0))
    assert st.rule_words("en", c6) == "close at or below Supertrend(10, 3)"
    for name in st.OPERAND_WORDS:                       # every word table entry formats without KeyError, in English
        assert not DEVANAGARI.search(st.operand_words("en", ind(name, 20, mult=2.0)) + st.operand_words("en", ind(name, 20, tf="60min")))


def test_parse_request_reads_english_requests():
    p = st.parse_request("banknifty long only scalping")
    assert (p["symbol"], p["style"], p["direction"], p["language"]) == ("NIFTY BANK", "scalping", "long", "en") and set(p["matched"]) == {"symbol", "style", "direction"}
    p = st.parse_request("RELIANCE intraday both sides")
    assert (p["symbol"], p["style"], p["direction"], p["language"]) == ("RELIANCE", "intraday", "both", "en")
    p = st.parse_request("nifty bearish 5 min")
    assert (p["symbol"], p["style"], p["direction"]) == ("NIFTY 50", "intraday", "short")
    p = st.parse_request("short only on finnifty")
    assert p["symbol"] == "NIFTY FIN SERVICE" and p["direction"] == "short" and "style" not in p["matched"]
    p = st.parse_request("sensex bullish")
    assert p["symbol"] == "SENSEX" and p["direction"] == "long"
    p = st.parse_request("1 min scalp on INFY, long and short")
    assert (p["symbol"], p["style"], p["direction"]) == ("INFY", "scalping", "both")
    # All-caps requests: grammar words are never the ticker, the first real ticker wins.
    assert st.parse_request("SHORT ONLY ON RELIANCE")["matched"] == {"symbol": "RELIANCE", "direction": "short"}
    assert st.parse_request("BUY TCS")["symbol"] == "TCS" and st.parse_request("15 MIN scalping on TCS")["symbol"] == "TCS"
    for text in ("INTRADAY BOTH", "USE INTRADAY", "SCALPING", "PAPER trade LIVE", "LONG and SHORT"):
        assert "symbol" not in st.parse_request(text)["matched"], text
    assert st.parse_request("LIVE trade on INFY")["symbol"] == "INFY"
    assert st.parse_request("nifty midcap long")["symbol"] == "NIFTY MID SELECT"
    assert "Understood: INFY" in st.request_summary("en", st.parse_request("INFY"))


def test_a_request_in_another_script_keeps_the_defaults_and_stays_english():
    """The Marathi request words were removed with the rest of the Marathi outside the interview: a request in another
    script names nothing the parser knows (only its Latin words count), and the summary is English."""
    p = st.parse_request(MR_BANK_NIFTY_LONG, default_symbol="HDFCBANK")
    assert p["language"] == "en" and "language" not in p["matched"]
    assert p["matched"] == {"style": "scalping", "direction": "long"} and p["symbol"] == "HDFCBANK"
    summary = st.request_summary("mr", p)
    assert summary.startswith("Understood: HDFCBANK") and not DEVANAGARI.search(summary)


def test_plans_carry_english_words_and_the_api_answers_in_english():
    df = _sessions(days=12, minutes=1)
    study = ms.study(df, "NIFTY 50", "mr")                  # an older "mr" caller
    out = st.build(df, study, "mr", direction="long")
    best = out["candidates"][0]
    assert best["direction_text"] == "LONG only" and best["timeframe_text"] == "5-minute" and best["summary"].startswith(best["name"])
    assert len(best["rules_text"]["long"]) == len(best["rules"]["long"]) and not DEVANAGARI.search(str(out) + str(study))
    out_en = st.build(df, study, "en", direction="short", style="scalping")
    best_en = out_en["candidates"][0]
    assert best_en["direction_text"] == "SHORT only" and best_en["timeframe_text"] == "1-minute" and all(" " in r for r in best_en["rules_text"]["short"])

    headers = {"Authorization": f"Bearer {_register('bf-strategist@example.com')}"}
    candles = _bars(_sessions(days=10, minutes=1))
    parsed = client.post("/api/ai/strategist/parse", headers=headers, json={"request": "nifty long only scalping", "symbol": "RELIANCE", "language": "mr"}).json()
    assert parsed["symbol"] == "NIFTY 50" and parsed["style"] == "scalping" and parsed["direction"] == "long" and parsed["language"] == "en"
    assert parsed["summary"].startswith("Understood: NIFTY 50")

    # The request overrides the fields it names; the reply is English whatever `language` says, and candles sent by the
    # page are sample data.
    r = client.post("/api/ai/strategist/build", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "mr", "direction": "short",
                                                                        "request": "nifty long only"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "en" and body["sides"] == ["long"] and body["request_parsed"]["matched"] == {"symbol": "NIFTY 50", "direction": "long"}
    assert body["base_timeframe"] == "5min" and all(t["trend_text"] in ("uptrend", "downtrend", "mixed") for t in body["study"]["timeframes"])
    assert all(c["direction_text"] == "LONG only" for c in body["candidates"]) and body["study"]["lines"]
    assert all(c["verdict"] == "sample" and "not real performance" in c["verdict_text"] for c in body["candidates"])
    assert not DEVANAGARI.search(r.text)
    study_mr = client.post("/api/ai/strategist/study", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "mr", "request": "nifty both sides intraday"}).json()
    assert study_mr["request_parsed"]["language"] == "en" and all(t["trend_text"] in ("uptrend", "downtrend", "mixed") for t in study_mr["timeframes"])
    assert study_mr["request_parsed"]["summary"].startswith("Understood")
    study_en = client.post("/api/ai/strategist/study", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "en"}).json()
    assert study_en["request_parsed"] is None and all(t["trend_text"] in ("uptrend", "downtrend", "mixed") for t in study_en["timeframes"])
    # Without a request nothing changes.
    plain = client.post("/api/ai/strategist/build", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "en"}).json()
    assert plain["request_parsed"] is None and plain["language"] == "en"
