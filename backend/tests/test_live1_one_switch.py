"""LIVE-1 (owner order, binding): ONE live switch.

`live_trading` is the single button. OFF (the production default): PAPER only - no new LIVE entry reaches a broker
from any path, with a plain sentence. ON: PAPER and LIVE on every order path, each user picks his own mode (PAPER stays
the default); the user's PAPER choice stays paper. Exits always go out; the AI never places (ADR-0004 / ADR-0006).

The broker is a spy throughout (tests/test_trading_worker.py's fake broker). The static part pins that no second LIVE
switch comes back: the stored `phase0_paper_only` entry and the flag's organisation list decide nothing, nothing reads
`live_trading` with the per-organisation allow-list semantics, and no per-feature "paper only" refusal remains.
"""
import ast
import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from app.auth.scopes import has_scope
from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.core.enums import OrderSide, UserRole
from app.db.models import Tenant, User
from app.execution.tagging import LEG_EXIT
from app.plans import limits
from app.platform import controls, live_owner, live_switch, phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _store_broker
from tests.test_trading_worker import (
    BAR_TS,
    LATE_NOW,
    OPEN_NOW,
    STRATEGY,
    _deploy,
    _FakeBroker,
    _force_signal,
    _signal,
    _tenant,
    _trades,
    _worker,
)
from tests.test_z10c_phase0_lock import _Inner, _StopStandingBroker

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")

APP = Path(__file__).resolve().parents[1] / "app"


def _run(coro):
    return asyncio.run(coro)


def _store(entries: dict) -> None:
    """Stores flag entries the way Admin -> Controls does (the owner checks are covered by test_m55_s1_owner_live.py)."""
    async def go():
        async with _session_factory() as session:
            flags = dict((await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {})
            flags.update(entries)
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _clear() -> None:
    async def go():
        async with _session_factory() as session:
            flags = dict((await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {})
            for name in ("live_trading", "phase0_paper_only", "strategy_templates"):
                flags.pop(name, None)
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())
    phase0_lock.reset_cache()


def _live(on: bool) -> None:
    _store({"live_trading": {"on": on, "tenants": []}})


@pytest.fixture(autouse=True)
def _defaults():
    _clear()
    yield
    _clear()


def _allowed(tenant_id: int | None) -> bool:
    async def go():
        async with _session_factory() as session:
            return await live_switch.live_allowed(session, tenant_id)
    return _run(go())


# --- the one decision -------------------------------------------------------------------------------------------------

def test_off_by_default_and_on_only_by_the_switch():
    assert "live_trading" in controls.DEFAULT_OFF_FLAGS                  # production default unchanged: OFF
    assert _allowed(1) is False
    _live(True)
    assert _allowed(1) is True and _allowed(None) is True
    _live(False)
    assert _allowed(1) is False


def test_the_former_extra_gates_decide_nothing():
    """A stored phase0_paper_only (either way) and the organisation list (the former grant) never change the answer."""
    _store({"live_trading": {"on": False, "tenants": [1, 2]}, "phase0_paper_only": {"on": False, "tenants": []}})
    assert _allowed(1) is False
    _store({"live_trading": {"on": True, "tenants": []}, "phase0_paper_only": {"on": True, "tenants": [1]}})
    assert _allowed(1) is True

    async def view():
        async with _session_factory() as session:
            return await controls.features_for_tenant(session, 1)
    features = _run(view())
    assert features["live_trading"] is True and features["phase0_paper_only"] is False
    _store({"live_trading": {"on": False, "tenants": [1]}})
    features = _run(view())
    assert features["live_trading"] is False and features["phase0_paper_only"] is True   # the list grants nothing


def test_the_refusal_is_one_plain_sentence():
    async def go():
        async with _session_factory() as session:
            return await live_switch.live_refusal(session, 1)
    assert _run(go()) == live_switch.LIVE_OFF
    assert "phase0" not in live_switch.LIVE_OFF and "flag" not in live_switch.LIVE_OFF
    _live(True)
    assert _run(go()) is None


def test_every_user_may_pick_live_the_switch_decides():
    for role in (UserRole.USER, UserRole.OWNER):
        assert has_scope(User(id=1, tenant_id=1, email="x@example.com", hashed_password="x", role=role.value), "trading:live")
    assert not has_scope(User(id=1, tenant_id=1, email="x@example.com", hashed_password="x", role=UserRole.VIEWER.value),
                         "trading:live")


def test_the_plan_check_follows_the_switch():
    t = _tenant("live1-plan@example.com")

    async def go():
        async with _session_factory() as session:
            return await limits.live_allowed_for(session, await session.get(Tenant, t["tenant_id"]))
    assert _run(go()) is False
    _live(True)
    assert _run(go()) is True

    async def org_open():
        async with _session_factory() as session:
            return await live_owner.org_live_open(session, t["tenant_id"])
    assert _run(org_open()) is True                                       # the name the F&O ticket (#1002) reads


# --- live_trading OFF: no LIVE entry reaches a broker from any path ------------------------------------------------------

def test_off_the_broker_chokepoint_refuses_an_entry_and_passes_an_exit():
    async def go():
        async with _session_factory() as session:
            await live_switch.live_allowed(session, 7)                  # the fresh read every entry path makes first
        inner = _Inner()
        broker = RateLimitedBroker(inner, RateBudget(RateLimits(100.0, 100, 1000)), tenant_id=7)  # type: ignore[arg-type]
        entry = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=1,
                                   intent=ORDER_INTENT_ENTRY)
        exit_order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.SELL, quantity=1)
        return (await broker.place_order(entry)).status, (await broker.place_order(exit_order)).status, inner.calls
    entry, exit_status, calls = _run(go())
    assert entry == "REJECTED" and exit_status == "OPEN" and len(calls) == 1      # only the exit reached the broker
    _live(True)
    entry, _, calls = _run(go())
    assert entry == "OPEN" and len(calls) == 2


def test_off_entry_blocks_say_the_sentence_for_live_only():
    async def go(mode: str):
        async with _session_factory() as session:
            user = User(id=0, tenant_id=1, email="x@example.com", hashed_password="x", role="OWNER")
            return await controls.entry_blocks(session, user=user, mode=mode, broker_name="upstox")
    assert phase0_lock.ENTRY_REASON in _run(go("LIVE")) and phase0_lock.ENTRY_REASON not in _run(go("PAPER"))
    _live(True)
    assert phase0_lock.ENTRY_REASON not in _run(go("LIVE"))


def test_off_a_live_deployment_cannot_be_created_with_the_sentence():
    t = _tenant("live1-route-off@example.com")
    res = client.post("/api/deployments", headers=t["headers"],
                      json={"strategy_id": STRATEGY, "symbol": "reliance", "mode": "LIVE", "broker_name": "upstox"})
    assert res.status_code == 503 and res.json()["detail"] == live_switch.LIVE_OFF
    assert client.post("/api/deployments", headers=t["headers"], json={"strategy_id": STRATEGY, "symbol": "tcs"}).status_code == 201


def test_off_the_worker_sends_no_live_entry(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("live1-worker-off@example.com")
    _deploy(t, mode="LIVE")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert broker.placed == [] and _trades(t["tenant_id"]) == []


def test_off_an_order_pipeline_handed_a_broker_still_refuses_a_live_entry(monkeypatch):
    """Belt and braces: even a caller that hands the shared pipeline a broker gets no LIVE entry while the switch is off."""
    from app.execution.signal_execution import execute_signal_for_user
    t = _tenant("live1-pipeline-off@example.com")
    broker = _FakeBroker(ltp=101.0)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_signal_for_user(session, user, mode="LIVE", strategy_id=STRATEGY, signal=_signal(),
                                                 broker=broker)
    result, _order = _run(go())
    assert broker.placed == [] and result.executed is False
    assert any(live_switch.LIVE_OFF in r or phase0_lock.ENTRY_REASON in r for r in result.reasons), result.reasons


# --- live_trading ON: the user's choice ------------------------------------------------------------------------------

def test_on_a_live_bot_reaches_the_broker_once_with_his_values_and_exits_go_out(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    monkeypatch.setattr("app.trading.position_monitor.FILL_POLL_DELAY_SECONDS", 0)
    _live(True)
    t = _tenant("live1-worker-on@example.com")
    _deploy(t, mode="LIVE")
    broker = _StopStandingBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    trades = _trades(t["tenant_id"])
    entries = [o for o in broker.placed if o.intent == ORDER_INTENT_ENTRY]
    assert len(trades) == 1 and trades[0].mode == "LIVE" and len(entries) == 1
    assert entries[0].symbol == "RELIANCE" and entries[0].transaction_type == OrderSide.BUY
    # The owner switches LIVE off again: the open LIVE position is still squared off at the broker (ADR-0004).
    _live(False)
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(hours=4, minutes=46)))
    report = _run(worker.run_cycle(now=LATE_NOW))
    assert report.positions_closed == 1 and _trades(t["tenant_id"])[0].exit_reason == "Intraday square-off"
    assert broker.placed[-1].tag is not None and broker.placed[-1].tag.endswith(LEG_EXIT)
    assert len([o for o in broker.placed if o.intent == ORDER_INTENT_ENTRY]) == 1       # no new entry


def test_on_a_paper_bot_stays_paper(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _live(True)
    t = _tenant("live1-paper-on@example.com")
    _deploy(t, mode="PAPER")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1 and trades[0].mode == "PAPER" and broker.placed == []


def test_on_a_live_deployment_and_a_live_template_are_created_paper_stays_the_default():
    _live(True)
    t = _tenant("live1-route-on@example.com")
    _store_broker(t["headers"], token_status="VALID")
    live = client.post("/api/deployments", headers=t["headers"],
                       json={"strategy_id": STRATEGY, "symbol": "reliance", "mode": "LIVE", "broker_name": "upstox"})
    assert live.status_code == 201, live.text
    assert live.json()["mode"] == "LIVE"
    # The v2 bot form names no broker: his one stored broker is the one LIVE goes through.
    unnamed = client.post("/api/deployments", headers=t["headers"], json={"strategy_id": STRATEGY, "symbol": "hdfcbank", "mode": "LIVE"})
    assert unnamed.status_code == 201, unnamed.text
    assert unnamed.json()["mode"] == "LIVE" and unnamed.json()["broker_name"] == "upstox"
    paper = client.post("/api/deployments", headers=t["headers"], json={"strategy_id": STRATEGY, "symbol": "tcs"})
    assert paper.status_code == 201 and paper.json()["mode"] == "PAPER"              # nothing sent = PAPER
    # A strategy template runs LIVE like any strategy now (the former "paper only for now" refusal is folded).
    _store({"strategy_templates": {"on": True, "tenants": []}})
    pre = client.post("/api/strategy-templates/D2/adopt", headers=t["headers"], json={"timeframe": "15m"}).json()["deployment"]
    assert pre["mode"] == "PAPER"                                                       # the pre-fill stays PAPER
    tpl = client.post("/api/deployments", headers=t["headers"],
                      json={**pre, "symbol": "infy", "instrument_kind": "UNDERLYING", "mode": "LIVE", "broker_name": "upstox"})
    assert tpl.status_code == 201, tpl.text
    assert tpl.json()["mode"] == "LIVE" and tpl.json()["strategy_id"] == pre["strategy_id"]
    _live(False)
    again = client.post("/api/deployments", headers=t["headers"],
                        json={**pre, "symbol": "wipro", "instrument_kind": "UNDERLYING", "mode": "LIVE", "broker_name": "upstox"})
    assert again.status_code == 503 and again.json()["detail"] == live_switch.LIVE_OFF


# --- static: no second switch comes back ------------------------------------------------------------------------------

def _sources():
    for path in sorted(APP.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


# Where the name of the former lock may still be written: the flag registry, the derived value and the modules that
# document / report it. Nowhere else may code read it - the one switch decides.
PAPER_ONLY_NAME_ALLOWED = {
    "platform/controls.py", "platform/phase0_lock.py", "platform/live_owner.py", "platform/deployment_phase.py",
    "platform/flag_catalog.py", "platform/flag_state.py", "platform/trader_pack.py", "observability/routes.py",
}


def test_static_the_former_lock_name_is_read_nowhere_else():
    offenders = []
    for path, tree in _sources():
        rel = path.relative_to(APP).as_posix()
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and node.value == "phase0_paper_only" and id(node) not in docstrings
                    and rel not in PAPER_ONLY_NAME_ALLOWED):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, "phase0_paper_only is read outside the one switch (use app/platform/live_switch.py): " + ", ".join(offenders)


def test_static_live_trading_is_never_read_with_the_allow_list_semantics():
    """`require_flag` / `flag_enabled` answer ON for an allow-listed organisation; the one switch is the global `on` only."""
    offenders = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name in ("require_flag", "flag_enabled") and any(
                        isinstance(a, ast.Constant) and a.value == "live_trading" for a in node.args):
                    offenders.append(f"{path.relative_to(APP).as_posix()}:{node.lineno}")
    assert not offenders, "live_trading read with the allow-list semantics: " + ", ".join(offenders)


def test_static_no_per_feature_paper_only_refusal_remains():
    banned = ("paper only for now", "run on paper only", "plan is paper trading only", "plan does not include live trading")
    offenders = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                offenders += [f"{path.relative_to(APP).as_posix()}:{node.lineno}: {b}" for b in banned if b in low]
    assert not offenders, "a per-feature LIVE refusal is back (LIVE-1: the one switch decides): " + "; ".join(offenders)


def test_static_the_ai_never_asks_for_a_live_order():
    """ADR-0006: no module of the AI package names the LIVE mode in an order call or reads the live switch."""
    offenders = []
    for path in sorted((APP / "ai").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "mode" and (
                    (isinstance(node.value, ast.Constant) and node.value.value == "LIVE")
                    or (isinstance(node.value, ast.Attribute) and node.value.attr == "LIVE")):
                offenders.append(f"{path.relative_to(APP).as_posix()}:{node.value.lineno}")
            if isinstance(node, (ast.Import, ast.ImportFrom)) and "live_switch" in ast.unparse(node):
                offenders.append(f"{path.relative_to(APP).as_posix()}:{node.lineno} imports live_switch")
    assert not offenders, "the AI path names a LIVE order: " + ", ".join(offenders)
