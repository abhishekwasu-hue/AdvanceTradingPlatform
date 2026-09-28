"""Phase T (V3.1-3.5): broker-selection rules - the pure chooser, the deployment/tenant API,
and the worker routing a LIVE entry to the account the policy picks."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.accounts.routing import RoutingPolicy, choose_account, policy_for, utilisation
from app.brokers import token_lifecycle
from app.brokers.models import BrokerPosition, BrokerProfile, MarginInfo
from app.db.models import BrokerAccountRecord, StrategyDeploymentRecord, TradeRecord
from app.workers import trading_worker as tw
from tests.test_accounts import _mark_valid
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker

NOW = datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _acct(id, *, broker="upstox", label="primary", status="ACTIVE", available=None, used=0.0, synced_ago=60, default=False):
    return BrokerAccountRecord(id=id, tenant_id=1, broker_name=broker, account_label=label, status=status, is_default=default,
                               available_balance=available, used_margin=used,
                               last_sync_at=(NOW - timedelta(seconds=synced_ago)) if synced_ago is not None else None)


# --- pure chooser ---------------------------------------------------------------------------------

def test_choose_account_policies():
    a = _acct(1, available=100_000, used=100_000, default=True)          # 50% utilised
    b = _acct(2, label="second", available=250_000, used=50_000)         # 17% utilised, most margin
    c = _acct(3, label="third", available=400_000, status="DISABLED")    # never eligible
    d = _acct(4, label="stale", available=900_000, synced_ago=3600)      # fresh enough? no (15 min limit)
    cands = [a, b, c, d]

    explicit = choose_account(cands, RoutingPolicy.EXPLICIT, now=NOW, explicit=a, default=a)
    assert explicit.account is a and "EXPLICIT" in explicit.note and not explicit.fell_back

    most = choose_account(cands, RoutingPolicy.MOST_MARGIN, now=NOW, default=a)
    assert most.account is b and "MOST_MARGIN" in most.note and "250,000" in most.note   # stale 900k ignored, disabled ignored

    least = choose_account(cands, RoutingPolicy.LEAST_UTILISED, now=NOW, default=a)
    assert least.account is b and "17% margin used" in least.note and round(utilisation(a), 2) == 0.5

    fewest = choose_account(cands, RoutingPolicy.FEWEST_POSITIONS, now=NOW, open_positions={1: 0, 2: 3, 4: 0}, default=a)
    assert fewest.account is d and "0 open" in fewest.note        # ties on 0 broken by the larger balance (d), stale or not

    # No fresh balance anywhere: a capital policy falls back to the default and says so.
    stale_only = [_acct(1, available=1, synced_ago=None, default=True), _acct(2, label="x", available=2, synced_ago=7200)]
    fb = choose_account(stale_only, RoutingPolicy.MOST_MARGIN, now=NOW, default=stale_only[0])
    assert fb.account is stale_only[0] and fb.fell_back and "needs a balance synced within 15 min" in fb.note

    # One active candidate: no comparison needed.
    solo = choose_account([a, c], RoutingPolicy.LEAST_UTILISED, now=NOW)
    assert solo.account is a and "only active candidate" in solo.note
    # Nothing active at all: whatever explicit/default says, else None.
    assert choose_account([c], RoutingPolicy.MOST_MARGIN, now=NOW).account is None
    assert choose_account([c], RoutingPolicy.MOST_MARGIN, now=NOW, default=a).account is a


def test_policy_precedence_and_unknown_values():
    assert policy_for("MOST_MARGIN", "EXPLICIT") == RoutingPolicy.MOST_MARGIN
    assert policy_for(None, "LEAST_UTILISED") == RoutingPolicy.LEAST_UTILISED
    assert policy_for("bogus", "FEWEST_POSITIONS") == RoutingPolicy.FEWEST_POSITIONS
    assert policy_for(None, None) == RoutingPolicy.EXPLICIT


# --- API ------------------------------------------------------------------------------------------

def test_deployment_and_tenant_routing_api():
    headers = _auth("t-routing-api@example.com")
    _store_broker(headers, token_status="VALID")
    bad = _create(headers, symbol="RELIANCE", mode="PAPER", routing_policy="MOST_MARGIN")
    assert bad.status_code == 400 and "LIVE" in bad.json()["detail"]
    bad = _create(headers, symbol="RELIANCE", mode="PAPER", route_across_brokers=True)
    assert bad.status_code == 400
    ok = _create(headers, symbol="RELIANCE", mode="LIVE", broker_name="upstox", routing_policy="LEAST_UTILISED", route_across_brokers=True)
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["routing_policy"] == "LEAST_UTILISED" and body["route_across_brokers"] is True and body["last_route"] is None

    tenant = client.get("/api/team/tenant", headers=headers).json()
    assert tenant["default_routing_policy"] == "EXPLICIT"
    assert client.patch("/api/team/tenant", headers=headers, json={"default_routing_policy": "nope"}).status_code == 400
    updated = client.patch("/api/team/tenant", headers=headers, json={"default_routing_policy": "most_margin"}).json()
    assert updated["default_routing_policy"] == "MOST_MARGIN"


# --- worker ---------------------------------------------------------------------------------------

class _MarginBroker(_FakeBroker):
    """A fake whose funds endpoint answers, so the worker's account refresh has something to store."""
    def __init__(self, available: float, ident: str, funds_down: bool = False):
        super().__init__(ltp=101.0)
        self.available, self.ident, self.funds_down = available, ident, funds_down

    async def get_profile(self):
        return BrokerProfile(broker="upstox", user_id=self.ident)

    async def get_margins(self):
        if self.funds_down:
            raise RuntimeError("funds endpoint unavailable")
        return MarginInfo(available_cash=self.available, used_margin=10_000.0, available_margin=self.available)

    async def get_positions(self):
        return []


def _set_balance(account_id: int, available: float, synced_at):
    async def go():
        async with _session_factory() as session:
            acc = await session.get(BrokerAccountRecord, account_id)
            acc.available_balance, acc.used_margin, acc.last_sync_at = available, 10_000.0, synced_at
            await session.commit()
    _run(go())


def test_worker_routes_a_live_entry_by_most_margin_and_records_the_choice(monkeypatch):
    t = _tenant("t-routing-worker@example.com")
    assert client.post("/api/broker/upstox/credentials?account_label=second", headers=t["headers"],
                       json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    _mark_valid(t["tenant_id"], "second")
    accounts = {a["account_label"]: a for a in client.get("/api/accounts", headers=t["headers"]).json()}
    now = OPEN_NOW.astimezone(timezone.utc)
    # Synced 10 minutes ago: fresh enough to route on (15 min limit), old enough for the
    # worker's 5-minute refresh to pull new numbers through the sessions it holds.
    _set_balance(accounts["primary"]["id"], 50_000.0, now - timedelta(minutes=10))
    _set_balance(accounts["second"]["id"], 300_000.0, now - timedelta(minutes=10))   # the richer account, not the default
    dep_id = _deploy(t, mode="LIVE")

    async def set_policy():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.routing_policy = "MOST_MARGIN"
            await session.commit()
    _run(set_policy())

    primary, secondary = _MarginBroker(50_000.0, "P1"), _MarginBroker(300_000.0, "S2")
    by_label = {"primary": primary, "second": secondary}
    worker = _worker(monkeypatch, primary)
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.trading.position_monitor.build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1, report.errors
    assert [o.order_type for o in secondary.placed] == ["MARKET", "SL-M"] and primary.placed == []
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.last_route.startswith(f"account #{accounts['second']['id']} (upstox/second) by MOST_MARGIN") and "300,000" in dep.last_route
    trade = _trades(t["tenant_id"])[0]
    assert trade.mode == "LIVE" and trade.broker_account_id == accounts["second"]["id"]

    # The refresh pulled fresh balances through the sessions the worker already held.
    refreshed = {a["account_label"]: a for a in client.get("/api/accounts", headers=t["headers"]).json()}
    assert refreshed["primary"]["broker_account_identifier"] == "P1" and refreshed["second"]["broker_account_identifier"] == "S2"
    assert refreshed["primary"]["last_sync_at"] > (now - timedelta(minutes=10)).isoformat()


def test_worker_falls_back_to_default_when_balances_are_stale(monkeypatch):
    t = _tenant("t-routing-stale@example.com")
    assert client.post("/api/broker/upstox/credentials?account_label=second", headers=t["headers"],
                       json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    _mark_valid(t["tenant_id"], "second")
    accounts = {a["account_label"]: a for a in client.get("/api/accounts", headers=t["headers"]).json()}
    stale = OPEN_NOW.astimezone(timezone.utc) - timedelta(hours=2)
    _set_balance(accounts["primary"]["id"], 50_000.0, stale)
    _set_balance(accounts["second"]["id"], 300_000.0, stale)
    dep_id = _deploy(t, mode="LIVE")

    async def set_policy():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.routing_policy = "MOST_MARGIN"
            await session.commit()
    _run(set_policy())

    # Both funds endpoints are down this cycle, so the worker's refresh cannot replace the
    # two-hour-old numbers (the failure is recorded on the account rows instead).
    primary, secondary = _MarginBroker(50_000.0, "P1", funds_down=True), _MarginBroker(300_000.0, "S2", funds_down=True)
    by_label = {"primary": primary, "second": secondary}
    worker = _worker(monkeypatch, primary)
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.trading.position_monitor.build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1, report.errors
    refreshed = {a["account_label"]: a for a in client.get("/api/accounts", headers=t["headers"]).json()}
    assert "funds endpoint unavailable" in (refreshed["second"]["last_sync_error"] or "")
    # Stale numbers never decide: the default (primary) account traded, and the card says why.
    assert [o.order_type for o in primary.placed] == ["MARKET", "SL-M"] and secondary.placed == []
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert "needs a balance synced within 15 min" in dep.last_route and f"account #{accounts['primary']['id']}" in dep.last_route


def test_fewest_positions_counts_open_live_trades_per_account(monkeypatch):
    t = _tenant("t-routing-fewest@example.com")
    assert client.post("/api/broker/upstox/credentials?account_label=second", headers=t["headers"],
                       json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    _mark_valid(t["tenant_id"], "second")
    accounts = {a["account_label"]: a for a in client.get("/api/accounts", headers=t["headers"]).json()}
    dep_id = _deploy(t, mode="LIVE")

    async def seed():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.routing_policy = "FEWEST_POSITIONS"
            # The default (primary) account already carries an open LIVE position from elsewhere.
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id="x", symbol="TCS",
                                    direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=1, stop_loss=90.0,
                                    target1=120.0, target2=130.0, broker_account_id=accounts["primary"]["id"]))
            await session.commit()
    _run(seed())

    primary, secondary = _MarginBroker(50_000.0, "P1"), _MarginBroker(50_000.0, "S2")
    by_label = {"primary": primary, "second": secondary}
    worker = _worker(monkeypatch, primary)
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.trading.position_monitor.build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1, report.errors
    assert [o.order_type for o in secondary.placed] == ["MARKET", "SL-M"]
    # The seeded TCS position had no standing stop: the guard re-armed it in *its* account (primary),
    # which is the only order the primary session saw this cycle.
    assert [(o.order_type, o.symbol) for o in primary.placed] == [("SL-M", "TCS")]
    assert "by FEWEST_POSITIONS: 0 open" in _get(StrategyDeploymentRecord, dep_id).last_route


def test_exits_and_stop_guard_follow_the_trades_own_account(monkeypatch):
    """A position in the second account is closed and stop-guarded through the second session,
    even though the primary account is the default - never through the first session found."""
    t = _tenant("t-routing-exit@example.com")
    assert client.post("/api/broker/upstox/credentials?account_label=second", headers=t["headers"],
                       json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    _mark_valid(t["tenant_id"], "second")
    accounts = {a["account_label"]: a for a in client.get("/api/accounts", headers=t["headers"]).json()}
    _deploy(t, mode="LIVE")   # gives the worker two LIVE sessions to hold

    async def seed():
        async with _session_factory() as session:
            # Target already reached at the fake LTP (101): the monitor closes it this cycle.
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id="x", symbol="TCS",
                                    direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=1, stop_loss=90.0,
                                    target1=100.5, target2=130.0, sl_order_id="SL-OLD", broker_account_id=accounts["second"]["id"]))
            # An untouched position in the second account with no standing stop: the guard re-arms it there.
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id="x", symbol="INFY",
                                    direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=1, stop_loss=90.0,
                                    target1=130.0, target2=140.0, sl_order_id=None, broker_account_id=accounts["second"]["id"]))
            await session.commit()
    _run(seed())

    primary, secondary = _MarginBroker(50_000.0, "P1"), _MarginBroker(50_000.0, "S2")
    by_label = {"primary": primary, "second": secondary}
    worker = _worker(monkeypatch, primary)
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.trading.position_monitor.build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.positions_closed == 1 and report.stops_rearmed == 1, report.errors
    assert primary.placed == []
    kinds = sorted(o.order_type for o in secondary.placed)
    assert kinds == ["MARKET", "SL-M"]          # TCS exit and the INFY re-armed stop, both in the second account
    infy = next(tr for tr in _trades(t["tenant_id"]) if tr.symbol == "INFY")
    assert infy.sl_order_id is not None and infy.exit_time is None
