"""Day-one fix 5 (README row 19): the Copilot's answers in a beginner's words - no screen code, no "the function's
default", "Find setups" (the v2 page name) instead of "the Screener", plain trend / structure words, India dates and PCR
explained (app/ai/copilot_chat.py, app/screener/idea.py)."""
import asyncio
import json

from app.ai import copilot_chat as chat
from app.screener import idea
from evals import runner

CODE = ('==', '()', 'UPTREND', 'BOS', 'CHOCH', "function's default", "Open the Screener", "Dow structure")


def test_trend_event_and_day_words():
    assert chat.trend_words("UPTREND") == "up" and chat.trend_words("range") == "sideways" and chat.trend_words("DOWNTREND") == "down"
    assert chat.trend_words(None) == "not known yet" and chat.trend_words("down (weak)") == "down (weak)"
    assert chat.event_words("BOS") == "break past the last swing (a break of structure)"
    assert chat.event_words("CHoCH") == "first break against the trend (a change of character)"
    assert chat.day_words("2026-10-20") == "20 Oct 2026" and chat.day_words("2026-01-02T03:30:00+00:00") == "2 Jan 2026"
    assert chat.day_words("yesterday") == "yesterday"


def test_stage_words_use_the_reader_s_plain_words():
    d = idea.read_idea("stocks in a daily uptrend near a demand zone", "1d")
    words = chat.stage_words(d.parts)
    assert words('Trend() == "UPTREND"', 0) == "The market-structure trend is up"
    assert words("NearZone( \"demand\" )", 5) == "The close is at a buyer area (a demand zone)"
    assert words("Something(else)", 1) == "The close is at a buyer area (a demand zone)"      # by position
    assert words("Something(else)", 9) == "Condition 10"
    assert all("function's default" not in a for p in d.parts for a in p.assumptions)


def _answer(question):
    fx = runner.copilot_fixtures()["default"]
    return asyncio.run(chat.answer(question, runner.recorded_runner(fx))).as_dict()


def test_the_screen_answer_and_the_market_line_carry_no_code():
    a = _answer("Find stocks in a daily uptrend near a demand zone")
    text = json.dumps({k: a[k] for k in ("summary", "answer", "sections", "action")})
    for word in CODE:
        assert word not in text, word
    assert a["action"]["label"] == "Open Find setups"
    fx = runner.copilot_fixtures()["default"]
    line = asyncio.run(chat.market_line(runner.recorded_runner(fx)))
    assert "Dow structure" not in json.dumps(line) and "daily trend is" in json.dumps(line)


def test_the_job_list_names_find_setups():
    job = chat.BY_ID["screen"]
    assert job.page == ("Open Find setups", "/screener")
