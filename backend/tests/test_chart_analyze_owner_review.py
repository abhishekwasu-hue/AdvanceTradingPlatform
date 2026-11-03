"""Owner review of an image-only read (3 Nov, GLENMARK): input words never reach the user; a long text ends at a whole
sentence instead of mid-sentence."""
from app.chart_analyze import contract as c


def test_input_words_are_dropped_sentence_by_sentence():
    story = ("This is an NSE stock, not NIFTY or BANKNIFTY. There are no FACTS or OHLC text. "
             "The impulse lifts the price to a higher high.")
    out = c.text(story)
    assert "FACTS" not in out and "OHLC" not in out
    assert out.endswith("The impulse lifts the price to a higher high.")


def test_a_long_text_ends_at_its_last_whole_sentence():
    first = "The larger structure is down: a lower high and a lower low. "
    second = "The rally closed above the last lower high, a change of character. "
    long = first + second + "An up trend is confirmed only by a higher low and then a new high, which has not formed yet on this chart."
    out = c.text(long, c.SHORT_MAX * 2 - 100)
    assert out == (first + second).strip()
    assert not out.endswith("...")


def test_one_long_sentence_still_cuts_at_a_word():
    out = c.text("word " * 200, 50)
    assert out.endswith("...") and len(out) <= 53


def test_ca_q3_choch_and_last_price_are_kept():
    """Owner CA-Q3: the CHoCH line (with its price) and the chart's last price survive the contract."""
    from tests.chart_analyze_fakes import WIDTH, HEIGHT, answer
    raw = answer("none")
    raw["annotations"]["choch"] = {"x0": 100, "y0": 50, "x1": 300, "y1": 50, "price": 2290, "note": "close above the last lower high"}
    raw["recognised"]["last_price"] = "2,407.5"
    out = c.validate(raw, mode="image_only", width=WIDTH, height=HEIGHT)
    assert out["annotations"]["choch"]["price"] == 2290 and out["annotations"]["choch"]["x1"] == 300
    assert out["recognised"]["last_price"] == 2407.5
    raw["annotations"]["choch"] = {"price": 1}            # no line on the picture: nothing drawn
    assert c.validate(raw, mode="image_only", width=WIDTH, height=HEIGHT)["annotations"]["choch"] is None
