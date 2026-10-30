"""M55 T3 (data honesty): backtest, scanner and S&R read the server's own candles; a client label is never trusted.

- A request that names symbol + timeframe (no candles, or data_mode "server") gets its bars from the server loader
  (`bar_source.resolve`, the one `/api/backtest/bars` uses) and the run's data_source is the loader's label.
- Posted candles are "paste your own data": recorded as "uploaded" whatever the client called them.
- data_mode "paste" with no candles is a plain 422; data_mode "server" ignores posted candles.
- With UNIVERSE_SYNC_ENABLED off the system / universe lists say they are the fallback copy.
"""
import pandas as pd
import pytest

from app.backtest import bar_source, server_candles
from app.core import config
from tests.test_tpl2_routes import _org, _set_flag, client
from tests.utils import make_series


def _payload(df: pd.DataFrame) -> list[dict]:
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
             "volume": float(r.volume)} for ts, r in df.iterrows()]


def _series(n: int = 300, rising: bool = True) -> pd.DataFrame:
    return make_series([100.0 + (i if rising else -i) * 0.2 for i in range(n)])


@pytest.fixture
def loader(monkeypatch):
    """bar_source.resolve stubbed: records every call and answers with the server's own bars."""
    calls: list[dict] = []
    frames: dict[str, pd.DataFrame] = {}

    async def resolve(session, tenant_id, symbol, exchange, timeframe, *, lookback_days=30, user=None, broker=None, **_):
        calls.append({"symbol": symbol, "timeframe": timeframe, "lookback_days": lookback_days, "broker": broker,
                      "exchange": exchange, "tenant": tenant_id})
        frame = frames.get(symbol.upper())
        if frame is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail=bar_source.not_stored(symbol))
        return bar_source.BacktestBars(frame, "lake:daily", "stored", bar_source.STORED_WORDS)

    monkeypatch.setattr(bar_source, "resolve", resolve)
    return {"calls": calls, "frames": frames}


@pytest.fixture(scope="module")
def org():
    return _org("m55t3")


def _bt(**extra) -> dict:
    return {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY", "base_timeframe": "1min", **extra}


# ----------------------------------------------------------------------------------------------------------- backtest
def test_backtest_with_symbol_and_timeframe_loads_server_bars_and_sets_the_source(loader, org):
    loader["frames"]["NIFTY"] = _series()
    res = client.post("/api/backtest", headers=org["headers"], json=_bt(lookback_days=20, data_source="broker:zerodha"))
    assert res.status_code == 200, res.text
    assert res.json()["data_source"] == "lake:daily"                        # what the server loaded, not the client label
    assert loader["calls"][0] == {"symbol": "NIFTY", "timeframe": "1min", "lookback_days": 20, "broker": None, "exchange": "NSE",
                                  "tenant": org["tenant_id"]}
    runs = client.get("/api/backtests", headers=org["headers"]).json()
    assert runs[0]["id"] == res.json()["run_id"] and runs[0]["data_source"] == "lake:daily" and runs[0]["bars"] == 300


def test_server_mode_ignores_posted_candles(loader, org):
    loader["frames"]["NIFTY"] = _series(250)
    res = client.post("/api/backtest", headers=org["headers"], json=_bt(data_mode="server", candles=_payload(_series(120, rising=False))))
    assert res.status_code == 200, res.text
    assert res.json()["data_source"] == "lake:daily" and len(loader["calls"]) == 1
    assert client.get("/api/backtests", headers=org["headers"]).json()[0]["bars"] == 250


@pytest.mark.parametrize("label", ["broker:upstox", "lake:daily", "sample", "uploaded"])
def test_posted_candles_are_recorded_as_uploaded_whatever_the_label(loader, org, label):
    res = client.post("/api/backtest", headers=org["headers"], json=_bt(candles=_payload(_series()), data_source=label))
    assert res.status_code == 200, res.text
    assert res.json()["data_source"] == "uploaded" and not loader["calls"]
    assert client.get("/api/backtests", headers=org["headers"]).json()[0]["data_source"] == "uploaded"


def test_paste_mode_runs_on_the_pasted_candles_and_needs_some(loader, org):
    ok = client.post("/api/backtest", headers=org["headers"], json=_bt(data_mode="paste", candles=_payload(_series())))
    assert ok.status_code == 200 and ok.json()["data_source"] == "uploaded" and not loader["calls"]
    empty = client.post("/api/backtest", headers=org["headers"], json=_bt(data_mode="paste"))
    assert empty.status_code == 422 and empty.json()["detail"] == server_candles.PASTE_NEEDS_CANDLES


def test_no_server_bars_is_a_plain_refusal_never_sample(loader, org):
    res = client.post("/api/backtest", headers=org["headers"], json=_bt(symbol="NOSUCH"))
    assert res.status_code == 409 and res.json()["detail"] == bar_source.not_stored("NOSUCH")


def test_walk_forward_and_monte_carlo_load_server_bars(loader, org):
    loader["frames"]["NIFTY"] = _series(400)
    wf = client.post("/api/backtest/walk-forward?folds=2", headers=org["headers"], json=_bt())
    assert wf.status_code == 200, wf.text
    mc = client.post("/api/backtest/monte-carlo?runs=100", headers=org["headers"], json=_bt())
    assert mc.status_code == 200, mc.text
    assert mc.json()["backtest"]["data_source"] == "lake:daily" and len(loader["calls"]) == 2


# ------------------------------------------------------------------------------------------------------------ scanner
def _scan(**extra) -> dict:
    return {"indicator_conditions": [{"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "GT",
                                      "right": {"type": "value", "value": 60}}], **extra}


def test_scanner_loads_each_symbols_bars_on_the_server(loader, org):
    loader["frames"]["RISING"] = make_series([100.0 + i for i in range(30)])
    loader["frames"]["FALLING"] = make_series([130.0 - i for i in range(30)])
    res = client.post("/api/scanner/run", headers=org["headers"], json=_scan(
        symbols=[{"symbol": "RISING", "timeframe": "day"}, {"symbol": "FALLING", "timeframe": "day"}, {"symbol": "MISSING"}]))
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["data_source"] == "lake:daily" and [m["symbol"] for m in out["matches"]] == ["RISING"]
    assert out["scanned_count"] == 2 and out["warnings"] == [f"MISSING: {bar_source.not_stored('MISSING')}"]
    assert [c["timeframe"] for c in loader["calls"]] == ["day", "day", "5min"]


def test_scanner_posted_candles_are_uploaded(loader, org):
    res = client.post("/api/scanner/run", headers=org["headers"], json=_scan(
        symbols=[{"symbol": "RISING", "candles": _payload(make_series([100.0 + i for i in range(30)]))}]))
    assert res.status_code == 200 and res.json()["data_source"] == "uploaded" and not loader["calls"]
    empty = client.post("/api/scanner/run", headers=org["headers"], json=_scan(data_mode="paste", symbols=[{"symbol": "RISING"}]))
    assert empty.status_code == 422


def test_scanner_with_no_server_bars_at_all_is_refused(loader, org):
    res = client.post("/api/scanner/run", headers=org["headers"], json=_scan(symbols=[{"symbol": "MISSING"}]))
    assert res.status_code == 409


# ---------------------------------------------------------------------------------------------------------------- S&R
def test_sr_zones_load_server_bars_and_say_where_from(loader, org):
    loader["frames"]["NIFTY"] = _series(200)
    res = client.post("/api/support-resistance/zones", headers=org["headers"], json={"symbol": "NIFTY", "timeframe": "day"})
    assert res.status_code == 200, res.text
    assert res.headers["X-Data-Source"] == "lake:daily" and loader["calls"][0]["timeframe"] == "day"
    pasted = client.post("/api/support-resistance/zones", headers=org["headers"],
                         json={"symbol": "NIFTY", "candles": _payload(_series(200)), "data_source": "broker:upstox"})
    assert pasted.status_code == 200 and pasted.headers["X-Data-Source"] == "uploaded" and len(loader["calls"]) == 1
    assert pasted.json() == client.post("/api/support-resistance/zones", headers=org["headers"],
                                        json={"symbol": "NIFTY", "data_mode": "paste", "candles": _payload(_series(200))}).json()


# ----------------------------------------------------------------------------------------------------------- universe
@pytest.mark.parametrize("sync", [False, True])
def test_the_lists_say_when_they_are_the_fallback_copy(monkeypatch, sync):
    monkeypatch.setattr(config, "UNIVERSE_SYNC_ENABLED", sync)
    o = _org(f"m55t3u{int(sync)}", "screener_v2", "watchlists")
    _set_flag("screener_v2", o["tenant_id"])
    universes = client.get("/api/screener/universes", headers=o["headers"])
    assert universes.status_code == 200, universes.text
    assert universes.json()["fallback"] is (not sync)
    system = client.get("/api/watchlists/system", headers=o["headers"])
    assert system.status_code == 200, system.text
    assert system.json()["fallback"] is (not sync)
