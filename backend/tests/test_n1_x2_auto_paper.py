"""N1 X2: Auto PAPER from scans - the execution core's PAPER route (flag scan_auto_paper, off; N1-3 is the owner's
decision).

Nothing places unless all three switches are on: the operator's `scan_auto_paper`, approval-only mode off, and the
template's own owner arming it (a key in `gate_settings_json`; missing or malformed means disarmed). Then the
dispatcher asks approval-only again, the owner's pause / P1-d / F2 caps, the scan limits and a re-quote through the
owner's own price, and places one PAPER order through the pipeline without a broker. Every fallback is a proposal.
The setup run here is the L1 golden live entry (via the X1 runner)."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update

from app.db.models import (AiActionRecord, AuditLogRecord, ExecutionIntentRecord, NotificationRecord, OrderRecord, ScanThenTemplateRecord,
                           TradeRecord, User)
from app.execution import approval
from app.execution.core import dispatcher, intents, policy
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.portfolio.risk_text import has_advice
from app.screener.then import auto_paper as AP
from app.screener.then import templates as T
from tests.order_path_graph import CallGraph, endpoint_node
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_n1_x1_scan_then import CASE, NOW, _drain, _email, _patch_market, _rows, _run, _runner, _setup, _template
from tests.test_z10c_order_paths import GATED
from tests.test_z10c_tenancy_fixes import _member

UTC = timezone.utc
ENTRY = CASE["out"]["entry_price"]
ALL = ("scan_then", "execution_core", AP.FLAG)


def _price(monkeypatch, value=None, error=None, seen=None):
    """The owner's own live price (worker_bridge.price_lookup_for_user, as Approve uses)."""
    from app.ai import worker_bridge

    async def factory(session, user):
        if value is None and error is None:
            return None

        async def lookup(symbol, exchange="NSE"):
            if seen is not None:
                seen.append((user.id, symbol, exchange))
            if error is not None:
                raise error
            return value
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", factory)


def _arm(rule_id, by=None, raw=None):
    async def go():
        async with _session_factory() as session:
            row = await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
            if raw is not None:
                stored = json.loads(row.gate_settings_json or "{}")
                stored[T.ARM_KEY] = raw
                row.gate_settings_json = json.dumps(stored)
            else:
                T.set_arm(row, by if by is not None else row.user_id, datetime.now(UTC).isoformat())
            await session.commit()
    _run(go())


def _armed_setup(tag, monkeypatch, flags=ALL, arm=True, price=ENTRY):
    _patch_market(monkeypatch)
    t, rule_id = _setup(tag, mode="AUTO_PAPER", flags=flags)
    if arm:
        _arm(rule_id)
    _price(monkeypatch, price)
    return t, rule_id


def _intent_of(t):
    [row] = [r for r in _rows(ExecutionIntentRecord, t["tenant_id"]) if r.strategy_id != "seed"]
    return row


def _trades(t):
    return _rows(TradeRecord, t["tenant_id"])


def _go(t):
    """The X1 runner raises the intent at the commitment candle's close; the worker's drain dispatches it a minute later."""
    assert _runner().submitted == 1
    _drain()
    return _intent_of(t)


# --- the three switches -------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default_and_auto_live_is_refused_in_every_case():
    assert AP.FLAG in DEFAULT_OFF_FLAGS
    for approval_required in (False, True):
        for locked in (False, True):
            for auto in (False, True):
                d = policy.decide(policy.PolicyInputs(mode="AUTO_LIVE", approval_required=approval_required, phase0_locked=locked,
                                                      auto_paper_enabled=auto))
                assert d.route == policy.ROUTE_REFUSE
                paper = policy.decide(policy.PolicyInputs(mode="AUTO_PAPER", approval_required=approval_required, phase0_locked=locked,
                                                          auto_paper_enabled=auto))
                assert paper.route == (policy.ROUTE_PAPER if auto and not approval_required else policy.ROUTE_PROPOSE)


def test_with_the_flag_off_an_armed_template_is_a_proposal(monkeypatch):
    t, _ = _armed_setup("flagoff", monkeypatch, flags=("scan_then", "execution_core"))
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and row.status == intents.DONE and _trades(t) == []
    assert len(_rows(AiActionRecord, t["tenant_id"])) == 1


def test_with_approval_only_on_it_is_a_proposal(monkeypatch):
    t, _ = _armed_setup("approval", monkeypatch)
    _flag("approval_only_entries", t["tenant_id"])
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and policy.WHY_AUTO_APPROVAL in row.reason and _trades(t) == []


def test_an_unarmed_template_is_a_proposal(monkeypatch):
    t, _ = _armed_setup("unarmed", monkeypatch, arm=False)
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and _trades(t) == []


def test_all_three_on_places_one_paper_order_without_a_broker(monkeypatch):
    seen = []
    t, rule_id = _armed_setup("placed", monkeypatch)
    _price(monkeypatch, ENTRY + 0.05, seen=seen)
    row = _go(t)
    assert row.status == intents.DONE and row.route == policy.ROUTE_PAPER and "PAPER order #" in row.reason
    [trade] = _trades(t)
    assert trade.mode == "PAPER" and trade.user_id == t["user_id"] and trade.symbol == "GATEDEMO" and trade.direction == "LONG"
    assert trade.stop_loss == pytest.approx(CASE["out"]["sl"]) and trade.broker_order_id is None
    assert seen == [(t["user_id"], "GATEDEMO", "NSE")]                         # re-quoted through the owner's own price
    [order] = _rows(OrderRecord, t["tenant_id"])
    assert order.mode == "PAPER" and order.idempotency_key == f"intent:{row.id}"
    assert _rows(AiActionRecord, t["tenant_id"]) == []                         # no proposal: placed
    note = next(n for n in _rows(NotificationRecord, t["tenant_id"]) if n.title.startswith("Auto PAPER entry"))
    assert "PAPER only" in note.message and "Exit now" in note.message and not has_advice(note.message)
    audit = [a for a in _rows(AuditLogRecord, t["tenant_id"]) if a.event == "scan_auto_paper_entry"]
    assert len(audit) == 1 and f"intent #{row.id}" in audit[0].detail
    # Idempotent per intent: dispatched again (an interrupted worker), nothing new is placed.

    async def again():
        async with _session_factory() as session:
            await session.execute(update(ExecutionIntentRecord).where(ExecutionIntentRecord.id == row.id)
                                  .values(status=intents.CLAIMED, attempts=2))
            await session.commit()
            fresh = await session.get(ExecutionIntentRecord, row.id, populate_existing=True)
            return await dispatcher.dispatch_one(session, fresh, NOW + timedelta(minutes=1))
    _run(again())
    assert len(_trades(t)) == 1 and len(_rows(OrderRecord, t["tenant_id"])) == 1


# --- the fallbacks ---------------------------------------------------------------------------------------------------------------

def test_approval_only_switched_on_between_the_decision_and_the_call_is_a_proposal(monkeypatch):
    t, _ = _armed_setup("race", monkeypatch)
    real = approval.required
    calls = []

    async def flips(session, tenant_id, deployment=None):
        calls.append(tenant_id)
        if tenant_id == t["tenant_id"] and len([c for c in calls if c == t["tenant_id"]]) >= 3:
            return True                                            # on by the time _paper asks again
        return await real(session, tenant_id, deployment)
    monkeypatch.setattr(approval, "required", flips)
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and _trades(t) == [] and len(_rows(AiActionRecord, t["tenant_id"])) == 1


@pytest.mark.parametrize("case", ["no_lookup", "error", "drift", "through_stop"])
def test_no_price_or_a_moved_price_is_a_proposal(monkeypatch, case):
    t, _ = _armed_setup(f"price-{case}", monkeypatch)
    if case == "no_lookup":
        _price(monkeypatch, None)
    elif case == "error":
        _price(monkeypatch, error=RuntimeError("no session"))
    elif case == "drift":
        _price(monkeypatch, ENTRY * 1.03)
    else:
        _price(monkeypatch, CASE["out"]["sl"] - 1.0)
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and row.status == intents.DONE and _trades(t) == []


@pytest.mark.parametrize("case", ["paused", "lockout", "caps"])
def test_pause_lockout_and_caps_refuse_and_place_nothing(monkeypatch, case):
    t, _ = _armed_setup(f"refuse-{case}", monkeypatch)
    if case == "paused":
        from app.paper import service

        async def paused(session, user_id):
            return "Your PAPER entries are paused"
        monkeypatch.setattr(service, "paused_reason", paused)
    elif case == "lockout":
        from app.risk_engine import by_default

        async def locked(session, tenant_id, user_id, now=None):
            return "Daily loss lockout"
        monkeypatch.setattr(by_default, "proposal_refusal", locked)
    else:
        from app.portfolio import caps

        async def capped(session, user, candidate, now=None, **kw):
            return ["Portfolio caps: 3 open positions already (your limit is 3)"]
        monkeypatch.setattr(caps, "entry_refusals", capped)
    row = _go(t)
    assert row.status == intents.REFUSED and _trades(t) == [] and _rows(OrderRecord, t["tenant_id"]) == []


def test_the_daily_limit_and_the_cool_down_make_a_proposal(monkeypatch):
    from app.core import config as app_config
    t, rule_id = _armed_setup("limits", monkeypatch)

    async def seed(n, symbol, minutes_ago):
        async with _session_factory() as session:
            for i in range(n):
                session.add(ExecutionIntentRecord(
                    tenant_id=t["tenant_id"], user_id=t["user_id"], source="scan", source_ref=f"rule:{rule_id}:symbol:{symbol}",
                    idempotency_key=f"scan:seed:{uuid.uuid4().hex}", requested_mode="AUTO_PAPER", route="PAPER", status="DONE",
                    symbol=symbol, exchange="NSE", direction="LONG", strategy_id="seed", signal_json="{}", evidence_json="{}",
                    expires_at=NOW + timedelta(minutes=5), created_at=NOW - timedelta(minutes=minutes_ago),
                    finished_at=NOW - timedelta(minutes=minutes_ago)))
            await session.commit()

    async def reason():
        async with _session_factory() as session:
            probe = ExecutionIntentRecord(id=-1, tenant_id=t["tenant_id"], user_id=t["user_id"], source="scan",
                                          source_ref=f"rule:{rule_id}:symbol:GATEDEMO", symbol="GATEDEMO")
            return await AP.limit_reason(session, probe, NOW)
    assert _run(reason()) is None
    _run(seed(1, "GATEDEMO", 10))                                          # same symbol 10 minutes ago: cool-down
    assert "cool-down" in _run(reason())
    monkeypatch.setattr(app_config, "SCAN_AUTO_PAPER_COOLDOWN_MINUTES", 5)
    assert _run(reason()) is None
    _run(seed(2, "OTHER", 60))                                             # 3 today from this template: the limit
    assert "limit reached: 3 entries" in _run(reason())
    row = _go(t)                                                           # the dispatcher makes it a proposal
    assert row.route == policy.ROUTE_PROPOSE and "limit reached" in row.reason and _trades(t) == []


def test_a_failure_inside_the_checks_fails_the_intent_and_leaves_the_session_usable(monkeypatch):
    """The checks run in a savepoint on the worker's shared session (the Postgres version is test_n1_x2_pg_savepoint)."""
    t, _ = _armed_setup("sqlerr", monkeypatch)
    seen = []

    async def broken(session, intent, now):
        seen.append(session.in_nested_transaction())
        await session.execute(text("SELECT * FROM no_such_table_x2"))
    monkeypatch.setattr(AP, "limit_reason", broken)
    assert _runner().submitted == 1

    async def go():
        async with _session_factory() as session:
            report = await dispatcher.drain(session, NOW + timedelta(minutes=1))
            ok = (await session.execute(text("SELECT 1"))).scalar()
            return report, ok
    report, ok = _run(go())
    row = _intent_of(t)
    assert seen == [True] and ok == 1 and row.status == intents.FAILED and "checks could not run" in row.reason
    assert _trades(t) == []


# --- BT1b ------------------------------------------------------------------------------------------------------------------------

def test_without_the_owner_s_own_session_it_is_a_proposal_under_byob(monkeypatch):
    """The re-quote reads the owner's own entitlement only (worker_bridge.price_lookup_for_user, real): with
    byob_data_tier on and no own session, no tenant credential is used - a proposal instead."""
    _patch_market(monkeypatch)
    t, rule_id = _setup("byob", mode="AUTO_PAPER", flags=ALL + ("byob_data_tier",))
    _arm(rule_id)
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and "live price" in row.reason.lower() and _trades(t) == []


# --- exits (ADR-0004) -----------------------------------------------------------------------------------------------------------

def test_an_auto_paper_position_exits_as_usual_even_with_the_flag_off(monkeypatch):
    from app.trading.position_monitor import close_position
    t, _ = _armed_setup("exit", monkeypatch)
    _go(t)
    [trade] = _trades(t)
    _flag(AP.FLAG, t["tenant_id"], on=False)

    async def stop():
        async with _session_factory() as session:
            return await close_position(session, await session.get(TradeRecord, trade.id), CASE["out"]["sl"], "Stop Loss")
    assert _run(stop()).closed
    t2, _ = _armed_setup("exitnow", monkeypatch)
    _go(t2)
    [trade2] = _trades(t2)
    _flag(AP.FLAG, t2["tenant_id"], on=False)

    async def exit_now():
        async with _session_factory() as session:
            user = await session.get(User, t2["user_id"])
            return await approval.exit_now(session, await session.get(TradeRecord, trade2.id), user)
    [outcome] = _run(exit_now())
    assert outcome.closed


# --- arming ----------------------------------------------------------------------------------------------------------------------

def test_arming_is_the_owner_s_disarmed_is_the_default_and_a_change_disarms():
    def row(mode="AUTO_PAPER", user_id=7, raw=None, gate=None):
        stored = dict(gate or {})
        if raw is not None:
            stored[T.ARM_KEY] = raw
        return ScanThenTemplateRecord(mode=mode, user_id=user_id, gate_settings_json=json.dumps(stored))
    good = {"at": "2026-10-12T10:00:00+00:00", "by": 7}
    assert T.is_armed(row(raw=good))
    for bad in (None, "yes", {"at": "x"}, {"by": 7}, {"at": "", "by": 7}, {"at": "x", "by": 8}, {"at": "x", "by": True}, {"at": 1, "by": 7}, []):
        assert not T.is_armed(row(raw=bad)), bad
    assert not T.is_armed(row(mode="ONE_CLICK", raw=good))                 # another mode: not armed
    assert not T.is_armed(ScanThenTemplateRecord(mode="AUTO_PAPER", user_id=7, gate_settings_json="{not json"))
    armed = row(raw=good, gate={"min_rr": 2.0})
    assert T.gate_overrides(armed) == {"min_rr": 2.0} and T.settings_of(armed)["min_rr"] == 2.0
    T.set_arm(armed, None, None)
    assert not T.is_armed(armed) and T.gate_overrides(armed) == {"min_rr": 2.0}


def test_the_arm_routes(monkeypatch):
    _patch_market(monkeypatch)
    t, rule_id = _setup("routes", mode="ONE_CLICK", flags=("scan_then", "execution_core"))
    url = f"/api/alerts/rules/{rule_id}/then"
    arm = lambda headers, confirm="AUTO_PAPER": client.put(f"{url}/arm", headers=headers, json={"confirm": confirm})  # noqa: E731
    assert arm(t["headers"]).status_code == 409                            # not an Auto PAPER template
    assert client.put(url, headers=t["headers"], json={"mode": "AUTO_PAPER"}).status_code == 200
    off = arm(t["headers"])
    assert off.status_code == 409 and "operator" in off.text                # the platform switch is off
    _flag(AP.FLAG, t["tenant_id"])
    assert arm(t["headers"], "yes").status_code == 422                      # the confirmation
    member = _member(_email("arm-member"), t["tenant_id"], "USER")
    assert arm(member).status_code in (403, 404)                            # not the template's owner
    body = arm(t["headers"]).json()
    assert body["auto_paper"]["armed"] is True and body["auto_paper"]["available"] is True and T.ARM_KEY not in body["gate"]
    got = client.get(url, headers=t["headers"]).json()
    assert got["auto_paper"]["armed"] is True and got["auto_paper"]["max_per_day"] == 3 and got["auto_paper"]["today"] == 0
    _flag("approval_only_entries", t["tenant_id"])
    got = client.get(url, headers=t["headers"]).json()["auto_paper"]
    assert got["available"] is False and "Approval-only" in got["why"]
    _flag("approval_only_entries", t["tenant_id"], on=False)
    # A template PUT (any change) disarms, audited.
    assert client.put(url, headers=t["headers"], json={"mode": "AUTO_PAPER", "gate": {"min_rr": 2.5}}).json()["auto_paper"]["armed"] is False
    assert not T.is_armed(_template(rule_id))
    arm(t["headers"])
    assert T.is_armed(_template(rule_id))
    gone = client.delete(f"{url}/arm", headers=t["headers"]).json()
    assert gone["auto_paper"]["armed"] is False

    async def audit():
        async with _session_factory() as session:
            return [a.event for a in await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == t["tenant_id"]))]
    actions = _run(audit())
    assert actions.count("scan_auto_paper_armed") == 2 and actions.count("scan_auto_paper_disarmed") == 2


def test_the_organisation_owner_disarms_but_never_arms_a_member_s_template():
    """X2-5: the entries go to the template owner's book, so only they arm it; the OWNER may disarm any template."""
    from app.db.models import AlertRuleRecord
    from tests.test_trading_worker import _tenant
    t = _tenant(_email("own"), with_credentials=False)
    for name in ALL:
        _flag(name, t["tenant_id"])
    member = _member(_email("own-member"), t["tenant_id"], "USER")

    async def seed():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.tenant_id == t["tenant_id"], User.id != t["user_id"]))
            rule = AlertRuleRecord(tenant_id=t["tenant_id"], created_by=user.id, name="m", kind="screen", base_tf="5m", status="active",
                                   exchange="NSE")
            session.add(rule)
            await session.flush()
            session.add(ScanThenTemplateRecord(tenant_id=t["tenant_id"], rule_id=rule.id, user_id=user.id, mode="AUTO_PAPER", confirm_tf="5m",
                                               gate_settings_json="{}", enabled=True))
            await session.commit()
            return rule.id
    rule_id = _run(seed())
    url = f"/api/alerts/rules/{rule_id}/then/arm"
    me = client.get("/api/auth/me", headers=t["headers"]).json()
    assert me["role"] == "OWNER"
    assert client.put(url, headers=t["headers"], json={"confirm": "AUTO_PAPER"}).status_code == 403
    assert client.put(url, headers=member, json={"confirm": "AUTO_PAPER"}).json()["auto_paper"]["armed"] is True
    assert client.delete(url, headers=t["headers"]).json()["auto_paper"]["armed"] is False
    assert not T.is_armed(_template(rule_id))


# --- the order-path guard ------------------------------------------------------------------------------------------------------

def test_no_api_route_reaches_the_paper_route_and_the_core_reaches_no_order():
    from app.main import app
    graph = CallGraph(gated=GATED)
    assert "app.execution.core.dispatcher._paper" in graph.functions
    core = {name for name in graph.functions if name.startswith(("app.execution.core.", "app.screener.then."))}
    assert not core & graph.reaching()                                     # the pipeline is called without a broker
    targets = {"app.execution.core.dispatcher._paper", "app.execution.core.dispatcher.drain"}
    reached = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        node = endpoint_node(endpoint)
        if node in graph.functions and graph.reaches_any(node, targets) is not None:
            reached.append(node)
    assert reached == []
    callers = graph.callers.get("app.execution.core.dispatcher.drain", set())
    assert callers and all(c.startswith("app.workers.trading_worker.") for c in callers), callers
