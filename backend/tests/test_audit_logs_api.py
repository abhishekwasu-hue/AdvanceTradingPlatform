from tests.test_auth_api import _register, client


def test_audit_logs_requires_authentication():
    assert client.get("/api/audit-logs").status_code in (401, 403)


def test_registering_and_logging_in_are_audited():
    token = _register("greg@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    login = client.post("/api/auth/login", json={"email": "greg@example.com", "password": "S3cur3Pass!"})
    assert login.status_code == 200

    logs = client.get("/api/audit-logs", headers=headers).json()
    events = [entry["event"] for entry in logs]
    assert "user_registered" in events
    assert "user_login" in events


def test_audit_logs_are_private_per_user():
    token_a = _register("hank@example.com")
    token_b = _register("ivy@example.com")

    logs_b = client.get("/api/audit-logs", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert all("hank" not in entry["detail"] for entry in logs_b)


def test_broker_credential_actions_are_audited():
    token = _register("jack@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    store = client.post(
        "/api/broker/zerodha/credentials", headers=headers,
        json={"api_key": "k", "api_secret": "s", "access_token": "t"},
    )
    assert store.status_code == 204

    logs = client.get("/api/audit-logs", headers=headers).json()
    events = [entry["event"] for entry in logs]
    assert "broker_credentials_stored" in events
