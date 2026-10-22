"""Analyze a chart: the security review's fixes (A1) - clear 4xx instead of 500s, stale "running" rows, the advisory lock,
the decode off the event loop, the cost reservation, retention that never orphans a file. Made-up data only; no network."""
import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List

import httpx
import pytest
from PIL import Image

from app.ai import metering
from app.ai.providers import Completion
from app.chart_analyze import contract, engine, imaging, limits, linkfetch, service, settings as settings_mod, storage
from app.db.models import ChartAnalysisRecord, Tenant, User
from tests.chart_analyze_fakes import FakeProvider, answer
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import (BASE, PNG, _fake_answer, _isolated, _mock_network, _owner, _row, _run, _set_settings, _streamed,  # noqa: F401
                                          _upload)

SETTINGS = settings_mod.merge({})


def _age(item_id: int, **fields) -> None:
    async def go():
        async with _session_factory() as session:
            row = await session.get(ChartAnalysisRecord, item_id)
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()
    _run(go())


# --- 1. clear errors, never a 500 -----------------------------------------------------------------------------------------

def test_an_upload_over_the_size_setting_but_under_the_body_limit_is_413_not_500():
    who = _owner("big6mb")
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * (6 * 1024 * 1024)            # 6 MB: over the 5 MB default, under the 8 MB body limit
    response = _upload(who, big)
    assert response.status_code == 413, response.text
    assert response.json()["detail"]["code"] == "image_rejected"


def _fetch(url: str, table, handler=None):
    sent: List[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return (handler or (lambda r: _streamed(PNG, "image/png")))(request)

    async def resolve(host):
        return list(table.get(host, []))
    return asyncio.run(linkfetch.fetch_image(url, SETTINGS, resolver=resolve, transport=httpx.MockTransport(record))), sent


def test_internationalised_and_full_width_hosts_are_encoded_for_dns_host_and_sni():
    fetched, sent = _fetch("https://bücher.de/chart.png", {"xn--bcher-kva.de": ["8.8.8.8"]})
    assert fetched.data == PNG and sent[0].headers["host"] == "xn--bcher-kva.de" and sent[0].extensions["sni_hostname"] == "xn--bcher-kva.de"
    fetched, sent = _fetch("https://ｅxample.com/chart.png", {"example.com": ["8.8.8.8"]})
    assert sent[0].headers["host"] == "example.com" and fetched.target.source_host == "example.com"


def test_a_redirect_to_an_internationalised_host_is_encoded_and_checked():
    def handler(request):
        if request.headers["host"] == "img.example.com":
            return httpx.Response(302, headers={"location": "https://xn--bcher-kva.de/real.png"})
        return _streamed(PNG, "image/png")
    fetched, sent = _fetch("https://img.example.com/c.png", {"img.example.com": ["8.8.8.8"], "xn--bcher-kva.de": ["1.1.1.1"]}, handler)
    assert fetched.data == PNG and [r.headers["host"] for r in sent] == ["img.example.com", "xn--bcher-kva.de"]


@pytest.mark.parametrize("url", ["https://a..b/c.png", "https://xn--/c.png", "https://" + "a" * 70 + ".com/c.png", "https://\u200b/c.png",
                                 "https://exa mple.com/c.png"])
def test_malformed_hosts_are_unreadable_links_not_errors(url):
    with pytest.raises(linkfetch.LinkUnreadable):
        _fetch(url, {})


def test_any_unexpected_error_while_fetching_is_an_unreadable_link():
    def boom(request):
        raise ValueError("something odd")
    with pytest.raises(linkfetch.LinkUnreadable):
        _fetch("https://img.example.com/c.png", {"img.example.com": ["8.8.8.8"]}, boom)


def test_the_api_answers_the_screenshot_message_for_odd_hosts(monkeypatch):
    who = _owner("idn")
    _mock_network(monkeypatch, lambda r: _streamed(PNG, "image/png"), {})
    for link in ("https://bücher.de/x.png", "https://ｅxample.com/", "https://a..b/x.png"):
        response = client.post(f"{BASE}/items", headers=who["headers"], data={"mode": "image_only", "link": link})
        assert response.status_code == 422, (link, response.text)
        assert response.json()["detail"]["message"] == "Please upload a screenshot instead"


# --- 2. stale "running" rows --------------------------------------------------------------------------------------------

def test_a_fresh_running_row_is_protected_and_a_stale_one_can_be_retried_and_deleted(monkeypatch):
    who = _owner("stale")
    _fake_answer(monkeypatch, "formed")
    item = _upload(who).json()
    now = datetime.now(timezone.utc)
    _age(item["id"], status="running", started_at=now - timedelta(minutes=1))
    assert client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).status_code == 409
    assert client.delete(f"{BASE}/items/{item['id']}", headers=who["headers"]).status_code == 409
    _age(item["id"], started_at=now - timedelta(minutes=service.running_timeout_minutes(SETTINGS) + 1))
    retried = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert retried.status_code == 200 and retried.json()["status"] == "done"
    other = _upload(who).json()
    _age(other["id"], status="running", started_at=None)                  # a row from before started_at existed: stale
    assert client.delete(f"{BASE}/items/{other['id']}", headers=who["headers"]).status_code == 200


def test_an_interrupted_analysis_never_stays_running(monkeypatch):
    """A cancelled request (the client went away) or any other BaseException: the row ends failed, not running."""
    who = _owner("cancel")
    item = _upload(who).json()

    async def cancelled(*a, **k):
        raise asyncio.CancelledError()
    monkeypatch.setattr(service, "ask", cancelled)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            tenant = await session.get(Tenant, who["tenant_id"])
            with pytest.raises(asyncio.CancelledError):
                await service.analyze(session, user, tenant, SETTINGS, item["id"])
    from app.chart_analyze import provider
    provider.set_for_tests(FakeProvider())
    _run(go())
    row = _row(item["id"])
    assert row.status == "failed" and "interrupted" in row.error


def test_retention_also_clears_stale_running_rows_but_not_live_ones(tmp_path):
    who = _owner("stalepurge")
    live, stale = _upload(who).json(), _upload(who).json()
    old = datetime.now(timezone.utc) - timedelta(days=40)
    _age(live["id"], status="running", started_at=datetime.now(timezone.utc), created_at=old)
    _age(stale["id"], status="running", started_at=old, created_at=old)

    async def purge():
        async with _session_factory() as session:
            await service.purge_expired(session)
            await session.commit()
    _run(purge())
    assert _row(live["id"]).status == "running"
    assert _row(stale["id"]) is None or _row(stale["id"]).status == "deleted"


# --- 3. the lock ------------------------------------------------------------------------------------------------------------

class _FakePgSession:
    def __init__(self):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.sql: List[str] = []

    async def execute(self, statement, params=None):
        self.sql.append(str(statement))


def test_the_add_lock_is_a_transaction_advisory_lock_never_a_row_lock_on_users():
    session = _FakePgSession()
    _run(limits.lock_user(session, SimpleNamespace(id=42)))
    assert len(session.sql) == 1 and "pg_advisory_xact_lock" in session.sql[0] and "FOR UPDATE" not in session.sql[0].upper()


def test_the_early_check_takes_no_lock(monkeypatch):
    taken = []

    async def lock(session, user):
        taken.append(user.id)

    async def usage(*a, **k):
        return {"tier": "pro", "daily_limit": 5, "monthly_limit": 5, "daily_used": 0, "monthly_used": 0, "cost_cap_usd": 1.0, "cost_used_usd": 0.0}
    monkeypatch.setattr(limits, "lock_user", lock)
    monkeypatch.setattr(limits, "usage", usage)
    tenant = SimpleNamespace(status="active", plan="pro", name="t")
    _run(limits.check_can_add(None, SimpleNamespace(id=1), tenant, SETTINGS))
    assert taken == []
    _run(limits.check_can_add(None, SimpleNamespace(id=1), tenant, SETTINGS, lock=True))
    assert taken == [1]


# --- 4. the decode ------------------------------------------------------------------------------------------------------------

def test_the_decode_runs_off_the_event_loop(monkeypatch):
    from app.chart_analyze import routes
    seen = []
    real = imaging.sanitize

    def sanitize(raw, settings):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return real(raw, settings)
    monkeypatch.setattr(routes.imaging, "sanitize", sanitize)
    who = _owner("thread")
    assert _upload(who).status_code == 201
    assert seen == ["thread"]


def test_a_16_bit_greyscale_png_is_read_and_the_pixel_default_covers_4k_screenshots():
    img = Image.new("I;16", (200, 100))
    img.putdata([(x * 300) % 65535 for x in range(200 * 100)])
    out = io.BytesIO()
    img.save(out, format="PNG")
    clean = imaging.sanitize(out.getvalue(), SETTINGS)
    with Image.open(io.BytesIO(clean.png)) as back:
        assert back.mode == "RGB" and back.size == (200, 100)
    assert 3840 * 2160 <= SETTINGS["max_image_pixels"] <= 9_000_000


# --- 5. the cost reservation and attribution -----------------------------------------------------------------------------------

def test_running_analyses_reserve_the_estimate_against_the_cost_cap(monkeypatch):
    who = _owner("reserve")
    called = []

    class FakeLLM(metering.MeteredProvider):
        async def complete_full(self, *a, **k):
            called.append(1)
            return Completion(text=json.dumps(answer("formed")), model="claude-sonnet-5-5")

    async def fake_provider_for(session, tenant, client=None, task="general", user_id=None):
        return FakeLLM(inner=SimpleNamespace(name="anthropic", model="claude-sonnet-5-5"), session=session, tenant_id=tenant.id, feature=task)
    monkeypatch.setattr("app.ai.settings.provider_for", fake_provider_for)
    estimate = engine._estimate("anthropic", "claude-sonnet-5-5", SETTINGS)
    assert estimate > 0
    _set_settings(limits={"pro": {"daily": 10, "monthly": 150, "cost_cap_usd": round(estimate * 1.5, 6)}})
    try:
        busy, item = _upload(who).json(), _upload(who).json()
        _age(busy["id"], status="running", started_at=datetime.now(timezone.utc))   # another run in flight reserves one estimate
        refused = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
        assert refused.status_code == 429 and called == []
        _age(busy["id"], status="failed")
        ok = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
        assert ok.status_code == 200 and called == [1]
    finally:
        _set_settings(limits={"pro": {"daily": 10, "monthly": 150, "cost_cap_usd": 10.0}})


def test_spend_counts_in_the_month_the_analysis_ran():
    who = _owner("attrib")
    item = _upload(who).json()
    now = datetime.now(timezone.utc)
    _age(item["id"], created_at=now - timedelta(days=45), analysed_at=now, cost_usd=0.5, status="done")

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            return await limits.platform_cost(session, user, metering.month_start(now))
    assert _run(go()) == pytest.approx(0.5)


# --- 6. retention never orphans a file, and finishes ------------------------------------------------------------------------

def test_a_failed_file_delete_keeps_the_path_and_retention_retries_it(monkeypatch, tmp_path):
    who = _owner("orphan")
    item = _upload(who).json()
    path = _row(item["id"]).image_path
    real_delete = storage.delete
    monkeypatch.setattr(storage, "delete", lambda relative: False)
    assert client.delete(f"{BASE}/items/{item['id']}", headers=who["headers"]).status_code == 200
    row = _row(item["id"])
    assert row.status == "deleted" and row.image_path == path and (tmp_path / "private" / path).exists()
    monkeypatch.setattr(storage, "delete", real_delete)

    async def purge():
        async with _session_factory() as session:
            await service.purge_expired(session)
            await session.commit()
    _run(purge())
    assert _row(item["id"]).image_path is None and not (tmp_path / "private" / path).exists()


def test_retention_works_through_every_batch():
    who = _owner("batches")
    ids = [_upload(who).json()["id"] for _ in range(3)]
    for i in ids:
        _age(i, created_at=datetime.now(timezone.utc) - timedelta(days=31))

    async def purge():
        async with _session_factory() as session:
            n = await service.purge_expired(session, batch=1)
            await session.commit()
            return n
    assert _run(purge()) >= 3
    assert all(_row(i).status == "deleted" for i in ids)


# --- 8. a framed call is withheld too ---------------------------------------------------------------------------------------------

def test_a_text_the_filter_would_frame_as_a_specific_call_is_withheld():
    raw = answer("formed")
    raw["story"] = ["One could buy the 25000 CE above 120 here.", "The swings rise."]
    result = contract.validate(raw, mode="image_only", width=640, height=360)
    assert result["story"][0] == contract.WITHHELD and result["story"][1] == "The swings rise."


def test_pillow_is_at_least_the_fixed_release():
    import PIL
    major, minor, patch = (int(x) for x in PIL.__version__.split(".")[:3])
    assert (major, minor, patch) >= (13, 0, 0)


# --- re-review: a run only ever finishes its own claim; shielded cleanup; stale rows reserve nothing ---------------------

def test_a_late_finish_after_a_re_claim_or_a_delete_changes_nothing():
    who = _owner("late")
    item = _upload(who).json()
    first = datetime.now(timezone.utc) - timedelta(minutes=30)
    second = datetime.now(timezone.utc)
    _age(item["id"], status="running", started_at=second)              # another request re-claimed the stale row

    async def finish(claimed):
        async with _session_factory() as session:
            return await service._finish(session, item["id"], claimed, "done", result='{"x":1}', cost=1.0)
    assert _run(finish(first)) is False
    row = _row(item["id"])
    assert row.status == "running" and row.result_json is None and row.cost_usd == 0
    assert _run(finish(second)) is True and _row(item["id"]).status == "done"
    other = _upload(who).json()
    _age(other["id"], status="running", started_at=first)
    assert client.delete(f"{BASE}/items/{other['id']}", headers=who["headers"]).status_code == 200      # stale: deletable

    async def late():
        async with _session_factory() as session:
            return await service._finish(session, other["id"], first, "done", result='{"x":1}', cost=1.0)
    assert _run(late()) is False
    row = _row(other["id"])
    assert row.status == "deleted" and row.result_json is None                                            # not resurrected


def test_the_setting_never_goes_below_the_ai_calls_worst_case():
    assert settings_mod.BOUNDS["running_timeout_minutes"][0] >= 10
    assert service.running_timeout_minutes({**SETTINGS, "running_timeout_minutes": 10}) >= service.ai_floor_minutes()


def test_a_cancelled_run_is_cleaned_up_even_under_cancellation(monkeypatch):
    import anyio
    from app.chart_analyze import provider
    who = _owner("shield")
    item = _upload(who).json()

    async def slow(*a, **k):
        await anyio.sleep(30)
    monkeypatch.setattr(service, "ask", slow)
    provider.set_for_tests(FakeProvider())

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            tenant = await session.get(Tenant, who["tenant_id"])
            async with anyio.create_task_group() as tg:
                async def run():
                    await service.analyze(session, user, tenant, SETTINGS, item["id"])
                tg.start_soon(run)
                await anyio.sleep(0.3)
                tg.cancel_scope.cancel()
    anyio.run(go)
    row = _row(item["id"])
    assert row.status == "failed" and "interrupted" in row.error


def test_a_stale_running_row_reserves_no_cost():
    who = _owner("stalecost")
    item = _upload(who).json()
    now = datetime.now(timezone.utc)

    async def count():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            return await limits.running_count(session, user, SETTINGS, now)
    _age(item["id"], status="running", started_at=now - timedelta(minutes=service.running_timeout_minutes(SETTINGS) + 5))
    assert _run(count()) == 0
    _age(item["id"], started_at=now - timedelta(minutes=1))
    assert _run(count()) == 1


def test_at_most_two_decodes_run_at_once_and_waiting_ones_hold_no_thread(monkeypatch):
    import threading
    import time
    from app.chart_analyze import routes
    lock = threading.Lock()
    state = {"now": 0, "max": 0, "threads": set()}

    def sanitize(raw, settings):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            state["threads"].add(threading.get_ident())
        time.sleep(0.15)
        with lock:
            state["now"] -= 1
        return "ok"
    monkeypatch.setattr(routes.imaging, "sanitize", sanitize)
    handed = {"now": 0, "max": 0}
    real_pool = routes.run_in_threadpool

    async def counting_pool(fn, *args):
        handed["now"] += 1
        handed["max"] = max(handed["max"], handed["now"])
        try:
            return await real_pool(fn, *args)
        finally:
            handed["now"] -= 1
    monkeypatch.setattr(routes, "run_in_threadpool", counting_pool)

    async def go():
        return await asyncio.gather(*(routes._decode(b"x", SETTINGS) for _ in range(6)))
    assert asyncio.run(go()) == ["ok"] * 6
    assert state["max"] == routes.DECODE_SLOTS
    assert handed["max"] == routes.DECODE_SLOTS          # the other four waited on the loop, not in a worker thread


def test_a_raw_non_ascii_query_is_percent_encoded_not_refused():
    fetched, sent = _fetch("https://img.example.com/c.png?q=ü&x=%20a", {"img.example.com": ["8.8.8.8"]})
    assert fetched.data == PNG
    assert sent[0].url.query == b"q=%C3%BC&x=%20a"
