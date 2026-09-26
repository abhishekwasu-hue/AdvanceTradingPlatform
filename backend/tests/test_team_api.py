"""Phase B1: multi-user tenants - owner role on registration, invites, roles, removal, per-user
notification read state, and write endpoints refusing VIEWER/SUPPORT."""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import TenantInviteRecord, User
from app.notifications.service import notify
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _invite(headers, email, role="USER"):
    return client.post("/api/team/invites", headers=headers, json={"email": email, "role": role})


def _token_from(invite_url: str) -> str:
    return parse_qs(urlparse(invite_url).query)["invite"][0]


def _accept(invite_url: str, password="S3cur3Pass!"):
    return client.post(f"/api/auth/invite/{_token_from(invite_url)}/accept", json={"password": password})


def _join(owner_headers, email, role="USER"):
    """Owner invites, invitee accepts; returns the new member's headers + profile."""
    created = _invite(owner_headers, email, role)
    assert created.status_code == 201, created.text
    accepted = _accept(created.json()["invite_url"])
    assert accepted.status_code == 201, accepted.text
    headers = {"Authorization": f"Bearer {accepted.json()['access_token']}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def test_registration_creates_tenant_owner():
    _, me = _owner("team-owner@example.com")
    assert me["role"] == "OWNER"
    tenant = client.get("/api/team/tenant", headers={"Authorization": f"Bearer {_register('team-owner2@example.com')}"}).json()
    assert tenant["members"] == 1 and tenant["plan"] == "free" and tenant["status"] == "active"


def test_invite_accept_joins_same_tenant_with_invited_role():
    owner_headers, owner = _owner("inv-owner@example.com")
    created = _invite(owner_headers, "Trader@Example.com", "STRATEGY_CREATOR")
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "trader@example.com" and body["role"] == "STRATEGY_CREATOR"
    assert "?invite=" in body["invite_url"]

    info = client.get(f"/api/auth/invite/{_token_from(body['invite_url'])}").json()
    assert info["valid"] and info["email"] == "trader@example.com" and info["tenant_name"] == "inv-owner@example.com"

    member_headers, member = _join(owner_headers, "trader2@example.com")
    assert member["tenant_id"] == owner["tenant_id"] and member["role"] == "USER"
    members = client.get("/api/team/members", headers=member_headers).json()
    assert {m["email"] for m in members} >= {"inv-owner@example.com", "trader2@example.com"}
    # Audit logs are listed per user: the invite is on the owner's trail, the acceptance on the member's.
    assert "member_invited" in [l["event"] for l in client.get("/api/audit-logs", headers=owner_headers).json()]
    assert "invite_accepted" in [l["event"] for l in client.get("/api/audit-logs", headers=member_headers).json()]


def test_invite_is_single_use_and_token_is_only_stored_hashed():
    owner_headers, owner = _owner("inv-once@example.com")
    created = _invite(owner_headers, "once@example.com").json()
    token = _token_from(created["invite_url"])

    async def stored():
        async with _session_factory() as session:
            return await session.scalar(select(TenantInviteRecord).where(TenantInviteRecord.tenant_id == owner["tenant_id"]))
    record = _run(stored())
    assert record.token_hash != token and token not in record.token_hash

    assert _accept(created["invite_url"]).status_code == 201
    second = _accept(created["invite_url"])
    assert second.status_code == 410 and "already been used" in second.json()["detail"]
    assert client.get(f"/api/auth/invite/{token}").json()["valid"] is False


def test_expired_invite_is_refused():
    owner_headers, owner = _owner("inv-exp@example.com")
    created = _invite(owner_headers, "late@example.com").json()

    async def expire():
        async with _session_factory() as session:
            record = await session.scalar(select(TenantInviteRecord).where(TenantInviteRecord.tenant_id == owner["tenant_id"]))
            record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
    _run(expire())
    assert _accept(created["invite_url"]).status_code == 410
    assert client.get("/api/team/invites", headers=owner_headers).json() == []  # expired ones are not listed


def test_unknown_invite_is_404_and_bad_password_is_422():
    assert client.get("/api/auth/invite/not-a-real-token").status_code == 404
    owner_headers, _ = _owner("inv-pw@example.com")
    created = _invite(owner_headers, "weak@example.com").json()
    assert _accept(created["invite_url"], password="short").status_code == 422


def test_cannot_invite_owner_role_or_existing_email():
    owner_headers, _ = _owner("inv-rules@example.com")
    assert _invite(owner_headers, "x@example.com", "OWNER").status_code == 400
    assert _invite(owner_headers, "x@example.com", "SUPER_ADMIN").status_code == 400
    _register("taken@example.com")
    assert _invite(owner_headers, "taken@example.com").status_code == 409


def test_reinvite_replaces_open_invite_and_revoke_works():
    owner_headers, _ = _owner("inv-replace@example.com")
    first = _invite(owner_headers, "again@example.com").json()
    second = _invite(owner_headers, "again@example.com", "VIEWER").json()
    assert _accept(first["invite_url"]).status_code == 404  # replaced link is dead
    listed = client.get("/api/team/invites", headers=owner_headers).json()
    assert [i["role"] for i in listed] == ["VIEWER"]
    assert client.delete(f"/api/team/invites/{second['id']}", headers=owner_headers).status_code == 204
    assert client.get("/api/team/invites", headers=owner_headers).json() == []


def test_only_owner_manages_team():
    owner_headers, _ = _owner("team-rbac@example.com")
    trader_headers, trader = _join(owner_headers, "rbac-trader@example.com")
    assert _invite(trader_headers, "someone@example.com").status_code == 403
    assert client.get("/api/team/invites", headers=trader_headers).status_code == 403
    assert client.patch(f"/api/team/members/{trader['id']}", headers=trader_headers, json={"role": "OWNER"}).status_code == 403
    assert client.get("/api/team/members", headers=trader_headers).status_code == 200  # everyone can see the team


def test_role_change_and_last_owner_protection():
    owner_headers, owner = _owner("team-roles@example.com")
    trader_headers, trader = _join(owner_headers, "roles-trader@example.com")

    # The only owner cannot demote themselves...
    assert client.patch(f"/api/team/members/{owner['id']}", headers=owner_headers, json={"role": "USER"}).status_code == 409
    # ...but can promote a teammate, after which they may step down.
    promoted = client.patch(f"/api/team/members/{trader['id']}", headers=owner_headers, json={"role": "OWNER"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "OWNER"
    assert client.patch(f"/api/team/members/{owner['id']}", headers=owner_headers, json={"role": "VIEWER"}).status_code == 200
    # Now the former owner is a VIEWER and can no longer manage the team.
    assert _invite(owner_headers, "later@example.com").status_code == 403
    # The remaining sole owner cannot be demoted by anyone.
    assert client.patch(f"/api/team/members/{trader['id']}", headers=trader_headers, json={"role": "USER"}).status_code == 409


def test_removed_member_loses_access_immediately_but_stays_listed():
    owner_headers, owner = _owner("team-remove@example.com")
    trader_headers, trader = _join(owner_headers, "remove-me@example.com")
    assert client.get("/api/auth/me", headers=trader_headers).status_code == 200

    assert client.delete(f"/api/team/members/{owner['id']}", headers=owner_headers).status_code == 409  # not yourself
    assert client.delete(f"/api/team/members/{trader['id']}", headers=owner_headers).status_code == 204
    assert client.get("/api/auth/me", headers=trader_headers).status_code == 401  # existing token dead
    assert client.post("/api/auth/login", json={"email": "remove-me@example.com", "password": "S3cur3Pass!"}).status_code == 401
    members = {m["email"]: m for m in client.get("/api/team/members", headers=owner_headers).json()}
    assert members["remove-me@example.com"]["is_active"] is False

    assert client.post(f"/api/team/members/{trader['id']}/reactivate", headers=owner_headers).status_code == 200
    assert client.post("/api/auth/login", json={"email": "remove-me@example.com", "password": "S3cur3Pass!"}).status_code == 200


def test_viewer_is_read_only_across_trading_endpoints():
    owner_headers, _ = _owner("team-viewer@example.com")
    viewer_headers, _ = _join(owner_headers, "viewer@example.com", "VIEWER")
    assert client.get("/api/deployments", headers=viewer_headers).status_code == 200
    assert client.get("/api/risk-settings", headers=viewer_headers).status_code == 200
    assert client.get("/api/broker/credentials", headers=viewer_headers).status_code == 200

    blocked = [
        client.post("/api/broker/upstox/credentials", headers=viewer_headers, json={"api_key": "k"}),
        client.put("/api/risk-settings", headers=viewer_headers, json=client.get("/api/risk-settings", headers=viewer_headers).json()),
        client.post("/api/deployments", headers=viewer_headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "X"}),
        client.put("/api/alert-channels/telegram", headers=viewer_headers, json={"config": {"bot_token": "1:a", "chat_id": "1"}}),
        client.post("/api/kill-switch/tenant/engage", headers=viewer_headers, json={"reason": "no"}),
        client.post("/api/kill-switch/emergency-exit", headers=viewer_headers, json={"reason": "no", "prices": {}}),
        client.post("/api/custom-strategies", headers=viewer_headers, json={"name": "x", "timeframe": "1min", "long_conditions": [], "short_conditions": [], "stop_loss_atr_mult": 1, "atr_period": 14, "target_rr": [1.5, 2], "min_rr": 1.2}),
        client.post("/api/webhooks/tradingview/token/rotate", headers=viewer_headers),
    ]
    assert [r.status_code for r in blocked] == [403] * len(blocked)


def test_notification_read_state_is_per_user():
    owner_headers, owner = _owner("team-notif@example.com")
    trader_headers, _ = _join(owner_headers, "notif-trader@example.com")

    async def raise_alert():
        async with _session_factory() as session:
            return (await notify(session, owner["tenant_id"], NotificationType.SYSTEM_FAILURE, title="shared", severity=NotificationSeverity.CRITICAL)).id
    note_id = _run(raise_alert())

    assert client.post(f"/api/notifications/{note_id}/read", headers=owner_headers).json()["read"] is True
    owner_view = {n["id"]: n for n in client.get("/api/notifications", headers=owner_headers).json()}
    trader_view = {n["id"]: n for n in client.get("/api/notifications", headers=trader_headers).json()}
    assert owner_view[note_id]["read"] is True and trader_view[note_id]["read"] is False
    assert any(n["id"] == note_id for n in client.get("/api/notifications?unread_only=true", headers=trader_headers).json())
    assert client.post("/api/notifications/read-all", headers=trader_headers).json()["marked_read"] >= 1
    assert all(n["read"] for n in client.get("/api/notifications", headers=trader_headers).json())


def test_webhook_attributes_orders_to_an_owner():
    from app.webhooks.routes import _get_tenant_owner
    owner_headers, owner = _owner("team-webhook@example.com")
    _join(owner_headers, "wh-trader@example.com")

    async def who():
        async with _session_factory() as session:
            return (await _get_tenant_owner(session, owner["tenant_id"])).email
    assert _run(who()) == "team-webhook@example.com"
