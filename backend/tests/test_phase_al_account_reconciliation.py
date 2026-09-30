"""Phase AL: reconciliation per broker account (V3.1-3.5 residual) - each account against its own
session, unassigned trades to the broker's default account, the tenant flag settled jointly."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.brokers.models import BrokerPosition
from app.db.models import BrokerAccountRecord, BrokerCredentialRecord, Tenant, TradeRecord
from app.reconciliation.service import reconcile_accounts, run_reconciliation, trades_in_account
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me
from tests.test_safety_gates import _PositionBroker
from tests.test_trading_worker import _FakeBroker, _worker
from app.workers import trading_worker as tw


def _run(coro):
    return asyncio.run(coro)


def _two_accounts(email):
    headers = _auth(email)
    me = _me(headers)
    for label, key in (("primary", "k1"), ("hedge", "k2")):
        assert client.post(f"/api/broker/upstox/credentials?account_label={label}", headers=headers,
                           json={"api_key": key, "api_secret": "s", "access_token": "t"}).status_code == 204
    accounts = client.get("/api/accounts", headers=headers).json()
    by_label = {a["account_label"]: a for a in accounts}

    async def mark():
        async with _session_factory() as session:
            for record in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"])):
                record.token_status = "VALID"
                record.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
                record.last_verified_at = datetime.now(timezone.utc)
            await session.commit()
    _run(mark())
    return headers, me, by_label


def _seed(me, symbol, qty, account_id=None):
    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="LIVE", symbol=symbol, strategy_id="s",
                                    direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=qty,
                                    stop_loss=98.0, target1=104.0, broker_account_id=account_id))
            await session.commit()
    _run(go())


def _tenant_row(tenant_id):
    async def go():
        async with _session_factory() as session:
            return await session.get(Tenant, tenant_id)
    return _run(go())


def test_each_account_is_compared_with_its_own_trades_and_the_flag_settles_jointly():
    headers, me, acct = _two_accounts("al-two@example.com")
    primary, hedge = acct["primary"], acct["hedge"]
    _seed(me, "RELIANCE", 10, primary["id"])
    _seed(me, "TCS", 5, hedge["id"])
    _seed(me, "INFY", 7, None)                     # recorded before accounts were tracked -> default (primary)

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            p = await session.get(BrokerAccountRecord, primary["id"])
            h = await session.get(BrokerAccountRecord, hedge["id"])
            assert sorted(t.symbol for t in await trades_in_account(session, tenant.id, p)) == ["INFY", "RELIANCE"]
            assert sorted(t.symbol for t in await trades_in_account(session, tenant.id, h, include_unassigned=False)) == ["TCS"]
            # Both books agree with their own session: clean, the flag is (or stays) clear.
            tenant.broker_uncertain_since = datetime.now(timezone.utc)
            tenant.broker_uncertain_reason = "order FAILED"
            await session.commit()
            reports = await reconcile_accounts(session, tenant, [
                (p, _PositionBroker([BrokerPosition(symbol="RELIANCE", quantity=10, average_price=100.0), BrokerPosition(symbol="INFY", quantity=7, average_price=100.0)])),
                (h, _PositionBroker([BrokerPosition(symbol="TCS", quantity=5, average_price=100.0)])),
            ], user_id=me["id"], source="test")
            assert [r.account_label for r in reports] == ["primary", "hedge"] and all(r.mismatched_count == 0 for r in reports)
            assert tenant.broker_uncertain_since is None
            # The hedge account loses its position at the broker: only that account is at fault, and
            # the flag names it; the primary account's clean run does not clear it.
            reports = await reconcile_accounts(session, tenant, [
                (p, _PositionBroker([BrokerPosition(symbol="RELIANCE", quantity=10, average_price=100.0), BrokerPosition(symbol="INFY", quantity=7, average_price=100.0)])),
                (h, _PositionBroker([])),
            ], user_id=me["id"], source="test")
            assert [r.mismatched_count for r in reports] == [0, 1]
            assert reports[1].items[0].account_label == "hedge" and reports[1].items[0].status.value == "MISSING_AT_BROKER"
            assert tenant.broker_uncertain_since is not None and "MISSING_AT_BROKER TCS [hedge]" in tenant.broker_uncertain_reason
            # Before Phase AL the whole book was compared with one session: TCS would be "missing"
            # at the primary account and RELIANCE/INFY "untracked" at the hedge one.
            single = await run_reconciliation(session, tenant, "upstox", _PositionBroker([BrokerPosition(symbol="TCS", quantity=5, average_price=100.0)]),
                                              user_id=me["id"], source="test", account=h, settle=False)
            assert single.mismatched_count == 0 and single.account_label == "hedge"
    _run(go())


def test_route_reconciles_every_account_at_the_broker_or_one_by_label(monkeypatch):
    headers, me, acct = _two_accounts("al-route@example.com")
    _seed(me, "RELIANCE", 10, acct["primary"]["id"])
    _seed(me, "TCS", 5, acct["hedge"]["id"])
    books = {"k1": [BrokerPosition(symbol="RELIANCE", quantity=10, average_price=100.0)],
             "k2": [BrokerPosition(symbol="TCS", quantity=4, average_price=100.0)]}
    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter", lambda name, creds: _PositionBroker(books[creds.api_key]))

    body = client.post("/api/reconciliation/upstox", headers=headers).json()
    assert body["accounts"] == ["primary", "hedge"] and body["mismatched_count"] == 1
    rows = {(i["account_label"], i["symbol"]): i["status"] for i in body["items"]}
    assert rows[("primary", "RELIANCE")] == "MATCHED" and rows[("hedge", "TCS")] == "QUANTITY_MISMATCH"
    assert client.get("/api/reconciliation/status", headers=headers).json()["broker_uncertain"] is True

    one = client.post("/api/reconciliation/upstox?account_label=primary", headers=headers).json()
    assert one["account_label"] == "primary" and one["mismatched_count"] == 0 and [i["symbol"] for i in one["items"]] == ["RELIANCE"]
    assert client.post("/api/reconciliation/upstox?account_label=nope", headers=headers).status_code == 404

    class _Broken(_FakeBroker):
        async def get_positions(self):
            raise RuntimeError("socket closed")
    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter",
                        lambda name, creds: _PositionBroker(books["k1"]) if creds.api_key == "k1" else _Broken())
    resp = client.post("/api/reconciliation/upstox", headers=headers)
    assert resp.status_code == 502 and "hedge" in resp.json()["detail"]


def test_worker_start_up_reconciles_each_account_through_its_own_session(monkeypatch):
    headers, me, acct = _two_accounts("al-worker@example.com")
    _seed(me, "RELIANCE", 10, acct["primary"]["id"])
    _seed(me, "TCS", 5, acct["hedge"]["id"])
    by_key = {"k1": _PositionBroker([BrokerPosition(symbol="RELIANCE", quantity=10, average_price=100.0)]),
              "k2": _PositionBroker([BrokerPosition(symbol="TCS", quantity=5, average_price=100.0)])}
    worker = _worker(monkeypatch, _FakeBroker())

    def per_record(record, client=None):
        # Other tests' tenants may hold LIVE trades too; their sessions answer with an empty book,
        # exactly as the pre-existing start-up test's single fake does.
        from app.brokers.token_lifecycle import load_credentials
        return by_key.get(load_credentials(record).api_key, _PositionBroker([]))
    monkeypatch.setattr(tw, "build_adapter", per_record)
    from app.brokers import token_lifecycle
    monkeypatch.setattr(token_lifecycle, "build_adapter", per_record)

    assert _run(worker.reconcile_on_start()) >= 2
    assert _tenant_row(me["tenant_id"]).broker_uncertain_since is None

    by_key["k2"] = _PositionBroker([])          # the hedge session now reports nothing
    assert _run(worker.reconcile_on_start()) >= 2
    tenant = _tenant_row(me["tenant_id"])
    assert tenant.broker_uncertain_since is not None and "TCS [hedge]" in tenant.broker_uncertain_reason
