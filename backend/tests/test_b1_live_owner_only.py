"""B1 (owner decision 14 Nov, LIVE-1 amended): LIVE orders through ATP's server are for the owner's organisation only.

`live_trading` stays the ONE switch. ON opens LIVE for the organisation of the owner account (`LIVE_OWNER_USER_ID`, an
active platform administrator - `live_owner.is_owner`); every other organisation is refused with one plain sentence on
every entry path (the F&O ticket, the basket, Reverse, deployments, the trading worker, the shared order pipeline and the
broker-order chokepoint) and keeps PAPER. Exits are never blocked (ADR-0004): an organisation that is not the owner's and
still holds a LIVE position exits / squares off through the server.

The brokers are spies throughout; nothing real is sent.
"""
import asyncio
from datetime import timedelta

import pytest

from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.core import config
from app.core.enums import OrderSide, UserRole
from app.db.models import StrategyDeploymentRecord, User
from app.execution.tagging import LEG_EXIT
from app.platform import controls, live_switch, phase0_lock
from app.terminal import status as terminal_status
from tests.live_switch_helpers import make_owner_org
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _store_broker
from tests.test_fno1_chain_ticket import LOT, _body, _org_tenant, _place, _preview, _price
from tests.test_live2_basket_reverse import (
    _legs,
    _paper_position,
    _post,
    desk,  # noqa: F401 - the basket / Reverse fixture
)
from tests.test_live2_basket_reverse import _switch as _switch_both
from tests.test_m96_ticket_live import (
    _orders,
    _ref,
    _trade,
    setup,  # noqa: F401 - the ticket fixture
)
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

NOT_OWNER = live_switch.NOT_OWNER_ORG


def _run(coro):
    return asyncio.run(coro)


def _live(on: bool) -> None:
    _switch_both(on)            # live_trading on / off (the lock follows it); the organisation list stays empty
    phase0_lock.reset_cache()


@pytest.fixture(autouse=True)
def _restore():
    yield
    from tests.test_m55_s1_owner_live import _set_stored
    _set_stored(None)
    phase0_lock.reset_cache()


def _allowed(tenant_id):
    async def go():
        async with _session_factory() as session:
            return await live_switch.live_allowed(session, tenant_id), await live_switch.live_refusal(session, tenant_id)
    return _run(go())


# --- the one decision ---------------------------------------------------------------------------------------------------

def test_the_sentence_is_the_owners_words():
    assert NOT_OWNER == "Live orders from ATP are for the platform owner only. Use Trade in your broker app."
    assert phase0_lock.NOT_OWNER_REASON == NOT_OWNER


def test_on_only_the_owners_organisation_off_nobody(monkeypatch):
    owner = _tenant("b1-owner-org@example.com")["tenant_id"]
    other = _tenant("b1-other-org@example.com")["tenant_id"]
    make_owner_org(monkeypatch, owner)
    assert _allowed(owner) == (False, live_switch.LIVE_OFF) and _allowed(other) == (False, live_switch.LIVE_OFF)
    _live(True)
    assert _allowed(owner) == (True, None)
    assert _allowed(other) == (False, NOT_OWNER)
    assert _allowed(None) == (False, NOT_OWNER)                    # no organisation known = not the owner's
    _live(False)
    assert _allowed(owner) == (False, live_switch.LIVE_OFF)


def test_the_owner_is_resolved_like_is_owner(monkeypatch):
    """The owner account must be an active platform administrator; otherwise no organisation gets server LIVE."""
    owner = _tenant("b1-resolve@example.com")["tenant_id"]
    owner_id = make_owner_org(monkeypatch, owner)
    _live(True)
    assert _allowed(owner)[0] is True

    async def set_user(**fields):
        async with _session_factory() as session:
            user = await session.get(User, owner_id)
            for key, value in fields.items():
                setattr(user, key, value)
            await session.commit()
    _run(set_user(role=UserRole.USER.value))
    assert _allowed(owner) == (False, NOT_OWNER)
    _run(set_user(role=UserRole.SUPER_ADMIN.value, is_active=False))
    assert _allowed(owner) == (False, NOT_OWNER)
    _run(set_user(is_active=True))
    assert _allowed(owner)[0] is True
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", 2**31 - 1)          # an id with no account
    assert _allowed(owner) == (False, NOT_OWNER)


def test_the_users_view_and_the_terminal_line_follow_the_organisation(monkeypatch):
    owner = _tenant("b1-view-owner@example.com")
    other = _tenant("b1-view-other@example.com")
    make_owner_org(monkeypatch, owner["tenant_id"])
    _live(True)

    async def view(t):
        async with _session_factory() as session:
            features = await controls.features_for_tenant(session, t["tenant_id"])
            mode = await terminal_status._mode(session, await session.get(User, t["user_id"]))
            return features["live_trading"], features["phase0_paper_only"], mode
    assert _run(view(owner)) == (True, False, {"mode": "PAPER", "live_allowed": True, "message": terminal_status.LIVE_ON_MESSAGE})
    assert _run(view(other)) == (False, True, {"mode": "PAPER", "live_allowed": False, "message": terminal_status.NOT_OWNER_MESSAGE})


def test_the_admin_switch_says_own_organisation_only():
    from app.platform.flag_catalog import info_for
    assert "Live through ATP: your own organisation only" in info_for("live_trading")["plain"]
    assert "Live through ATP: your own organisation only" in controls.FEATURE_FLAGS["live_trading"]


# --- every entry path refuses another organisation while the switch is ON -----------------------------------------------

def test_chokepoint_refuses_another_organisations_entry_and_passes_its_exit(monkeypatch):
    owner = _tenant("b1-choke-owner@example.com")["tenant_id"]
    other = _tenant("b1-choke-other@example.com")["tenant_id"]
    make_owner_org(monkeypatch, owner)
    _live(True)

    async def go(tenant_id):
        async with _session_factory() as session:
            await live_switch.live_allowed(session, tenant_id)            # the fresh read every entry path makes first
        inner = _Inner()
        broker = RateLimitedBroker(inner, RateBudget(RateLimits(100.0, 100, 1000)), tenant_id=tenant_id)  # type: ignore[arg-type]
        entry = await broker.place_order(BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY,
                                                            quantity=1, intent=ORDER_INTENT_ENTRY))
        exit_order = await broker.place_order(BrokerOrderRequest(symbol="RELIANCE", exchange="NSE",
                                                                 transaction_type=OrderSide.SELL, quantity=1))
        return entry, exit_order.status, inner.calls
    entry, exit_status, calls = _run(go(other))
    assert entry.status == "REJECTED" and NOT_OWNER in entry.message
    assert exit_status == "OPEN" and len(calls) == 1                       # only the exit reached the broker
    entry, exit_status, calls = _run(go(owner))
    assert entry.status == "OPEN" and exit_status == "OPEN" and len(calls) == 2


def test_the_shared_pipeline_and_entry_blocks_refuse_another_organisation(monkeypatch):
    """The single-leg pipeline; the multi-leg executor and incomplete structures share `entry_refusals` with it."""
    from app.execution.signal_execution import entry_refusals, execute_signal_for_user
    owner = _tenant("b1-pipe-owner@example.com")
    other = _tenant("b1-pipe-other@example.com")
    make_owner_org(monkeypatch, owner["tenant_id"])
    _live(True)
    broker = _FakeBroker(ltp=101.0)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, other["user_id"])
            from app.db.models import Tenant
            tenant = await session.get(Tenant, other["tenant_id"])
            reasons = await entry_refusals(session, tenant, other["tenant_id"], "LIVE", STRATEGY, user=user, broker_name="upstox")
            paper = await entry_refusals(session, tenant, other["tenant_id"], "PAPER", STRATEGY, user=user, broker_name="upstox")
            blocks = await controls.entry_blocks(session, user=user, mode="LIVE", broker_name="upstox")
            result, _order = await execute_signal_for_user(session, user, mode="LIVE", strategy_id=STRATEGY, signal=_signal(),
                                                           broker=broker)
            return reasons, paper, blocks, result
    reasons, paper, blocks, result = _run(go())
    assert NOT_OWNER in reasons and NOT_OWNER not in paper and NOT_OWNER in blocks
    assert reasons.count(NOT_OWNER) == 1                                   # one sentence, not two
    assert result.executed is False and broker.placed == []


def test_deployments_another_organisation_is_refused_the_owners_is_created(monkeypatch):
    owner = _tenant("b1-dep-owner@example.com")
    other = _tenant("b1-dep-other@example.com")
    make_owner_org(monkeypatch, owner["tenant_id"])
    _live(True)
    for t in (owner, other):
        _store_broker(t["headers"], token_status="VALID")
    body = {"strategy_id": STRATEGY, "symbol": "reliance", "mode": "LIVE", "broker_name": "upstox"}
    refused = client.post("/api/deployments", headers=other["headers"], json=body)
    assert refused.status_code == 503 and refused.json()["detail"] == NOT_OWNER
    paper = client.post("/api/deployments", headers=other["headers"], json={"strategy_id": STRATEGY, "symbol": "tcs"})
    assert paper.status_code == 201 and paper.json()["mode"] == "PAPER"        # PAPER unchanged
    assert client.post("/api/deployments", headers=owner["headers"], json=body).status_code == 201


def test_worker_sends_no_live_entry_for_another_organisation_paper_still_trades(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    owner = _tenant("b1-worker-owner@example.com")
    make_owner_org(monkeypatch, owner["tenant_id"])
    _live(True)
    other = _tenant("b1-worker-other@example.com")
    _deploy(other, mode="LIVE")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert broker.placed == [] and _trades(other["tenant_id"]) == []

    async def last_error():
        from sqlalchemy import select
        async with _session_factory() as session:
            return [d.last_error for d in await session.scalars(select(StrategyDeploymentRecord).where(
                StrategyDeploymentRecord.tenant_id == other["tenant_id"]))]
    assert _run(last_error()) == [phase0_lock.WORKER_NOT_OWNER]
    paper = _tenant("b1-worker-paper@example.com")
    _deploy(paper, mode="PAPER")
    _run(worker.run_cycle(now=OPEN_NOW))
    trades = _trades(paper["tenant_id"])
    assert len(trades) == 1 and trades[0].mode == "PAPER" and broker.placed == []


def test_ticket_another_organisation_is_refused_and_no_broker_is_built(setup, monkeypatch):  # noqa: F811
    t, expiry, strike, _, spy = setup
    _live(True)
    make_owner_org(monkeypatch, _org_tenant("b1-ticket-owner")["tenant_id"])   # the owner is now another organisation
    view = _preview(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    assert view["mode"] == "LIVE" and not view["can_place"] and NOT_OWNER in view["refusals"]
    placed = _place(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    assert placed["status"] == "REFUSED" and placed["order_id"] is None and NOT_OWNER in placed["reasons"]
    assert spy.built == 0 and spy.placed == [] and _orders(t["tenant_id"]) == []
    assert client.get("/api/paper/fno/live-status", headers=t["headers"]).json() == {"open": False, "text": NOT_OWNER}
    paper = _place(t, _body(expiry, strike, mode="PAPER", client_ref=_ref()))
    assert paper["status"] == "PLACED" and paper["mode"] == "PAPER" and spy.placed == []      # PAPER unchanged


def test_basket_and_reverse_another_organisation_is_refused(desk, monkeypatch):  # noqa: F811
    t, expiry, base, _, _, spy = desk
    _live(True)
    make_owner_org(monkeypatch, _org_tenant("b1-basket-owner")["tenant_id"])
    view = _post(t, "basket/preview", {"legs": _legs(expiry, base), "product": "MIS", "mode": "LIVE"})
    assert not view["can_execute"] and NOT_OWNER in view["refusals"]
    out = _post(t, "basket/execute", {"legs": _legs(expiry, base), "product": "MIS", "mode": "LIVE", "basket_ref": _ref()})
    assert out["status"] == "REFUSED" and out["placed"] == 0 and out["reasons"] == [NOT_OWNER]
    trade_id = _paper_position(t, expiry, base)
    rev = _post(t, f"positions/{trade_id}/reverse", {"client_ref": _ref(), "stop_loss": 80.0, "mode": "LIVE"})
    assert rev["status"] == "EXITED" and rev["entry"]["status"] == "REFUSED" and rev["entry"]["reasons"] == [NOT_OWNER]
    assert _trade(trade_id).exit_time is not None                       # the exit ran (never blocked)
    assert spy.built == 0 and spy.placed == []


# --- exits are never blocked (ADR-0004) ---------------------------------------------------------------------------------

def test_another_organisations_live_position_still_exits_through_the_ticket(setup, monkeypatch):  # noqa: F811
    """A LIVE position opened while its organisation was the owner's (e.g. before B1); the owner then is another
    organisation: the exit still goes to his broker, the switch ON or OFF."""
    t, expiry, strike, _, spy = setup
    _price(monkeypatch, 60.0)                          # 2 lots fit his own risk per trade (the fixture's)
    spy.ltp = 60.0
    _live(True)
    placed = _place(t, _body(expiry, strike, mode="LIVE", lots=2, client_ref=_ref()))
    assert placed["status"] == "PLACED", placed
    make_owner_org(monkeypatch, _org_tenant("b1-exit-owner")["tenant_id"])
    assert _allowed(t["tenant_id"]) == (False, NOT_OWNER)                    # new entries refused now
    out = client.post(f"/api/paper/fno/positions/{placed['trade_id']}/exit-part", headers=t["headers"],
                      json={"lots": 1, "client_ref": _ref(), "confirm": True})
    assert out.status_code == 200, out.text
    _live(False)
    out = client.post(f"/api/paper/fno/positions/{placed['trade_id']}/exit-part", headers=t["headers"],
                      json={"lots": 1, "client_ref": _ref(), "confirm": True})
    assert out.status_code == 200, out.text
    exits = [o for o in spy.placed if o.intent != ORDER_INTENT_ENTRY]
    assert len(exits) == 2 and all(o.transaction_type == OrderSide.SELL and o.quantity == LOT for o in exits)
    assert _trade(placed["trade_id"]).exit_time is not None


def test_another_organisations_live_bot_position_is_still_squared_off(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    monkeypatch.setattr("app.trading.position_monitor.FILL_POLL_DELAY_SECONDS", 0)
    _live(True)
    t = _tenant("b1-squareoff@example.com")
    make_owner_org(monkeypatch, t["tenant_id"])
    _deploy(t, mode="LIVE")
    broker = _StopStandingBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert len(_trades(t["tenant_id"])) == 1 and _trades(t["tenant_id"])[0].mode == "LIVE"
    # The owner is now another organisation: this one holds a LIVE position it may no longer add to.
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", 2**31 - 1)          # (a new tenant here would stop the bot)
    phase0_lock.reset_cache()
    assert _allowed(t["tenant_id"]) == (False, NOT_OWNER)                    # new entries refused now
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(hours=4, minutes=46)))
    report = _run(worker.run_cycle(now=LATE_NOW))
    assert report.positions_closed == 1 and _trades(t["tenant_id"])[0].exit_reason == "Intraday square-off"
    assert broker.placed[-1].tag is not None and broker.placed[-1].tag.endswith(LEG_EXIT)
    assert len([o for o in broker.placed if o.intent == ORDER_INTENT_ENTRY]) == 1       # no new entry
