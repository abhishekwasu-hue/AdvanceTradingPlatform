"""Handover 11c-1: the Pine subset API - flag `strategy_lab_pine` (off: 404), per organisation; translate computes over the
body and stores nothing; "Run as screen" goes through the Screener's run path (screener_v2, the same validation, the run
stored under the caller's organisation, the same run limit); look-ahead and out-of-range inputs are refused."""
import asyncio
import types
import uuid

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core import rate_limit
from app.core.models import OHLCVBar
from app.db.models import ScreenRunRecord
from app.lab.pine.templates import TEMPLATES
from app.lab.pine.translate import FLAG
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

BASE = "/api/lab/pine"


def _run(coro):
    return asyncio.run(coro)


def _set_flag(name: str, tenant_id=None, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = dict(flags.get(name) or {"on": False, "tenants": []})
            if tenant_id is None:
                entry["on"] = on
            else:
                tenants = set(entry.get("tenants") or [])
                (tenants.add if on else tenants.discard)(tenant_id)
                entry["tenants"] = sorted(tenants)
            flags[name] = entry
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _org(tag: str, *, pine: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {_register(f'pine-{tag}-{uuid.uuid4().hex[:6]}@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if pine:
        _set_flag(FLAG, me["tenant_id"])
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


@pytest.fixture(autouse=True)
def _fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def screener_on():
    _set_flag("screener_v2", on=True)
    yield
    _set_flag("screener_v2", on=False)


def _bars(step: float, days: int = 80):
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    closes = 100 + np.arange(days) * step + np.sin(np.arange(days)) * 3
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=c - 0.5 * np.sign(step), high=c + 1, low=c - 1, close=float(c), volume=1000.0)
            for t, c in zip(idx, closes)]


@pytest.fixture
def broker(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        return _bars(1.0 if symbol == "UP" else -1.0)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


UP_SCRIPT = '//@version=5\nindicator("up")\nlen = input.int(10, "Length", minval=2, maxval=50)\nalertcondition(close > ta.sma(close, len), "above")\n'


def test_every_route_is_404_while_the_flag_is_off():
    who = _org("off", pine=False)
    for method, path, body in (("get", "/templates", None), ("post", "/translate", {"source": UP_SCRIPT}),
                               ("post", "/run", {"source": UP_SCRIPT, "symbols": ["UP"]})):
        r = getattr(client, method)(BASE + path, headers=who["headers"], **({"json": body} if body else {}))
        assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == FLAG, (path, r.status_code)


def test_anonymous_requests_are_refused():
    assert client.post(BASE + "/translate", json={"source": UP_SCRIPT}).status_code == 401


def test_the_flag_is_per_organisation():
    a, b = _org("a"), _org("b", pine=False)
    assert client.post(BASE + "/translate", headers=a["headers"], json={"source": UP_SCRIPT}).status_code == 200
    assert client.post(BASE + "/translate", headers=b["headers"], json={"source": UP_SCRIPT}).status_code == 404


def test_templates_and_translate():
    who = _org("tr")
    t = client.get(BASE + "/templates", headers=who["headers"]).json()
    assert [x["id"] for x in t["templates"]] == [x["id"] for x in TEMPLATES]
    for tpl in t["templates"]:
        out = client.post(BASE + "/translate", headers=who["headers"], json={"source": tpl["source"]}).json()
        assert out["ok"] and out["complete"] and out["primary"] is not None, tpl["id"]
    out = client.post(BASE + "/translate", headers=who["headers"], json={"source": UP_SCRIPT}).json()
    assert out["signals"][0]["text"] == "close > SMA(close, $len)" and out["params"]["len"]["max"] == 50
    assert "never run" in out["note"]
    bad = client.post(BASE + "/translate", headers=who["headers"], json={"source": UP_SCRIPT.replace("ta.sma", "ta.wma")}).json()
    assert not bad["ok"] and any(d["line"] == 4 and d["alternative"] for d in bad["diagnostics"] if d["severity"] == "error")
    assert client.post(BASE + "/translate", headers=who["headers"], json={"source": "x" * 50_001}).status_code == 422
    assert client.post(BASE + "/translate", headers=who["headers"], json={"source": UP_SCRIPT, "extra": 1}).status_code == 422


def test_translate_is_rate_limited():
    who = _org("rl")
    codes = [client.post(BASE + "/translate", headers=who["headers"], json={"source": UP_SCRIPT}).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_run_as_screen_goes_through_the_screener_and_is_stored_for_the_caller_only(screener_on, broker):
    a, b = _org("run-a"), _org("run-b")
    out = client.post(BASE + "/run", headers=a["headers"], json={"source": UP_SCRIPT, "symbols": ["UP", "DOWN"]})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["matched"] == ["UP"] and body["text"] == "close > SMA(close, $len)"
    assert body["pine"]["params"] == {"len": 10} and body["pine"]["signal"]["kind"] == "alert"

    async def rows():
        async with _session_factory() as session:
            return (await session.execute(select(ScreenRunRecord).where(ScreenRunRecord.id == body["run_id"]))).scalars().all()
    (row,) = _run(rows())
    assert (row.tenant_id, row.user_id) == (a["tenant_id"], a["user_id"])
    assert client.get(f"/api/screener/runs/{body['run_id']}", headers=a["headers"]).status_code == 200
    assert client.get(f"/api/screener/runs/{body['run_id']}", headers=b["headers"]).status_code == 404
    # the caller's input override, inside its bounds
    over = client.post(BASE + "/run", headers=a["headers"], json={"source": UP_SCRIPT, "symbols": ["UP"], "params": {"len": 20}})
    assert over.status_code == 200 and over.json()["pine"]["params"] == {"len": 20}


def test_run_refuses_bad_inputs_look_ahead_and_untranslated_signals(screener_on, broker):
    who = _org("run-bad")
    h = who["headers"]
    for params in ({"len": 99}, {"len": 2.5}, {"len": True}, {"other": 1}):
        r = client.post(BASE + "/run", headers=h, json={"source": UP_SCRIPT, "symbols": ["UP"], "params": params})
        assert r.status_code == 422, params
    look = UP_SCRIPT + "x = close[-1]\n"
    r = client.post(BASE + "/run", headers=h, json={"source": look, "symbols": ["UP"]})
    assert r.status_code == 422 and "look-ahead" in r.json()["detail"]["message"]
    wma = UP_SCRIPT.replace("ta.sma", "ta.wma")
    assert client.post(BASE + "/run", headers=h, json={"source": wma, "symbols": ["UP"]}).status_code == 422
    assert client.post(BASE + "/run", headers=h, json={"source": UP_SCRIPT, "symbols": ["UP"], "signal": 5}).status_code == 422


def test_run_needs_the_screener_flag_too():
    who = _org("no-screener")
    r = client.post(BASE + "/run", headers=who["headers"], json={"source": UP_SCRIPT, "symbols": ["UP"]})
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_v2"
