"""Phase Y: the AI scanner - plain language to a validated scan plan (LLM or deterministic),
a ranked, explained read of scan results with the platform's regime per symbol, fallbacks when
the model answers badly, metering and the safety envelope (nothing executes)."""
import asyncio
import json

import numpy as np

from app.ai import settings as ai_settings
from app.scanner import ai as scanner_ai
from app.scanner.engine import run_scanner
from app.scanner.models import ScannerMatch, ScannerRequest, ScannerResult, ScannerSymbolInput
from tests.test_auth_api import client
from tests.test_phase_k_commercial import _owner
from tests.utils import make_series


def _run(coro):
    return asyncio.run(coro)


class _FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def complete(self, system, user, *, max_tokens=2000):
        self.calls.append((system, user))
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


def _candles(df):
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
            for ts, r in df.iterrows()]


def _trend(n=300, up=True):
    idx = np.arange(n)
    return make_series(list(100 + (idx * 0.15 if up else -idx * 0.15) + np.sin(idx / 6) * 0.2))


def test_parse_plan_keeps_valid_filters_and_drops_the_rest_with_warnings():
    plan = scanner_ai.parse_plan({
        "timeframe": "2h", "symbols": ["reliance", " tcs "],
        "indicator_conditions": [
            {"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "GT", "right": {"type": "value", "value": 55}},
            {"left": {"type": "indicator", "indicator": "VOLUME", "period": 20}, "operator": "GT", "right": {"type": "value", "value": 1}},
        ],
        "structure_filters": [{"filter_type": "TREND_UPTREND"}, {"filter_type": "NEAR_VWAP"}],
        "option_filters": [{"filter_type": "PCR", "operator": "GT", "value": 1.2}, {"filter_type": "PCR"}, {"filter_type": "BIAS_BULLISH"}],
        "explanation": "Uptrending names with RSI over 55 and a bullish chain.", "warnings": ["volume cannot be screened"],
    })
    assert plan.timeframe == "5min" and plan.symbols == ["RELIANCE", "TCS"]
    assert [c.label() for c in plan.indicator_conditions] == ["RSI(14) > 55"]
    assert [s.filter_type.value for s in plan.structure_filters] == ["TREND_UPTREND"]
    assert [o.label() for o in plan.option_filters] == ["PCR GT 1.2", "Bias Bullish"]
    assert plan.filter_count == 4
    joined = " ".join(plan.warnings)
    assert "volume cannot be screened" in joined and "timeframe '2h'" in joined and "indicator condition dropped" in joined
    assert "structure filter dropped" in joined and "PCR filter dropped" in joined
    empty = scanner_ai.parse_plan({})
    assert empty.filter_count == 0 and "No filter could be built" in empty.warnings[0]


def test_rule_based_plan_reads_indicators_structure_options_symbols_and_timeframe():
    plan = scanner_ai.rule_based_plan("Scan NIFTY and RELIANCE on 15min. Go long when RSI(14) is above 55. "
                                      "Uptrend near support with PCR above 1.2 and a bullish option chain, close to max pain.")
    assert plan.provider == "rule_based" and plan.timeframe == "15min"
    assert any("RSI(14)" in c.label() and "> 55" in c.label() for c in plan.indicator_conditions), [c.label() for c in plan.indicator_conditions]
    assert {s.filter_type.value for s in plan.structure_filters} == {"TREND_UPTREND", "NEAR_SUPPORT"}
    assert [o.label() for o in plan.option_filters] == ["PCR GT 1.2", "Bias Bullish", "Near Max Pain"]
    assert "NIFTY 50" in plan.symbols and "RELIANCE" in plan.symbols and "RSI" not in plan.symbols
    assert "Deterministic parse" in plan.explanation and plan.filter_count >= 6
    bearish = scanner_ai.rule_based_plan("downtrend stocks near resistance with call writing, PCR below 0.8")
    assert {s.filter_type.value for s in bearish.structure_filters} == {"TREND_DOWNTREND", "NEAR_RESISTANCE"}
    assert [o.label() for o in bearish.option_filters] == ["PCR LT 0.8", "Bias Bearish"]


def test_plan_endpoint_uses_the_rule_based_provider_by_default_and_meters(monkeypatch):
    headers, me = _owner("scan-ai-rule@example.com")
    res = client.post("/api/scanner/ai/plan", headers=headers, json={"text": "uptrend near support, RSI(14) above 60 on 5min"})
    assert res.status_code == 200, res.text
    plan = res.json()
    assert plan["provider"] == "rule_based" and plan["prompt_version"] == scanner_ai.PROMPT_VERSION
    assert [s["filter_type"] for s in plan["structure_filters"]] == ["TREND_UPTREND", "NEAR_SUPPORT"]
    assert plan["indicator_conditions"][0]["left"]["indicator"] == "RSI"
    usage = client.get("/api/billing/usage", headers=headers).json()
    assert usage["metrics"].get("ai_scanner", 0) >= 1
    assert client.post("/api/scanner/ai/plan", json={"text": "anything at all here"}).status_code == 401
    assert client.post("/api/scanner/ai/plan", headers=headers, json={"text": "abc"}).status_code == 422

    # An external provider: its JSON becomes the plan; garbage falls back to the deterministic parse with a warning.
    fake = _FakeLLM({"timeframe": "15min", "symbols": ["TCS"], "indicator_conditions": [
        {"left": {"type": "indicator", "indicator": "EMA", "period": 20}, "operator": "GT", "right": {"type": "indicator", "indicator": "EMA", "period": 50}}],
        "structure_filters": [{"filter_type": "BOS_BULLISH"}], "option_filters": [], "explanation": "EMA20 over EMA50 with a bullish break.", "warnings": []})

    async def fake_provider(session, tenant, *, client=None):
        return fake
    monkeypatch.setattr(ai_settings, "provider_for", fake_provider)
    res = client.post("/api/scanner/ai/plan", headers=headers, json={"text": "TCS momentum with a fresh bullish break of structure", "language": "mr"}).json()
    assert res["provider"] == "fake" and res["model"] == "fake-1" and res["timeframe"] == "15min" and res["symbols"] == ["TCS"]
    assert res["indicator_conditions"][0]["right"]["indicator"] == "EMA" and res["structure_filters"][0]["filter_type"] == "BOS_BULLISH"
    assert "mr" in fake.calls[-1][0] and "REQUEST:" in fake.calls[-1][1]
    fake.answer = "Sorry, I cannot help with that."
    fallback = client.post("/api/scanner/ai/plan", headers=headers, json={"text": "uptrend stocks with RSI(14) above 60"}).json()
    assert fallback["provider"] == "rule_based" and "did not answer usably" in fallback["warnings"][0]
    assert [s["filter_type"] for s in fallback["structure_filters"]] == ["TREND_UPTREND"]


def test_read_ranks_matches_with_the_platform_regime_and_survives_a_bad_model_answer(monkeypatch):
    headers, _ = _owner("scan-ai-read@example.com")
    rising, falling = _trend(up=True), _trend(up=False)
    request = {"symbols": [{"symbol": "RISING", "candles": _candles(rising)}, {"symbol": "FALLING", "candles": _candles(falling)},
                           {"symbol": "THIN", "candles": _candles(make_series([100.0 + i for i in range(30)]))}],
               "structure_filters": [], "option_filters": [], "swing_window": 3,
               "indicator_conditions": [{"left": {"type": "indicator", "indicator": "EMA", "period": 20}, "operator": "GT",
                                         "right": {"type": "indicator", "indicator": "EMA", "period": 50}}]}
    result = client.post("/api/scanner/run", json=request).json()
    matched = {m["symbol"] for m in result["matches"]}
    assert "RISING" in matched and "FALLING" not in matched

    # Deterministic read: regime agreement lifts the score; a thin series is capped as UNKNOWN.
    read = client.post("/api/scanner/ai/read", headers=headers, json={"request": request, "result": result})
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["provider"] == "rule_based" and body["disclaimer"] == scanner_ai.DISCLAIMER and body["ranked"]
    top = next(r for r in body["ranked"] if r["symbol"] == "RISING")
    assert top["regime"] == "TRENDING_UP" and top["score"] >= 50 and "Regime TRENDING_UP" in top["thesis"] and "paper" in top["next_step"].lower()
    if "THIN" in matched:
        thin = next(r for r in body["ranked"] if r["symbol"] == "THIN")
        assert thin["regime"] == "UNKNOWN" and thin["score"] <= 60
    assert body["ranked"] == sorted(body["ranked"], key=lambda r: -r["score"])

    # Model read: unknown symbols are dropped, unknown-regime scores capped, missing ones noted.
    fake = _FakeLLM({"summary": "Two names with momentum.", "ranked": [
        {"symbol": "RISING", "score": 82, "thesis": "Trend and structure agree.", "risks": "Extended move.", "next_step": "Backtest first."},
        {"symbol": "THIN", "score": 95, "thesis": "Looks great.", "risks": "", "next_step": ""},
        {"symbol": "INFY", "score": 70, "thesis": "not in the scan", "risks": "", "next_step": ""}], "warnings": ["small sample"]})

    async def fake_provider(session, tenant, *, client=None):
        return fake
    monkeypatch.setattr(ai_settings, "provider_for", fake_provider)
    body = client.post("/api/scanner/ai/read", headers=headers, json={"request": request, "result": result}).json()
    assert body["provider"] == "fake" and body["summary"] == "Two names with momentum."
    by_symbol = {r["symbol"]: r for r in body["ranked"]}
    assert by_symbol["RISING"]["score"] == 82 and by_symbol["RISING"]["regime"] == "TRENDING_UP"
    if "THIN" in matched:
        assert by_symbol["THIN"]["score"] <= 60 and by_symbol["THIN"]["next_step"]
    assert "INFY" not in by_symbol and any("INFY" in w for w in body["warnings"]) and "small sample" in body["warnings"]
    sent = json.loads(fake.calls[-1][1].split("SCAN RESULT:\n", 1)[1])
    assert "candles" not in json.dumps(sent) and sent["matches"][0]["regime"]["kind"] in ("TRENDING_UP", "UNKNOWN", "RANGING", "VOLATILE", "QUIET", "TRENDING_DOWN")
    fake.answer = "{not json"
    body = client.post("/api/scanner/ai/read", headers=headers, json={"request": request, "result": result}).json()
    assert body["provider"] == "rule_based" and "did not answer usably" in body["warnings"][0]
    # No matches: nothing to rank, no model call.
    empty = ScannerResult(scanned_count=1, matched_count=0, matches=[])
    body = client.post("/api/scanner/ai/read", headers=headers, json={"request": request, "result": empty.model_dump()}).json()
    assert body["ranked"] == [] and "nothing to rank" in body["summary"]


def test_rule_based_read_penalises_conflicts_and_volatility():
    matches = [ScannerMatch(symbol="A", close=100, matched_structure_labels=["Trend Uptrend"]),
               ScannerMatch(symbol="B", close=100, matched_structure_labels=["Trend Uptrend"], matched_option_labels=["Bias Bullish"]),
               ScannerMatch(symbol="C", close=100, matched_structure_labels=["Bearish Pattern"])]
    regimes = {"A": {"kind": "TRENDING_DOWN", "confidence": 0.8, "reasons": []}, "B": {"kind": "TRENDING_UP", "confidence": 0.9, "reasons": []},
               "C": {"kind": "VOLATILE", "confidence": 0.7, "reasons": []}}
    read = scanner_ai.rule_based_read(matches, regimes)
    scores = {r.symbol: r.score for r in read.ranked}
    assert scores["B"] > scores["C"] > scores["A"] and read.ranked[0].symbol == "B"
    assert "conflicts" in next(r for r in read.ranked if r.symbol == "A").thesis and "Volatile" in next(r for r in read.ranked if r.symbol == "C").risks
