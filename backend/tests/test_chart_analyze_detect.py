"""M84: Advanced analysis from the uploaded picture alone - the symbol and timeframe are read from what is printed on the
chart (the same AI picture read as "AI only"), mapped to ATP's instruments / timeframes, and the user's own value wins.

Made-up data only: a synthetic picture, the fake engine (tests/chart_analyze_fakes.py) and a fake `ask` with recorded
replies - no paid model call, no network. A few made-up NSE equity rows stand in for the instrument master.
"""
import json
import uuid

import pytest
from sqlalchemy import delete

from app.chart_analyze import detect, hybrid, service
from app.chart_analyze.engine import Answer, EngineUnavailable
from app.db.models import InstrumentRecord
from tests.chart_analyze_fakes import answer
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import BASE, _run
from tests.test_chart_analyze_hybrid import PNG, SYMBOL, _hybrid_owner, _isolated, _lake, _zones  # noqa: F401 - shared fixtures

TAG = uuid.uuid4().hex[:6].upper()
TWIN_A, TWIN_B, FUT = f"TWA{TAG}", f"TWB{TAG}", f"{SYMBOL}26NOVFUT"
ROWS = [(SYMBOL, "NSE", "EQ", f"Made Up {TAG} Ltd"), (TWIN_A, "NSE", "EQ", f"Twin {TAG} Limited"), (TWIN_B, "NSE", "EQ", f"Twin {TAG} Ltd"),
        (FUT, "NFO", "FUT", None)]


async def _drop_master() -> None:
    async with _session_factory() as session:
        await session.execute(delete(InstrumentRecord).where(InstrumentRecord.instrument_key.like(f"M84{TAG}|%")))
        await session.commit()


async def _put_master() -> None:
    async with _session_factory() as session:
        session.add_all([InstrumentRecord(broker="upstox", exchange=ex, segment=None, instrument_key=f"M84{TAG}|{i}", tradingsymbol=sym, name=name,
                                          underlying=SYMBOL if kind == "FUT" else sym, instrument_type=kind, lot_size=1, tick_size=0.05, weekly=False)
                         for i, (sym, ex, kind, name) in enumerate(ROWS)])
        await session.commit()


@pytest.fixture(scope="module", autouse=True)
def _master():
    _run(_drop_master())
    _run(_put_master())
    yield
    _run(_drop_master())


def _printed(symbol, timeframe, exchange="NSE"):
    out = answer("forming")
    out["recognised"] = {"symbol": symbol, "exchange": exchange, "timeframe": timeframe, "first_bar": None, "last_bar": None}
    return out


def _fake_ask(monkeypatch, read=None, *, read_raises=None):
    """Recorded replies: the picture read ("image ..." instructions) answers `read`; the hybrid AI part answers the
    fake engine's contract. Every call is recorded; none leaves the process."""
    calls = []

    async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
        kind = "read" if instr.user.startswith("image ") else "hybrid"
        calls.append({"kind": kind, "png": png})
        if kind == "read" and read_raises is not None:
            raise read_raises
        reply = read if kind == "read" else answer("forming")
        return Answer(text=json.dumps(reply), error=None, provider="anthropic", model="claude-test", cost_usd=0.01, billed_to_user_key=False)
    monkeypatch.setattr(service, "ask", fake_ask)
    return calls


def _read_as(monkeypatch, symbol: str):
    """ATP's reading for `symbol` is made from the test lake's candles (stored under SYMBOL); the pick it was asked
    for is recorded, so the test sees exactly what the detection handed on."""
    seen = []
    real = hybrid.read

    async def fake_read(session, user, pick, *, now=None):
        seen.append(dict(pick))
        out = await real(session, user, {**pick, "symbol": SYMBOL}, now=now)
        return {**out, "symbol": pick["symbol"]} if pick["symbol"] == symbol else out
    monkeypatch.setattr(hybrid, "read", fake_read)
    return seen


def _add(who, **data):
    return client.post(f"{BASE}/items", headers=who["headers"], files={"file": ("chart.png", PNG, "image/png")}, data={"mode": "hybrid", **data})


def _analyze(who, item_id, body=None):
    return client.post(f"{BASE}/items/{item_id}/analyze", headers=who["headers"], **({"json": body} if body is not None else {}))


# --- end to end ----------------------------------------------------------------------------------------------------------

def test_detection_reads_nifty_50_and_15_from_the_chart(monkeypatch):
    who = _hybrid_owner("m84-ok")
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch, _printed("NIFTY 50", "15"))
    seen = _read_as(monkeypatch, "NIFTY 50")
    item = _add(who).json()
    assert item["pick"] == {"symbol": None, "timeframe": None}
    body = _analyze(who, item["id"]).json()
    assert body["status"] == "done", body
    assert seen == [{"symbol": "NIFTY 50", "timeframe": "15m"}]
    res = body["hybrid"]
    assert res["pick"] == {"symbol": "NIFTY 50", "timeframe": "15m"} and res["pick_source"] == {"symbol": "chart", "timeframe": "chart"}
    assert res["read_from_chart"] == {"symbol": "NIFTY 50", "timeframe": "15m", "symbol_text": "NIFTY 50", "timeframe_text": "15"}
    assert res["reading"]["symbol"] == "NIFTY 50" and res["reading"]["timeframe"] == "15m"
    # the picture read got the user's picture; the hybrid AI part got ATP's own chart, never the picture
    assert [c["kind"] for c in calls] == ["read", "hybrid"] and calls[0]["png"] == PNG and calls[1]["png"] != PNG
    assert body["cost_usd"] == pytest.approx(0.02) and body["needs"] == []
    # every price still comes from the stored candles: the picture read's made-up prices appear nowhere in the reading
    assert "120.5" not in json.dumps(res["reading"]) and res["approximate_prices"] is False


def test_an_unmappable_name_gives_the_exact_reason_and_asks_only_for_the_symbol(monkeypatch):
    who = _hybrid_owner("m84-unmapped")
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch, _printed("Zzqx Holdings", "15m"))
    item = _add(who).json()
    body = _analyze(who, item["id"]).json()
    assert body["status"] == "ready" and body["hybrid"] is None
    assert body["needs"] == [{"field": "symbol", "reason": "“Zzqx Holdings” from your chart is not in ATP's instrument list.",
                              "ask": detect.ASK["symbol"]}]
    assert body["read_from_chart"]["timeframe"] == "15m" and body["read_from_chart"]["symbol"] is None
    # the user types the symbol in place: no second picture read, the read timeframe is kept
    done = _analyze(who, item["id"], {"symbol": SYMBOL.lower()}).json()
    assert done["id"] == item["id"] and done["status"] == "done", done
    assert done["hybrid"]["pick"] == {"symbol": SYMBOL, "timeframe": "15m"}
    assert done["hybrid"]["pick_source"] == {"symbol": "you", "timeframe": "chart"}
    assert [c["kind"] for c in calls] == ["read", "hybrid"]


def test_a_missing_timeframe_asks_only_for_the_timeframe(monkeypatch):
    who = _hybrid_owner("m84-notf")
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch, _printed(f"NSE:{SYMBOL}", None))
    item = _add(who).json()
    body = _analyze(who, item["id"]).json()
    assert body["status"] == "ready"
    assert body["needs"] == [{"field": "timeframe", "reason": detect.TF_NOT_PRINTED, "ask": detect.ASK["timeframe"]}]
    assert body["read_from_chart"]["symbol"] == SYMBOL
    bad = _analyze(who, item["id"], {"timeframe": "7m"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_timeframe"
    done = _analyze(who, item["id"], {"timeframe": "15m"}).json()
    assert done["status"] == "done" and done["hybrid"]["pick"] == {"symbol": SYMBOL, "timeframe": "15m"}
    assert done["hybrid"]["pick_source"] == {"symbol": "chart", "timeframe": "you"} and [c["kind"] for c in calls] == ["read", "hybrid"]


def test_the_users_value_wins_over_the_read_one_and_a_change_reruns_in_place(monkeypatch):
    who = _hybrid_owner("m84-override")
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch, _printed("NIFTY 50", "15"))
    item = _add(who, symbol=SYMBOL).json()
    assert item["pick"] == {"symbol": SYMBOL, "timeframe": None}
    body = _analyze(who, item["id"]).json()
    assert body["status"] == "done", body
    res = body["hybrid"]
    assert res["pick"] == {"symbol": SYMBOL, "timeframe": "15m"} and res["pick_source"] == {"symbol": "you", "timeframe": "chart"}
    assert res["read_from_chart"]["symbol_text"] == "NIFTY 50" and res["read_from_chart"]["timeframe"] == "15m"
    # both values given: no picture read at all
    both = _analyze(who, _add(who, symbol=SYMBOL, timeframe="15m").json()["id"]).json()
    assert both["status"] == "done" and both["hybrid"]["read_from_chart"] is None and both["hybrid"]["pick_source"] == {"symbol": "you", "timeframe": "you"}
    assert [c["kind"] for c in calls] == ["read", "hybrid", "hybrid"]
    # the same values again: the same analysis; a changed value: a new analysis of the same picture, no second read
    assert _analyze(who, item["id"], {"symbol": SYMBOL}).json()["id"] == item["id"]
    other_symbol = f"HYO{TAG}"
    again = _analyze(who, item["id"], {"symbol": other_symbol}).json()
    assert again["id"] != item["id"] and again["pick"]["symbol"] == other_symbol
    assert again["status"] == "failed" and other_symbol in again["error"]           # no stored candles for it: a plain failure
    assert [c["kind"] for c in calls] == ["read", "hybrid", "hybrid"]
    assert _analyze(who, item["id"]).json()["hybrid"]["pick"]["symbol"] == SYMBOL           # the first analysis is unchanged


def test_no_ai_for_the_picture_read_asks_for_both_values_with_the_reason(monkeypatch):
    who = _hybrid_owner("m84-noai")
    _fake_ask(monkeypatch, read_raises=EngineUnavailable("No AI is set up for chart analysis."))
    item = _add(who, timeframe="15m").json()
    body = _analyze(who, item["id"]).json()
    assert body["status"] == "ready"
    assert body["needs"] == [{"field": "symbol", "reason": "Your chart could not be read: No AI is set up for chart analysis.", "ask": detect.ASK["symbol"]}]


def test_tenancy_another_user_cannot_fill_rerun_or_read_the_item(monkeypatch):
    who = _hybrid_owner("m84-owner")
    _zones(monkeypatch)
    _fake_ask(monkeypatch, _printed("Zzqx Holdings", "15m"))
    item = _add(who).json()
    _analyze(who, item["id"])
    other = _hybrid_owner("m84-other")
    for body in (None, {"symbol": SYMBOL}):
        assert _analyze(other, item["id"], body).status_code == 404
    assert client.get(f"{BASE}/items/{item['id']}", headers=other["headers"]).status_code == 404
    assert client.get(f"{BASE}/items", headers=other["headers"]).json()["items"] == []
    assert _analyze(who, item["id"], {"symbol": SYMBOL}).json()["status"] == "done"
    assert client.get(f"{BASE}/items", headers=other["headers"]).json()["items"] == []
    assert client.post(f"{BASE}/items/{item['id']}/analyze", json={"symbol": SYMBOL}).status_code == 401


# --- the mapping (pure and master) ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("label,tf", [("15", "15m"), ("15m", "15m"), ("15 min", "15m"), ("15minute", "15m"), ("5", "5m"), ("60", "1h"),
                                      ("1H", "1h"), ("1h", "1h"), ("1 hour", "1h"), ("D", "1d"), ("1D", "1d"), ("Daily", "1d"), ("1 day", "1d")])
def test_timeframe_labels_map_to_atps_timeframes(label, tf):
    assert detect.map_timeframe(label) == (tf, None)


@pytest.mark.parametrize("label,why", [(None, detect.TF_NOT_PRINTED), ("", detect.TF_NOT_PRINTED),
                                       ("W", "Your chart is weekly (W); Advanced analysis reads 5-minute, 15-minute, 1-hour or Daily charts."),
                                       ("1M", "Your chart is monthly (1M); Advanced analysis reads 5-minute, 15-minute, 1-hour or Daily charts."),
                                       ("1m", "Your chart is 1-minute (1m); Advanced analysis reads 5-minute, 15-minute, 1-hour or Daily charts."),
                                       ("4h", "Your chart is 4-hour (4h); Advanced analysis reads 5-minute, 15-minute, 1-hour or Daily charts."),
                                       ("Range", "“Range” on your chart is not a timeframe ATP recognises.")])
def test_timeframe_labels_that_do_not_map_say_exactly_why(label, why):
    assert detect.map_timeframe(label) == (None, why)


def test_symbol_names_map_to_atps_instruments_or_say_why():
    async def go():
        async with _session_factory() as session:
            return {text: await detect.map_symbol(session, text) for text in (
                "NIFTY 50", "Nifty Bank", "NIFTY", "BANKNIFTY", "SENSEX", f"NSE:{SYMBOL}", f"{SYMBOL}-EQ", f"Made Up {TAG} Limited",
                f"Twin {TAG}", FUT, "NIFTY NEXT 50", None)}
    got = _run(go())
    assert got["NIFTY 50"] == ("NIFTY 50", None) and got["Nifty Bank"] == ("NIFTY BANK", None) and got["NIFTY"] == ("NIFTY 50", None)
    assert got["BANKNIFTY"] == ("NIFTY BANK", None) and got["SENSEX"] == ("SENSEX", None)
    assert got[f"NSE:{SYMBOL}"] == (SYMBOL, None) and got[f"{SYMBOL}-EQ"] == (SYMBOL, None) and got[f"Made Up {TAG} Limited"] == (SYMBOL, None)
    assert got[f"Twin {TAG}"] == (None, f"“Twin {TAG}” from your chart matches more than one instrument ({TWIN_A}, {TWIN_B}).")
    assert got[FUT][0] is None and "futures or option contract" in got[FUT][1]
    assert got["NIFTY NEXT 50"] == (None, "ATP does not read the NIFTY NEXT 50 index yet.")
    assert got[None] == (None, detect.SYMBOL_NOT_PRINTED)
