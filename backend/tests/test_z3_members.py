"""Z3 review (L7/L8): helpers for a second member of an organisation, through the ordinary invite flow - shared by the Z3
test modules. (No tests of its own; the file name keeps it next to them.)"""
import uuid
from urllib.parse import parse_qs, urlparse

from tests.test_auth_api import client


def member(owner: dict, role: str = "USER") -> dict:
    email = f"z3-member-{uuid.uuid4().hex[:8]}@example.com"
    invite = client.post("/api/team/invites", headers=owner["headers"], json={"email": email, "role": role})
    assert invite.status_code in (200, 201), invite.text
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}
