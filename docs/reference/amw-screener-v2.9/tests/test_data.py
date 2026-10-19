"""Data layer tests. All OHLCV are SYNTHETIC TEST FIXTURES (not market data). No network."""
import numpy as np
import pandas as pd
import pytest

from src.config import validate_settings
from src.data.provider import TIMEFRAME_MAP, DiskCache, YFinanceProvider, tf_to_yf
from src.data.universe import custom_universe, load_universe, parse_constituents, to_yahoo
from src.data.validation import (INSUFFICIENT, INVALID, OK, STALE, UNAVAILABLE, clean_and_validate, count_missing_bars,
                                 is_forming, trading_days_between)
from tests.conftest import make_ohlc


def test_validation_drops_bad_rows_never_fills(cfg, now_after):
    df = make_ohlc(np.linspace(100, 130, 40))
    df.iloc[5, df.columns.get_loc("Close")] = np.nan
    df.iloc[6, df.columns.get_loc("High")] = df.iloc[6].Low - 5
    df = pd.concat([df, df.iloc[[10]]])
    df.iloc[3, df.columns.get_loc("Volume")] = 0
    clean, rep = clean_and_validate(df, "T.NS", "1D", cfg, now=now_after)
    assert (rep.nan_rows, rep.ohlc_violations, rep.duplicates) == (1, 1, 1)
    assert len(clean) == 38 and clean.Close.notna().all()
    assert np.isnan(clean.Volume.iloc[3])
    assert rep.status == STALE


def test_unsorted_and_negative_volume(cfg, now_after):
    df = make_ohlc(np.linspace(100, 120, 30)).iloc[::-1]
    df.iloc[0, df.columns.get_loc("Volume")] = -5
    clean, rep = clean_and_validate(df, "X", "1D", cfg, now=now_after)
    assert clean.index.is_monotonic_increasing
    assert np.isnan(clean.Volume.loc[df.index[0]])


def test_missing_invalid_insufficient(cfg, now_after):
    assert clean_and_validate(None, "X", "1D", cfg)[1].status == UNAVAILABLE
    assert clean_and_validate(pd.DataFrame({"Open": [1.0]}), "X", "1D", cfg)[1].status == INVALID
    cfg["data"]["min_bars"]["1D"] = 100
    assert clean_and_validate(make_ohlc(np.linspace(1, 2, 30)), "X", "1D", cfg, now=now_after)[1].status == INSUFFICIENT


def test_forming_candle_excluded(cfg):
    df = make_ohlc(np.linspace(100, 110, 60), tf="15m", start="2026-10-16 09:15")
    clean, rep = clean_and_validate(df, "X", "15m", cfg, now=df.index[-1] + pd.Timedelta(minutes=5))
    assert rep.dropped_forming and len(clean) == 59 and rep.status == OK


def test_session_and_weekly_forming():
    tz, m = "Asia/Kolkata", {"session_close": "15:30"}
    d = pd.Timestamp("2026-10-16", tz=tz)
    assert is_forming(d, "1D", pd.Timestamp("2026-10-16 11:00", tz=tz), m)
    assert not is_forming(d, "1D", pd.Timestamp("2026-10-16 16:00", tz=tz), m)
    h = pd.Timestamp("2026-10-16 15:15", tz=tz)
    assert not is_forming(h, "1H", pd.Timestamp("2026-10-16 15:45", tz=tz), m)
    w = pd.Timestamp("2026-10-12", tz=tz)   # Monday
    assert is_forming(w, "1W", pd.Timestamp("2026-10-15 12:00", tz=tz), m)
    assert not is_forming(w, "1W", pd.Timestamp("2026-10-17 10:00", tz=tz), m)


def test_holidays_and_stale_use_trading_days(cfg):
    tz = "Asia/Kolkata"
    a, b = pd.Timestamp("2026-10-16", tz=tz), pd.Timestamp("2026-10-19", tz=tz)   # Fri -> Mon
    assert trading_days_between(a, b, []) == 1
    assert trading_days_between(a, b, ["2026-10-19"]) == 0
    df = make_ohlc(np.linspace(100, 110, 150), start="2026-03-01")
    cfg["data"]["stale_days"]["1D"] = 5
    _, rep = clean_and_validate(df, "X", "1D", cfg, now=df.index[-1] + pd.Timedelta(days=3))   # weekend gap only
    assert rep.status == OK


def test_missing_bars_counted_not_filled(cfg):
    df = make_ohlc(np.linspace(100, 110, 60)).drop(index=make_ohlc(np.linspace(100, 110, 60)).index[[10, 11]])
    assert count_missing_bars(df.index, "1D", {"holidays": []}) == 2


def test_corporate_action_flagged_not_altered(cfg, now_after):
    c = list(np.linspace(100, 110, 40)) + list(np.linspace(55, 60, 20))
    clean, rep = clean_and_validate(make_ohlc(c), "X", "1D", cfg, now=now_after)
    assert rep.suspected_corporate_actions == 1 and clean.Close.iloc[-1] == pytest.approx(60)


def test_timeframe_mapping_respects_yahoo_limits():
    assert tf_to_yf("1D") == ("1d", "2y") and tf_to_yf("1W")[0] == "1wk"
    assert all(TIMEFRAME_MAP[t][1] == "60d" for t in ("30m", "15m", "5m"))
    with pytest.raises(ValueError):
        tf_to_yf("4H")


def test_symbols_and_universe_fallback(cfg, tmp_path):
    assert to_yahoo("m&m") == "M&M.NS" and to_yahoo("bad sym!") is None
    u = custom_universe("TCS, infy BAD!! TCS")
    assert list(u.symbols.ticker) == ["TCS.NS", "INFY.NS"] and len(u.errors) == 1

    def deny(*a, **k):
        raise ConnectionError("403")
    uni = load_universe("Nifty 50", cfg, tmp_path, getter=deny)
    assert uni.is_fallback and "FALLBACK" in uni.source and "NOT" in uni.source


def test_universe_local_file_and_min_rows(cfg, tmp_path):
    rows = "\n".join(f"Co{i},IT,SYM{i},EQ,INE{i:06d}" for i in range(60))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ind_nifty50list.csv").write_text("Company Name,Industry,Symbol,Series,ISIN Code\n" + rows)
    u = load_universe("Nifty 50", cfg, tmp_path)
    assert not u.is_fallback and u.count == 60 and u.symbols.industry.iloc[0] == "IT"
    with pytest.raises(ValueError):
        parse_constituents("Company Name,Industry,Symbol,Series,ISIN Code\nA,B,C,EQ,D", min_rows=50)


def test_provider_bounded_retries_dedup_and_cache(cfg, tmp_path):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise ConnectionError("offline")
    p = YFinanceProvider(cfg, cache=None, downloader=boom, sleeper=lambda s: None)
    assert p.fetch_many(["A.NS", "B.NS", "A.NS"], "1D") == {"A.NS": None, "B.NS": None}
    assert calls["n"] == cfg["data"]["max_retries"] + 1 and p.last_error.startswith("ConnectionError")

    df = make_ohlc(np.linspace(1, 2, 30))
    got = {"n": 0}

    def ok(tickers, **k):
        got["n"] += 1
        return df
    cache = DiskCache(tmp_path, {"1D": 60})
    p2 = YFinanceProvider(cfg, cache=cache, downloader=ok)
    assert p2.fetch_many(["A.NS"], "1D")["A.NS"] is not None
    assert p2.fetch_many(["A.NS"], "1D")["A.NS"] is not None
    assert got["n"] == 1 and cache.stats()["hits"] == 1
    assert cache.key("A.NS", "1D") != cache.key("A.NS", "1H")


def test_settings_validation(cfg):
    bad = {**cfg, "scoring": {**cfg["scoring"], "labels": {"a1": 50, "strong": 70, "watch": 55}}}
    with pytest.raises(ValueError):
        validate_settings(bad)
    cfg["strategies"]["intraday"]["timeframes"] = ["1D"]
    with pytest.raises(ValueError):
        validate_settings(cfg)
