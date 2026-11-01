"""SCR-FIX PR B - truthful results.

B1  a symbol that could not be read / checked is never "did not match" (reason code + one action; NaN has a reason)
B2  the run payload / preview carry the unread counts and the first error; nothing read = a run-level error
B3  plain reasons: no exception class names to users, the full text in the log, the API budget caught by class
B5  why it matched: a group's operator and negation carried into every row (OR: the true branch, NOT over a group)
B6  replay parity: the lake's daily bars attached as "1d", and the view says up front whether replay is available
"""

from __future__ import annotations

import asyncio
import logging
import types
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError, BrokerBudgetUsed
from app.core.models import OHLCVBar
from app.market_data.api_budget import BudgetUsed
from app.screener import compile_screen, reasons
from app.screener import evidence as ev
from app.screener.runtime import SymbolData, run_screen, stage_survivors
from tests.test_auth_api import client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _flag

WAIT = "waited for the broker's limit - run again shortly"


def _daily(count=160, start="2026-01-01", step=0.4) -> pd.DataFrame:
    i = np.arange(count, dtype=float)
    close = 100 + step * i + 3 * np.sin(i / 5)
    idx = pd.date_range(start, periods=count, freq="1D", tz="UTC")
    return pd.DataFrame({"open": close - 0.5, "high": close + 1.2, "low": close - 1.1, "close": close, "volume": 1000.0 + 0 * i}, index=idx)


# ------------------------------------------------------------------------------------------------------------ B3 reasons
@pytest.mark.parametrize("exc, code, action", [
    (BrokerAuthenticationError("token expired at 09:14:58 for client XYZ"), "login_ended", "login"),
    (BrokerAPIError("unauthorised", status_code=401), "login_ended", "login"),
    (BrokerAPIError("too many requests", status_code=429), "rate_limited", "retry"),
    (BrokerAPIError("Interval accepts one of (1minute,30minute,day,week,month)", status_code=400), "timeframe_refused", "timeframe"),
    (BrokerAPIError("instrument key invalid", status_code=400), "broker_refused", "retry"),
    (httpx.ConnectTimeout("timed out"), "unreachable", "retry"),
    (RuntimeError("something odd " + "x" * 300), "not_read", "retry"),
])
def test_an_exception_becomes_a_code_a_plain_sentence_and_one_action(exc, code, action, caplog):
    with caplog.at_level(logging.WARNING, logger="app.screener.reasons"):
        r = reasons.from_exception(exc, symbol="ABC")
    assert (r.code, r.action) == (code, action)
    assert type(exc).__name__ not in r and str(exc)[:20] not in r and r.endswith(".")      # never the class name / raw text
    assert str(exc) in caplog.text and type(exc).__name__ in caplog.text                  # the full text, not cut at 120, logged


@pytest.mark.parametrize("exc", [BudgetUsed(), BrokerBudgetUsed("no token")])
def test_the_api_budget_is_caught_by_class_and_keeps_the_wait_sentence(exc):
    r = reasons.from_exception(exc, wait_text=WAIT)
    assert r == WAIT and r.code == "rate_limited" and r.action == "retry"


def test_a_reason_is_a_string_that_keeps_its_code_through_a_copy():
    import copy
    r = reasons.of(reasons.LOGIN_ENDED)
    assert isinstance(r, str) and copy.copy(r).code == "login_ended" and copy.deepcopy(r).action == "login"
    assert reasons.as_reason("old text").code == "not_read" and reasons.as_reason(r) is r


def test_the_grouped_reason_is_the_largest_group_with_one_action():
    rows = [reasons.row("A", False, reasons.of(reasons.LOGIN_ENDED)), reasons.row("B", False, reasons.of(reasons.LOGIN_ENDED)),
            reasons.row("C", False, reasons.short_history(["1d"])), reasons.row("D", True, None), reasons.row("E", False, None)]
    assert [r["symbol"] for r in reasons.unread_rows(rows)] == ["A", "B", "C"]                # "did not match" is not unread
    assert reasons.summary(rows) == {"code": "login_ended", "text": reasons.of(reasons.LOGIN_ENDED), "action": "login", "count": 2}
    tie = [reasons.row("A", False, reasons.of(reasons.UNREACHABLE)), reasons.row("B", False, reasons.of(reasons.LOGIN_ENDED))]
    assert reasons.summary(tie)["action"] == "login"                                          # a tie: the action that fixes most
    assert reasons.summary([reasons.row("D", True, None)]) is None


# ------------------------------------------------------------------------------------------------------- B1 runtime
def test_no_value_on_the_last_bar_is_could_not_be_checked_never_did_not_match():
    from app.screener.runtime import last_match
    m = last_match("NAN", float("nan"))                     # the runtime's and the vectorised path's last-bar step
    assert m.matched is False and m.reason is not None and m.reason.code == "no_value" and m.reason.action == "timeframe"
    assert last_match("T", np.float64(1.0)).reason is None and last_match("F", False) == last_match("F", 0.0)
    sma, sma_v = compile_screen("close > SMA(close, 100)", base_tf="1d")
    short = run_screen(sma, sma_v, [SymbolData("SHORT", {"1d": _daily(20)})], base_tf="1d")[0]
    assert short.reason == "not enough history on 1d" and short.reason.code == "short_history" and short.reason.action == "timeframe"


def test_the_funnel_leaves_unchecked_symbols_out_of_with_data():
    ast, validated = compile_screen("close > SMA(close, 100)", base_tf="1d")
    universe = [SymbolData("LONG", {"1d": _daily(200)}), SymbolData("SHORT", {"1d": _daily(20)})]
    stages = stage_survivors(ast, validated, universe, base_tf="1d")
    assert stages["with_data"] == 1


# ------------------------------------------------------------------------------------------------------- B1 / B2 run route
def _bars(days=60):
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=100.0 + k, high=101.0 + k, low=99.0 + k, close=100.5 + k, volume=1000.0)
            for k, t in enumerate(idx)]


@pytest.fixture
def broker(monkeypatch):
    _flag(True)
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service
    failing: dict = {}

    async def pick(session, tenant_id, broker, label, **_k):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        if symbol in failing:
            raise failing[symbol]
        return _bars()
    adapter = types.SimpleNamespace(name="fake", native_intervals=frozenset({"1min", "30min", "day", "week", "month"}))
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: adapter)
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)
    yield failing
    _flag(False)


def test_unread_symbols_are_counted_apart_with_one_grouped_reason(broker):
    headers, _ = _owner("scrb-unread@example.com")
    broker["BAD1"] = BrokerAuthenticationError("session expired")
    broker["BAD2"] = BrokerAuthenticationError("session expired")
    body = {"source": "close > 0", "symbols": ["OK1", "OK2", "BAD1", "BAD2"]}
    out = client.post("/api/screener/run", json=body, headers=headers)
    assert out.status_code == 200, out.text
    run = out.json()
    assert run["matched"] == ["OK1", "OK2"] and run["unread"] == 2 and run["funnel"]["with_data"] == 2
    assert run["unread_reason"]["code"] == "login_ended" and run["unread_reason"]["action"] == "login" and run["unread_reason"]["count"] == 2
    assert run["first_error"]["symbol"] == "BAD1" and "BrokerAuthenticationError" not in out.text and "session expired" not in out.text
    preview = client.post("/api/screener/run", json={**body, "preview": True}, headers=headers).json()
    assert preview["matched_count"] == 2 and preview["unread"] == 2 and preview["first_error"]["code"] == "login_ended"


def test_a_run_where_nothing_could_be_read_is_a_run_level_error(broker):
    headers, _ = _owner("scrb-nothing@example.com")
    for s in ("A", "B"):
        broker[s] = BrokerAPIError("Interval accepts one of (1minute,30minute,day,week,month)", status_code=400)
    out = client.post("/api/screener/run", json={"source": "close > 0", "symbols": ["A", "B"]}, headers=headers)
    assert out.status_code == 422
    detail = out.json()["detail"]
    assert detail["code"] == "nothing_read" and detail["reason_code"] == "timeframe_refused" and detail["action"] == "timeframe"
    assert detail["unread"] == 2 and detail["message"].startswith("Could not read any of the 2 stocks.") and "BrokerAPIError" not in out.text
    # a live preview keeps answering with the counts (the page shows "0 matched · 2 could not be read")
    preview = client.post("/api/screener/run", json={"source": "close > 0", "symbols": ["A", "B"], "preview": True}, headers=headers)
    assert preview.status_code == 200 and preview.json()["unread"] == 2 and preview.json()["matched_count"] == 0


# ------------------------------------------------------------------------------------------------------- B5 why it matched
def _rows(src: str, data: SymbolData):
    ast, validated = compile_screen(src, base_tf="1d")
    assert validated.ok, validated.problems
    return ev.build_pack(ast, validated, data, base_tf="1d", debug=True)


def test_an_or_group_marks_the_true_branch_and_greys_the_others():
    pack = _rows("close > 0 AND (close != 1 OR close != close)", SymbolData("ABC", {"1d": _daily()}))
    assert pack["matched"] is True
    by_text = {r["text"]: r for r in pack["rows"]}
    true_branch, other = by_text["close != 1"], by_text["close != close"]
    assert true_branch["state"] == "met" and true_branch["passed"] is True and true_branch["group"]["op"] == "ANY"
    assert other["state"] == "not_needed" and other["passed"] is None and other["raw"] is False and other["group"]["op"] == "ANY"
    assert by_text["close > 0"]["group"] is None


def test_a_not_over_a_group_of_not_equal_conditions_is_carried_into_every_row():
    data = SymbolData("ABC", {"1d": _daily()})
    pack = _rows("close > 0 AND NOT (open != open OR high != high)", data)
    assert pack["matched"] is True
    block = pack["blocks"][1]
    rows = [r for r in pack["rows"] if r["block"] == block["id"]]
    # the rows say what the block says: each one must be FALSE, and reads as its negation, joined by AND (De Morgan)
    assert [r["text"] for r in rows] == ["NOT (open != open)", "NOT (high != high)"]
    assert all(r["required"] is False and r["raw"] is False and r["state"] == "met" and r["passed"] is True for r in rows)
    assert all(r["group"] == {"id": rows[0]["group"]["id"], "op": "ALL", "negated": True} for r in rows)
    assert all(r["verify"].startswith("NOT (") for r in rows) and block["passed"] is True
    # the caption (the text) and the table rows agree
    assert [line for line in pack["caption"][2:] if "!=" in line] == [f"✅ {r['verify']}" for r in rows]


def test_a_condition_required_false_never_shows_a_tick_for_being_true():
    pack = _rows("close > 0 AND NOT (close != 1 OR open != open)", SymbolData("ABC", {"1d": _daily()}))
    assert pack["matched"] is False
    rows = {r["text"]: r for r in pack["rows"]}
    true_one = rows["NOT (close != 1)"]
    assert true_one["raw"] is True and true_one["state"] == "failed" and true_one["passed"] is False
    assert rows["NOT (open != open)"]["state"] == "met"
    assert not any(line.startswith("✅") and "close != 1" in line for line in pack["caption"])


def test_conditions_in_carries_nested_operators_and_negation():
    ast, _v = compile_screen("NOT (close > 1 AND (open > 1 OR NOT (high > 1 OR low > 1)))", base_tf="1d")
    found = ev.conditions_in(ast)
    got = [(ev.n.to_text(c.node), c.required, [g.op for g in c.groups]) for c in found]
    assert got == [("close > 1", False, ["ANY"]), ("open > 1", False, ["ANY", "ALL"]),
                   ("high > 1", True, ["ANY", "ALL", "ANY"]), ("low > 1", True, ["ANY", "ALL", "ANY"])]
    assert ev.conditions_of(ast) == [c.node for c in found]


# ------------------------------------------------------------------------------------------------------- B6 replay parity
def _lake(bars: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": bars.index, "bar_end": bars.index, **{k: bars[k].to_numpy() for k in ("open", "high", "low", "close", "volume")}})


def _intraday(days: pd.DatetimeIndex) -> pd.DataFrame:
    stamps = [d + pd.Timedelta(hours=3, minutes=45) + pd.Timedelta(minutes=15 * k) for d in days for k in range(25)]
    i = np.arange(len(stamps), dtype=float)
    close = 200 + 0.05 * i + np.sin(i / 7)
    return pd.DataFrame({"open": close - 0.2, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 500.0 + 0 * i},
                        index=pd.DatetimeIndex(stamps, tz="UTC"))


def test_replay_attaches_the_lake_daily_bars_so_it_equals_the_run():
    from app.screener.evidence_routes import _replay_pack, daily_need
    src = "close > 0 AND SMA(close, 5)@1d > SMA(close, 100)@1d"
    ast, validated = compile_screen(src, base_tf="15m")
    assert validated.ok, validated.problems
    assert daily_need(validated, "15m") and daily_need(compile_screen("close > 0", base_tf="15m")[1], "15m") == {}
    days = pd.bdate_range("2025-06-02", periods=180, tz="UTC")
    daily = pd.DataFrame({"open": 99.0 + np.arange(180), "high": 101.0 + np.arange(180), "low": 98.0 + np.arange(180),
                          "close": 100.0 + np.arange(180), "volume": 1000.0}, index=days + pd.Timedelta(hours=3, minutes=45))
    intraday = _intraday(days[-10:])
    at = (intraday.index[-1] + pd.Timedelta(minutes=15)).to_pydatetime()
    run_data = SymbolData("ABC", {"15m": intraday, "1d": daily}, exchange="NSE")           # what the run read (fetch_frames)
    pack = ev.build_pack(ast, validated, run_data, base_tf="15m", as_of=at, debug=True, context=True)
    stored = {**pack, "screen": {**pack["screen"], "params": {}}}
    replayed = _replay_pack(_lake(intraday), ast, validated, stored, "15m", at, True, "lake:1m", _lake(daily))
    assert [(r["text"], r["state"], r["terms"]) for r in replayed["rows"]] == [(r["text"], r["state"], r["terms"]) for r in pack["rows"]]
    assert replayed["matched"] == pack["matched"] is True
    # without the daily bars the replay resampled 1d from ten days of 15m bars - not the run's computation
    short = _replay_pack(_lake(intraday), ast, validated, stored, "15m", at, True, "lake:1m", None)
    assert [r["state"] for r in short["rows"]] != [r["state"] for r in pack["rows"]]


def test_replay_daily_reads_the_lake_daily_window_only_when_the_screen_needs_it(monkeypatch):
    from app.charts import price_action_layer as PAL
    from app.screener.evidence_routes import replay_daily
    asked = []

    async def load_frame(session, key, tf, start, end, **kw):
        asked.append((key, tf, start, end))
        return _lake(_daily(5)), "lake:1d"
    monkeypatch.setattr(PAL, "load_frame", load_frame)
    at = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    _a, validated = compile_screen("close > 0 AND SMA(close, 100)@1d > 0", base_tf="15m")
    got = asyncio.run(replay_daily(None, validated, "15m", at, "NSE:ABC"))
    assert got is not None and asked[0][1] == "1d" and asked[0][3] == at and (at - asked[0][2]).days > 100
    _a, plain = compile_screen("close > 0", base_tf="15m")
    assert asyncio.run(replay_daily(None, plain, "15m", at, "NSE:ABC")) is None and len(asked) == 1


def test_replay_plan_says_up_front_whether_replay_is_available():
    from app.screener.evidence_routes import replay_plan
    pack = ev.build_pack(*compile_screen("close > 0", base_tf="1d"), SymbolData("ABC", {"1d": _daily()}), base_tf="1d")
    assert replay_plan(pack)[0] is None and replay_plan(pack)[3] == "NSE:ABC"
    ranked = ev.build_pack(*compile_screen("Rank(close) <= 2", base_tf="1d"), SymbolData("ABC", {"1d": _daily()}), base_tf="1d",
                           constants={})
    assert "cross-sectional" in (replay_plan(ranked)[0] or "")
    assert "not replayed yet" in (replay_plan({**pack, "base_tf": "1w"})[0] or "")
