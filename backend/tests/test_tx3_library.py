"""U9 TX3 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3 / §4): regime-aware greying of the screen library and the
setup-of-the-day digest (`app/screener/regime_today.py`, `app/screener/digest.py`; flag `screener_tx3`, off).

The regime is read from closed sessions of the shared history as of a time: the lake rows below live in 2018-2019 so no
other test's bars are in the window, and a bar after `now` (or ingested after it) never changes the answer."""
import asyncio
import copy
import json
import types
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from app.core.models import OHLCVBar
from app.db.models import MdCandleRecord, ScreenDigestRecord
from app.market_lake.bhavcopy_backfill import bar_end
from app.platform import controls
from app.screener import digest as dg
from app.screener import regime_today as rt
from app.screener import taxonomy as tx
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

SOURCE = "test_tx3"
NOW = datetime(2019, 12, 31, 12, 0, tzinfo=timezone.utc)           # a Tuesday; the last closed session is 30 Dec
FLAGS = ("screener_v2", "screener_gallery", dg.FLAG)


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def tx3_on():
    _set_flags(**{f: True for f in FLAGS})
    yield
    _set_flags(**{f: False for f in FLAGS})


def _sessions(n, last=date(2019, 12, 30)):
    return [d.date() for d in pd.bdate_range(end=last, periods=n)]


def _seed(key, closes, days, ingested=None, version=1):
    async def go():
        async with _session_factory() as session:
            for day, c in zip(days, closes):
                session.add(MdCandleRecord(instrument_key=key, timeframe="day", ts=bar_end(day), source=SOURCE, version=version, open=float(c),
                                           high=float(c) + 1, low=float(c) - 1, close=float(c), volume=1000,
                                           ingested_at=ingested or bar_end(day) + timedelta(hours=3)))
            await session.commit()
    _run(go())


def _clear():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.source == SOURCE))
            await session.commit()
    _run(go())


@pytest.fixture
def lake():
    _clear()
    yield
    _clear()


def _today(now=NOW):
    async def go():
        async with _session_factory() as session:
            return await rt.today(session, now)
    return _run(go())


def _rising(n=320):
    t = np.arange(n)
    return 10000 + 6.0 * t + 120 * np.sin(t / 5)


# --- taxonomy: the regimes a screen is designed for --------------------------------------------------------------------

def test_the_regime_filing_lints_and_is_in_the_payload():
    assert tx.lint() == []
    tax = tx.load()
    assert {r["id"]: r["axis"] for r in tax.regimes} == {"trend": "trend", "range": "trend", "high_vol": "volatility", "low_vol": "volatility"}
    by_id = {s.id: s for s in tax.screens}
    assert by_id["starter.oversold"].regimes == ("range",) and by_id["legends.crabel_nr7"].regimes == ("low_vol",)
    assert by_id["breakouts.swing_1h_range"].regimes == ()                                  # absent = any regime
    payload = tx.payload(tax)
    assert payload["regimes"] and payload["screens"][0]["regimes"] == ["trend"]


@pytest.mark.parametrize("mutate,problem", [
    (lambda d: d["screens"][0].__setitem__("regimes", ["sideways"]), "regimes must be a list from the vocabulary"),
    (lambda d: d["screens"][0].__setitem__("regimes", ["trend", "range"]), "at most one regime per axis"),
    (lambda d: d["screens"][0].__setitem__("regimes", "trend"), "regimes must be a list"),
    (lambda d: d["regimes"].append({"id": "calm", "axis": "mood", "label": "Calm"}), "an axis"),
    (lambda d: d["regimes"].__setitem__(0, {"id": "trend", "axis": "trend", "label": "Best trend to buy"}), "advice wording"),
])
def test_broken_regime_filings_fail_the_lint(mutate, problem):
    data = json.loads(tx.DATA_FILE.read_text(encoding="utf-8"))
    broken = copy.deepcopy(data)
    mutate(broken)
    problems = tx.parse(broken)[1]
    assert any(problem in p for p in problems), problems


# --- today's regime ------------------------------------------------------------------------------------------------------

def test_trend_axis_needs_breadth_to_confirm_and_vol_axis_uses_vix_percentiles():
    assert rt.trend_axis("UP", "x", (300, 100)).value == "trend"
    assert rt.trend_axis("UP", "x", (100, 300)).value is None                             # breadth disagrees: unclear
    assert rt.trend_axis("DOWN", "x", (100, 300)).value == "trend"
    assert rt.trend_axis("RANGE", "x", None).value == "range"
    assert rt.trend_axis("UP", "x", None).value is None and rt.trend_axis(None, "x", (1, 0)).value is None
    calm = pd.Series(np.r_[np.linspace(20, 30, 249), [12.0]])
    wild = pd.Series(np.r_[np.linspace(12, 20, 249), [28.0]])
    assert rt.vol_axis(calm).value == "low_vol" and rt.vol_axis(wild).value == "high_vol"
    assert rt.vol_axis(pd.Series(np.r_[np.linspace(10, 30, 249), [20.0]])).value is None
    assert rt.vol_axis(pd.Series([15.0] * 5)).value is None


def test_index_trend_reads_ties_symmetrically():
    n = 300
    idx = pd.date_range("2018-01-01", periods=n, freq="B", tz="UTC")
    flat = 200 + np.abs((np.arange(n) % 40) - 20) * 1.0                                   # equal swing highs and lows
    frame = pd.DataFrame({"open": flat, "high": flat + 1, "low": flat - 1, "close": flat, "volume": 1.0}, index=idx)
    assert rt.trend_of(frame)[0] == "RANGE"                                                # not DOWN (TX2-3)
    up = pd.DataFrame({"open": _rising(n), "high": _rising(n) + 1, "low": _rising(n) - 1, "close": _rising(n), "volume": 1.0}, index=idx)
    assert rt.trend_of(up)[0] == "UP"
    assert rt.trend_of(up.iloc[:100])[0] is None                                           # warm-up: no reading on a short history


def test_today_reads_closed_sessions_of_the_shared_history_only(lake):
    days = _sessions(320)
    _seed(rt.index_key("NIFTY 50"), _rising(), days)
    _seed(rt.index_key("INDIA VIX"), np.r_[np.linspace(12, 20, 319), [27.0]], days)
    _seed("NSE:AAA", [100, 101], days[-2:])
    _seed("NSE:BBB", [100, 102], days[-2:])
    _seed("NSE:CCC", [100, 99], days[-2:])
    got = _today()
    assert got.session == "2019-12-30" and got.trend.value == "trend" and got.volatility.value == "high_vol"
    assert got.trend.numbers["advancers"] == 2 and got.trend.numbers["decliners"] == 1
    before = got.as_dict()
    # A later session (and a correction ingested after `now`) changes nothing about the answer as of `now`.
    _seed(rt.index_key("INDIA VIX"), [9.0], [date(2019, 12, 31)])
    _seed("NSE:AAA", [50], [date(2019, 12, 31)])
    _seed(rt.index_key("NIFTY 50"), [1.0], [date(2019, 12, 27)], ingested=NOW + timedelta(hours=1), version=2)
    assert _today().as_dict() | {"as_of": None} == before | {"as_of": None}


def test_missing_history_greys_nothing():
    got = _today(datetime(2001, 1, 2, tzinfo=timezone.utc))
    assert got.trend.value is None and got.volatility.value is None
    tax = tx.load()
    assert all(f["fits"] is not False for f in rt.fits(tax, got).values())


def test_fit_greys_only_a_clear_mismatch_and_says_why():
    tax = tx.load()
    by_id = {s.id: s for s in tax.screens}
    ranging = rt.Today("now", "2019-12-30", rt.Axis("range"), rt.Axis("low_vol"))
    assert rt.fit(by_id["starter.momentum"], ranging, tax) == {
        "fits": False, "reason": "Not today's regime: designed for a trending market; today reads range-bound market."}
    assert rt.fit(by_id["starter.oversold"], ranging, tax)["fits"] is True
    assert rt.fit(by_id["legends.crabel_nr7"], ranging, tax)["fits"] is True
    assert rt.fit(by_id["breakouts.swing_1h_range"], ranging, tax)["fits"] is True               # designed for any
    unclear = rt.Today("now", None, rt.Axis(None), rt.Axis("high_vol"))
    assert rt.fit(by_id["starter.momentum"], unclear, tax)["fits"] is None
    assert rt.fit(by_id["legends.crabel_nr7"], unclear, tax)["fits"] is False
    for f in rt.fits(tax, ranging).values():
        assert tx.advice_words(f["reason"]) == []
    assert tx.advice_words(rt.NOTE) == []


# --- the digest ------------------------------------------------------------------------------------------------------

def test_digest_settings_are_validated():
    with pytest.raises(ValueError):
        dg.DigestSettings(send_time="25:00")
    with pytest.raises(ValueError):
        dg.DigestSettings(pinned=[f"starter.s{i}" for i in range(dg.MAX_PINNED + 1)])
    ok = dg.DigestSettings(send_time="09:05", pinned=[" starter.momentum ", "starter.momentum"], symbols=[" tcs", "TCS", ""])
    assert ok.pinned == ["starter.momentum"] and ok.symbols == ["TCS"]
    assert dg.lint() == []


def test_due_only_once_per_trading_day_after_the_chosen_time():
    row = ScreenDigestRecord(enabled=True, send_time="16:00", last_sent_on=None)
    ist = lambda d, h, m: datetime(d.year, d.month, d.day, h, m, tzinfo=timezone(timedelta(hours=5, minutes=30)))  # noqa: E731
    monday = date(2026, 10, 12)
    assert not dg.due(row, ist(monday, 15, 59)) and dg.due(row, ist(monday, 16, 0))
    assert not dg.due(row, ist(date(2026, 10, 11), 17, 0))                                    # Sunday
    assert not dg.due(row, ist(monday, 17, 0), {monday})                                     # a holiday
    row.last_sent_on = monday
    assert not dg.due(row, ist(monday, 18, 0)) and dg.due(row, ist(date(2026, 10, 13), 16, 1))
    row.enabled = False
    assert not dg.due(row, ist(date(2026, 10, 13), 16, 1))


def test_messages_are_one_per_method_folder_without_advice_words():
    digest = {"day": "2026-10-12", "problems": [], "methods": [
        {"method": "indicator", "label": "Indicator", "screens": [{"name": "Momentum", "first": ["AAA", "BBB"], "more": 3, "evidence_id": None}]},
        {"method": "price_action", "label": "Price action", "screens": [{"name": "52-week high", "first": [], "more": 0, "evidence_id": None}]}]}
    out = dg.messages(digest)
    assert len(out) == 2 and out[0].startswith("Setups of the day - Indicator (2026-10-12)") and "Momentum: AAA, BBB (+3 more)" in out[0]
    assert "52-week high: no match" in out[1] and all(dg.NOTE in m and tx.advice_words(m) == [] for m in out)
    assert "could run" in dg.messages({"day": "2026-10-12", "methods": [], "problems": ["x: broken"]})[0]


def _bars(step, days=120):
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    closes = 300 + np.arange(days) * step
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=c - 0.5, high=c + 1, low=c - 1, close=float(c), volume=1000.0) for t, c in zip(idx, closes)]


@pytest.fixture
def fake_bars(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        return _bars({"UP": 2.0, "DOWN": -2.0}.get(symbol, 0.0))
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


def test_the_routes_are_off_by_default():
    headers, _ = _owner("tx3-off@example.com")
    _set_flags(screener_v2=True, screener_gallery=True)
    try:
        for r in (client.get("/api/screener/library/regime", headers=headers), client.get("/api/screener/library/digest", headers=headers),
                  client.put("/api/screener/library/digest", json={}, headers=headers),
                  client.post("/api/screener/library/digest/preview", json={"pinned": ["starter.momentum"], "symbols": ["UP"]}, headers=headers)):
            assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == dg.FLAG
    finally:
        _set_flags(screener_v2=False, screener_gallery=False)
    assert dg.FLAG in controls.DEFAULT_OFF_FLAGS and dg.FLAG in controls.FEATURE_FLAGS


def test_regime_endpoint_gives_the_reading_and_each_screens_fit(tx3_on):
    headers, _ = _owner("tx3-regime@example.com")
    body = client.get("/api/screener/library/regime", headers=headers).json()
    assert body["index"] == "NIFTY 50" and set(body["trend"]) == {"value", "reasons", "numbers"}
    assert "starter.momentum" in body["fits"] and not any(k.startswith("legends.") for k in body["fits"])   # Legends flag off
    assert body["note"] == rt.NOTE


def test_digest_settings_are_the_users_own(tx3_on):
    headers, me = _owner("tx3-digest@example.com")
    assert client.get("/api/screener/library/digest", headers=headers).json()["enabled"] is False
    refused = client.put("/api/screener/library/digest", json={"enabled": True, "pinned": [], "symbols": []}, headers=headers)
    assert refused.status_code == 422
    unknown = client.put("/api/screener/library/digest", json={"pinned": ["legends.crabel_nr7"], "symbols": ["UP"]}, headers=headers)
    assert unknown.status_code == 422                                                       # Legends flag off: not available
    saved = client.put("/api/screener/library/digest", json={"enabled": True, "send_time": "18:30", "pinned": ["starter.momentum"],
                                                             "symbols": ["up", "DOWN"]}, headers=headers)
    assert saved.status_code == 200 and saved.json()["symbols"] == ["UP", "DOWN"] and saved.json()["send_time"] == "18:30"
    other, _ = _owner("tx3-digest-other@example.com")
    assert client.get("/api/screener/library/digest", headers=other).json()["pinned"] == []


def test_preview_groups_the_first_matches_by_method(tx3_on, fake_bars):
    headers, _ = _owner("tx3-preview@example.com")
    saved = client.post("/api/screener/screens", json={"name": "Falling", "source": "close < SMA(close, 10)"}, headers=headers).json()
    r = client.post("/api/screener/library/digest/preview", json={"pinned": ["starter.momentum", f"saved:{saved['id']}", "nope.x"],
                                                                  "symbols": ["UP", "DOWN", "FLAT"]}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    folders = {f["method"]: f for f in out["methods"]}
    assert [f["method"] for f in out["methods"]] == ["indicator", "my_screens"]               # in the taxonomy's method order
    assert folders["indicator"]["screens"][0]["first"] == ["UP"] and folders["my_screens"]["screens"][0]["first"] == ["DOWN"]
    assert any(p.startswith("nope.x:") for p in out["problems"]) and len(out["messages"]) == 2 and out["preview"] is True


def test_the_worker_pass_builds_once_keeps_it_in_the_app_and_sends_only_to_the_own_chat(tx3_on, fake_bars, monkeypatch):
    headers, me = _owner("tx3-worker@example.com")
    client.put("/api/screener/library/digest", json={"enabled": True, "send_time": "16:00", "pinned": ["starter.momentum"],
                                                     "symbols": ["UP", "DOWN"]}, headers=headers)
    when = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)                                 # Monday 16:30 IST
    _run(dg.run_due(_session_factory, when))
    body = client.get("/api/screener/library/digest", headers=headers).json()
    assert body["last_sent_on"] == "2026-10-12" and body["last_status"].startswith("Kept in the app")
    assert body["last_digest"]["methods"][0]["screens"][0]["first"] == ["UP"] and body["messages"][0].startswith("Setups of the day")
    assert _run(dg.run_due(_session_factory, when + timedelta(hours=1))) == 0                 # once per trading day

    # With a linked private chat: one sendMessage per folder, to that chat only.
    from app.screener import evidence_store
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async def chat(session, tenant_id, user_id):
        return "own-chat-42" if user_id == me["id"] else None

    async def bot(session, tenant_id):
        return "123456:ABCDEFGHIJ"
    monkeypatch.setattr(evidence_store, "own_chat", chat)
    monkeypatch.setattr(evidence_store, "_bot", bot)

    async def go():
        async with _session_factory() as session:
            from app.db.models import User
            user = await session.get(User, me["id"])
            digest = await dg.build(session, user, ["starter.momentum"], ["UP", "DOWN"], "NSE", when)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                return await dg.deliver(session, user, digest, http)
    status = _run(go())
    assert status.startswith("Sent to your Telegram: 1 message(s)")
    assert [p for p, _ in sent] == ["/bot123456:ABCDEFGHIJ/sendMessage"] and sent[0][1]["chat_id"] == "own-chat-42"
    assert "123456:ABCDEFGHIJ" not in status


def test_a_digest_is_not_built_while_the_flag_is_off(fake_bars):
    _set_flags(screener_v2=True, screener_gallery=True, **{dg.FLAG: True})
    headers, _ = _owner("tx3-flagoff@example.com")
    client.put("/api/screener/library/digest", json={"enabled": True, "send_time": "16:00", "pinned": ["starter.momentum"],
                                                     "symbols": ["UP"]}, headers=headers)
    _set_flags(screener_v2=False, screener_gallery=False, **{dg.FLAG: False})
    _run(dg.run_due(_session_factory, datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc)))

    async def row():
        async with _session_factory() as session:
            from sqlalchemy import select
            from app.db.models import User
            user_id = (await session.scalar(select(User).where(User.email == "tx3-flagoff@example.com"))).id
            return await session.scalar(select(ScreenDigestRecord).where(ScreenDigestRecord.user_id == user_id))
    got = _run(row())
    assert got.last_status == "Not built: the digest is turned off for this organisation." and got.last_digest_json is None
