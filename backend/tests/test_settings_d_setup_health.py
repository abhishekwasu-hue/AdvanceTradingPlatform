"""Settings redesign part D ("Setup health", docs/design/SETTINGS_REDESIGN_REVIEW.md R8): `GET /api/me/setup` carries
`health`, the items of the caller's setup that need an action now (app/me/setup_health.py). One test per state, the
"nothing when all is fine" answer, `ui_v2` off, the cache per (tenant, user), and no call to Telegram."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import byok
from app.alerts import platform_bot
from app.brokers import upstox_analytics
from app.db.models import AlertChannelRecord, BrokerCredentialRecord, UserAiKeyRecord
from app.me import setup_health
from app.observability import status_page
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _flags
from tests.test_ui2_4a1_prefs import _who

BASE = "/api/me/setup"
_REAL_FEED = status_page._data_feed


def _run(coro):
    return asyncio.run(coro)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


def _health(who: dict):
    got = client.get(BASE, headers=who["headers"])
    assert got.status_code == 200, got.text
    return got.json()["health"]


def _keys(items) -> dict:
    return {item["key"]: item for item in items}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    setup_health.reset_cache()
    monkeypatch.setattr(platform_bot, "configured", lambda: False)

    async def operational(*_a, **_k):                           # other suites may load an older end-of-day file
        return {"state": status_page.OPERATIONAL}

    monkeypatch.setattr(status_page, "_data_feed", operational)
    _flags(ui_v2=True, ai_one_key=True)
    yield
    _flags(ui_v2=False, ai_one_key=False)
    setup_health.reset_cache()


def _ai_key(who: dict, ok, error=None) -> UserAiKeyRecord:
    return UserAiKeyRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], provider="anthropic", encrypted_key="x",
                           key_hint="sk-a…a9F2", model="", last_test_ok=ok, last_test_error=error,
                           last_tested_at=datetime.now(timezone.utc))


def test_nothing_when_all_is_fine():
    who = _who("sd-fine")
    assert _health(who) == []


def test_the_real_data_feed_read_runs(monkeypatch):
    monkeypatch.setattr(status_page, "_data_feed", _REAL_FEED)
    assert isinstance(_health(_who("sd-real-feed")), list)


def test_ui_v2_off_carries_no_health():
    who = _who("sd-off")
    _flags(ui_v2=False)
    assert _health(who) is None


def test_ai_key_out_of_credit_and_failing():
    who = _who("sd-ai-credit")
    _add(_ai_key(who, False, byok.PLAIN_ERRORS["no_credit"]))
    item = _keys(_health(who))["ai_key"]
    assert item == {"key": "ai_key", "state": "no_credit", "message": "This key has no credit left."}

    other = _who("sd-ai-auth")
    _add(_ai_key(other, False, byok.PLAIN_ERRORS["auth"]))
    assert _keys(_health(other))["ai_key"]["state"] == "failing"


def test_ai_key_passing_break_or_working_or_absent_shows_nothing():
    for tag, ok, error in (("rate", False, byok.PLAIN_ERRORS["rate"]), ("ok", True, None), ("untested", None, None),
                           ("legacy", False, "provider said 500")):
        who = _who(f"sd-ai-{tag}")
        _add(_ai_key(who, ok, error))
        assert "ai_key" not in _keys(_health(who)), tag
    # the Connect AI panel's flag off: no AI row even for a failing key
    who = _who("sd-ai-flag")
    _add(_ai_key(who, False, byok.PLAIN_ERRORS["no_credit"]))
    _flags(ai_one_key=False)
    assert "ai_key" not in _keys(_health(who))


def test_telegram_not_connected_reads_the_row_only_and_never_calls_telegram(monkeypatch):
    calls = []

    async def no_poll(*_a, **_k):
        calls.append("poll")
        raise AssertionError("setup health must not poll Telegram")

    async def no_call(*_a, **_k):
        calls.append("call")
        raise AssertionError("setup health must not call Telegram")

    monkeypatch.setattr(platform_bot, "poll_once", no_poll)
    monkeypatch.setattr(platform_bot, "_call", no_call)
    monkeypatch.setattr(platform_bot, "configured", lambda: True)
    who = _who("sd-tg")
    assert _keys(_health(who))["telegram"] == {"key": "telegram", "state": "not_connected"}
    # an organisation channel that is on: nothing to connect
    other = _who("sd-tg-on")
    _add(AlertChannelRecord(tenant_id=other["tenant_id"], channel_type="TELEGRAM", encrypted_config="x", enabled=True))
    assert "telegram" not in _keys(_health(other))
    # switched off counts as not connected
    off = _who("sd-tg-off")
    _add(AlertChannelRecord(tenant_id=off["tenant_id"], channel_type="TELEGRAM", encrypted_config="x", enabled=False))
    assert "telegram" in _keys(_health(off))
    assert calls == []


def test_telegram_not_shown_when_the_server_cannot_connect_it():
    assert "telegram" not in _keys(_health(_who("sd-tg-none")))


def test_data_feed_delayed_and_a_failing_read(monkeypatch):
    async def degraded(*_a, **_k):
        return {"state": status_page.DEGRADED}

    monkeypatch.setattr(status_page, "_data_feed", degraded)
    assert _keys(_health(_who("sd-feed")))["data_feed"] == {"key": "data_feed", "state": "delayed"}

    async def broken(*_a, **_k):
        raise RuntimeError("no calendar")

    setup_health.reset_cache()
    monkeypatch.setattr(status_page, "_data_feed", broken)
    assert "data_feed" not in _keys(_health(_who("sd-feed-x")))


def test_analytics_token_expired_and_expiring_today(monkeypatch):
    now = datetime.now(timezone.utc)
    views = {}
    monkeypatch.setattr(upstox_analytics, "record_view", lambda record, _now=None: views[record.account_label])
    who = _who("sd-token")
    _add(BrokerCredentialRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], broker_name="upstox", account_label="a", encrypted_payload="x"))
    views["a"] = {"analytics_token_set": True, "analytics_token_expired": True, "analytics_token_expires_at": (now - timedelta(hours=1)).isoformat()}
    assert _keys(_health(who))["analytics_token"]["state"] == "expired"

    setup_health.reset_cache()
    views["a"] = {"analytics_token_set": True, "analytics_token_expired": False, "analytics_token_expires_at": (now + timedelta(minutes=5)).isoformat()}
    assert _keys(_health(who))["analytics_token"]["state"] == "expiring"

    setup_health.reset_cache()
    views["a"] = {"analytics_token_set": True, "analytics_token_expired": False, "analytics_token_expires_at": (now + timedelta(days=30)).isoformat()}
    assert "analytics_token" not in _keys(_health(who))

    setup_health.reset_cache()
    views["a"] = {"analytics_token_set": False}
    assert "analytics_token" not in _keys(_health(who))


def test_cached_per_tenant_and_user_and_dropped_when_the_key_changes(monkeypatch):
    who = _who("sd-cache")
    other = _who("sd-cache-2")
    assert _health(who) == []
    _add(_ai_key(who, False, byok.PLAIN_ERRORS["no_credit"]))
    assert _health(who) == []                                   # cached: the new row is not read yet
    assert _health(other) == []                                 # another caller has their own entry
    setup_health.forget(who["tenant_id"], who["user_id"])
    assert _keys(_health(who))["ai_key"]["state"] == "no_credit"
    # the cache runs out after CACHE_SECONDS
    real = setup_health.time.monotonic
    monkeypatch.setattr(setup_health.time, "monotonic", lambda: real() + setup_health.CACHE_SECONDS + 1)
    assert "ai_key" in _keys(_health(who))
    assert 30 <= setup_health.CACHE_SECONDS <= 60


def test_put_answers_health_too():
    who = _who("sd-put")
    body = client.get(BASE, headers=who["headers"]).json()
    saved = client.put(f"{BASE}?version={body['version']}", headers=who["headers"], json=body["stored"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["health"] == []
