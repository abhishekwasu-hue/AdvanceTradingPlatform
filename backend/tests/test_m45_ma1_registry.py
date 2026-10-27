"""M45 MA-1 (docs/design/M45_MULTI_ACCOUNT.md): the caller's own accounts as Primary / Secondary - flag gate, one
Primary per user with confirmation, Secondary groups, own accounts only (G1), the open-LIVE-trade guard (M45-3), audit."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import AuditLogRecord, BrokerAccountRecord, TradeRecord
from app.platform import controls
from tests.broker_fakes import UPSTOX_KEY, UPSTOX_KEY_2, UPSTOX_SECRET, UPSTOX_SECRET_2
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _three_accounts(email):
    headers = _auth(email)
    for label, key, secret in (("primary", UPSTOX_KEY, UPSTOX_SECRET), ("hedge", UPSTOX_KEY_2, UPSTOX_SECRET_2),
                               ("third", UPSTOX_KEY, UPSTOX_SECRET)):
        resp = client.post(f"/api/broker/upstox/credentials?account_label={label}", headers=headers,
                           json={"api_key": key, "api_secret": secret, "access_token": "t"})
        assert resp.status_code == 204, resp.text
    ids = {a["account_label"]: a["id"] for a in client.get("/api/accounts", headers=headers).json()}
    return headers, ids


def _role(headers, account_id, role, confirm=False):
    return client.post(f"/api/accounts/mine/{account_id}/role", headers=headers, json={"role": role, "confirm": confirm})


def test_off_by_default_answers_404_with_the_flag_named():
    headers, _ = _three_accounts("ma1-off@example.com")
    _set_flags(ui_v2=True, multi_account=False)
    resp = client.get("/api/accounts/mine", headers=headers)
    assert resp.status_code == 404 and resp.headers["X-Feature-Disabled"] == "multi_account"
    assert "multi_account" in controls.DEFAULT_OFF_FLAGS


def test_one_primary_per_user_needs_confirmation_and_groups_are_for_secondaries():
    headers, ids = _three_accounts("ma1-roles@example.com")
    _set_flags(ui_v2=True, multi_account=True)
    try:
        assert _role(headers, ids["primary"], "primary").json()["role"] == "PRIMARY"
        assert _role(headers, ids["hedge"], "SECONDARY").json()["role"] == "SECONDARY"

        # A second Primary is refused without confirmation and names the current one.
        refused = _role(headers, ids["third"], "PRIMARY")
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert detail["code"] == "confirm_primary" and detail["current_primary"]["id"] == ids["primary"]
        # Confirmed: the old Primary becomes Secondary; still exactly one Primary.
        assert _role(headers, ids["third"], "PRIMARY", confirm=True).json()["role"] == "PRIMARY"
        rows = {r["id"]: r for r in client.get("/api/accounts/mine", headers=headers).json()["accounts"]}
        assert [r["role"] for r in rows.values()].count("PRIMARY") == 1
        assert rows[ids["primary"]]["role"] == "SECONDARY" and rows[ids["third"]]["role"] == "PRIMARY"
        assert rows[ids["third"]]["enabled"] is True and "token_expires_at" in rows[ids["third"]]

        # Groups: only for a Secondary; names are cleaned; clearing works; a bad name is refused.
        grouped = client.post(f"/api/accounts/mine/{ids['hedge']}/group", headers=headers, json={"group_name": "  Family   A "})
        assert grouped.status_code == 200 and grouped.json()["group_name"] == "Family A"
        assert client.post(f"/api/accounts/mine/{ids['third']}/group", headers=headers,
                           json={"group_name": "X"}).json()["detail"]["code"] == "not_secondary"
        assert client.post(f"/api/accounts/mine/{ids['hedge']}/group", headers=headers,
                           json={"group_name": "bad/name"}).json()["detail"]["code"] == "bad_group"
        # Taking the account out of multi-account clears its role and group.
        out = _role(headers, ids["hedge"], None).json()
        assert out["role"] is None and out["group_name"] is None
        assert _role(headers, ids["hedge"], "LEADER").json()["detail"]["code"] == "bad_role"

        async def audit():
            async with _session_factory() as session:
                return list(await session.scalars(select(AuditLogRecord.event).where(
                    AuditLogRecord.event.in_(("broker_account_role", "broker_account_group")))))
        assert len(_run(audit())) >= 6
    finally:
        _set_flags(multi_account=False)


def test_only_own_accounts_and_the_open_live_trade_guard():
    headers, ids = _three_accounts("ma1-guard@example.com")
    me = _me(headers)
    _set_flags(ui_v2=True, multi_account=True)
    try:
        # G1: an account another member linked (here: no owner) is not listed and takes no role.
        async def orphan():
            async with _session_factory() as session:
                account = await session.get(BrokerAccountRecord, ids["third"])
                account.user_id = None
                await session.commit()
        _run(orphan())
        listed = {r["id"] for r in client.get("/api/accounts/mine", headers=headers).json()["accounts"]}
        assert listed == {ids["primary"], ids["hedge"]}
        assert _role(headers, ids["third"], "SECONDARY").json()["detail"]["code"] == "not_owner"

        # M45-3: an open LIVE trade with no recorded account holds back any role (it is listed); a closed one does not.
        async def live_trade(closed):
            async with _session_factory() as session:
                trade = TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="LIVE", symbol="RELIANCE",
                                    strategy_id="manual", direction="LONG", entry_time=datetime.now(timezone.utc),
                                    entry_price=100.0, quantity=1, stop_loss=95.0,
                                    exit_time=datetime.now(timezone.utc) if closed else None)
                session.add(trade)
                await session.commit()
                return trade.id
        _run(live_trade(closed=True))
        open_id = _run(live_trade(closed=False))
        refused = _role(headers, ids["primary"], "PRIMARY")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "unaccounted_live_trades"
        assert [t["id"] for t in refused.json()["detail"]["trades"]] == [open_id]
        assert [t["id"] for t in client.get("/api/accounts/mine", headers=headers).json()["unaccounted_live_trades"]] == [open_id]
        # Taking a role away is never held back.
        assert _role(headers, ids["primary"], None).status_code == 200

        # Another organisation sees nothing of it.
        other = _auth("ma1-guard-other@example.com")
        assert client.get("/api/accounts/mine", headers=other).json()["accounts"] == []
        assert _role(other, ids["primary"], "PRIMARY").status_code == 404
    finally:
        _set_flags(multi_account=False)
