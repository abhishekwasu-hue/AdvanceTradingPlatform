"""Phase BF: the strategist in the trader's language - rules in words (mr/en), direction and timeframe words,
a one-line summary, and plain-words requests in Marathi or English parsed into symbol / style / direction /
language (API: /strategist/parse, and `request` on /study and /build)."""
from app.ai import market_study as ms
from app.ai import strategist as st
from app.ai.strategist import cond, ind, val
from tests.test_auth_api import _register, client
from tests.test_phase_ap_interview import _sessions
from tests.test_phase_aw_strategist import _bars


def test_rules_in_words_cover_every_operand_family_in_both_languages():
    c1 = cond(ind("CLOSE"), "CROSSES_ABOVE", ind("OR_HIGH", 15))
    assert st.rule_words("en", c1) == "close crosses above opening-range high (15 min)"
    assert st.rule_words("mr", c1) == "close भाव opening range high (15 मिनिट) च्या वर ओलांडतो"
    c2 = cond(ind("EMA", 9), "GT", ind("EMA", 50, tf="15min"))
    assert st.rule_words("en", c2) == "EMA(9) above EMA(50) on 15-minute candles" and st.rule_words("mr", c2) == "EMA(9) EMA(50) (15 मिनिट candles) च्या वर"
    c3 = cond(ind("LOW"), "LT", ind("BB_LOWER", 20, mult=2.5))
    assert "lower Bollinger band (20, 2.5σ)" in st.rule_words("en", c3) and "खालचा Bollinger band (20, 2.5σ)" in st.rule_words("mr", c3)
    c4 = cond(ind("RSI", 14), "CROSSES_BELOW", val(55))
    assert st.rule_words("en", c4) == "RSI(14) crosses below 55" and st.rule_words("mr", c4) == "RSI(14) 55 च्या खाली ओलांडतो"
    c5 = cond(ind("CLOSE"), "GTE", ind("PDH"))
    assert st.rule_words("en", c5) == "close at or above yesterday's high" and "कालचा high" in st.rule_words("mr", c5)
    c6 = cond(ind("CLOSE"), "LTE", ind("SUPERTREND", 10, mult=3.0))
    assert st.rule_words("en", c6) == "close at or below Supertrend(10, 3)"
    for name in st.OPERAND_WORDS:                       # every word table entry formats without KeyError
        st.operand_words("mr", ind(name, 20, mult=2.0))
        st.operand_words("en", ind(name, 20, tf="60min"))


def test_parse_request_reads_marathi_and_english_requests():
    p = st.parse_request("बँक निफ्टी फक्त long scalping")
    assert (p["symbol"], p["style"], p["direction"], p["language"]) == ("NIFTY BANK", "scalping", "long", "mr") and set(p["matched"]) == {"symbol", "style", "direction", "language"}
    p = st.parse_request("RELIANCE intraday both sides")
    assert (p["symbol"], p["style"], p["direction"], p["language"]) == ("RELIANCE", "intraday", "both", "en")
    p = st.parse_request("निफ्टी मध्ये मंदीसाठी ५ मिनिट")                      # Devanagari digits, bearish wording
    assert (p["symbol"], p["style"], p["direction"], p["language"]) == ("NIFTY 50", "intraday", "short", "mr")
    p = st.parse_request("short only on finnifty")
    assert p["symbol"] == "NIFTY FIN SERVICE" and p["direction"] == "short" and "style" not in p["matched"]
    p = st.parse_request("सेन्सेक्स वर खरेदी")
    assert p["symbol"] == "SENSEX" and p["direction"] == "long"
    p = st.parse_request("काहीतरी वेगळं", default_symbol="HDFCBANK")             # nothing recognised: defaults stay, only the script is noted
    assert (p["symbol"], p["style"], p["direction"], p["language"], p["matched"]) == ("HDFCBANK", "intraday", "auto", "mr", {"language": "mr"})
    p = st.parse_request("1 min scalp on INFY, long and short")
    assert (p["symbol"], p["style"], p["direction"]) == ("INFY", "scalping", "both")
    # All-caps requests: grammar words are never the ticker, the first real ticker wins.
    assert st.parse_request("SHORT ONLY ON RELIANCE")["matched"] == {"symbol": "RELIANCE", "direction": "short"}
    assert st.parse_request("BUY TCS")["symbol"] == "TCS" and st.parse_request("15 MIN scalping on TCS")["symbol"] == "TCS"
    for text in ("INTRADAY BOTH", "USE INTRADAY", "SCALPING", "PAPER trade LIVE", "LONG and SHORT"):
        assert "symbol" not in st.parse_request(text)["matched"], text
    assert st.parse_request("LIVE trade on INFY")["symbol"] == "INFY"
    # Marathi variants and word order.
    assert st.parse_request("निफ्टी बँक मध्ये तेजी")["matched"] == {"language": "mr", "symbol": "NIFTY BANK", "direction": "long"}
    assert st.parse_request("nifty midcap मंदी")["symbol"] == "NIFTY MID SELECT" and st.parse_request("नीफ्टी")["symbol"] == "NIFTY 50"
    assert st.parse_request("banknifty long")["language"] == "en" and "language" not in st.parse_request("banknifty long")["matched"]
    assert "समजले: NIFTY BANK" in st.request_summary("mr", st.parse_request("बँक निफ्टी")) and "Understood: INFY" in st.request_summary("en", st.parse_request("INFY"))


def test_plans_carry_words_and_the_api_accepts_a_request_in_marathi():
    df = _sessions(days=12, minutes=1)
    study = ms.study(df, "NIFTY 50", "mr")
    out = st.build(df, study, "mr", direction="long")
    best = out["candidates"][0]
    assert best["direction_text"] == "फक्त LONG" and best["timeframe_text"] == "5 मिनिट" and best["summary"].startswith(best["name"])
    assert len(best["rules_text"]["long"]) == len(best["rules"]["long"]) and all("च्या" in r or "ओलांडतो" in r for r in best["rules_text"]["long"])
    out_en = st.build(df, study, "en", direction="short", style="scalping")
    best_en = out_en["candidates"][0]
    assert best_en["direction_text"] == "SHORT only" and best_en["timeframe_text"] == "1-minute" and all(" " in r for r in best_en["rules_text"]["short"])

    headers = {"Authorization": f"Bearer {_register('bf-strategist@example.com')}"}
    candles = _bars(_sessions(days=10, minutes=1))
    parsed = client.post("/api/ai/strategist/parse", headers=headers, json={"request": "निफ्टी फक्त long scalping", "symbol": "RELIANCE"}).json()
    assert parsed["symbol"] == "NIFTY 50" and parsed["style"] == "scalping" and parsed["direction"] == "long" and parsed["language"] == "mr" and "समजले" in parsed["summary"]

    # The request overrides the fields it names and sets the language; the study and plans come back in Marathi.
    r = client.post("/api/ai/strategist/build", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "en", "direction": "short",
                                                                        "request": "निफ्टी फक्त long"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "mr" and body["sides"] == ["long"] and body["request_parsed"]["matched"] == {"language": "mr", "symbol": "NIFTY 50", "direction": "long"}
    assert body["base_timeframe"] == "5min" and all(t["trend_text"] in ("वरचा trend", "खालचा trend", "मिश्र") for t in body["study"]["timeframes"])
    assert all(c["direction_text"] == "फक्त LONG" for c in body["candidates"]) and body["study"]["lines"]
    # A Latin-only request keeps the form's language (a Marathi UI stays Marathi even though tickers are Latin).
    study_mr = client.post("/api/ai/strategist/study", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "mr", "request": "nifty both sides intraday"}).json()
    assert study_mr["request_parsed"]["language"] == "en" and all(t["trend_text"] in ("वरचा trend", "खालचा trend", "मिश्र") for t in study_mr["timeframes"])
    assert "समजले" in study_mr["request_parsed"]["summary"]
    study_en = client.post("/api/ai/strategist/study", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "en"}).json()
    assert study_en["request_parsed"] is None and all(t["trend_text"] in ("uptrend", "downtrend", "mixed") for t in study_en["timeframes"])
    # Without a request nothing changes.
    plain = client.post("/api/ai/strategist/build", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "en"}).json()
    assert plain["request_parsed"] is None and plain["language"] == "en"
