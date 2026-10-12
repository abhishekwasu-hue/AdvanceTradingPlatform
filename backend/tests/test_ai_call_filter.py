"""H-C1 c, Phase 0 gap fix: trade calls aimed at an instrument are blocked in every supported language (EN / MR / HI),
whatever the answer language - and descriptive analysis still passes. A corpus of both sides."""
import pytest

from app.ai import output_filter

SHOULD_BLOCK = [
    # imperative verbs aimed at an instrument
    "Sell RELIANCE", "Go long RELIANCE", "Go short NIFTY here.", "Accumulate RELIANCE", "Short sell TCS.", "Book profits in TCS.",
    "Price is weak. Exit INFY now.", "Please buy HDFCBANK above 1650.", "Average down INFY.", "Load up on SBIN.",
    "RELIANCE: buy the dip", "TATAMOTORS - sell", "Buy this stock now", "Buy the shares before results",
    # entry / stop / target sets
    "Entry RELIANCE 2500 SL 2450 TGT 2600", "Buy above 2500, SL 2450, target 2600.", "NIFTY: stop 24800, target 25200",
    "Entry 2500 stop-loss 2450 targets 2600", "एन्ट्री 2500, स्टॉप लॉस 2450, लक्ष्य 2600",
    # price-target certainty
    "RELIANCE to 3000 for sure", "NIFTY will hit 26000 definitely", "TCS 4500 तक पक्का", "NIFTY 25500 पर्यंत नक्की",
    # Marathi
    "RELIANCE खरेदी करा", "TCS घ्या", "INFY विका", "हा शेअर खरेदी करा", "विक्री करा RELIANCE",
    # Hindi
    "RELIANCE खरीदें", "यह शेयर खरीदें", "TCS बेचें", "INFY ले लो", "बेच दो RELIANCE",
    # review of #230, HIGH1: a danda ends the verb
    "RELIANCE खरीदें।", "यह शेयर खरीदें।", "TCS बेचें।", "RELIANCE खरेदी करा।", "NIFTY 25500 तक पक्का।", "अभी खरीदें।",
    # HIGH2: markdown, lists, emoji, quotes, brackets, labels and arrows
    "- Buy RELIANCE", "* Buy RELIANCE", "• Buy RELIANCE", "- Buy: RELIANCE", "**Buy** RELIANCE", "`Buy RELIANCE`", "> Buy RELIANCE",
    "🚀 Buy RELIANCE", "(Buy RELIANCE)", "\"Buy RELIANCE\"", "Recommendation - Buy RELIANCE", "BUY: RELIANCE", "Buy -> RELIANCE",
    "1) Buy RELIANCE", "Buy → RELIANCE", "ｂｕｙ RELIANCE", "B\u200buy RELIANCE", "Вuy RELIANCE", "B u y RELIANCE", "8uy RELIANCE now",
    # HIGH3: entry / stop / target across lines and sentences
    "Entry 2500\nSL 2450\nTarget 2600", "Entry 2500. SL 2450. Target 2600.", "Long above 2500, stop 2450, target 2600",
    # HIGH4: instruments by name, in any case and in Devanagari
    "buy reliance", "Buy Reliance Industries", "रिलायन्स खरेदी करा", "रिलायंस खरीदें", "Sell infosys now.", "इन्फोसिस विका",
    # MED7: more calls
    "Price is weak, sell INFY.", "Long RELIANCE", "Purchase RELIANCE", "Square off all INFY positions", "Exit all INFY positions",
    "Buy a few shares of RELIANCE", "Then buy RELIANCE", "Just buy RELIANCE", "Time to buy RELIANCE!", "Consider buying RELIANCE.",
    "I would buy RELIANCE here", "I'd buy RELIANCE", "I’d buy RELIANCE", "One could buy RELIANCE here", "My pick is RELIANCE",
    "RELIANCE is a buy", "RELIANCE looks like a buy", "NIFTY will surely reach 26000", "It will certainly reach 3000",
    "three thousand for sure", "RELIANCE kharedi kara", "TCS khareed lo", "kharido RELIANCE", "INFY becho", "TCS le lo", "TCS ghya",
    "INFY vika", "TCS खरेदी करावी", "RELIANCE खरीदना चाहिए", "TCS घेऊ शकता", "RELIANCE ख़रीदें",
    # LOW9: an imperative with a level and no ticker, and a modal call on a ticker with a level
    "Buy 25000 CE above 120", "I would sell TCS above 4000",
]

SHOULD_PASS = [
    "Price closed above SMA 20.", "The stop-loss rule in your template exits at 2%.", "Buyers absorbed the selling near 2450.",
    "NIFTY 50 trend is up; support near 24850.", "Bulls hold 25200.00; room to 25550.00, wrong below 24850.00.", "Shorting is risky.",
    "A sell-off below 24850 would weaken the read.", "RSI 14 is above 60.", "Add SMA(close, 20) to the chart.",
    "The template has a stop of 2% and a target of 4%.", "Exit rules: the stop moves to break-even at T1.",
    "Sell signals were rare last month.", "I don't know for sure.", "The index rose to 25000 last week.",
    "This is not a recommendation to buy anything.", "Selling pressure eased after 14:00.", "The buy-side volume doubled.",
    "Entry and exit rules are listed below.", "Your PAPER position in RELIANCE closed at the stop.",
    "RELIANCE closed at 2480, below its 20-day average.", "Exits are never blocked.", "Add RELIANCE to your watchlist.",
    "Enter NIFTY in the symbol box to load its chart.",
    # Marathi
    "हे लक्षात घ्या: कल वरचा आहे.", "बाजार 24850 च्या वर बंद झाला.", "RELIANCE चा भाव 2480 वर बंद झाला.", "विक्रीचा दबाव कमी झाला.",
    "तुमच्या template मधील स्टॉप लॉस नियम 2% आहे.",
    # Hindi
    "कीमत 2450 के ऊपर बंद हुई।", "खरीदारों ने बिकवाली को सोख लिया।", "RELIANCE का भाव 20 दिन के औसत से नीचे है।", "बाज़ार में बिकवाली का दबाव रहा।",
    # review of #230, MED6: logs, backtests, percentages, status, rules, help, negations, past certainty
    "Backtest trade 3: entry 2480, stop 2430, target 2580", "Trade log: RELIANCE long, entry 2480, SL 2430",
    "ORB: stop 1%, target 2%", "Proposal: buy RELIANCE, 10 shares, awaiting your approval", "Status: BUY RELIANCE filled",
    "Side: SELL TCS", "Rule: Buy NIFTY when RSI < 30", "How to: Buy RELIANCE in paper mode", "NIFTY: buy signals were rare",
    "NIFTY चा आढावा घ्या", "RELIANCE खरेदी करा, असे आम्ही सांगत नाही.", "We never say: Sell RELIANCE",
    "NIFTY hit 25000 for sure on Friday.", "Buy when the close is above the 20-day high.", "The long-term trend is up.",
    "Short-term momentum faded near 2450.", "Long upper wicks near 2500 show supply.", "Cover image of the report.",
    "Exit all is under the Positions page.", "The trade was closed at the target 2600 after the stop 2450 moved up; entry was 2500.",
]


@pytest.mark.parametrize("text", SHOULD_BLOCK)
def test_calls_aimed_at_an_instrument_are_blocked(text):
    for lang in ("en", "mr", "hi"):                                   # the request language never matters
        s = output_filter.screen(text, lang)
        assert not s.ok, (text, lang)
        ok, shown, why = output_filter.check(text, lang)
        assert not ok and shown is None and "advice/guarantee" in why


@pytest.mark.parametrize("text", SHOULD_PASS)
def test_descriptive_analysis_passes(text):
    for lang in ("en", "mr", "hi"):
        s = output_filter.screen(text, lang)
        assert s.ok and s.text == text, (text, lang, s.blocked)


def test_the_call_labels_name_what_was_found():
    assert output_filter.call_terms("Sell RELIANCE") == ["call: sell reliance"]
    assert output_filter.call_terms("x" * 40_001) == ["call: text too long to check (over 40000 characters)"]
    assert output_filter.call_terms("Entry RELIANCE 2500 SL 2450 TGT 2600") == ["call: entry / stop / target levels"]
    assert output_filter.call_terms("Price closed above SMA 20.") == []


def test_a_modal_call_on_a_level_without_an_instrument_stays_framed():
    s = output_filter.screen("One could buy the 25000 CE above 120.", "en")
    assert s.ok and s.framed                                  # documented: the spec allows framing here (call_detect docstring)
    assert not output_filter.screen("Buy 25000 CE above 120", "en").ok     # the imperative is blocked (consistent, LOW9)


def test_the_support_input_guard_does_not_refuse_how_to_questions_with_trade_words():
    from app.support import guard
    for question in ("Where do I enter stop 2% and target 4% for NIFTY?", "Sell TCS: where is the sell button?",
                     "How do I place a stop loss on a PAPER trade?"):
        assert guard.classify(question) is None, question             # calls are an output check, not an input one (MED5)
    assert guard.classify("Should I buy RELIANCE?") == "advice"
    assert output_filter.blocked_terms("Sell TCS") == [] and output_filter.findings("Sell TCS") == ["call: sell tcs"]


def test_names_from_the_instrument_master_count_in_any_case():
    from app.ai import instrument_names
    assert output_filter.screen("Buy Zydus Wellness here", "en").ok
    instrument_names.add(["Zydus Wellness Limited", "ACC", "Trent"])
    try:
        assert not output_filter.screen("Buy Zydus Wellness here", "en").ok
        assert not output_filter.screen("buy trent", "en").ok
        assert output_filter.screen("Add a cc line.", "en").ok           # one-word names under five letters are not added
    finally:
        for name in ("zydus wellness", "trent"):
            instrument_names._NAMES.discard(name)
            instrument_names._FROM_MASTER.discard(name)


def test_long_inputs_are_linear_and_capped():
    import time
    started = time.monotonic()
    assert not output_filter.screen("\n" * 39_000 + "Buy RELIANCE", "en").ok
    assert output_filter.screen("a, " * 13_000, "en").ok
    assert time.monotonic() - started < 5
    assert not output_filter.screen("x" * 40_001, "en").ok
