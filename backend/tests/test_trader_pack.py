"""Owner decision 23 Oct: the admin action "Enable trader features" (app/platform/trader_pack.py) - the pack never holds a
forbidden or owner-decision flag, its dependencies are met, enable / undo are audited, idempotent and exact, and only a
SUPER_ADMIN with a two-factor session reaches the routes."""
import asyncio
import json

import pytest
from sqlalchemy import select

from app.db.models import AuditLogRecord, PlatformControlRecord, User
from app.platform import controls, trader_pack
from app.platform.flag_catalog import FLAG_INFO
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client

BASE = "/api/admin/controls/trader-pack"


def _run(coro):
    return asyncio.run(coro)


async def _read(key):
    async with _session_factory() as session:
        row = await session.get(PlatformControlRecord, key)
        return None if row is None else row.value_json


async def _write(key, value):
    async with _session_factory() as session:
        row = await session.get(PlatformControlRecord, key)
        if value is None:
            if row is not None:
                await session.delete(row)
        else:
            if row is None:
                row = PlatformControlRecord(key=key)
                session.add(row)
            row.value_json = value
        await session.commit()


@pytest.fixture(autouse=True)
def _keep_flags():
    """Flags are platform-wide: every test leaves them exactly as it found them."""
    keys = (controls.KEY_FEATURE_FLAGS, trader_pack.KEY_TRADER_PACK)
    saved = {k: _run(_read(k)) for k in keys}
    _run(_write(controls.KEY_FEATURE_FLAGS, None))
    _run(_write(trader_pack.KEY_TRADER_PACK, None))
    yield
    for k, v in saved.items():
        _run(_write(k, v))


def _stored_flags():
    raw = _run(_read(controls.KEY_FEATURE_FLAGS))
    return (json.loads(raw).get("flags") or {}) if raw else {}


def _audit(event):
    async def go():
        async with _session_factory() as session:
            return list((await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.event == event))).all())
    return _run(go())


# --- the pack definition --------------------------------------------------------------------------------------------

def test_the_pack_never_holds_a_forbidden_or_decision_flag():
    for name in ("marketplace", "public_api", "self_signup", "scan_auto_paper", "live_trading", "phase0_paper_only"):
        assert name in trader_pack.FORBIDDEN and name not in trader_pack.PACK
    decision = {n for n, info in FLAG_INFO.items() if info.status == "decision"}
    assert {"marketplace_ai_listings", "broker_app_links", "byob_data_tier", "beta_cohort_gate", "legal_pages",
            "plan_limits_v2", "thesis_stock_targets"} <= decision
    assert not (set(trader_pack.PACK) & (decision | trader_pack.FORBIDDEN))
    assert len(set(trader_pack.PACK)) == len(trader_pack.PACK)
    assert set(trader_pack.PACK) <= set(controls.FEATURE_FLAGS)


def test_every_dependency_is_in_the_pack_before_it_or_on_by_default():
    for i, name in enumerate(trader_pack.PACK):
        for dep in FLAG_INFO[name].depends_on:
            assert dep in trader_pack.PACK[:i] or dep in controls.DEFAULT_ON_FLAGS, (name, dep)


def test_the_owner_listed_flags_are_in_the_pack():
    for name in ("ui_v2", "watchlists", "screener_v2", "screener_gallery", "screener_builder_u2", "screener_breakouts",
                 "screener_breakouts_bk3", "screener_breakouts_bk4", "screener_legends", "screener_legends_lg2",
                 "screener_board_views", "screener_rankings", "screener_history", "screener_backtest", "screener_tx2",
                 "screenql_amw_functions", "evidence_charts", "evidence_context", "idea_library", "strategy_templates",
                 "execution_core", "scan_then", "ai_copilot", "ai_agent", "ai_one_key", "ai_multiview", "screener_copilot",
                 "context_panel", "chart_price_action", "chart_studies", "chart_trades_layer", "chart_workspace",
                 "market_zones", "markets_view", "history_backfill", "discipline_engine", "paper_account",
                 "approval_only_entries", "risk_by_default", "decision_tools", "options_builder", "options_builder_saved",
                 "daily_rhythm", "signal_language_v2", "alerts_stale_guard", "alerts_reliable_outbox", "vix_gate",
                 "portfolio_risk"):
        assert name in trader_pack.PACK, name


def test_every_other_flag_is_listed_as_excluded_with_a_reason():
    out = trader_pack.excluded()
    assert set(out) | set(trader_pack.PACK) == set(controls.FEATURE_FLAGS)
    assert not set(out) & set(trader_pack.PACK)
    assert all(reason for reason in out.values())
    assert set(trader_pack.EXCLUDED_REASONS) <= set(controls.FEATURE_FLAGS)


def test_a_bad_pack_refuses_before_writing(monkeypatch):
    monkeypatch.setattr(trader_pack, "PACK", trader_pack.PACK + ("live_trading",))
    headers, _ = _admin("tp-bad-pack@example.com")
    with pytest.raises(RuntimeError):
        client.post(BASE, headers=headers)
    assert _stored_flags() == {}


# --- enable / undo --------------------------------------------------------------------------------------------------

def test_enable_turns_the_pack_on_audits_each_flag_and_leaves_the_rest():
    headers, _ = _admin("tp-enable@example.com")
    # A stored state before: one pack flag OFF with an allow-list, one forbidden flag OFF, one decision flag ON.
    client.put("/api/admin/controls/flags/screener_v2", headers=headers, json={"on": False, "tenants": [3]})
    client.put("/api/admin/controls/flags/marketplace", headers=headers, json={"on": False, "tenants": []})
    before_flags = _stored_flags()
    res = client.post(BASE, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["all_on"] is True and body["can_undo"] is True and body["on_count"] == len(trader_pack.PACK)
    assert "screener_v2" in body["changed"] and "ai_copilot" not in body["changed"]     # ON by default: not written
    flags = _stored_flags()
    assert flags["screener_v2"] == {"on": True, "tenants": [3]}
    assert flags["marketplace"] == before_flags["marketplace"]
    for name in trader_pack.FORBIDDEN:
        assert flags.get(name) == before_flags.get(name)
    resolved = _run(_resolved())
    assert all(resolved[name]["on"] for name in trader_pack.PACK)
    assert resolved["live_trading"]["on"] == _default_on("live_trading")
    details = _audit("feature_flag_set")
    for name in body["changed"]:
        assert f"{name}: on=True (trader pack)" in details
    assert any("screener_v2" in d for d in _audit("trader_pack_enabled"))


def test_enable_is_idempotent_and_keeps_the_first_before_state():
    headers, _ = _admin("tp-idem@example.com")
    first = client.post(BASE, headers=headers).json()
    saved = _run(_read(trader_pack.KEY_TRADER_PACK))
    events = len(_audit("trader_pack_enabled"))
    second = client.post(BASE, headers=headers).json()
    assert first["changed"] and second["changed"] == []
    assert _run(_read(trader_pack.KEY_TRADER_PACK)) == saved
    assert len(_audit("trader_pack_enabled")) == events
    # A flag turned off by hand after the enable comes back on, and Undo still restores the very first state.
    client.put("/api/admin/controls/flags/watchlists", headers=headers, json={"on": False, "tenants": []})
    third = client.post(BASE, headers=headers).json()
    assert third["changed"] == ["watchlists"]
    assert json.loads(_run(_read(trader_pack.KEY_TRADER_PACK)))["before"]["watchlists"] is None


def test_undo_restores_exactly_the_stored_state_and_is_safe_twice():
    headers, _ = _admin("tp-undo@example.com")
    client.put("/api/admin/controls/flags/screener_v2", headers=headers, json={"on": False, "tenants": [5, 2]})
    client.put("/api/admin/controls/flags/ai_copilot", headers=headers, json={"on": False, "tenants": []})
    before_flags = _stored_flags()
    before_resolved = _run(_resolved())
    client.post(BASE, headers=headers)
    undone = client.post(f"{BASE}/undo", headers=headers)
    assert undone.status_code == 200
    body = undone.json()
    assert "screener_v2" in body["restored"] and "ai_copilot" in body["restored"] and body["can_undo"] is False
    assert _stored_flags() == before_flags
    assert {n: v["on"] for n, v in _run(_resolved()).items()} == {n: v["on"] for n, v in before_resolved.items()}
    assert any("trader pack undo" in d for d in _audit("feature_flag_set"))
    again = client.post(f"{BASE}/undo", headers=headers).json()
    assert again["restored"] == [] and _stored_flags() == before_flags


def test_status_lists_every_pack_flag_with_plain_words_and_never_a_key():
    headers, _ = _admin("tp-status@example.com")
    body = client.get(BASE, headers=headers).json()
    assert [r["name"] for r in body["flags"]] == list(trader_pack.PACK)
    assert body["can_undo"] is False and body["all_on"] is False
    row = {r["name"]: r for r in body["flags"]}
    assert row["watchlists"]["on"] is False and row["watchlists"]["state"] == "off"
    assert row["ai_copilot"]["on"] is True and row["ai_copilot"]["state"] in ("working", "needs_setup")
    client.post(BASE, headers=headers)
    row = {r["name"]: r for r in client.get(BASE, headers=headers).json()["flags"]}
    assert row["watchlists"]["state"] in ("working", "needs_setup")
    assert row["vix_gate"]["state"] == "on" and row["vix_gate"]["check"] == "On, nothing to check"
    assert row["screener_v2"]["plain"] == FLAG_INFO["screener_v2"].plain
    assert "(the keys are never shown)" in row["ai_copilot"]["check"] or row["ai_copilot"]["state"] == "needs_setup"


# --- who may call it ------------------------------------------------------------------------------------------------

def test_a_trader_gets_403_and_nothing_changes():
    trader = {"Authorization": f"Bearer {_register('tp-trader@example.com')}"}
    for method, path in (("get", BASE), ("post", BASE), ("post", f"{BASE}/undo")):
        assert getattr(client, method)(path, headers=trader).status_code == 403
    assert _stored_flags() == {}


def test_an_admin_without_two_factor_gets_403():
    headers = {"Authorization": f"Bearer {_register('tp-nomfa@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()

    async def promote():
        async with _session_factory() as session:
            (await session.get(User, me["id"])).role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    for method, path in (("get", BASE), ("post", BASE), ("post", f"{BASE}/undo")):
        res = getattr(client, method)(path, headers=headers)
        assert res.status_code == 403 and "two-factor" in res.json()["detail"]
    assert _stored_flags() == {}


async def _resolved():
    async with _session_factory() as session:
        return await controls.feature_flags(session)


def _default_on(name):
    return name not in controls.DEFAULT_OFF_FLAGS
