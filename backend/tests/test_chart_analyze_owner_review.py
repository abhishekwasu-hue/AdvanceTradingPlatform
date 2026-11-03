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
