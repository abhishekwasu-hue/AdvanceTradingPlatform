"""Strategy templates step 2: the API behind the flag `strategy_templates` - the template cards, "Adopt as a paper strategy"
(a pre-fill only: nothing is created and nothing starts), and a template id through the existing deployment and backtest
routes (PAPER only)."""
import asyncio
import uuid

import pytest

from app.core import rate_limit
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from app.quality.lookahead import synthetic_bars
from tests.test_auth_api import _register, _session_factory, client
from tests.test_deployments_api import _store_broker, _upgrade_plan

FLAG = "strategy_templates"


def _run(coro):
    return asyncio.run(coro)


def _set_flag(name: str, tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = dict(flags.get(name) or {"on": False, "tenants": []})
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            entry["tenants"] = sorted(tenants)
            flags[name] = entry
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _org(tag: str, *flags: str) -> dict:
    headers = {"Authorization": f"Bearer {_register(f'tpl2-{tag}-{uuid.uuid4().hex[:6]}@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"])
    for flag in flags:
        _set_flag(flag, me["tenant_id"])
    return {"headers": headers, "tenant_id": me["tenant_id"]}


@pytest.fixture(autouse=True)
def _fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


def test_the_flag_is_registered_and_off_by_default():
    assert FLAG in controls.FEATURE_FLAGS and FLAG in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO[FLAG].status == "unfinished" and "ui_v2" in FLAG_INFO[FLAG].depends_on


def test_off_means_404_and_a_template_id_resolves_to_nothing():
    org = _org("off")
    assert client.get("/api/strategy-templates", headers=org["headers"]).status_code == 404
    assert client.post("/api/strategy-templates/D2/adopt", headers=org["headers"], json={}).status_code == 404
    _store_broker(org["headers"])
    made = client.post("/api/deployments", headers=org["headers"],
                       json={"strategy_id": "template:D2:1:15m:both:,,,,,,", "symbol": "reliance", "timeframe": "15min"})
    assert made.status_code == 404
    assert client.get("/api/strategy-templates").status_code in (401, 403)


def test_the_cards_show_words_and_availability_by_the_functions_flags():
    org = _org("cards", FLAG)
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert {"D1", "D2", "B1", "B3"} <= set(cards)
    d2 = cards["D2"]
    assert d2["name"] == "Moving-average crossover" and d2["family_name"] == "Trend following" and d2["difficulty"] == "Beginner"
    assert {"value": "15m", "label": "15-minute"} in d2["timeframes"] and all(t["value"] != "1w" for t in d2["timeframes"])
    ma = next(p for p in d2["params"] if p["key"] == "ma_type")
    assert ma["choices"][0] == {"value": "EMA", "label": "Exponential"}
    assert "The fast average must have fewer bars than the slow one." in d2["rules"]
    assert cards["D2"]["available"] and cards["D1"]["available"]
    assert not cards["B1"]["available"] and not cards["B3"]["available"]          # the breakout screens are off here
    for flag in ("screener_breakouts", "screener_breakouts_bk4"):
        _set_flag(flag, org["tenant_id"])
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert cards["B1"]["available"] and cards["B3"]["available"]
    text = str(cards)
    assert "screener_breakouts" not in text and "strategy_templates" not in text   # flag names are never sent


def test_adopt_is_a_pre_fill_only():
    org = _org("adopt", FLAG)
    before = client.get("/api/deployments", headers=org["headers"]).json()
    r = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"],
                    json={"timeframe": "15m", "params": {"fast": 5, "slow": 34}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy_id"] == "template:D2:1:15m:both:,5,34,,,,"
    assert body["deployment"] == {"strategy_id": body["strategy_id"], "timeframe": "15min", "holding": "INTRADAY", "mode": "PAPER"}
    assert body["settings"]["params"]["fast"] == 5 and body["history"]["bars"] >= 250
    assert any("Nothing starts until you confirm" in n for n in body["notes"])
    assert client.get("/api/deployments", headers=org["headers"]).json() == before     # nothing was created
    again = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"],
                        json={"timeframe": "15m", "params": {"fast": 5, "slow": 34}}).json()
    assert again["strategy_id"] == body["strategy_id"]                                # the same settings, the same id
    daily = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"], json={"timeframe": "1d"}).json()
    assert daily["deployment"]["timeframe"] == "day" and daily["deployment"]["holding"] == "SWING"
    deep = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"], json={"params": {"swing_degree": 2}}).json()
    assert any("history" in n for n in deep["notes"])                                  # R6: the fetch-dependent note


@pytest.mark.parametrize("body, status, words", [
    ({"params": {"fast": 30, "slow": 20}}, 422, "fewer bars than the slow one"),
    ({"params": {"fast": 0}}, 422, "between 2 and 200"),
    ({"params": {"nonsense": 1}}, 422, "unknown setting"),
    ({"timeframe": "2h"}, 422, "timeframe must be one of"),
    ({"timeframe": "1w"}, 422, "must be one of"),
    ({"sides": "sideways"}, 422, ""),
])
def test_adopt_refuses_bad_settings_in_plain_words(body, status, words):
    org = _org("bad", FLAG)
    r = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"], json=body)
    assert r.status_code == status
    assert words in str(r.json()["detail"])


def test_adopt_unknown_or_unavailable():
    org = _org("unknown", FLAG)
    weekly = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"], json={"timeframe": "1w"})
    assert weekly.status_code == 422 and "scans only" in str(weekly.json()["detail"])          # TPL-Q7
    assert client.post("/api/strategy-templates/Z9/adopt", headers=org["headers"], json={}).status_code == 404
    r = client.post("/api/strategy-templates/B1/adopt", headers=org["headers"], json={})
    assert r.status_code == 409 and "not switched on" in str(r.json()["detail"])


def test_a_template_runs_through_the_deployment_route_on_paper_only():
    org = _org("deploy", FLAG)
    _store_broker(org["headers"])
    pre = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()["deployment"]
    made = client.post("/api/deployments", headers=org["headers"], json={**pre, "symbol": "reliance", "instrument_kind": "UNDERLYING"})
    assert made.status_code == 201, made.text
    assert made.json()["strategy_id"] == pre["strategy_id"] and made.json()["mode"] == "PAPER" and made.json()["timeframe"] == "15min"
    wrong_base = client.post("/api/deployments", headers=org["headers"], json={**pre, "symbol": "tcs", "timeframe": "60min"})
    assert wrong_base.status_code == 400                       # a 60-minute base cannot build 15-minute bars
    live = client.post("/api/deployments", headers=org["headers"], json={**pre, "symbol": "infy", "mode": "LIVE"})
    assert live.status_code in (400, 403, 503)                 # LIVE is refused (the live gates first, then "paper only")


def test_a_template_runs_through_the_backtest_route():
    org = _org("backtest", FLAG)
    df = synthetic_bars(400, tf="15m", seed=3)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
               for ts, r in df.iterrows()]
    sid = client.post("/api/strategy-templates/D2/adopt", headers=org["headers"],
                      json={"timeframe": "15m", "params": {"invalidation": "slow_average"}}).json()["strategy_id"]
    r = client.post("/api/backtest", headers=org["headers"],
                    json={"strategy_id": sid, "symbol": "nifty", "base_timeframe": "15min", "candles": candles, "data_source": "sample"})
    assert r.status_code == 200, r.text
    assert r.json()["total_trades"] >= 1
    other = _org("backtest-off")
    assert client.post("/api/backtest", headers=other["headers"],
                       json={"strategy_id": sid, "symbol": "nifty", "base_timeframe": "15min", "candles": candles}).status_code == 404
