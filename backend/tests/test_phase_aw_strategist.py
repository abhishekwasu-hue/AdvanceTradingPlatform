"""Phase AW: the Copilot strategist - new strategy operands, the market study, synthesis + walk-forward, the API."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.ai import market_study as ms
from app.ai import strategist as st
from app.strategy_engine.declarative import Condition, CustomStrategyConfig, DeclarativeStrategy, Operand
from tests.test_auth_api import _register, client
from tests.test_phase_ap_interview import _sessions

UTC = timezone.utc


def _day(open_, closes, start=datetime(2026, 9, 21, 3, 45, tzinfo=UTC), volume=100.0):
    """One session of 1-minute bars from 09:15 IST."""
    idx = [start + timedelta(minutes=i) for i in range(len(closes))]
    opens = [open_] + closes[:-1]
    return pd.DataFrame({"open": opens, "high": [max(o, c) + 1 for o, c in zip(opens, closes)],
                         "low": [min(o, c) - 1 for o, c in zip(opens, closes)], "close": closes, "volume": [volume] * len(closes)},
                        index=pd.DatetimeIndex(idx))


def op(name, period=14, **kw):
    return Operand(type="indicator", indicator=name, period=period, **kw)


def test_session_operands():
    d1 = _day(100, [100 + i * 0.1 for i in range(375)])
    d2 = _day(140, [140 - i * 0.05 for i in range(60)], start=datetime(2026, 9, 22, 3, 45, tzinfo=UTC))
    df = pd.concat([d1, d2])
    pdh = op("PDH").series(df)
    assert pdh.iloc[:375].isna().all() and pdh.iloc[-1] == pytest.approx(d1["high"].max())
    assert op("PDC").series(df).iloc[-1] == pytest.approx(d1["close"].iloc[-1])
    or_high = op("OR_HIGH", 15).series(df)
    second = df.index >= d2.index[0]
    assert or_high[second].iloc[:14].isna().all() and or_high[second].iloc[14] == pytest.approx(d2["high"].iloc[:15].max())
    assert op("DAY_OPEN").series(df).iloc[-1] == pytest.approx(140)
    no_volume = df.assign(volume=0.0)
    vwap = op("VWAP").series(no_volume)
    assert not vwap.isna().any() and vwap.iloc[-1] == pytest.approx(((d2["high"] + d2["low"] + d2["close"]) / 3).mean())
    assert op("BB_UPPER", 20, multiplier=2.0).series(df).iloc[-1] > op("BB_LOWER", 20, multiplier=2.0).series(df).iloc[-1]
    assert op("VOLUME_SMA", 5).series(df).iloc[-1] == pytest.approx(100.0)
    assert op("EMA", 20, timeframe="15min").label() == "EMA(20)[15min]"


def test_higher_timeframe_operand_never_sees_the_future():
    df = _sessions(days=3, minutes=1)
    close_15 = op("CLOSE", timeframe="15min").series(df)
    # 09:15-09:29 bars see no 15-minute bar of today yet; 09:29's close completes the first one.
    day = df.index.tz_convert("Asia/Kolkata").date == df.index.tz_convert("Asia/Kolkata").date[-1]
    today = df[day]
    assert close_15[today.index[14]] == pytest.approx(today["close"].iloc[14])
    assert close_15[today.index[13]] != pytest.approx(today["close"].iloc[13])
    # Truncating the future changes nothing in the past.
    cut = op("CLOSE", timeframe="15min").series(df.iloc[:-100])
    assert np.allclose(cut.dropna().values, close_15.loc[cut.dropna().index].values)


def test_declarative_strategy_with_new_operands_still_trades():
    df = _sessions(days=4, minutes=5)
    cfg = CustomStrategyConfig(name="vwap htf", timeframe="5min",
                               long_conditions=[Condition(left=op("CLOSE"), operator="GT", right=op("VWAP")),
                                                Condition(left=op("EMA", 20, timeframe="15min"), operator="GT", right=op("EMA", 50, timeframe="15min"))])
    strategy = DeclarativeStrategy("custom:test", cfg)
    assert strategy.timeframes == ["5min"] and strategy.min_history()["5min"] > 55
    sig = strategy.analyze({"5min": df}, "NIFTY 50")
    assert sig.direction.value in ("LONG", "NO_TRADE")
    holds = cfg.long_conditions[0].holds_series(df, "5min")
    assert holds.dtype == bool and len(holds) == len(df)


def test_market_study_reads_the_trend_and_the_levels():
    df = _sessions(days=6, minutes=1)
    s = ms.study(df, "NIFTY 50", "mr")
    assert s["bias"] == "BULLISH" and s["character"] == "TREND" and s["confidence"] > 50
    assert {t["timeframe"] for t in s["timeframes"]} >= {"5min", "15min"}
    assert {"pdh", "pdl", "pdc", "pivot", "vwap", "or_high"} <= set(s["levels"])
    assert [r["price"] for r in s["ladder"]] == sorted((r["price"] for r in s["ladder"]), reverse=True)
    assert {sc["id"] for sc in s["scenarios"]} <= {"bull", "bear", "range"} and s["scenarios"]
    assert any("VWAP" in line for line in s["lines"])
    with pytest.raises(ValueError):
        ms.study(df.iloc[:50], "NIFTY 50")


def test_strategist_builds_validated_candidates():
    df = _sessions(days=12, minutes=1)
    s = ms.study(df, "NIFTY 50", "en")
    # P0.8-D: the trader names the side; a bullish study no longer picks LONG for them, and nothing is marked best.
    assert st.build(df, s, "en")["sides"] == ["both"]
    out = st.build(df, s, "en", direction="long")
    assert out["sides"] == ["long"] and out["base_timeframe"] == "5min" and out["tested"] >= 3 and out["best"] is None
    best = out["candidates"][0]
    assert best["direction"] == "LONG" and not best["rules"]["short"] and best["triggers"] == []
    assert best["all"]["trades"] > 0 and best["verdict"] in ("robust", "overfit", "weak", "untested", "thin")
    assert best["in_sample"]["trades"] + best["out_of_sample"]["trades"] <= best["all"]["trades"] + 1
    assert best["risk_amount"] == 500.0 and best["stop_points"] > 0
    CustomStrategyConfig.model_validate(best["config"])
    _, _, oos = st.split_sessions(st.resample_ohlc(df, "5min"))
    assert len(oos) >= 3
    both = st.build(df, s, "en", direction="both", style="scalping")
    assert both["sides"] == ["both"] and both["base_timeframe"] == "1min"


def test_simulator_honours_stop_first_and_square_off():
    df = _day(100, [100.0] * 30 + [101.0] + [90.0] * 344)
    cfg = CustomStrategyConfig(name="t", timeframe="1min", long_conditions=[Condition(left=op("CLOSE"), operator="CROSSES_ABOVE", right=Operand(type="value", value=100.5))],
                               stop_loss_atr_mult=1.0, target_rr=(1.5, 2.5))
    trades = st.simulate(cfg, df, warmup=20)
    assert len(trades) == 1 and trades[0]["reason"] == "stop" and trades[0]["r"] < -0.9
    m = st.metrics(trades)
    assert m["trades"] == 1 and m["win_rate"] == 0.0


def test_ai_proposals_are_parsed_strictly():
    good = {"name": "AI vwap", "config": {"name": "x", "timeframe": "5min", "long_conditions": [
        {"left": {"type": "indicator", "indicator": "CLOSE"}, "operator": "CROSSES_ABOVE", "right": {"type": "indicator", "indicator": "VWAP"}}]}}
    bad = {"name": "bad", "config": {"name": "y", "timeframe": "5min", "long_conditions": [{"left": {"type": "indicator", "indicator": "MAGIC"}}]}}
    parsed = st.parse_ai("here: " + json.dumps({"strategies": [good, bad]}))
    assert [n for n, _ in parsed] == ["AI vwap"]
    assert st.parse_ai("no json") == []
    df = _sessions(days=10, minutes=1)
    s = ms.study(df, "NIFTY 50", "en")
    out = st.build(df, s, "en", direction="both", extra_configs=parsed)
    assert any(c["source"] == "ai" for c in out["candidates"]) or out["tested"] >= 1


def _bars(df):
    return [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]


def test_strategist_api_build_and_adopt_into_a_paper_deployment():
    headers = {"Authorization": f"Bearer {_register('strategist@example.com')}"}
    candles = _bars(_sessions(days=10, minutes=1))
    r = client.post("/api/ai/strategist/build", headers=headers, json={"symbol": "NIFTY 50", "candles": candles, "language": "mr"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data_source"] == "candles" and body["study"]["symbol"] == "NIFTY 50" and body["candidates"]
    study = client.post("/api/ai/strategist/study", headers=headers, json={"symbol": "NIFTY 50", "candles": candles}).json()
    assert study["bias"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert client.post("/api/ai/strategist/build", headers=headers, json={"symbol": "NIFTY 50"}).status_code == 409   # no broker, no candles
    assert client.post("/api/ai/strategist/study", headers=headers, json={"candles": candles[:40]}).status_code == 422

    best = body["candidates"][0]
    assert best["candidate_id"] > 0                                       # P0.8 / A3: the server holds the candidate
    # A config typed by the browser is not a candidate; the server's candidate needs the risk acceptance.
    assert client.post("/api/ai/strategist/adopt", headers=headers, json={"name": "bad", "config": {"name": "x"}}).status_code == 422
    refused = client.post("/api/ai/strategist/adopt", headers=headers, json={"candidate_id": best["candidate_id"], "name": "Copilot test"})
    assert refused.status_code == 400 and "accept" in refused.text.lower()
    adopted = client.post("/api/ai/strategist/adopt", headers=headers, json={"candidate_id": best["candidate_id"], "name": "Copilot test", "accept_risk": True})
    assert adopted.status_code == 201, adopted.text
    a = adopted.json()
    assert a["strategy_id"].startswith("custom:") and a["deployment"]["mode"] == "PAPER" and a["compliance"]["ok"] is True
    saved = client.get(f"/api/custom-strategies/{a['strategy_id'].split(':')[1]}", headers=headers)
    assert saved.status_code == 200 and saved.json()["config"]["timeframe"] == best["config"]["timeframe"]
    assert client.post("/api/ai/strategist/adopt", headers=headers, json={"candidate_id": best["candidate_id"], "accept_risk": True}).status_code == 409   # already adopted
    assert client.post("/api/ai/strategist/adopt", headers=headers, json={"candidate_id": 999999, "accept_risk": True}).status_code == 404
