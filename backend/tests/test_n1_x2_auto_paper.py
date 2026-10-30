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

from app.db.models import AiActionRecord, AuditLogRecord, ExecutionIntentRecord, NotificationRecord, OrderRecord, ScanThenTemplateRecord, TradeRecord, User
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
    """The X1 runner raises the intent at the commitment candle's close; the worker's drain dispatches it a minute later.
    (Other tests' templates may raise theirs in the same run; only this organisation's intent is read.)"""
    assert _runner().submitted >= 1
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


def test_a_live_price_that_is_not_a_number_is_a_proposal_that_says_so(monkeypatch):
    """UI2-3 review: a NaN quote is "not usable", not "the price moved since the setup"."""
    t, _ = _armed_setup("price-nan", monkeypatch)
    _price(monkeypatch, float("nan"))
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and row.status == intents.DONE and _trades(t) == []
    assert "is not usable" in (row.reason or "") and "moved" not in (row.reason or "")


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

    async def seed(n, symbol, minutes_ago, status="DONE"):
        """Auto PAPER orders placed from this template's rule (key intent:<id>, with a position). The intents' own status
        does not matter (review of X2, M1): a retried or expired intent still counts by its order."""
        async with _session_factory() as session:
            for i in range(n):
                at = NOW - timedelta(minutes=minutes_ago)
                intent = ExecutionIntentRecord(
                    tenant_id=t["tenant_id"], user_id=t["user_id"], source="scan", source_ref=f"rule:{rule_id}:symbol:{symbol}",
                    idempotency_key=f"scan:seed:{uuid.uuid4().hex}", requested_mode="AUTO_PAPER", route=None, status=status,
                    symbol=symbol, exchange="NSE", direction="LONG", strategy_id="seed", signal_json="{}", evidence_json="{}",
                    expires_at=NOW + timedelta(minutes=5), created_at=at, finished_at=at)
                session.add(intent)
                trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="seed",
                                    direction="LONG", entry_time=at, entry_price=100.0, quantity=1.0, stop_loss=99.0)
                session.add(trade)
                await session.flush()
                session.add(OrderRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id="seed", symbol=symbol,
                                        direction="LONG", quantity=1.0, status="FILLED", idempotency_key=f"intent:{intent.id}",
                                        trade_id=trade.id, created_at=at))
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
    _run(seed(1, "OTHER", 60, status="EXPIRED"))                           # an expired intent's order still counts
    _run(seed(1, "OTHER", 50, status="FAILED"))                            # 3 today from this template: the limit
    assert "limit reached: 3 entries" in _run(reason())


    async def counts():
        async with _session_factory() as session:
            return (await AP.entries_today_by_rule(session, t["tenant_id"], NOW),
                    await AP.entries_today(session, t["tenant_id"], rule_id, NOW))
    assert _run(counts()) == ({rule_id: 3}, 3)                             # re-check of X2: the batch read counts the same
    row = _go(t)                                                           # the dispatcher makes it a proposal
    assert row.route == policy.ROUTE_PROPOSE and "limit reached" in row.reason
    assert len([x for x in _trades(t) if x.strategy_id != "seed"]) == 0


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
    # Re-check of X2: the batch read (GET /alerts/then) reads the organisation's switches once, today's counts in one query.
    calls = []
    real = AP.switches

    async def counted(session, tenant_id):
        calls.append(tenant_id)
        return await real(session, tenant_id)

    async def per_rule(*a, **k):
        raise AssertionError("GET /alerts/then counted one rule at a time")
    monkeypatch.setattr(AP, "switches", counted)
    with monkeypatch.context() as m:
        m.setattr(AP, "entries_today", per_rule)
        batch = client.get("/api/alerts/then", headers=t["headers"]).json()["templates"][str(rule_id)]["auto_paper"]
    assert calls == [t["tenant_id"]] and batch == client.get(url, headers=t["headers"]).json()["auto_paper"]
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


# --- review of X2 ----------------------------------------------------------------------------------------------------------------

def _reset_to_claimed(intent_id, attempts=2, status=intents.CLAIMED):
    async def go():
        async with _session_factory() as session:
            await session.execute(update(ExecutionIntentRecord).where(ExecutionIntentRecord.id == intent_id)
                                  .values(status=status, attempts=attempts, route=None, finished_at=None, reason=None,
                                          claimed_at=NOW.astimezone(UTC), expires_at=(NOW + timedelta(minutes=5)).astimezone(UTC)))
            await session.commit()
    _run(go())


def test_a_retried_intent_whose_order_was_placed_never_raises_a_proposal(monkeypatch):
    """H1: crash after the order was placed (the row was never finished), the owner disarms, the worker retries: the
    order is linked (DONE, route PAPER) - no proposal for a second entry, exactly one order."""
    t, rule_id = _armed_setup("h1", monkeypatch)
    row = _go(t)
    assert row.route == policy.ROUTE_PAPER and len(_rows(OrderRecord, t["tenant_id"])) == 1
    _reset_to_claimed(row.id)

    async def disarm_and_retry():
        async with _session_factory() as session:
            tpl = await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
            T.set_arm(tpl, None, None)
            await session.commit()
            fresh = await session.get(ExecutionIntentRecord, row.id, populate_existing=True)
            return await dispatcher.dispatch_one(session, fresh, NOW + timedelta(minutes=2))
    assert _run(disarm_and_retry()) == intents.DONE
    again = _intent_of(t)
    assert again.route == policy.ROUTE_PAPER and "placed before an interruption" in again.reason
    assert _rows(AiActionRecord, t["tenant_id"]) == [] and len(_rows(OrderRecord, t["tenant_id"])) == 1


def test_the_expiry_and_the_stale_sweep_link_an_order_already_placed(monkeypatch):
    """M1: an interrupted Auto PAPER intent whose order exists is DONE (route PAPER), never EXPIRED or FAILED."""
    from app.core import config as app_config
    t, _ = _armed_setup("m1", monkeypatch)
    row = _go(t)
    _reset_to_claimed(row.id, attempts=1, status=intents.QUEUED)

    async def expire():
        async with _session_factory() as session:
            return await intents.expire_overdue(session, None, NOW + timedelta(minutes=10))
    _run(expire())
    assert _intent_of(t).status == intents.DONE and _intent_of(t).route == policy.ROUTE_PAPER
    _reset_to_claimed(row.id, attempts=app_config.EXECUTION_CORE_MAX_ATTEMPTS)

    async def sweep():
        async with _session_factory() as session:
            return await intents.requeue_stale(session, None, NOW + timedelta(hours=1))
    _run(sweep())
    assert _intent_of(t).status == intents.DONE and "placed before an interruption" in _intent_of(t).reason


def test_the_entry_window(monkeypatch):
    """M2: Auto PAPER places only while NSE is open and before the last entry time (15:00 on NSE, the session calendar);
    otherwise a proposal."""
    from app.core import config as app_config
    from app.market_data.calendar import IST

    def at(h, m, day=NOW.astimezone(IST).date()):
        return datetime(day.year, day.month, day.day, h, m, tzinfo=IST).astimezone(UTC)

    async def why(now):
        async with _session_factory() as session:
            return await AP.window_reason(session, now)
    assert _run(why(at(10, 0))) is None
    assert "last entry time (15:00" in _run(why(at(15, 16)))
    assert "last entry time (15:00" in _run(why(at(15, 0)))
    assert _run(why(at(15, 45))) == AP.WHY_CLOSED.format(venue="NSE")
    saturday = NOW.astimezone(IST).date() + timedelta(days=(5 - NOW.astimezone(IST).weekday()) % 7)
    assert _run(why(at(11, 0, saturday))) == AP.WHY_CLOSED.format(venue="NSE")

    async def venue(exchange):
        async with _session_factory() as session:
            return await AP.window_reason(session, at(10, 0), exchange)
    assert "Phase 0" in _run(venue("MCX")) and _run(venue("NFO")) is None and _run(venue("BSE")) is None
    monkeypatch.setattr(app_config, "SCAN_AUTO_PAPER_LAST_ENTRY", "09:30")        # earlier, configured: the setup is late
    assert "09:30" in _run(why(at(10, 0)))
    monkeypatch.setattr(app_config, "SCAN_AUTO_PAPER_LAST_ENTRY", "bad")          # malformed never widens it
    assert _run(why(at(10, 0))) is None and _run(why(at(15, 16))) is not None
    monkeypatch.setattr(app_config, "SCAN_AUTO_PAPER_LAST_ENTRY", "09:30")
    t, _ = _armed_setup("late", monkeypatch)
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and "last entry time" in row.reason and _trades(t) == []


def test_an_arming_expires_and_a_switch_change_voids_it(monkeypatch):
    """L1: valid only if made after the operator last changed the switch, and for SCAN_AUTO_PAPER_ARM_DAYS days."""
    from app.audit.log import write_audit_log
    t, rule_id = _armed_setup("arm-age", monkeypatch, arm=False)
    _arm(rule_id, raw={"at": (NOW - timedelta(days=8)).isoformat(), "by": t["user_id"]})

    async def state(now):
        async with _session_factory() as session:
            tpl = await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
            return await AP.arm_state(session, tpl, now)
    armed, why = _run(state(NOW))
    assert not armed and "expired after 7 days" in why
    assert _run(state(NOW - timedelta(days=2)))[0] is True                  # 6 days old then: still valid
    _arm(rule_id)                                                           # armed now

    async def switch_changed():
        async with _session_factory() as session:
            await write_audit_log(session, None, t["user_id"], "feature_flag_set", f"{AP.FLAG}: on=True tenants=-")
            await session.commit()
    assert _run(state(datetime.now(UTC)))[0] is True
    _run(switch_changed())
    armed, why = _run(state(datetime.now(UTC)))
    assert not armed and "predates the platform switch" in why
    row = _go(t)
    assert row.route == policy.ROUTE_PROPOSE and _trades(t) == []


def test_an_owner_who_may_not_trade_is_refused(monkeypatch):
    """L1: the role is asked again right before placing (the runner raised the intent while the owner could trade)."""
    t, _ = _armed_setup("cantrade", monkeypatch)

    async def demote():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            user.role = "VIEWER"
            await session.commit()
    assert _runner().submitted >= 1
    _run(demote())
    _drain()
    row = _intent_of(t)
    assert row.status == intents.REFUSED and "may not trade" in row.reason and _trades(t) == []



# --- re-review of X2 (LOWs) ------------------------------------------------------------------------------------------------------

def _order(t, user_id, key, status="POSITION_OPEN", trade=True, mode="PAPER"):
    async def go():
        async with _session_factory() as session:
            trade_id = None
            if trade:
                row = TradeRecord(tenant_id=t["tenant_id"], user_id=user_id, mode="PAPER", symbol="GATEDEMO", strategy_id="seed",
                                  direction="LONG", entry_time=NOW.astimezone(UTC), entry_price=ENTRY, quantity=1.0, stop_loss=CASE["out"]["sl"])
                session.add(row)
                await session.flush()
                trade_id = row.id
            order = OrderRecord(tenant_id=t["tenant_id"], user_id=user_id, mode=mode, strategy_id="seed", symbol="GATEDEMO", direction="LONG",
                                quantity=1.0, status=status, idempotency_key=key, trade_id=trade_id)
            session.add(order)
            await session.commit()
            return order.id
    return _run(go())


def _next_intent_id():
    async def go():
        async with _session_factory() as session:
            from sqlalchemy import func
            return int(await session.scalar(select(func.max(ExecutionIntentRecord.id))) or 0) + 1
    return _run(go())


def test_another_member_cannot_pre_claim_an_intent_s_order(monkeypatch):
    """L1: an order under the key text `intent:<id>` that is not the owner's own PAPER order is never linked, and the
    console route refuses a caller-supplied `intent:` key."""
    from tests.test_z10c_tenancy_fixes import _member
    t, _ = _armed_setup("preclaim", monkeypatch)
    member_headers = _member(_email("preclaim-m"), t["tenant_id"], "USER")
    me = client.get("/api/auth/me", headers=member_headers).json()
    upcoming = _next_intent_id()
    _order(t, me["id"], f"intent:{upcoming}")                       # the member's own order under the reserved key text
    row = _go(t)
    # Never linked, never replayed: the intent is refused (the key is taken) and A has no order under B's.
    assert row.id == upcoming and row.status == intents.REFUSED and "used by another order" in row.reason
    assert {o.user_id for o in _rows(OrderRecord, t["tenant_id"])} == {me["id"]}
    assert all(x.user_id != t["user_id"] for x in _trades(t))

    async def outcome():
        async with _session_factory() as session:
            return await intents.earlier_outcome(session, t["tenant_id"], upcoming, NOW)
    assert _run(outcome()) is None                                     # the member's order is not the intent's
    r = client.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=member_headers,
                    json={"symbol": "RELIANCE", "candles": [], "idempotency_key": f"intent:{upcoming + 1}"})
    assert r.status_code == 422 and "reserved" in r.text


def test_an_order_in_flight_in_another_attempt_is_left_to_it(monkeypatch):
    """L2: the stale-requeue overlap - the other attempt's order is still being filled: this attempt neither refuses
    the intent nor places a second order."""
    t, _ = _armed_setup("inflight", monkeypatch)
    assert _runner().submitted >= 1
    intent = _intent_of(t)
    _order(t, t["user_id"], f"intent:{intent.id}", status="VALIDATING", trade=False)
    _drain()
    row = _intent_of(t)
    assert row.status == intents.CLAIMED and row.route is None and len(_rows(OrderRecord, t["tenant_id"])) == 1


def test_a_retry_reports_a_rejected_order_and_announces_a_filled_one_once(monkeypatch):
    """L3: an earlier attempt's order that was rejected makes the intent REFUSED (route PAPER); one that filled is
    linked DONE and, when the crash came before the announcement, the audit row and the notification are written once."""
    t, _ = _armed_setup("rejected", monkeypatch)
    assert _runner().submitted >= 1
    intent = _intent_of(t)
    order_id = _order(t, t["user_id"], f"intent:{intent.id}", status="REJECTED", trade=False)
    _reset_to_claimed(intent.id)

    async def retry():
        async with _session_factory() as session:
            fresh = await session.get(ExecutionIntentRecord, intent.id, populate_existing=True)
            return await dispatcher.dispatch_one(session, fresh, NOW + timedelta(minutes=1))
    assert _run(retry()) == intents.REFUSED
    row = _intent_of(t)
    assert row.route == policy.ROUTE_PAPER and f"#{order_id} was not placed (rejected)" in row.reason
    t2, _ = _armed_setup("filled", monkeypatch)
    assert _runner().submitted >= 1
    intent2 = _intent_of(t2)
    _order(t2, t2["user_id"], f"intent:{intent2.id}")                  # filled, but the crash came before _placed
    _reset_to_claimed(intent2.id)

    async def retry2():
        async with _session_factory() as session:
            fresh = await session.get(ExecutionIntentRecord, intent2.id, populate_existing=True)
            return await dispatcher.dispatch_one(session, fresh, NOW + timedelta(minutes=1))
    assert _run(retry2()) == intents.DONE
    audits = [a for a in _rows(AuditLogRecord, t2["tenant_id"]) if a.event == "scan_auto_paper_entry"]
    notes = [n for n in _rows(NotificationRecord, t2["tenant_id"]) if n.title.startswith("Auto PAPER entry")]
    assert len(audits) == 1 and len(notes) == 1 and f"intent #{intent2.id}" in audits[0].detail
    _reset_to_claimed(intent2.id)
    assert _run(retry2()) == intents.DONE                                # a second retry does not announce again
    assert len([a for a in _rows(AuditLogRecord, t2["tenant_id"]) if a.event == "scan_auto_paper_entry"]) == 1


@pytest.mark.parametrize("swept", [intents.EXPIRED, intents.FAILED])
def test_a_row_swept_while_its_order_filled_becomes_done_and_is_announced_once(monkeypatch, swept):
    """Review of the X2 delta (L1): the expiry / stale sweep settled the row (it saw no order yet) while this attempt's
    pipeline was still filling the order. The filled PAPER order is the entry: the row moves to DONE (route PAPER) and the
    owner is told once - never an open PAPER position under an "Interrupted" intent with no notification."""
    t, _ = _armed_setup(f"swept{swept.lower()}", monkeypatch)
    real = dispatcher._placed

    async def swept_first(session, f, *args, **kwargs):
        await session.execute(update(ExecutionIntentRecord).where(ExecutionIntentRecord.id == f.intent_id)
                              .values(status=swept, reason="Interrupted: nothing was raised"))
        await session.commit()
        return await real(session, f, *args, **kwargs)
    monkeypatch.setattr(dispatcher, "_placed", swept_first)
    row = _go(t)
    assert row.status == intents.DONE and row.route == policy.ROUTE_PAPER and "placed while the row was being swept" in row.reason
    assert len(_rows(OrderRecord, t["tenant_id"])) == 1 and len(_trades(t)) == 1
    audits = [a for a in _rows(AuditLogRecord, t["tenant_id"]) if a.event == "scan_auto_paper_entry"]
    notes = [n for n in _rows(NotificationRecord, t["tenant_id"]) if n.title.startswith("Auto PAPER entry")]
    assert len(audits) == 1 and len(notes) == 1 and f"intent #{row.id}" in audits[0].detail


def test_a_retry_s_done_and_its_announcement_commit_together(monkeypatch):
    """Review of the X2 delta (L2): the retry path's DONE and the announcement are one commit - a failure while
    announcing leaves the row CLAIMED (retried later), never DONE without the owner told."""
    from app.execution.core import announce
    t, _ = _armed_setup("onecommit", monkeypatch)
    assert _runner().submitted >= 1
    intent = _intent_of(t)
    _order(t, t["user_id"], f"intent:{intent.id}")                      # filled, but the crash came before _placed
    _reset_to_claimed(intent.id)

    async def boom(*args, **kwargs):
        raise RuntimeError("notification store down")
    monkeypatch.setattr(announce, "placed_once", boom)

    async def retry():
        async with _session_factory() as session:
            fresh = await session.get(ExecutionIntentRecord, intent.id, populate_existing=True)
            try:
                await dispatcher.dispatch_one(session, fresh, NOW + timedelta(minutes=1))
            except RuntimeError:
                await session.rollback()
    _run(retry())
    assert _intent_of(t).status == intents.CLAIMED
