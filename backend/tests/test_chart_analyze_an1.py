"""AN-1 (owner 5 Nov, "Default yes, but 1. Give Both options as it is"): Analyze a chart from a symbol and timeframe
picked by search, with no picture - ATP draws its own plain candle chart of the stored candles and both modes run on it
(AI only reads that picture; Advanced analysis reads the candles). Reuses the hybrid tests' lake and fakes."""
from __future__ import annotations

from app.ai.chart_png import is_png
from tests.test_chart_analyze_hybrid import BASE, EMPTY_SYMBOL, PNG, SYMBOL, _fake_ask, _hybrid_owner, _isolated, _lake, _zones, client  # noqa: F401


def _pick(who: dict, mode: str, symbol: str = SYMBOL, timeframe: str = "15m"):
    return client.post(f"{BASE}/items", headers=who["headers"], data={"mode": mode, "symbol": symbol, "timeframe": timeframe})


def test_a_picked_symbol_is_drawn_by_atp_and_runs_as_advanced_analysis(monkeypatch):
    who = _hybrid_owner("an1-hyb")
    _zones(monkeypatch)
    _fake_ask(monkeypatch)
    added = _pick(who, "hybrid")
    assert added.status_code == 201, added.text
    item = added.json()
    assert item["source"] == "atp" and item["mode"] == "hybrid"
    image = client.get(f"{BASE}/items/{item['id']}/image", headers=who["headers"])
    assert image.status_code == 200 and is_png(image.content) and image.content != PNG
    done = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert done["status"] == "done" and done["hybrid"]["reading"]["symbol"] == SYMBOL and done["hybrid"]["reading"]["timeframe"] == "15m"


def test_a_picked_symbol_in_ai_only_sends_atps_plain_picture(monkeypatch):
    who = _hybrid_owner("an1-ai")
    calls = _fake_ask(monkeypatch)
    item = _pick(who, "image_only").json()
    assert item["source"] == "atp" and item["mode"] == "image_only"
    done = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert done["status"] == "done" and len(calls) == 1 and is_png(calls[0]["png"]) and calls[0]["png"] != PNG


def test_a_picked_symbol_needs_both_values_and_candles():
    who = _hybrid_owner("an1-bad")
    only_symbol = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only", "symbol": SYMBOL})
    assert only_symbol.status_code == 422 and only_symbol.json()["detail"]["code"] == "one_source"
    bad_tf = _pick(who, "image_only", timeframe="7m")
    assert bad_tf.status_code == 422 and bad_tf.json()["detail"]["code"] == "bad_timeframe"
    empty = _pick(who, "image_only", symbol=EMPTY_SYMBOL, timeframe="1h")
    assert empty.status_code == 422 and empty.json()["detail"]["code"] == "reading_unavailable"
    assert client.get(f"{BASE}/items", headers=who["headers"]).json()["items"] == []        # nothing stored on a refusal
