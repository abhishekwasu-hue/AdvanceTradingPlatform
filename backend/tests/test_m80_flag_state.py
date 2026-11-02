"""M80 (owner order 1 Nov): the truthful feature-flag status - one structured state per flag (ready / behind the scenes /
needs your setup / not built yet / owner decision) with its reason and where, the live checks (✓ / ✗ from this server's
own state, admin-only), and the server's refusal to turn on a flag that is not built yet (409; turning it off works)."""
import asyncio
import re
import uuid

from app.core import config
from app.db.models import AiProviderConfigRecord, AlertChannelRecord, BrokerCredentialRecord, User, UserAiKeyRecord
from app.platform import controls, flag_state
from app.platform.flag_catalog import FLAG_INFO, info_for
from app.platform.flag_state import FLAG_STATUS, READY_STATES, STATE_LABELS, run_probes, status_for
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"m80-{tag}-{uuid.uuid4().hex[:6]}@example.com"


# --- F1 / F5: the catalog -------------------------------------------------------------------------------------------

def test_every_flag_has_exactly_one_state_and_the_catalog_stays_in_sync():
    assert set(FLAG_STATUS) == set(controls.FEATURE_FLAGS) == set(FLAG_INFO)
    for name, entry in FLAG_STATUS.items():
        assert entry.state in STATE_LABELS, name
        for probe in entry.probes:
            assert probe in flag_state.PROBES and probe in flag_state.PROBE_LABELS, (name, probe)


def test_every_non_ready_state_has_a_reason():
    for name, entry in FLAG_STATUS.items():
        if entry.state not in READY_STATES:
            assert entry.item.strip(), name


def test_needs_your_setup_names_the_item_and_where_and_not_built_names_the_order():
    for name, entry in FLAG_STATUS.items():
        if entry.state == "needs_setup":
            assert entry.item.strip() and entry.where.strip(), name
        if entry.state == "not_built":
            assert entry.item.strip() and entry.order.strip(), name


def test_state_texts_are_plain_words():
    snake = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")
    for name, entry in FLAG_STATUS.items():
        for text in (entry.item, entry.where):
            assert not snake.search(text), (name, text)


def test_the_old_status_field_is_kept_for_its_readers():
    info = info_for("multi_account")
    assert info["status"] == "unfinished" and info["status_label"] == "Unfinished / needs setup"   # 22 Oct label, as before
    assert info["state"] == "ready" and info["state_label"] == "Ready"
    setup = info_for("telegram_inbound")
    assert setup["state"] == "needs_setup" and setup["state_reason"] == "Telegram bot"
    assert setup["state_where"] == "Settings › Alerts" and setup["probes"] == ["telegram"]
    assert info_for("ai_research")["state"] == "not_built" and info_for("ai_research")["state_order"] == "FN5"
    assert info_for("live_trading")["state"] == "decision"
    assert status_for("no_such_flag").state == "not_built"


def test_the_eleven_flags_with_an_empty_need_now_say_what_they_are():
    # The owner's finding: 11 "Unfinished / needs setup" flags named nothing.
    for name in ("vision_byok_v3", "strategy_lab_pine", "multi_account", "screener_legends_lg3", "screener_waves", "term_roll",
                 "term_whatif", "strategy_templates", "chart_analyze_share", "growth_v2", "marketing_site"):
        entry = status_for(name)
        assert entry.state != "needs_setup" or entry.item, name
        assert entry.state in READY_STATES or entry.item, name


# --- F3: the live checks ----------------------------------------------------------------------------------------------

async def _user(email: str) -> User:
    from sqlalchemy import select
    async with _session_factory() as session:
        return (await session.scalars(select(User).where(User.email == email))).one()


def _probe(user: User, ids):
    async def go():
        async with _session_factory() as session:
            return await run_probes(session, user, list(ids))
    return _run(go())


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def test_ai_key_probe_reads_the_users_key_or_the_organisations_provider():
    email = _email("ai")
    _register(email)
    user = _run(_user(email))
    assert _probe(user, ["ai_key"]) == {"ai_key": False}
    _add(AiProviderConfigRecord(tenant_id=user.tenant_id, provider="rule_based", model="", enabled=True))
    assert _probe(user, ["ai_key"]) == {"ai_key": False}                    # the built-in rules are not a key
    email2 = _email("ai2")
    _register(email2)
    other = _run(_user(email2))
    _add(UserAiKeyRecord(tenant_id=other.tenant_id, user_id=other.id, provider="openai", encrypted_key="not-a-real-key",
                         key_hint="…test"))
    assert _probe(other, ["ai_key"]) == {"ai_key": True}
    email3 = _email("ai3")
    _register(email3)
    org = _run(_user(email3))
    _add(AiProviderConfigRecord(tenant_id=org.tenant_id, provider="openai", model="", enabled=True,
                                encrypted_api_key="not-a-real-key"))
    assert _probe(org, ["ai_key"]) == {"ai_key": True}


def test_broker_probe_is_true_only_for_a_connected_broker():
    email = _email("broker")
    _register(email)
    user = _run(_user(email))
    assert _probe(user, ["broker"]) == {"broker": False}
    _add(BrokerCredentialRecord(tenant_id=user.tenant_id, user_id=user.id, broker_name="zerodha", account_label="primary",
                                encrypted_payload="not-real", token_status="EXPIRED"))
    assert _probe(user, ["broker"]) == {"broker": False}
    _add(BrokerCredentialRecord(tenant_id=user.tenant_id, user_id=user.id, broker_name="zerodha", account_label="second",
                                encrypted_payload="not-real", token_status="VALID"))
    assert _probe(user, ["broker"]) == {"broker": True}


def test_telegram_probe_reads_the_platform_bot_or_the_organisations_channel(monkeypatch):
    monkeypatch.setattr(config, "ATP_TELEGRAM_BOT_TOKEN", "")
    email = _email("tg")
    _register(email)
    user = _run(_user(email))
    assert _probe(user, ["telegram"]) == {"telegram": False}
    _add(AlertChannelRecord(tenant_id=user.tenant_id, channel_type="TELEGRAM", enabled=True, encrypted_config="not-real"))
    assert _probe(user, ["telegram"]) == {"telegram": True}
    email2 = _email("tg2")
    _register(email2)
    other = _run(_user(email2))
    monkeypatch.setattr(config, "ATP_TELEGRAM_BOT_TOKEN", "not-a-real-token")
    assert _probe(other, ["telegram"]) == {"telegram": True}


def test_private_package_probes_follow_the_loaders_and_keep_the_not_installed_line():
    from app.chart_analyze import provider
    from app.engine_provider import loader
    email = _email("pkg")
    _register(email)
    user = _run(_user(email))
    provider.set_for_tests(None)
    loader.set_for_tests(None)
    try:
        assert _probe(user, ["chart_engine", "signal_engine"]) == {"chart_engine": False, "signal_engine": False}
        headers, _ = _admin(_email("pkg-admin"))
        flags = client.get("/api/admin/controls/flags", headers=headers).json()
        assert flags["trade_engine"]["unavailable_reason"] == controls.TRADE_ENGINE_MISSING_REASON     # kept as it was
        assert flags["trade_engine"]["checks"] == [{"id": "signal_engine", "label": "Private package on this server", "ok": False}]
        assert {"id": "chart_engine", "label": "Private package on this server", "ok": False} in flags["chart_analyze"]["checks"]
        loader.set_for_tests(object())
        assert _probe(user, ["signal_engine"]) == {"signal_engine": True}
    finally:
        provider.set_for_tests(None)
        loader.set_for_tests(None)


def test_the_admin_list_serves_the_state_and_the_checks():
    headers, _ = _admin(_email("list"))
    flags = client.get("/api/admin/controls/flags", headers=headers).json()
    entry = flags["telegram_inbound"]
    assert entry["state"] == "needs_setup" and entry["state_label"] == "Needs your setup"
    assert entry["checks"] and entry["checks"][0]["id"] == "telegram" and isinstance(entry["checks"][0]["ok"], bool)
    assert "checks" not in flags["screener_gallery"]
    assert flags["screener_gallery"]["status"] == "ready"                     # the old field is still served


# --- F4: not built yet ------------------------------------------------------------------------------------------------

def test_a_not_built_flag_cannot_be_turned_on_but_can_be_turned_off():
    headers, _ = _admin(_email("nb"))
    for name in (n for n, e in FLAG_STATUS.items() if e.state == "not_built"):
        url = f"/api/admin/controls/flags/{name}"
        refused = client.put(url, headers=headers, json={"on": True, "tenants": []})
        assert refused.status_code == 409 and "not built yet" in refused.json()["detail"], name
        assert client.put(url, headers=headers, json={"on": False, "tenants": [1]}).status_code == 409
        assert client.put(url, headers=headers, json={"on": False, "tenants": []}).status_code == 200


def test_a_user_token_gets_403_on_the_admin_endpoints():
    trader = {"Authorization": f"Bearer {_register(_email('trader'))}"}
    assert client.get("/api/admin/controls/flags", headers=trader).status_code == 403
    assert client.put("/api/admin/controls/flags/screener_gallery", headers=trader,
                      json={"on": True, "tenants": []}).status_code == 403
