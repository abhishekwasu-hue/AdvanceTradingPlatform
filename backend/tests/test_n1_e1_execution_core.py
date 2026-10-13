"""N1 E1 (ADR-0014): the execution core - intent queue, one routing policy, the worker's drain (flag execution_core, off).

An intent becomes a notification (NOTIFY) or a Z3 PAPER entry proposal (ONE_CLICK, and AUTO_PAPER until X2), or is
refused with its reason (AUTO_LIVE always, bad levels, owner gone, paused). Nothing on this path places an order or
reaches a broker: the guard at the bottom walks the Z10-c call graph."""
import ast
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.core import config as app_config
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import AiActionRecord, ExecutionIntentRecord, NotificationRecord, OrderRecord, Tenant, User
from app.execution import approval
from app.execution.core import dispatcher, intents, policy
from app.platform import controls, phase0_lock
from tests.order_path_graph import CallGraph
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import CLOSED_NOW, _FakeBroker, _tenant, _worker
from tests.test_z10c_order_paths import GATED
from tests.test_z3_approval_entries import _price

UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]
CORE = BACKEND / "app" / "execution" / "core"


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n1e1-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _flag(name: str, tenant_id: int, on: bool = True) -> None:
    """A flag for this organisation only (the suite shares one database)."""
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[name] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _org(tag: str, core: bool = True):
    t = _tenant(_email(tag), with_credentials=False)
    if core:
        _flag(intents.FLAG, t["tenant_id"])
    return t


def _sig(entry=100.0, stop=98.0, target=104.0, direction=SignalDirection.LONG, symbol="RELIANCE") -> Signal:
    return Signal(symbol=symbol, strategy_id="scan:demo", strategy_name="Demo scan", direction=direction, timestamp=datetime.now(UTC),
                  entry=entry, stop_loss=stop, target1=target, risk_reward=2.0, score=70, grade=SignalGrade.HIGH_QUALITY,
                  reasons=["test setup"], timeframe_combo="5min")


def _submit(t, mode, *, key=None, signal=None, user_id=None, now=None, source="scan"):
    async def go():
        async with _session_factory() as session:
            out = await intents.submit(session, tenant_id=t["tenant_id"], user_id=user_id or t["user_id"], source=source,
                                       source_ref="rule:1:event:1", idempotency_key=key or uuid.uuid4().hex, mode=mode,
                                       signal=signal or _sig(), evidence={"study": "inside demand"}, now=now)
            await session.commit()
            return out.intent.id, out.created
    return _run(go())


def _drain(now=None):
    async def go():
        async with _session_factory() as session:
            return await dispatcher.drain(session, now or datetime.now(UTC))
    return _run(go())


def _intent(intent_id) -> ExecutionIntentRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(ExecutionIntentRecord, intent_id)
    return _run(go())


def _rows(model, tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(model).where(model.tenant_id == tenant_id).order_by(model.id)))
    return _run(go())


def _set(intent_id, **values):
    async def go():
        async with _session_factory() as session:
            await session.execute(update(ExecutionIntentRecord).where(ExecutionIntentRecord.id == intent_id).values(**values))
            await session.commit()
    _run(go())


# --- the policy (pure) -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("approval_required", [False, True])
@pytest.mark.parametrize("locked", [False, True])
def test_policy_table(approval_required, locked):
    def route(mode, auto=False):
        return policy.decide(policy.PolicyInputs(mode=mode, approval_required=approval_required, phase0_locked=locked,
                                                 auto_paper_enabled=auto))
    assert route("NOTIFY").route == policy.ROUTE_NOTIFY
    assert route("one_click").route == policy.ROUTE_PROPOSE
    assert route("AUTO_PAPER").route == policy.ROUTE_PROPOSE
    # Auto PAPER places only with X2's switch and never under approval-only mode.
    assert route("AUTO_PAPER", auto=True).route == (policy.ROUTE_PROPOSE if approval_required else policy.ROUTE_PAPER)
    live = route("AUTO_LIVE", auto=True)
    assert live.route == policy.ROUTE_REFUSE and live.reason == (policy.WHY_LIVE_LOCKED if locked else policy.WHY_LIVE)
    assert route("SOMETHING").route == policy.ROUTE_REFUSE and route("").route == policy.ROUTE_REFUSE


def test_no_mode_and_no_input_ever_routes_live():
    for mode in policy.MODES + ("LIVE", "AUTO LIVE", "auto_live"):
        for flags in ((a, b, c) for a in (False, True) for b in (False, True) for c in (False, True)):
            decision = policy.decide(policy.PolicyInputs(mode, *flags))
            assert decision.route in policy.ROUTES and "LIVE" not in decision.route


def test_level_problems():
    assert policy.level_problems("LONG", 100, 98, 104, need_stop=True) == []
    assert policy.level_problems("SHORT", 100, 102, 96, need_stop=True) == []
    assert "wrong side" in policy.level_problems("LONG", 100, 101, 104, need_stop=True)[0]
    assert "Target 1" in policy.level_problems("SHORT", 100, 102, 103, need_stop=True)[0]
    assert policy.level_problems("LONG", 100, None, None, need_stop=True) == ["A stop loss is needed"]
    assert policy.level_problems("LONG", 100, None, None, need_stop=False) == []
    assert "above zero" in policy.level_problems("LONG", 0, 98, 104, need_stop=False)[0]
    assert "LONG or SHORT" in policy.level_problems("NO_TRADE", 100, 98, 104, need_stop=False)[0]


def test_flag_and_settings_are_registered_off():
    assert intents.FLAG in controls.FEATURE_FLAGS and intents.FLAG in controls.DEFAULT_OFF_FLAGS
    assert app_config.EXECUTION_INTENT_TTL_SECONDS >= 30 and app_config.EXECUTION_CORE_MAX_ATTEMPTS >= 1


# --- submit ------------------------------------------------------------------------------------------------------------

def test_submit_is_refused_while_the_flag_is_off():
    t = _org("off", core=False)
    with pytest.raises(intents.CoreDisabled):
        _submit(t, "ONE_CLICK")
    assert _rows(ExecutionIntentRecord, t["tenant_id"]) == []


def test_submit_is_idempotent_and_only_for_an_active_member_of_the_organisation():
    t, other = _org("idem"), _org("idem-other")
    first, created = _submit(t, "NOTIFY", key="rule:9:bar:1")
    again, created_again = _submit(t, "ONE_CLICK", key="rule:9:bar:1")
    assert created and not created_again and again == first
    row = _intent(first)
    assert row.idempotency_key == "scan:rule:9:bar:1" and row.status == intents.QUEUED and row.requested_mode == "NOTIFY"
    assert row.direction == "LONG" and json.loads(row.evidence_json) == {"study": "inside demand"}
    assert (row.expires_at.replace(tzinfo=UTC) - row.created_at.replace(tzinfo=UTC)) == timedelta(seconds=app_config.EXECUTION_INTENT_TTL_SECONDS)
    with pytest.raises(intents.IntentRejected):              # BT1b: never for a person of another organisation
        _submit(t, "NOTIFY", user_id=other["user_id"])
    with pytest.raises(intents.IntentRejected):
        _submit(t, "NOTIFY", source="webhook")
    with pytest.raises(intents.IntentRejected):
        _submit(t, "AUTO_EVERYTHING")
    assert len(_rows(ExecutionIntentRecord, t["tenant_id"])) == 1


def test_two_members_with_the_same_setup_key_get_their_own_intents():
    """Review M1 (BT1b): an organisation-wide rule fires for two members with the same key - two rows, never shared."""
    t = _org("two-members")

    async def member():
        from app.auth.security import hash_password
        async with _session_factory() as session:
            user = User(tenant_id=t["tenant_id"], email=_email("member"), hashed_password=hash_password("x" * 16), role="USER",
                        is_active=True)
            session.add(user)
            await session.commit()
            return user.id
    second = _run(member())
    mine, created = _submit(t, "ONE_CLICK", key="rule:7:RELIANCE:area:bar")
    theirs, created_theirs = _submit(t, "ONE_CLICK", key="rule:7:RELIANCE:area:bar", user_id=second)
    again, created_again = _submit(t, "ONE_CLICK", key="rule:7:RELIANCE:area:bar", user_id=second)
    assert created and created_theirs and not created_again and again == theirs and mine != theirs
    assert _intent(mine).user_id == t["user_id"] and _intent(theirs).user_id == second


def test_a_long_key_is_hashed_never_cut():
    t = _org("long-key")
    stem = "x" * 250
    first, _ = _submit(t, "NOTIFY", key=stem + "A")
    second, created = _submit(t, "NOTIFY", key=stem + "B")
    assert created and first != second
    key = _intent(first).idempotency_key
    assert key.startswith("scan:sha256:") and len(key) <= 200
    assert _submit(t, "NOTIFY", key=stem + "A") == (first, False)


# --- the drain ---------------------------------------------------------------------------------------------------------

def test_notify_intent_becomes_one_notification_for_its_owner_and_nothing_else():
    t = _org("notify")
    intent_id, _ = _submit(t, "NOTIFY", signal=_sig(stop=None, target=None))     # a notification needs no stop
    report = _drain()
    assert report.claimed >= 1
    row = _intent(intent_id)
    assert row.status == intents.DONE and row.route == policy.ROUTE_NOTIFY and row.notification_id is not None and row.attempts == 1
    notes = [n for n in _rows(NotificationRecord, t["tenant_id"]) if n.id == row.notification_id]
    assert len(notes) == 1 and notes[0].user_id == t["user_id"] and notes[0].event_type == "SCREEN_ALERT"
    assert "Notify only: nothing is proposed or placed" in notes[0].message
    assert json.loads(notes[0].metadata_json)["execution_intent_id"] == intent_id
    assert _rows(AiActionRecord, t["tenant_id"]) == [] and _rows(OrderRecord, t["tenant_id"]) == []
    _drain()                                                                     # a second pass changes nothing
    assert len([n for n in _rows(NotificationRecord, t["tenant_id"]) if n.event_type == "SCREEN_ALERT"]) == 1


def test_one_click_becomes_a_paper_entry_proposal_and_approve_places_one_paper_order(monkeypatch):
    t = _org("oneclick")
    intent_id, _ = _submit(t, "ONE_CLICK")
    _drain()
    row = _intent(intent_id)
    assert row.status == intents.DONE and row.route == policy.ROUTE_PROPOSE and row.ai_action_id is not None
    [action] = _rows(AiActionRecord, t["tenant_id"])
    assert action.id == row.ai_action_id and action.action == "ENTRY" and action.status == "PROPOSED"
    assert action.idempotency_key == f"intent:{intent_id}" and action.owner_user_id == t["user_id"]
    evidence = json.loads(action.evidence_json)
    assert evidence["source"] == "scan" and evidence["mode"] == "PAPER" and "Scan setup" in action.reason
    assert _rows(OrderRecord, t["tenant_id"]) == []                              # nothing until a person approves
    # The existing Z3 approval path: re-quoted, one PAPER order, never a broker order.
    _price(monkeypatch, 100.1)
    out = client.post(f"/api/ai/actions/{action.id}/approve", headers=t["headers"], json={})
    assert out.status_code == 200 and out.json()["status"] == "EXECUTED", out.text
    orders = _rows(OrderRecord, t["tenant_id"])
    assert [o.idempotency_key for o in orders] == [f"approval:{action.id}"] and orders[0].mode == "PAPER"


@pytest.mark.parametrize("approval_only", [False, True])
def test_auto_paper_becomes_a_proposal_until_x2(approval_only):
    t = _org(f"auto-{approval_only}")
    if approval_only:
        _flag(approval.FLAG, t["tenant_id"])
    intent_id, _ = _submit(t, "AUTO_PAPER")
    _drain()
    row = _intent(intent_id)
    assert row.status == intents.DONE and row.route == policy.ROUTE_PROPOSE
    assert row.reason.startswith(policy.WHY_AUTO_APPROVAL if approval_only else policy.WHY_AUTO_OFF)
    assert len(_rows(AiActionRecord, t["tenant_id"])) == 1 and _rows(OrderRecord, t["tenant_id"]) == []


def test_auto_live_is_always_refused_and_names_the_phase0_lock_when_it_is_on():
    t = _org("live")
    plain, _ = _submit(t, "AUTO_LIVE")
    _drain()
    _flag(phase0_lock.FLAG, t["tenant_id"])
    try:
        locked, _ = _submit(t, "AUTO_LIVE")
        _drain()
    finally:
        _flag(phase0_lock.FLAG, t["tenant_id"], on=False)
        phase0_lock.reset_cache()
    assert _intent(plain).status == intents.REFUSED and _intent(plain).reason == policy.WHY_LIVE
    assert _intent(locked).status == intents.REFUSED and _intent(locked).reason == policy.WHY_LIVE_LOCKED
    assert _rows(AiActionRecord, t["tenant_id"]) == [] and _rows(OrderRecord, t["tenant_id"]) == []


def test_bad_levels_are_refused_not_proposed():
    t = _org("levels")
    wrong, _ = _submit(t, "ONE_CLICK", signal=_sig(stop=101.0))
    no_stop, _ = _submit(t, "ONE_CLICK", signal=_sig(stop=None))
    _drain()
    assert _intent(wrong).status == intents.REFUSED and "wrong side" in _intent(wrong).reason
    assert _intent(no_stop).status == intents.REFUSED and _intent(no_stop).reason == "A stop loss is needed"
    assert _rows(AiActionRecord, t["tenant_id"]) == []


def test_an_intent_nobody_dispatched_in_time_expires_and_raises_nothing():
    t = _org("expire")
    old = datetime.now(UTC) - timedelta(seconds=app_config.EXECUTION_INTENT_TTL_SECONDS + 5)
    intent_id, _ = _submit(t, "ONE_CLICK", now=old)
    _drain()
    row = _intent(intent_id)
    assert row.status == intents.EXPIRED and row.attempts == 0 and _rows(AiActionRecord, t["tenant_id"]) == []


def test_owner_who_left_or_paused_is_refused_with_the_reason(monkeypatch):
    t = _org("owner")
    gone, _ = _submit(t, "ONE_CLICK")

    async def deactivate(active):
        async with _session_factory() as session:
            await session.execute(update(User).where(User.id == t["user_id"]).values(is_active=active))
            await session.commit()
    _run(deactivate(False))
    try:
        _drain()
    finally:
        _run(deactivate(True))
    assert _intent(gone).status == intents.REFUSED and _intent(gone).reason == dispatcher.WHY_OWNER_GONE

    async def paused(*args, **kwargs):
        raise approval.EntriesPaused("PAPER entries paused by you (/pause)")
    monkeypatch.setattr(approval, "propose_entry", paused)
    held, _ = _submit(t, "ONE_CLICK")
    _drain()
    assert _intent(held).status == intents.REFUSED and "paused" in _intent(held).reason
    assert _rows(AiActionRecord, t["tenant_id"]) == []


def test_one_failing_intent_fails_alone_and_the_next_runs(monkeypatch):
    t = _org("fail")
    real = approval.propose_entry

    async def flaky(session, tenant_id, **kwargs):
        if kwargs["signal"].symbol == "BROKEN":
            raise RuntimeError("boom")
        return await real(session, tenant_id, **kwargs)
    monkeypatch.setattr(approval, "propose_entry", flaky)
    bad, _ = _submit(t, "ONE_CLICK", signal=_sig(symbol="BROKEN"))
    good, _ = _submit(t, "ONE_CLICK")
    _drain()
    assert _intent(bad).status == intents.FAILED and "RuntimeError" in _intent(bad).reason
    assert _intent(good).status == intents.DONE and len(_rows(AiActionRecord, t["tenant_id"])) == 1


def test_an_interrupted_claim_is_retried_and_finds_its_proposal_instead_of_raising_a_second():
    t = _org("retry")
    intent_id, _ = _submit(t, "ONE_CLICK")
    now = datetime.now(UTC)

    async def crash_after_propose():
        async with _session_factory() as session:
            [row] = [r for r in await intents.claim(session, frozenset({t["tenant_id"]}), now, 10) if r.id == intent_id]
            return (await approval.propose_entry(session, t["tenant_id"], signal=_sig(), strategy_id="scan:demo", source="scan",
                                                 idempotency_key=f"intent:{row.id}", user_id=t["user_id"], now=now)).id
    action_id = _run(crash_after_propose())                     # the worker died before recording the outcome
    assert _intent(intent_id).status == intents.CLAIMED
    _drain(now + timedelta(seconds=app_config.EXECUTION_CORE_CLAIM_STALE_SECONDS - 5))
    assert _intent(intent_id).status == intents.CLAIMED          # not stale yet
    _drain(now + timedelta(seconds=app_config.EXECUTION_CORE_CLAIM_STALE_SECONDS + 1))
    row = _intent(intent_id)
    assert row.status == intents.DONE and row.ai_action_id == action_id and row.attempts == 2
    assert len(_rows(AiActionRecord, t["tenant_id"])) == 1


def _crashed_proposal(t, intent_id, now):
    async def go():
        async with _session_factory() as session:
            [row] = [r for r in await intents.claim(session, frozenset({t["tenant_id"]}), now, 10) if r.id == intent_id]
            return (await approval.propose_entry(session, t["tenant_id"], signal=_sig(), strategy_id="scan:demo", source="scan",
                                                 idempotency_key=f"intent:{row.id}", user_id=t["user_id"], now=now)).id
    return _run(go())


def test_a_retried_intent_past_its_window_links_its_proposal_instead_of_expiring():
    """Review L1: the stale sweep re-queued it, then its window passed - the proposal it raised is linked, not lost."""
    t = _org("retry-expire")
    now = datetime.now(UTC)
    intent_id, _ = _submit(t, "ONE_CLICK", now=now)
    action_id = _crashed_proposal(t, intent_id, now)
    _set(intent_id, status=intents.QUEUED, claimed_at=None, expires_at=now - timedelta(seconds=1))
    _drain(now)
    row = _intent(intent_id)
    assert row.status == intents.DONE and row.ai_action_id == action_id and "before an interruption" in row.reason


def test_a_claim_interrupted_too_often_links_its_proposal_if_one_was_raised():
    t = _org("stale-linked")
    now = datetime.now(UTC)
    intent_id, _ = _submit(t, "ONE_CLICK", now=now)
    action_id = _crashed_proposal(t, intent_id, now)
    _set(intent_id, claimed_at=now - timedelta(seconds=app_config.EXECUTION_CORE_CLAIM_STALE_SECONDS + 1),
         attempts=app_config.EXECUTION_CORE_MAX_ATTEMPTS)
    _drain(now)
    row = _intent(intent_id)
    assert row.status == intents.DONE and row.ai_action_id == action_id


def test_the_paper_route_places_nothing_without_an_armed_scan_template(monkeypatch):
    """Review L2 (E1), kept by N1 X2: even if the policy ever answered PAPER, the dispatcher places nothing unless the
    intent's own scan template has all three Auto PAPER switches on (tests/test_n1_x2_auto_paper.py covers that)."""
    t = _org("paper-route")
    monkeypatch.setattr(policy, "decide", lambda inputs: policy.Decision(policy.ROUTE_PAPER, "Auto PAPER"))
    intent_id, _ = _submit(t, "AUTO_PAPER")
    _drain()
    row = _intent(intent_id)
    assert row.status == intents.REFUSED and row.reason == dispatcher.WHY_PAPER_NOT_SCAN
    assert _rows(OrderRecord, t["tenant_id"]) == [] and _rows(AiActionRecord, t["tenant_id"]) == []


def test_a_suspended_organisation_is_refused():
    """Review L3."""
    t = _org("suspended")
    intent_id, _ = _submit(t, "ONE_CLICK")

    async def status(value):
        async with _session_factory() as session:
            await session.execute(update(Tenant).where(Tenant.id == t["tenant_id"]).values(status=value))
            await session.commit()
    _run(status("suspended"))
    try:
        _drain()
    finally:
        _run(status("active"))
    row = _intent(intent_id)
    assert row.status == intents.REFUSED and "suspended" in row.reason and _rows(AiActionRecord, t["tenant_id"]) == []


def test_the_drain_stops_claiming_at_its_deadline_and_limit():
    """Review L4: nothing is claimed once the time budget is spent; the limit caps one pass."""
    t = _org("deadline")
    _drain()                                   # other tests' leftovers first, so the limit below counts only these
    ids = [_submit(t, "NOTIFY")[0] for _ in range(3)]

    async def go(**kwargs):
        async with _session_factory() as session:
            return await dispatcher.drain(session, datetime.now(UTC), **kwargs)
    assert _run(go(deadline_seconds=0)).claimed == 0
    assert {_intent(i).status for i in ids} == {intents.QUEUED}
    _run(go(limit=1))
    assert sorted(_intent(i).status for i in ids).count(intents.QUEUED) == 2


def test_intents_are_never_deleted_by_retention():
    """Review L5: the core's decision trail is kept like orders."""
    from app.retention.policy import NEVER_DELETED
    assert "execution_intents" in NEVER_DELETED


def test_a_claim_interrupted_too_often_fails():
    t = _org("stale")
    intent_id, _ = _submit(t, "ONE_CLICK")
    long_ago = datetime.now(UTC) - timedelta(seconds=app_config.EXECUTION_CORE_CLAIM_STALE_SECONDS + 1)
    _set(intent_id, status=intents.CLAIMED, claimed_at=long_ago, attempts=app_config.EXECUTION_CORE_MAX_ATTEMPTS)
    _drain()
    row = _intent(intent_id)
    assert row.status == intents.FAILED and "Interrupted" in row.reason and _rows(AiActionRecord, t["tenant_id"]) == []


def test_only_organisations_with_the_flag_are_drained():
    t, off = _org("scope"), _org("scope-off")
    queued, _ = _submit(off, "ONE_CLICK")
    _flag(intents.FLAG, off["tenant_id"], on=False)              # switched off after queuing
    mine, _ = _submit(t, "ONE_CLICK")
    _drain()
    assert _intent(mine).status == intents.DONE
    assert _intent(queued).status == intents.QUEUED and _rows(AiActionRecord, off["tenant_id"]) == []

    async def finish_twice():                                    # a row is finished once (conditional on CLAIMED)
        async with _session_factory() as session:
            return await intents.finish(session, mine, intents.FAILED, route=None, reason="late", now=datetime.now(UTC))
    assert _run(finish_twice()) is False and _intent(mine).status == intents.DONE


def test_the_worker_cycle_drains_the_queue_market_open_or_not(monkeypatch):
    t = _org("worker")
    intent_id, _ = _submit(t, "NOTIFY", now=CLOSED_NOW.astimezone(UTC))
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.0))
    report = _run(worker.run_cycle(now=CLOSED_NOW))
    assert not [e for e in report.errors if "execution intents" in e]
    assert _intent(intent_id).status == intents.DONE


# --- the guard: nothing in the core can reach a broker order ------------------------------------------------------------

FORBIDDEN = {"place_order", "modify_order", "place_stop_loss_order", "exit_position", "execute_signal_for_user",
             "execute_structure", "OrderRouter", "get_broker_adapter", "build_adapter", "close_position"}


def test_the_core_names_no_broker_and_no_order_pipeline():
    """The core imports no broker and no order path. The one exception (N1 X2): the dispatcher's `_paper` calls the
    PAPER pipeline `execute_signal_for_user` - once, with mode "PAPER" and never a broker (a GATED callee)."""
    pipeline_calls = []
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                allowed = path.name == "dispatcher.py" and modules == ["app.execution.signal_execution"] \
                    and [a.name for a in node.names] == ["execute_signal_for_user"]
                assert allowed or not any(m.startswith(("app.brokers", "app.trading", "app.execution.router", "app.execution.signal_execution",
                                                        "app.execution.multileg")) for m in modules), (path.name, modules)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "execute_signal_for_user":
                pipeline_calls.append((path.name, node))
                continue
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if name == "execute_signal_for_user" and path.name == "dispatcher.py":
                continue
            assert name not in FORBIDDEN, (path.name, name)
    assert len(pipeline_calls) == 1 and pipeline_calls[0][0] == "dispatcher.py"
    call = pipeline_calls[0][1]
    keywords = {k.arg: k.value for k in call.keywords}
    # Review of X2 (L2): no **kwargs (a keyword with no name) and no *args could smuggle a broker in.
    assert all(k.arg is not None for k in call.keywords) and not any(isinstance(a, ast.Starred) for a in call.args)
    assert "broker" not in keywords and "quote_broker" not in keywords and len(call.args) == 2      # (session, owner) only
    assert isinstance(keywords["mode"], ast.Constant) and keywords["mode"].value == "PAPER"


def test_no_function_of_the_core_reaches_a_broker_order_method():
    """Z10-c's graph: the core is not an order path, so a producer - an API route included - can write intents
    without any route reaching a broker. The worker's drain is the only caller."""
    graph = CallGraph(gated=GATED)
    core = {name for name in graph.functions if name.startswith("app.execution.core.")}
    assert "app.execution.core.dispatcher.drain" in core and "app.execution.core.intents.submit" in core
    assert not core & graph.reaching()


def test_a_sql_error_in_one_intent_is_undone_in_its_savepoint_and_the_next_intent_runs(monkeypatch):
    """ADR-0004 / savepoints: an intent whose dispatch hits a SQL error (a failed flush) fails alone - its savepoint is
    rolled back, never the whole shared session - and the next intent in the same batch is dispatched."""
    from datetime import date
    from app.db.models import MarketHolidayRecord
    t = _org("sqlfail")
    real = approval.propose_entry

    async def flaky(session, tenant_id, **kwargs):
        if kwargs["signal"].symbol == "SQLBROKEN":
            session.add_all([MarketHolidayRecord(exchange="ZZE1", holiday_date=date(2031, 7, 1), description="dup"),
                             MarketHolidayRecord(exchange="ZZE1", holiday_date=date(2031, 7, 1), description="dup")])
            await session.flush()
        return await real(session, tenant_id, **kwargs)
    monkeypatch.setattr(approval, "propose_entry", flaky)
    bad, _ = _submit(t, "ONE_CLICK", signal=_sig(symbol="SQLBROKEN"))
    good, _ = _submit(t, "ONE_CLICK")

    async def go():
        async with _session_factory() as session:
            report = await dispatcher.drain(session, datetime.now(UTC))
            still = (await session.execute(select(Tenant.id).where(Tenant.id == t["tenant_id"]))).scalar()   # session usable
            return report, still
    report, still = _run(go())
    assert still == t["tenant_id"] and report.failed >= 1 and report.done >= 1
    assert _intent(bad).status == intents.FAILED and "IntegrityError" in _intent(bad).reason
    assert _intent(good).status == intents.DONE and len(_rows(AiActionRecord, t["tenant_id"])) == 1

    async def dup_rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(MarketHolidayRecord).where(MarketHolidayRecord.exchange == "ZZE1")))
    assert _run(dup_rows()) == []
