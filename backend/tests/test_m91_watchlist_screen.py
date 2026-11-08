"""M91 (owner order): a watchlist's "Screen" - his own saved screen on the list, and the same screen drawn on the chart.

W1 the choice is remembered per watchlist (server-side, his own; another member's screen is a 404; None removes it).
W2 the check answers which symbols match, with each symbol's per-condition marks (the page orders / dims / counts).
W3 `next_close` is the bar-close engine's clock: the page re-checks only when a newer bar of the screen's timeframe closed.
W4 the alert is OFF by default; once he turns it on, the first check is the baseline and only NEW entries are alerted.
C1 the overlay carries the screen's own drawables, a marker per bar where the whole screen held, and the legend.
C2 the list and the chart read ONE evaluation (`watch_screen.screen_marks`): the same match and the same mark per
   condition on the same bars - also equal to `run_screen` and to the evidence pack's rows.
Synthetic bars only; no broker, no network.
"""
import asyncio
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.screener import compile_screen
from app.screener import evidence as E
from app.screener import routes as R
from app.screener import watch_screen as W
from app.screener.runtime import SymbolData, run_screen
from tests.test_auth_api import _session_factory, client
from tests.test_copilot_v2_chat import _flag
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _flag as _screener_flag

SCREEN = "close > SMA(close, 5) AND RSI(3) > 40 AND (volume > 1.2 * SMA(volume, 5) OR close > high[1])"


def _run(coro):
    return asyncio.run(coro)


def _daily(count=80, slope=0.5, start="2026-06-01", wiggle=3.0) -> pd.DataFrame:
    i = np.arange(count, dtype=float)
    close = 100 + slope * i + wiggle * np.sin(i / 3)
    vol = 1000 + 300 * np.cos(i / 2)
    idx = pd.date_range(start, periods=count, freq="1D", tz="UTC")
    return pd.DataFrame({"open": close - 0.5, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": vol}, index=idx)


UNIVERSE = {"UPA": _daily(slope=0.8), "UPB": _daily(slope=0.3, wiggle=1.0), "DOWNA": _daily(slope=-0.9), "DOWNB": _daily(slope=-0.2, wiggle=4.0)}


@pytest.fixture
def on(monkeypatch):
    _screener_flag(True)
    _run(_flag("watchlist_screen", True))

    async def fake_load(session, user, ast, validated, params, symbols, exchange, base_tf, tier=None, **kw):
        universe = [SymbolData(s, {base_tf: UNIVERSE[s]}, exchange=exchange) for s in symbols if s in UNIVERSE]
        problems = {s: "not stored yet" for s in symbols if s not in UNIVERSE}
        return universe, problems, "lake:daily", {}
    monkeypatch.setattr(R, "load_universe", fake_load)
    yield
    _run(_flag("watchlist_screen", False))
    _screener_flag(False)


def _screen(headers, source=SCREEN, name="My trend screen"):
    out = client.post("/api/screener/screens", json={"name": name, "source": source, "base_tf": "1d"}, headers=headers)
    assert out.status_code == 201, out.text
    return out.json()["id"]


def _check(headers, screen_id, key="o:1", symbols=None):
    out = client.post("/api/screener/watchlist-screen/check", headers=headers,
                      json={"list_key": key, "list_name": "Swing", "screen_id": screen_id, "symbols": symbols or list(UNIVERSE)})
    assert out.status_code == 200, out.text
    return out.json()


def _notes(user_id):
    from app.db.models import NotificationRecord

    async def go():
        async with _session_factory() as session:
            return [r.title + " | " + r.message for r in await session.scalars(
                select(NotificationRecord).where(NotificationRecord.user_id == user_id).order_by(NotificationRecord.id))]
    return _run(go())


# --- the flag ------------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default_and_every_route_answers_503_while_off():
    from app.platform import controls
    assert "watchlist_screen" in controls.DEFAULT_OFF_FLAGS
    _screener_flag(True)
    headers, _ = _owner("m91-off@example.com")
    assert client.get("/api/screener/watchlist-screen/screens", headers=headers).status_code == 503
    assert client.get("/api/screener/watchlist-screen/settings", headers=headers).status_code == 503
    _screener_flag(False)


# --- W1 ------------------------------------------------------------------------------------------------------------------

def test_w1_the_choice_is_remembered_per_watchlist_and_only_his_own_screens_are_offered(on):
    headers, _ = _owner("m91-w1@example.com")
    other, _ = _owner("m91-w1-other@example.com")
    mine = _screen(headers)
    theirs = _screen(other, name="Not mine")
    listed = client.get("/api/screener/watchlist-screen/screens", headers=headers).json()["screens"]
    assert [s["id"] for s in listed] == [mine] and listed[0]["usable"] is True        # his own only; no default screen
    assert client.get("/api/screener/watchlist-screen/settings", headers=headers).json() == {"lists": {}}   # nothing chosen
    assert client.put("/api/screener/watchlist-screen/settings/o:1", json={"screen_id": mine}, headers=headers).status_code == 200
    assert client.put("/api/screener/watchlist-screen/settings/s:index-nifty50", json={"screen_id": mine, "matches_only": True},
                      headers=headers).status_code == 200
    got = client.get("/api/screener/watchlist-screen/settings", headers=headers).json()["lists"]
    assert got["o:1"] == {"screen_id": mine, "matches_only": False, "alert": False, "telegram": False}
    assert got["s:index-nifty50"]["matches_only"] is True and "o:2" not in got
    assert client.put("/api/screener/watchlist-screen/settings/o:2", json={"screen_id": theirs}, headers=headers).status_code == 404
    assert client.put("/api/screener/watchlist-screen/settings/bad key", json={"screen_id": mine}, headers=headers).status_code in (404, 422)
    assert client.put("/api/screener/watchlist-screen/settings/o:1", json={"screen_id": None}, headers=headers).status_code == 200
    assert "o:1" not in client.get("/api/screener/watchlist-screen/settings", headers=headers).json()["lists"]
    assert client.get("/api/screener/watchlist-screen/settings", headers=other).json() == {"lists": {}}    # per user


def test_a_screen_that_ranks_symbols_against_each_other_is_listed_as_not_usable(on):
    headers, _ = _owner("m91-rank@example.com")
    sid = _screen(headers, source="PercentileRank(close / close[5]) > 50", name="Rank")
    listed = client.get("/api/screener/watchlist-screen/screens", headers=headers).json()["screens"]
    assert listed[0]["usable"] is False and "ranks" in listed[0]["why"]
    assert client.post("/api/screener/watchlist-screen/check", headers=headers,
                       json={"list_key": "o:1", "screen_id": sid, "symbols": ["UPA"]}).status_code == 422


# --- W2 / C2 -------------------------------------------------------------------------------------------------------------

def test_w2_the_check_answers_the_matches_with_each_symbols_marks_and_the_run_shape(on):
    headers, _ = _owner("m91-w2@example.com")
    sid = _screen(headers)
    body = _check(headers, sid, symbols=[*UNIVERSE, "MISSING"])
    ast, validated = compile_screen(SCREEN, base_tf="1d")
    expected = sorted(m.symbol for m in run_screen(ast, validated, [SymbolData(s, {"1d": f}) for s, f in UNIVERSE.items()], base_tf="1d")
                      if m.matched)
    assert sorted(body["matched"]) == expected and 0 < len(expected) < len(UNIVERSE)      # the screener's own answer
    rows = {r["symbol"]: r for r in body["results"]}
    assert rows["MISSING"]["matched"] is False and rows["MISSING"]["reason"]              # never dropped, with its reason
    for s in expected:
        assert set(rows[s]["marks"]) <= {E.MET, E.NOT_NEEDED} and rows[s]["marks"].count(E.MET) >= 3
    assert any(E.FAILED in rows[s]["marks"] for s in UNIVERSE if s not in expected)
    assert body["funnel"]["universe"] == len(UNIVERSE) + 1 and body["scanned"] == len(UNIVERSE) + 1 and "budget" in body
    assert body["screen"]["name"] == "My trend screen" and body["base_tf"] == "1d"


def test_c2_the_list_and_the_chart_give_the_same_mark_per_condition_on_the_same_bars(on):
    headers, _ = _owner("m91-c2@example.com")
    sid = _screen(headers)
    listed = {r["symbol"]: r for r in _check(headers, sid)["results"]}
    for symbol in UNIVERSE:
        chart = client.post("/api/screener/watchlist-screen/overlay", json={"screen_id": sid, "symbol": symbol}, headers=headers).json()
        assert chart["available"] is True
        assert [row["state"] for row in chart["legend"]] == listed[symbol]["marks"]       # the same ✓ / ✗ per condition
        assert chart["matched"] == listed[symbol]["matched"]
        assert (chart["hits"][-1:] == [chart["as_of"]["bar_start"]]) == listed[symbol]["matched"]   # the newest marker too


def test_c2_the_shared_evaluation_equals_run_screen_and_the_evidence_packs_rows():
    ast, validated = compile_screen(SCREEN, base_tf="1d")
    for symbol, frame in UNIVERSE.items():
        data = SymbolData(symbol, {"1d": frame})
        marks = W.screen_marks(ast, validated, data, base_tf="1d")
        assert marks["matched"] == run_screen(ast, validated, [data], base_tf="1d")[0].matched
        pack = E.build_pack(ast, validated, data, base_tf="1d", debug=True)
        assert [(r["id"], r["state"]) for r in marks["rows"]] == [(r["id"], r["state"]) for r in pack["rows"]]
        assert [r["value"] for r in marks["rows"]] == [r["verify"] for r in pack["rows"]]   # the same value line


def test_c2_every_marker_is_the_screen_run_on_bars_cut_at_that_bar():
    """No look-ahead: each bar's marker equals the list's answer had the list been checked when that bar closed."""
    ast, validated = compile_screen(SCREEN, base_tf="1d")
    frame = UNIVERSE["UPB"]
    marks = W.screen_marks(ast, validated, SymbolData("UPB", {"1d": frame}), base_tf="1d", bars=30)
    assert len(marks["hits"]) == 30 and any(marks["hits"]) and not all(marks["hits"])
    for k in range(30):
        cut = frame.iloc[: len(frame) - 30 + k + 1]
        assert marks["hits"][k] == W.screen_marks(ast, validated, SymbolData("UPB", {"1d": cut}), base_tf="1d")["matched"], k


# --- C1 ------------------------------------------------------------------------------------------------------------------

def test_c1_the_overlay_draws_the_screens_own_parts_markers_and_the_legend(on):
    headers, _ = _owner("m91-c1@example.com")
    sid = _screen(headers)
    out = client.post("/api/screener/watchlist-screen/overlay", json={"screen_id": sid, "symbol": "UPA"}, headers=headers).json()
    assert out["available"] and out["base_tf"] == "1d" and out["screen"]["name"] == "My trend screen"
    assert len(out["legend"]) == 4 and all(r["value"] and r["state"] in (E.MET, E.FAILED, E.NOT_NEEDED, E.UNKNOWN) for r in out["legend"])
    assert {r["text"] for r in out["legend"]} >= {"close > SMA(close, 5)", "RSI(3) > 40"}
    drawn = [d for d in out["drawables"]]
    assert drawn and all(d["row"] in {r["id"] for r in out["legend"]} for d in drawn)      # only the screen's own conditions
    series = [s["name"] for d in drawn for s in d.get("series", [])]
    assert any("SMA(close, 5)" in s for s in series)                                       # his own parameter, not a default
    assert set(out["hits"]) <= set(out["bars"]["t"]) and out["hits"]
    missing = client.post("/api/screener/watchlist-screen/overlay", json={"screen_id": sid, "symbol": "NOPE"}, headers=headers).json()
    assert missing["available"] is False and missing["reason"]


def test_another_members_screen_is_a_404_for_the_check_and_the_overlay(on):
    headers, _ = _owner("m91-own@example.com")
    other, _ = _owner("m91-own-other@example.com")
    theirs = _screen(other)
    assert client.post("/api/screener/watchlist-screen/check", headers=headers,
                       json={"list_key": "o:1", "screen_id": theirs, "symbols": ["UPA"]}).status_code == 404
    assert client.post("/api/screener/watchlist-screen/overlay", json={"screen_id": theirs, "symbol": "UPA"}, headers=headers).status_code == 404


# --- W3 ------------------------------------------------------------------------------------------------------------------

def _ist(y, m, d, hh, mm):
    from app.market_data.calendar import IST
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_w3_the_next_check_is_the_next_bar_close_of_the_screens_timeframe():
    # a Wednesday: during the session a 15-minute screen is due at the next quarter's close, a daily one at the close
    assert W.next_close(_ist(2026, 11, 4, 10, 2), "15m") == _ist(2026, 11, 4, 10, 15).astimezone(UTC)
    assert W.next_close(_ist(2026, 11, 4, 10, 2), "1d") == _ist(2026, 11, 4, 15, 30).astimezone(UTC)
    # after the close: the next trading day (Friday -> Monday; a holiday is skipped)
    assert W.next_close(_ist(2026, 11, 6, 16, 0), "1d") == _ist(2026, 11, 9, 15, 30).astimezone(UTC)
    assert W.next_close(_ist(2026, 11, 6, 16, 0), "1d", holidays={date(2026, 11, 9)}) == _ist(2026, 11, 10, 15, 30).astimezone(UTC)
    assert W.next_close(_ist(2026, 11, 6, 16, 0), "5m") == _ist(2026, 11, 9, 9, 20).astimezone(UTC)
    # a weekly screen only when the week's last session has closed
    assert W.next_close(_ist(2026, 11, 4, 10, 0), "1w") == _ist(2026, 11, 6, 15, 30).astimezone(UTC)
    assert W.next_close(_ist(2026, 11, 4, 10, 0), "2h") is None


def test_w3_the_check_says_when_the_next_bar_closes(on):
    headers, _ = _owner("m91-w3@example.com")
    body = _check(headers, _screen(headers))
    assert body["next_check_at"] > datetime.now(UTC).timestamp() * 1000 and body["bar"]


# --- W4 ------------------------------------------------------------------------------------------------------------------

def test_w4_no_alert_while_the_choice_is_off_the_default(on):
    headers, me = _owner("m91-w4off@example.com")
    sid = _screen(headers)
    client.put("/api/screener/watchlist-screen/settings/o:1", json={"screen_id": sid}, headers=headers)
    for _ in range(2):
        assert _check(headers, sid)["alerted"] == []
    assert _notes(me["id"]) == []


def test_w4_alerts_only_stocks_that_newly_enter_the_matches(on, monkeypatch):
    headers, me = _owner("m91-w4@example.com")
    sid = _screen(headers)
    client.put("/api/screener/watchlist-screen/settings/o:7", json={"screen_id": sid, "alert": True}, headers=headers)
    first = _check(headers, sid, key="o:7")
    assert first["matched"] and first["alerted"] == [] and _notes(me["id"]) == []            # the baseline: nothing alerted
    assert _check(headers, sid, key="o:7")["alerted"] == []                                    # unchanged: nothing again
    newcomer = "DOWNA"
    assert newcomer not in first["matched"]
    monkeypatch.setitem(UNIVERSE, newcomer, UNIVERSE["UPA"])                                  # it now meets the screen
    third = _check(headers, sid, key="o:7")
    assert third["alerted"] == [newcomer]
    notes = _notes(me["id"])
    assert len(notes) == 1 and newcomer in notes[0] and "My trend screen" in notes[0]
    for word in ("buy", "sell", "target", "stop"):
        assert word not in notes[0].lower()
    assert _check(headers, sid, key="o:7")["alerted"] == []                                    # once, while it stays matched
    # a batch of other symbols does not forget the ones it did not check
    assert _check(headers, sid, key="o:7", symbols=["UPB"])["alerted"] == []
    assert _check(headers, sid, key="o:7")["alerted"] == []
    assert len(_notes(me["id"])) == 1


def test_w4_alert_text_names_the_stocks_and_the_screen():
    from app.screener.watch_screen_routes import alert_text
    title, line = alert_text("Trend", "Swing", ["A", "B"])
    assert title == "Trend: 2 new matches" and line == 'A, B now meet your screen "Trend" in Swing.'
    title, line = alert_text("Trend", "", [f"S{i}" for i in range(12)])
    assert "and 2 more" in line and title.endswith("12 new matches")
