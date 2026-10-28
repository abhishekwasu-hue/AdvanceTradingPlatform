"""Day-one fix 2 (README row 19): Find setups before the day's broker login (app/screener/stored_history.py).

A Daily (or coarser) screen with no usable broker session runs on ATP's stored end-of-day history when the flag
`backtest_stored_history` is on; intraday screens, other exchanges and the flag off keep the broker path's 409 exactly.
Equal bars give equal matches (the stored frame is the broker's Daily frame). Fake lake data only, no network.
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.screener import stored_history
from tests.test_backtest_bar_source import SYM, _clear, _days, _rows, _store, _today, _run
from tests.test_auth_api import client
from tests.test_copilot_v2_chat import _flag
from tests.test_deployments_api import _auth


@pytest.fixture
def screener_on():
    _run(_flag("screener_v2", True))
    yield
    _run(_flag("screener_v2", False))
    _run(_flag("backtest_stored_history", False))


def _scan(headers, source="close > 0", base_tf="1d", symbols=(SYM,), exchange="NSE"):
    return client.post("/api/screener/run", json={"source": source, "base_tf": base_tf, "symbols": list(symbols), "exchange": exchange},
                       headers=headers)


def test_applies_only_to_a_daily_or_coarser_nse_screen_refused_for_want_of_a_session():
    refused = HTTPException(status_code=409, detail="No broker session with a valid token.")
    assert stored_history.applies(refused, "1d", {"1d": 50}, "NSE")
    assert stored_history.applies(refused, "1w", {"1w": 20, "1d": 50}, "nse")
    assert not stored_history.applies(refused, "15m", {"15m": 50}, "NSE")
    assert not stored_history.applies(refused, "1d", {"1d": 50, "15m": 20}, "NSE")
    assert not stored_history.applies(refused, "1d", {"1d": 50}, "MCX")
    assert not stored_history.applies(HTTPException(status_code=403, detail="no"), "1d", {"1d": 50}, "NSE")
    assert stored_history.stored_days({"1d": 200}, "1d") == 290
    assert stored_history.stored_days({"1w": 52}, "1w") == 374
    assert stored_history.stored_days({"1M": 1000}, "1M") == stored_history.MAX_DAYS


def test_a_daily_scan_runs_on_stored_history_before_the_broker_login(screener_on):
    headers = _auth("d1-scan-stored@example.com")
    _store(_rows(_days(120, _today() - timedelta(days=1))))
    off = _scan(headers)
    assert off.status_code == 409 and "not stored" not in off.text          # flag off: the broker path's answer, unchanged
    _run(_flag("backtest_stored_history", True))
    out = _scan(headers, symbols=(SYM, "NOSUCHD1"))
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["data_source"] == "lake:daily" and body["matched"] == [SYM]
    missing = next(r for r in body["results"] if r["symbol"] == "NOSUCHD1")
    assert missing == {"symbol": "NOSUCHD1", "matched": False, "reason": "Daily history for NOSUCHD1 is not stored yet.",
                       "code": "not_stored", "action": "login"}                         # SCR-FIX B1: its code + one action
    weekly = _scan(headers, base_tf="1w")
    assert weekly.status_code == 200 and weekly.json()["matched"] == [SYM]
    _clear()


def test_intraday_scans_and_other_exchanges_still_need_the_broker(screener_on):
    headers = _auth("d1-scan-intraday@example.com")
    _run(_flag("backtest_stored_history", True))
    _store(_rows(_days(60, _today() - timedelta(days=1))))
    r = _scan(headers, base_tf="15m")
    assert r.status_code == 409 and "Broker page" in r.json()["detail"]
    r = _scan(headers, exchange="BSE")
    assert r.status_code == 409
    _clear()


def test_the_stored_frame_never_holds_today(screener_on):
    headers = _auth("d1-scan-today@example.com")
    _run(_flag("backtest_stored_history", True))
    _store(_rows(_days(80, _today())))                                       # a bar stamped today is in the lake
    out = _scan(headers, source="close > 0").json()
    assert out["data_source"] == "lake:daily" and out["matched"] == [SYM]

    async def go():
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            got = await stored_history.stored_universe(session, _tenant(headers), HTTPException(status_code=409, detail="x"), [SYM], "NSE",
                                                       "1d", {"1d": 50})
        return got
    universe, problems, source = _run(go())
    assert source == "lake:daily" and not problems
    from app.market_data.calendar import IST
    assert universe[0].frames["1d"].index[-1].tz_convert(IST).date() < _today()
    _clear()


def _tenant(headers):
    return client.get("/api/auth/me", headers=headers).json()["tenant_id"]
