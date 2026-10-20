"""Analyze a chart (PR-A): the API end to end on the test app - flag, upload, link, Image-only analysis with a fake
engine, history, delete, retention, plan limits, cost cap, tenancy. Made-up data only: synthetic images drawn in code
and a fake engine (tests/chart_analyze_fakes.py); no network (the link tests use a fake resolver and a mock transport)."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.chart_analyze import FLAG, engine, linkfetch, provider, service, settings as settings_mod
from app.chart_analyze.engine import Answer
from app.core import rate_limit
from app.db.models import ChartAnalysisRecord, SubscriptionRecord, Tenant
from app.platform import controls
from tests.chart_analyze_fakes import HEIGHT, WIDTH, FakeProvider, answer, synthetic_chart
from tests.test_auth_api import _register, _session_factory, client
from tests.test_z3b_paper_account import _member

BASE = "/api/chart-analyze"
PNG = synthetic_chart()


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"ca-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _set_flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(FLAG) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[FLAG] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _set_settings(**values) -> None:
    async def go():
        async with _session_factory() as session:
            current = await settings_mod.load(session)
            new = settings_mod.validate_update(current, values)
            await controls._set(session, settings_mod.KEY, new, None)
            await session.commit()
    _run(go())


def _set_plan(tenant_id: int, plan: str, *, trialing: bool = False) -> None:
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            sub = await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.tenant_id == tenant_id))
            if trialing and sub is None:
                session.add(SubscriptionRecord(tenant_id=tenant_id, plan_id=plan, status="TRIALING"))
            elif sub is not None:
                sub.status = "TRIALING" if trialing else "ACTIVE"
            await session.commit()
    _run(go())


def _owner(tag: str, *, flag: bool = True, plan: str = "pro") -> dict:
    headers = {"Authorization": f"Bearer {_register(_email(tag))}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if flag:
        _set_flag(me["tenant_id"])
    _set_plan(me["tenant_id"], plan)
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _upload(who: dict, data: bytes = PNG, name: str = "chart.png", ctype: str = "image/png", mode: str = "image_only"):
    return client.post(f"{BASE}/items", headers=who["headers"], files={"file": (name, data, ctype)}, data={"mode": mode})


def _row(item_id: int):
    async def go():
        async with _session_factory() as session:
            return await session.get(ChartAnalysisRecord, item_id)
    return _run(go())


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_ANALYZE_DATA_DIR", str(tmp_path / "private"))
    rate_limit.reset(engine.FEATURE)
    _set_settings(rate_limit_per_minute=600)
    provider.set_for_tests(FakeProvider())
    yield
    provider.reset_for_tests()
    rate_limit.reset(engine.FEATURE)


def _fake_answer(monkeypatch, kind: str = "formed", *, cost: float = 0.01, byok: bool = False, error=None):
    calls = []

    async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
        calls.append({"user": user.id, "png": png, "system": instr.system})
        return Answer(text=json.dumps(answer(kind)), error=error, provider="anthropic", model="claude-test", cost_usd=cost,
                      billed_to_user_key=byok)
    monkeypatch.setattr(service, "ask", fake_ask)
    return calls


# --- the flag ------------------------------------------------------------------------------------------------------------

def test_flag_is_registered_and_off_by_default():
    assert FLAG in controls.FEATURE_FLAGS and FLAG in controls.DEFAULT_OFF_FLAGS


def test_flag_off_answers_the_standard_feature_off_response_on_every_route():
    who = _owner("off", flag=False)
    for method, path in (("GET", "/status"), ("GET", "/items"), ("POST", "/items"), ("GET", "/items/1"), ("GET", "/items/1/image"),
                         ("POST", "/items/1/analyze"), ("DELETE", "/items/1")):
        response = client.request(method, BASE + path, headers=who["headers"])
        assert response.status_code == 503, (method, path, response.text)
        assert response.headers.get("X-Feature-Disabled") == FLAG


def test_anonymous_is_refused():
    assert client.get(f"{BASE}/status").status_code == 401
    assert client.post(f"{BASE}/items", files={"file": ("c.png", PNG, "image/png")}).status_code == 401


def test_status_says_what_the_page_needs():
    who = _owner("status")
    body = client.get(f"{BASE}/status", headers=who["headers"]).json()
    assert body["engine"]["installed"] is True and body["modes"] == {"image_only": True, "hybrid": False}
    assert body["label"] == "Image-only analysis: prices and volume are read from the picture and may be approximate"
    assert body["link_message"] == "Please upload a screenshot instead" and body["retention_days"] == 30
    assert body["limits"]["tier"] == "pro" and body["limits"]["daily_used"] == 0
    assert body["upload"]["types"] == ["image/png", "image/jpeg"]


# --- upload ---------------------------------------------------------------------------------------------------------------

def test_upload_stores_a_clean_png_privately(tmp_path):
    who = _owner("up")
    response = _upload(who, synthetic_chart("JPEG"), "photo.jpg", "image/jpeg")
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["status"] == "ready" and item["image"] == {"width": WIDTH, "height": HEIGHT, "url": f"{BASE}/items/{item['id']}/image"}
    row = _row(item["id"])
    stored = tmp_path / "private" / row.image_path
    assert stored.exists() and stored.read_bytes().startswith(b"\x89PNG") and row.image_path.startswith(f"{who['tenant_id']}/")
    image = client.get(item["image"]["url"], headers=who["headers"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, no-store" and image.content == stored.read_bytes()


def test_upload_refusals():
    who = _owner("upbad")
    _set_settings(max_image_bytes=64 * 1024)
    big = _upload(who, synthetic_chart(width=2000, height=1500, compress_level=0))
    assert big.status_code == 413, big.text
    _set_settings(max_image_bytes=5 * 1024 * 1024)
    wrong = _upload(who, b"GIF89a" + b"\0" * 64, "chart.png", "image/png")          # the name and type lie; the bytes decide
    assert wrong.status_code == 415 and "PNG or JPEG" in wrong.json()["detail"]["message"]
    broken = _upload(who, b"\x89PNG\r\n\x1a\n" + b"junk" * 50)
    assert broken.status_code == 422
    neither = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only"})
    assert neither.status_code == 422
    both = client.post(f"{BASE}/items", headers=who["headers"], files={"file": ("c.png", PNG, "image/png")},
                       data={"mode": "image_only", "link": "https://img.example.com/c.png"})
    assert both.status_code == 422
    hybrid = _upload(who, mode="hybrid")
    assert hybrid.status_code == 409 and hybrid.json()["detail"]["code"] == "hybrid_soon"
    assert client.get(f"{BASE}/items", headers=who["headers"]).json()["items"] == []        # nothing was stored


# --- link ----------------------------------------------------------------------------------------------------------------

def _mock_network(monkeypatch, handler, table):
    async def resolver(host):
        return list(table.get(host, []))
    original = linkfetch.make_client
    monkeypatch.setattr(linkfetch, "system_resolver", resolver)
    monkeypatch.setattr(linkfetch, "make_client", lambda settings, transport=None: original(settings, httpx.MockTransport(handler)))


def _streamed(data: bytes, ctype: str):
    async def body():
        yield data
    return httpx.Response(200, content=body(), headers={"content-type": ctype})


def test_tradingview_snapshot_link_is_stored_like_an_upload(monkeypatch):
    who = _owner("tv")
    seen = []

    def handler(request):
        seen.append((request.headers["host"], request.url.path))
        return _streamed(PNG, "image/png")
    _mock_network(monkeypatch, handler, {"s3.tradingview.com": ["8.8.8.8"]})
    response = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only", "link": "https://www.tradingview.com/x/Zx9Abc12/"})
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "link" and response.json()["source_host"] == "www.tradingview.com"
    assert seen == [("s3.tradingview.com", "/snapshots/z/Zx9Abc12.png")]


def test_an_unreadable_link_says_please_upload_a_screenshot_instead(monkeypatch):
    who = _owner("unread")
    _mock_network(monkeypatch, lambda r: _streamed(b"<html>Sign in</html>", "text/html"), {"img.example.com": ["8.8.8.8"]})
    for link in ("https://img.example.com/chart", "https://www.tradingview.com/chart/AbCd/", "http://img.example.com/c.png"):
        response = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only", "link": link})
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["message"] == "Please upload a screenshot instead" and detail["code"] == "link_unreadable" and detail["reason"]
    _mock_network(monkeypatch, lambda r: _streamed(b"\x89PNG\r\n\x1a\nbroken", "image/png"), {"img.example.com": ["8.8.8.8"]})
    broken = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only", "link": "https://img.example.com/c.png"})
    assert broken.status_code == 422 and broken.json()["detail"]["message"] == "Please upload a screenshot instead"


def test_a_link_to_a_private_address_is_refused(monkeypatch):
    who = _owner("ssrf")
    _mock_network(monkeypatch, lambda r: pytest.fail("no request may go out"), {"img.example.com": ["169.254.169.254"]})
    response = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only", "link": "https://img.example.com/c.png"})
    assert response.status_code == 422 and response.json()["detail"]["message"] == "Please upload a screenshot instead"


# --- analysis -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["formed", "forming", "none"])
def test_image_only_analysis_fills_the_contract(monkeypatch, kind):
    who = _owner(f"an-{kind}")
    calls = _fake_answer(monkeypatch, kind)
    item = _upload(who).json()
    response = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done" and body["label"] == "Image-only analysis: prices and volume are read from the picture and may be approximate"
    result = body["result"]
    assert result["label"] == body["label"] and result["disclaimer"] == body["disclaimer"] and "not investment advice" in result["disclaimer"]
    assert result["setup"]["status"] == kind and result["evidence"] and result["scenarios"]["primary"]
    assert calls and calls[0]["png"].startswith(b"\x89PNG") and calls[0]["png"] == client.get(item["image"]["url"], headers=who["headers"]).content
    assert body["cost_usd"] == pytest.approx(0.01) and body["engine_version"].startswith("fake-engine")
    again = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])           # a done item is not re-run
    assert again.status_code == 200 and len(calls) == 1
    listed = client.get(f"{BASE}/items", headers=who["headers"]).json()["items"]
    assert listed[0]["id"] == item["id"] and listed[0]["summary"]["setup_status"] == kind and listed[0]["result"] is None


def test_engine_not_installed(monkeypatch):
    who = _owner("noengine")
    provider.set_for_tests(None)
    assert client.get(f"{BASE}/status", headers=who["headers"]).json()["engine"] == {"installed": False, "message": "Analysis engine not installed"}
    item = _upload(who).json()
    response = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert response.status_code == 503 and response.json()["detail"] == {"message": "Analysis engine not installed", "code": "engine_not_installed"}
    assert _row(item["id"]).status == "ready"


def test_without_any_ai_the_user_is_told_why():
    """The real AI path: no organisation provider and no own key -> a clear refusal, nothing is called."""
    who = _owner("noai")
    item = _upload(who).json()
    response = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "no_ai"
    assert "No AI is set up" in response.json()["detail"]["message"]


def test_an_unreadable_answer_is_a_failed_analysis_never_a_partial_one(monkeypatch):
    who = _owner("badreply")

    async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
        return Answer(text='{"story": []}', error=None, provider="anthropic", model="m", cost_usd=0.02, billed_to_user_key=False)
    monkeypatch.setattr(service, "ask", fake_ask)
    item = _upload(who).json()
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert body["status"] == "failed" and body["result"] is None and "could not be read" in body["error"]
    assert body["cost_usd"] == pytest.approx(0.02)


def test_advice_in_the_answer_is_withheld(monkeypatch):
    who = _owner("advice")
    raw = answer("formed")
    raw["story"] = ["Buy RELIANCE now, guaranteed profit.", "The candles rise."]

    async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
        return Answer(text=json.dumps(raw), error=None, provider="anthropic", model="m", cost_usd=0.0, billed_to_user_key=False)
    monkeypatch.setattr(service, "ask", fake_ask)
    item = _upload(who).json()
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert body["result"]["story"][0].startswith("(This text was withheld") and "guaranteed" not in json.dumps(body)


# --- limits ----------------------------------------------------------------------------------------------------------------

def test_daily_limit_and_delete_does_not_reset_it():
    who = _owner("daily")
    _set_settings(limits={"pro": {"daily": 2, "monthly": 150}})
    try:
        first = _upload(who).json()
        assert _upload(who).status_code == 201
        refused = _upload(who)
        assert refused.status_code == 429 and refused.json()["detail"]["code"] == "limit_reached" and "a day" in refused.json()["detail"]["message"]
        assert client.delete(f"{BASE}/items/{first['id']}", headers=who["headers"]).status_code == 200
        assert _upload(who).status_code == 429
    finally:
        _set_settings(limits={"pro": {"daily": 10, "monthly": 150}})


def test_monthly_limit():
    who = _owner("monthly")
    _set_settings(limits={"business": {"daily": 1, "monthly": 1}})
    _set_plan(who["tenant_id"], "business")
    try:
        async def backdate():
            async with _session_factory() as session:
                session.add(ChartAnalysisRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], status="deleted",
                                                created_at=datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=1)))
                await session.commit()
        _run(backdate())
        refused = _upload(who)
        assert refused.status_code == 429
        assert "a month" in refused.json()["detail"]["message"] or "a day" in refused.json()["detail"]["message"]
    finally:
        _set_settings(limits={"business": {"daily": 50, "monthly": 1000}})


def test_free_plan_is_not_included_and_the_trial_gets_its_own_smaller_limit():
    free = _owner("free", plan="free")
    response = _upload(free)
    assert response.status_code == 402 and response.json()["detail"]["code"] == "not_in_plan"
    trial = _owner("trial", plan="pro")
    _set_plan(trial["tenant_id"], "pro", trialing=True)
    _set_settings(limits={"trial": {"daily": 1, "monthly": 10}})
    status = client.get(f"{BASE}/status", headers=trial["headers"]).json()
    assert status["limits"]["tier"] == "trial" and status["limits"]["daily_limit"] == 1
    assert _upload(trial).status_code == 201
    assert _upload(trial).status_code == 429


def test_the_ai_cost_cap_stops_platform_calls(monkeypatch):
    """The organisation's AI path: the cap is checked before the call (the call is never made over it)."""
    from app.ai import metering
    who = _owner("cost")
    _set_settings(limits={"pro": {"daily": 10, "monthly": 150, "cost_cap_usd": 0.0}})
    try:
        called = []

        class FakeLLM(metering.MeteredProvider):
            async def complete_full(self, *a, **k):
                called.append(1)
                raise AssertionError("over the cap: no call")

        async def fake_provider_for(session, tenant, client=None, task="general", user_id=None):
            return FakeLLM(inner=type("I", (), {"name": "anthropic", "model": "claude-sonnet-5-5"})(), session=session, tenant_id=tenant.id,
                           feature=task)
        monkeypatch.setattr("app.ai.settings.provider_for", fake_provider_for)
        item = _upload(who).json()
        response = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
        assert response.status_code == 429 and "budget" in response.json()["detail"]["message"] and called == []
        assert _row(item["id"]).status == "ready"
    finally:
        _set_settings(limits={"pro": {"daily": 10, "monthly": 150, "cost_cap_usd": 10.0}})


def test_the_rate_limit_applies():
    who = _owner("rate")
    _set_settings(rate_limit_per_minute=2)
    try:
        assert _upload(who).status_code == 201 and _upload(who).status_code == 201
        assert _upload(who).status_code == 429
    finally:
        _set_settings(rate_limit_per_minute=600)


# --- delete, retention ----------------------------------------------------------------------------------------------------

def test_delete_removes_the_file_and_the_result(monkeypatch, tmp_path):
    who = _owner("del")
    _fake_answer(monkeypatch)
    item = _upload(who).json()
    client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    path = tmp_path / "private" / _row(item["id"]).image_path
    assert path.exists()
    assert client.delete(f"{BASE}/items/{item['id']}", headers=who["headers"]).json() == {"deleted": item["id"]}
    assert not path.exists()
    row = _row(item["id"])
    assert row.status == "deleted" and row.result_json is None and row.image_path is None and row.deleted_at is not None
    assert client.get(f"{BASE}/items/{item['id']}", headers=who["headers"]).status_code == 404
    assert client.get(item["image"]["url"], headers=who["headers"]).status_code == 404
    assert client.delete(f"{BASE}/items/{item['id']}", headers=who["headers"]).status_code == 404


def test_retention_purges_expired_images_and_then_the_counters(tmp_path):
    who = _owner("ret")
    item = _upload(who).json()
    path = tmp_path / "private" / _row(item["id"]).image_path

    async def age(days):
        async with _session_factory() as session:
            row = await session.get(ChartAnalysisRecord, item["id"])
            row.created_at = datetime.now(timezone.utc) - timedelta(days=days)
            await session.commit()

    async def purge():
        async with _session_factory() as session:
            n = await service.purge_expired(session)
            await session.commit()
            return n

    _run(age(5))
    _run(purge())
    assert path.exists() and _row(item["id"]).status == "ready"         # inside the 30 days
    _run(age(31))
    assert _run(purge()) >= 1
    assert not path.exists() and _row(item["id"]).status == "deleted"
    _run(age(40))
    _run(purge())
    assert _row(item["id"]) is None


def test_the_daily_retention_job_runs_the_purge(tmp_path):
    from app.retention.service import run_retention
    who = _owner("retjob")
    item = _upload(who).json()

    async def go():
        async with _session_factory() as session:
            row = await session.get(ChartAnalysisRecord, item["id"])
            row.created_at = datetime.now(timezone.utc) - timedelta(days=31)
            await session.commit()
            return await run_retention(session)
    report = _run(go())
    assert report.deleted.get("chart_analyses", 0) >= 1 and _row(item["id"]).status == "deleted"


# --- tenancy ----------------------------------------------------------------------------------------------------------------

def test_another_member_or_organisation_never_sees_an_item(monkeypatch):
    owner = _owner("ten-a")
    _fake_answer(monkeypatch)
    item = _upload(owner).json()
    client.post(f"{BASE}/items/{item['id']}/analyze", headers=owner["headers"])
    member = _member(owner)
    other = _owner("ten-b")
    for who in (member, other):
        assert client.get(f"{BASE}/items", headers=who["headers"]).json()["items"] == []
        for method, path in (("GET", f"/items/{item['id']}"), ("GET", f"/items/{item['id']}/image"), ("POST", f"/items/{item['id']}/analyze"),
                             ("DELETE", f"/items/{item['id']}")):
            assert client.request(method, BASE + path, headers=who["headers"]).status_code == 404, (who, method, path)
    assert _row(item["id"]).status == "done"


# --- admin settings --------------------------------------------------------------------------------------------------------

def test_admin_settings_are_super_admin_only():
    who = _owner("adm")
    assert client.get("/api/admin/chart-analyze/settings", headers=who["headers"]).status_code == 403
    assert client.put("/api/admin/chart-analyze/settings", headers=who["headers"], json={"values": {"retention_days": 7}}).status_code == 403
