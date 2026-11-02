"""M68 PR-6 (owner order M68, TS1-060 .. 063): Automation - no pre-chosen entry mode, approval waiting time, re-quote
limit or scanner limit.

Per value: Not set => a bot asks / skips with "Needs your setting: <field>" and places nothing (the scanner makes a
proposal instead); saved => used; the bot's own value wins. "Ask me first" needs no organisation flag any more; the flag
stays an admin control (out of the trader pack). The migration keeps every stored bot value (TS-6) and goes back down.
"""
import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core import config as app_config
from app.db.models import StrategyDeploymentRecord, User
from app.deployments import why
from app.execution import approval, automation
from app.me import trade_settings as ts
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _force_signal, _get, _signal, _tenant, _trades, _worker
from tests.test_z3_approval_entries import _actions, _email, _flag_for, _orders, _price, _propose, _shift_expiry

BACKEND = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.real_automation,                    # M68: each user's own (empty) Automation settings
              pytest.mark.usefixtures("no_open_positions_from_earlier_tests", "owner_switched_live_on")]
URL = "/api/me/trade-settings"
KEYS = ("approval_mode", "approval_entry_minutes", "requote_drift_pct", "max_requotes", "scan_auto_max",
        "scan_auto_cooldown_minutes", "scan_auto_arming_days")


def _run(coro):
    return asyncio.run(coro)


def _save(t: dict, **values) -> None:
    out = client.put(URL, headers=t["headers"], json={"values": {f"automation.{k}": v for k, v in values.items()}})
    assert out.status_code == 200, out.text


def _mine(t: dict) -> automation.Automation:
    async def go():
        async with _session_factory() as session:
            return await automation.load(session, await session.get(User, t["user_id"]))
    return _run(go())


def _scan_flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get("scan_auto_paper") or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags["scan_auto_paper"] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


# --- the settings -----------------------------------------------------------------------------------------------------

def test_every_automation_value_is_not_set_for_a_new_user_and_saved_values_are_read():
    t = _tenant(_email("m68a-new"))
    assert _mine(t) == automation.Automation()
    _save(t, approval_mode="APPROVAL", approval_entry_minutes=20, requote_drift_pct=0.8, max_requotes=0)
    got = _mine(t)
    assert (got.approval_mode, got.approval_entry_minutes, got.requote_drift_pct, got.max_requotes) == ("APPROVAL", 20, 0.8, 0)
    assert got.scan_missing() == list(automation.SCAN_FIELDS)


def test_an_older_saved_entry_mode_is_kept_ts6():
    t = _tenant(_email("m68a-legacy"))
    out = client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"entry_mode": "APPROVAL"})
    assert out.status_code == 200, out.text
    assert _mine(t).approval_mode == "APPROVAL"
    _save(t, approval_mode="AUTO")                              # the Trade settings value wins over the older store
    assert _mine(t).approval_mode == "AUTO"


def test_needs_setting_words_and_the_why_code():
    text = automation.needs_setting("approval_mode")
    assert text == "Needs your setting: New entries (Settings › Trade settings › Automation)"
    assert why.held_back(f"Signal skipped. {text}") == "needs_setting"


def test_the_scanner_fields_show_only_where_its_auto_paper_is_on():
    t = _tenant(_email("m68a-scanflag"))
    keys = lambda: {f["key"] for s in client.get(URL, headers=t["headers"]).json()["sections"] for f in s["fields"]}   # noqa: E731
    assert "automation.approval_mode" in keys() and "automation.scan_auto_max" not in keys()
    refused = client.put(URL, headers=t["headers"], json={"values": {"automation.scan_auto_max": 2}})
    assert refused.status_code == 422
    _scan_flag(t["tenant_id"])
    try:
        assert {"automation.scan_auto_max", "automation.scan_auto_cooldown_minutes", "automation.scan_auto_arming_days"} <= keys()
        _save(t, scan_auto_max=2, scan_auto_cooldown_minutes=0, scan_auto_arming_days=3)
        assert _mine(t).scan_missing() == []
    finally:
        _scan_flag(t["tenant_id"], on=False)


# --- the bot form / API ------------------------------------------------------------------------------------------------

def _bot(t: dict, **kw) -> dict:
    body = {"strategy_id": "rsi_adx_momentum_5m", "symbol": f"M6A{uuid.uuid4().hex[:5].upper()}", "exchange": "NSE", "timeframe": "1min",
            "mode": "PAPER"}
    body.update(kw)
    out = client.post("/api/deployments", headers=t["headers"], json=body)
    assert out.status_code in (200, 201), out.text
    created = out.json()
    client.post(f"/api/deployments/{created['id']}/stop", headers=t["headers"], json={"reason": "test done"})
    return created


def test_a_bot_stores_a_mode_only_when_picked_for_it_and_otherwise_follows_trade_settings():
    t = _tenant(_email("m68a-bot"))
    bare = _bot(t)
    assert bare["entry_mode"] is None and bare["entry_mode_follows_settings"] is True
    assert bare["entry_mode_in_effect"] is None                             # Not set anywhere: places nothing
    _save(t, approval_mode="APPROVAL")                                      # no organisation flag needed (TS1-061)
    following = _bot(t)
    assert following["entry_mode"] is None                                  # his CURRENT setting is never copied onto the bot
    assert (following["entry_mode_in_effect"], following["entry_mode_follows_settings"]) == ("APPROVAL", True)
    own = _bot(t, entry_mode="AUTO")                                        # picked for this bot: its own value wins
    assert (own["entry_mode"], own["entry_mode_in_effect"], own["entry_mode_follows_settings"]) == ("AUTO", "AUTO", False)


def test_who_is_approval_only_flag_then_bot_then_user():
    t = _tenant(_email("m68a-required"))

    async def check(entry_mode):
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id="x", symbol="X", created_by=t["user_id"], entry_mode=entry_mode)
            return (await approval.required(session, t["tenant_id"], dep), await approval.required(session, t["tenant_id"], user_id=t["user_id"]),
                    await automation.entry_mode(session, dep))
    assert _run(check(None)) == (False, False, None)
    _save(t, approval_mode="APPROVAL")
    assert _run(check(None)) == (True, True, "APPROVAL")                   # his "Ask me first": his bots and his alerts
    assert _run(check("AUTO")) == (False, True, "AUTO")                     # a bot's own choice wins for that bot
    _save(t, approval_mode="AUTO")
    _flag_for(t["tenant_id"])
    try:
        assert _run(check("AUTO"))[:2] == (True, True)                     # the admin's flag for a team wins over both
    finally:
        _flag_for(t["tenant_id"], on=False)


def test_the_gate_refuses_a_bot_entry_with_no_mode_anywhere():
    t = _tenant(_email("m68a-gate"))
    dep_id = _deploy(t)

    async def gate():
        async with _session_factory() as session:
            return await approval.gate_refusal(session, t["tenant_id"], deployment_id=dep_id, origin=approval.ORIGIN_AUTOMATIC,
                                               approval_action_id=None, user_id=t["user_id"])
    assert _run(gate()) == automation.needs_setting("approval_mode")
    _save(t, approval_mode="AUTO")
    assert _run(gate()) is None


# --- the worker -------------------------------------------------------------------------------------------------------

def test_the_worker_skips_a_bot_with_no_mode_then_follows_his_setting(monkeypatch):
    t = _tenant(_email("m68a-worker"))
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.1))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == []
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert automation.needs_setting("approval_mode") in dep.last_error and _actions(t["tenant_id"], "ENTRY") == []

    _save(t, approval_mode="APPROVAL", approval_entry_minutes=25)         # "Ask me first": one request, his waiting time

    async def fresh_signal():                                               # the skipped bar is done; read a new one
        async with _session_factory() as session:
            row = await session.get(StrategyDeploymentRecord, dep_id)
            row.last_signal_at = None
            await session.commit()
    _run(fresh_signal())
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    entries = _actions(t["tenant_id"], "ENTRY")
    assert len(entries) == 1 and _orders(t["tenant_id"]) == []
    made = (OPEN_NOW + timedelta(minutes=1)).astimezone(UTC)
    assert entries[0].expires_at.replace(tzinfo=UTC) - made == timedelta(minutes=25)


def test_switching_trade_settings_to_ask_me_first_makes_a_following_bot_ask_and_an_auto_bot_keeps_entering(monkeypatch):
    t = _tenant(_email("m68a-switch"))
    _save(t, approval_mode="AUTO")
    following = _deploy(t, symbol="RELIANCE")

    async def own_auto():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id="x", symbol="Y", created_by=t["user_id"], entry_mode="AUTO")
            return await approval.required(session, t["tenant_id"], dep), await automation.entry_mode(session, dep)
    _save(t, approval_mode="APPROVAL")                                      # he switches later
    assert _run(own_auto()) == (False, "AUTO")                              # a bot he set to AUTO itself keeps AUTO
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.1))
    _run(worker.run_cycle(now=OPEN_NOW))
    entries = _actions(t["tenant_id"], "ENTRY")
    assert len(entries) == 1 and entries[0].deployment_id == following       # the following bot now asks first
    assert _orders(t["tenant_id"]) == [] and _trades(t["tenant_id"]) == []
    assert "platform time - set your own in Trade settings" in entries[0].reason   # M68Q-PR6-1: the card says whose time


def test_exits_never_read_the_automation_settings():
    """ADR-0004: nothing on the exit path reads Automation (an exit closes whatever New entries says)."""
    import subprocess
    out = subprocess.run(["grep", "-rlE", "--include=*.py", r"automation\.(load|entry_mode|needs_setting)|from app.execution import .*automation",
                          "app"], cwd=BACKEND, capture_output=True, text=True).stdout.split()
    assert sorted(out) == sorted(["app/deployments/routes.py", "app/execution/approval.py", "app/execution/core/dispatcher.py",
                                  "app/screener/then/auto_paper.py", "app/screener/then/routes.py", "app/workers/trading_worker.py"])
    text = (BACKEND / "app/execution/approval.py").read_text(encoding="utf-8")
    exit_part = text[text.index("# --- exits policy (ADR-0004)"):]
    assert "automation" not in exit_part


# --- the request: waiting time, price move, new prices ---------------------------------------------------------------

def test_a_request_waits_his_minutes_else_the_platform_time():
    t = _tenant(_email("m68a-ttl"))
    rows = {}

    async def expiry(action_id):
        async with _session_factory() as session:
            from app.db.models import AiActionRecord
            row = await session.get(AiActionRecord, action_id)
            return row.expires_at, row.created_at
    first = _propose(t)
    expires, made = _run(expiry(first))
    rows["platform"] = round((expires - made).total_seconds() / 60)
    _save(t, approval_entry_minutes=45)
    expires, made = _run(expiry(_propose(t)))
    rows["his"] = round((expires - made).total_seconds() / 60)
    assert rows == {"platform": app_config.APPROVAL_ENTRY_TTL_MINUTES, "his": 45}


def test_no_price_move_limit_set_places_at_the_price_then(monkeypatch):
    t = _tenant(_email("m68a-nodrift"))
    _flag_for(t["tenant_id"])
    try:
        first = _propose(t, entry=100.0)
        _shift_expiry(first)
        _price(monkeypatch, 101.5)                                          # +1.5 %: no limit of his own, so placed
        body = client.post(f"/api/ai/actions/{first}/approve", headers=t["headers"], json={}).json()
        assert body["status"] == "EXECUTED", body
    finally:
        _flag_for(t["tenant_id"], on=False)


def test_his_limit_with_no_new_prices_set_offers_none_and_places_nothing(monkeypatch):
    t = _tenant(_email("m68a-drift"))
    _flag_for(t["tenant_id"])
    try:
        _save(t, requote_drift_pct=0.5)
        first = _propose(t, entry=100.0)
        _shift_expiry(first)
        _price(monkeypatch, 101.0)
        body = client.post(f"/api/ai/actions/{first}/approve", headers=t["headers"], json={}).json()
        assert body["status"] == "EXPIRED" and automation.needs_setting("max_requotes") in body["result"]
        assert len(_actions(t["tenant_id"], "ENTRY")) == 1 and _orders(t["tenant_id"]) == []
        _save(t, max_requotes=1)                                            # his own number: one new price is offered
        second = _propose(t, entry=100.0)
        _shift_expiry(second)
        body = client.post(f"/api/ai/actions/{second}/approve", headers=t["headers"], json={}).json()
        assert body["status"] == "EXPIRED" and "Re-quoted as proposal #" in body["result"]
    finally:
        _flag_for(t["tenant_id"], on=False)


# --- the scanner's Auto PAPER -----------------------------------------------------------------------------------------

def test_the_scanner_limits_not_set_make_a_proposal_and_arming_asks():
    from app.db.models import ExecutionIntentRecord
    from app.screener.then import auto_paper
    t = _tenant(_email("m68a-scan"))
    intent = ExecutionIntentRecord(id=10**9, tenant_id=t["tenant_id"], user_id=t["user_id"], source="scan",
                                   source_ref="rule:1:symbol:TCS", symbol="TCS")

    async def reason():
        async with _session_factory() as session:
            return await auto_paper.limit_reason(session, intent, OPEN_NOW)
    text = _run(reason())
    assert text.startswith(automation.needs_setting("scan_auto_max")) and text.endswith("a proposal instead")
    _scan_flag(t["tenant_id"])
    try:
        _save(t, scan_auto_max=2, scan_auto_cooldown_minutes=0, scan_auto_arming_days=3)
    finally:
        _scan_flag(t["tenant_id"], on=False)
    assert _run(reason()) is None


# --- the trader pack, the migration, the model -------------------------------------------------------------------------

def test_the_trader_pack_no_longer_turns_approval_on_for_everyone():
    from app.platform import trader_pack
    assert approval.FLAG not in trader_pack.PACK and approval.FLAG in trader_pack.excluded()


def test_every_wired_automation_field_is_read_here():
    wired = {f.name for f in ts.FIELDS if f.section == "automation" and f.wired and f.pr == 6}   # M68 PR-3 wires the entry window (exit_settings)
    assert wired == set(KEYS)


def _migration():
    path = BACKEND / "alembic/versions/6b9d1f3a5c27_m68_entry_mode_not_set.py"
    spec = importlib.util.spec_from_file_location("m68_pr6_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_every_stored_value_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.down_revision == "8d2f4a6c0e19" and module.ROLLBACK_SAFE is False
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, symbol VARCHAR(20), "
                             "entry_mode VARCHAR(10) NOT NULL DEFAULT 'AUTO')"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, symbol, entry_mode) VALUES (1, 'A', 'APPROVAL'), (2, 'B', 'AUTO')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = conn.execute(sa.text("SELECT id, entry_mode FROM strategy_deployments ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, "APPROVAL"), (2, "AUTO")]                                # TS-6: kept
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, symbol) VALUES (3, 'C')"))
        assert conn.execute(sa.text("SELECT entry_mode FROM strategy_deployments WHERE id = 3")).scalar() is None
        col = {c["name"]: c for c in sa.inspect(conn).get_columns("strategy_deployments")}["entry_mode"]
        assert col["nullable"] and col["default"] is None
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        rows = conn.execute(sa.text("SELECT id, entry_mode FROM strategy_deployments ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, "APPROVAL"), (2, "AUTO"), (3, "AUTO")]
        col = {c["name"]: c for c in sa.inspect(conn).get_columns("strategy_deployments")}["entry_mode"]
        assert not col["nullable"]


def test_the_model_carries_no_default_for_entry_mode():
    col = StrategyDeploymentRecord.__table__.c["entry_mode"]
    assert col.nullable and col.default is None and col.server_default is None
    assert json.dumps(automation.Automation().__dict__)          # all None: nothing pre-chosen
