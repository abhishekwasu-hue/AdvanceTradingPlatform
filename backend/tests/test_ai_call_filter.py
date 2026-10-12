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
    assert output_filter.call_terms("Entry RELIANCE 2500 SL 2450 TGT 2600") == ["call: entry / stop / target levels"]
    assert output_filter.call_terms("Price closed above SMA 20.") == []
