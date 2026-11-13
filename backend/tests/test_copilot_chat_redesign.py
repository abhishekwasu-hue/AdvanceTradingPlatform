"""Owner 13 Nov ("chat window is not attractive and not looking real"), the data fixes behind the Copilot chat redesign:

* Check my trade: "the price" is the live / last traded price with its time, never the last daily close passed off as
  current; with no quote the last close is said as such, with its date. Every number stays grounded.
* The market strip: a priced index with no change (a tick-fed price, no quote of the day yet) gets its change from the
  previous session's close in the NSE daily files - never from a close of the price's own day or later, and not at
  all when the files are behind.
No network: recorded tool results and a faked daily-file read."""
import asyncio
import copy
from datetime import date, datetime, timezone

from app.ai import copilot_chat as chat
from app.ai import grounding
from app.ai.output_filter import findings
from app.market_data import strip_routes
from evals import runner

FIX = runner.copilot_fixtures()
run = asyncio.run


def _quote_fixture(ltp: float, at: str):
    fixture = copy.deepcopy(FIX["default"])
    fixture["get_quote:NIFTY 50"] = {"ok": True, "as_of": at, "source": "broker",
                                     "data": {"symbol": "NIFTY 50", "ltp": ltp, "prev_close": 24850.5, "change_pct": 0.1}}
    return fixture


def _sections(out):
    return {s["title"]: s["lines"] for s in out.sections}


def test_check_my_trade_reads_the_live_price_with_its_time_not_the_last_close():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    calls = []
    out = run(chat.answer("Check my trade idea", runner.recorded_runner(_quote_fixture(24560.25, now.isoformat()), calls)))
    hhmm = now.astimezone(chat.IST).strftime("%H:%M")
    names = [c["tool"] for c in calls]
    assert names[:3] == ["get_chart_structure", "get_quote", "get_market_zones"]
    assert calls[1]["args"] == {"symbol": "NIFTY 50", "exchange": "NSE"}
    assert "price" not in calls[2]["args"]                                # #1051: the zone tool reads its own live price
    shown = _sections(out)
    assert shown["Price"] == [f"Price: 24,560.25 at {hhmm}"]
    assert shown["Area"][0].startswith(f"The price 24,560.25 at {hhmm} is inside the demand area")
    assert "24,850.5" not in " ".join(shown["Area"])                     # yesterday's close is never "the price"
    allowed = grounding.allowed_from_text("\n".join([*out.trusted_text, chat.FIXED_TEXT, "Check my trade idea"]))
    assert all(grounding.check_numbers(line, allowed)[0] for line in out.lines())
    assert "dropped_lines" not in out.diagnostics and not findings("\n".join(out.lines()))


def test_an_older_quote_carries_its_day_and_no_quote_says_last_close_with_its_date():
    old = "2026-10-20T09:59:00+00:00"                                     # 15:29 IST on an earlier day
    out = run(chat.answer("Check my trade idea", runner.recorded_runner(_quote_fixture(24850.5, old), [])))
    assert _sections(out)["Price"] == ["Price: 24,850.5 at 20 Oct 15:29"]
    none = run(chat.answer("Check my trade idea", runner.recorded_runner(FIX["default"], [])))   # no quote recorded
    shown = _sections(none)
    assert shown["Price"] == ["Last close: 24,850.5 (21 Oct 2026)"]
    assert shown["Area"][0].startswith("The last close 24,850.5 (21 Oct 2026) is ")
    assert "dropped_lines" not in none.diagnostics


def test_no_trade_words_in_the_new_lines():
    out = run(chat.answer("Check my trade idea", runner.recorded_runner(_quote_fixture(24560.25, datetime.now(timezone.utc).isoformat()), [])))
    text = " ".join(_sections(out)["Price"] + _sections(out)["Area"]).lower()
    assert not any(w in text for w in ("buy", "sell", "target", "stop-loss", "stop loss"))


# --- the strip's missing change -------------------------------------------------------------------------------------------

def _item(ltp: float, as_of: str):
    return {"key": "NIFTY BANK", "label": "BANK NIFTY", "symbol": "NIFTY BANK", "exchange": "NSE", "ltp": ltp, "prev_close": None,
            "change": None, "change_pct": None, "as_of": as_of, "stale": False, "source": "live", "missing": False}


def _fake_close(monkeypatch, close, prev, day):
    async def last_close(_session, symbol):
        return {"symbol": symbol, "close": close, "prev_close": prev, "change": None, "change_pct": None, "date": day, "missing": False}
    from app.market_data import eod_routes
    monkeypatch.setattr(eod_routes, "last_close", last_close)


def test_a_live_price_without_a_change_gets_it_from_the_previous_session_close(monkeypatch):
    _fake_close(monkeypatch, 61000.0, 60500.0, "2026-11-12")
    got = run(strip_routes._with_eod_prev(None, _item(60948.65, "2026-11-13T06:40:00+00:00"), {}))   # Fri 13 Nov 12:10 IST
    assert (got["prev_close"], got["change"], got["change_pct"]) == (61000.0, -51.35, -0.08)


def test_the_files_holding_the_price_day_use_the_close_before_it(monkeypatch):
    _fake_close(monkeypatch, 60948.65, 61000.0, "2026-11-13")             # after the close: today's bar is stored
    got = run(strip_routes._with_eod_prev(None, _item(60948.65, "2026-11-13T10:00:00+00:00"), {}))
    assert got["prev_close"] == 61000.0 and got["change"] == -51.35


def test_files_behind_by_a_session_leave_the_change_unknown(monkeypatch):
    _fake_close(monkeypatch, 61000.0, 60500.0, "2026-11-11")             # Wed: Thu 12 Nov is missing from the files
    item = _item(60948.65, "2026-11-13T06:40:00+00:00")
    assert run(strip_routes._with_eod_prev(None, item, {})) == item
    # a holiday in between is not a missing session
    assert run(strip_routes._with_eod_prev(None, item, {date(2026, 11, 12): "Holiday"}))["change"] == -51.35
