"""Phase AB: go-live checklists - a fresh organisation's blockers for PAPER and the extra LIVE
items, the statuses moving as the operator completes steps, and the SUPER_ADMIN platform list."""
from app.core import config
from tests.test_admin_api import _admin
from tests.test_auth_api import client
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_phase_k_commercial import _owner


def _by_key(body):
    return {i["key"]: i for i in body["items"]}


def test_fresh_tenant_sees_blockers_then_clears_them():
    headers, _me = _owner("ab-fresh@example.com")
    assert client.get("/api/readiness").status_code == 401
    body = client.get("/api/readiness", headers=headers).json()
    items = _by_key(body)
    assert body["target"] == "PAPER" and body["ready"] is False
    assert items["broker_credentials"]["status"] == "todo" and "Settings > Brokers" in items["broker_credentials"]["fix"] and items["broker_credentials"]["link"] == "settings"
    assert items["broker_session"]["status"] == "todo" and items["deployment"]["status"] == "todo"
    assert items["worker"]["status"] in ("todo", "warn", "ok")            # depends on other tests' heartbeats; never missing
    assert items["kill_switch"]["status"] == "ok" and items["broker_uncertain"]["status"] == "ok"
    # PAPER treats account hygiene as optional; LIVE requires it.
    assert items["mfa"]["status"] == "info" and items["mfa"]["scope"] == "LIVE" and items["algo_id"]["status"] == "info"
    assert items["ai_provider"]["status"] == "info" and items["ai_provider"]["scope"] == "OPTIONAL"
    assert body["summary"]["todo"] >= 3 and "nothing here changes anything" in body["note"]

    live = _by_key(client.get("/api/readiness?target=live", headers=headers).json())
    assert live["mfa"]["status"] == "todo" and live["email_verified"]["status"] == "todo" and live["algo_id"]["status"] == "todo"
    assert live["risk"]["status"] == "todo" and live["alerts"]["status"] == "todo"          # warn on PAPER, blockers on LIVE
    assert client.get("/api/readiness?target=nonsense", headers=headers).status_code == 422

    # Storing a key without a login flips the first item only; a VALID token clears the second.
    _store_broker(headers)
    items = _by_key(client.get("/api/readiness", headers=headers).json())
    assert items["broker_credentials"]["status"] == "ok" and "upstox" in items["broker_credentials"]["detail"]
    assert items["broker_session"]["status"] == "todo" and "expire every trading day" in items["broker_session"]["fix"]
    _store_broker(headers, token_status="VALID")
    items = _by_key(client.get("/api/readiness", headers=headers).json())
    assert items["broker_session"]["status"] == "ok" and "upstox (primary)" in items["broker_session"]["detail"]

    # An active deployment, an alert channel and risk settings clear their rows.
    created = _create(headers)
    assert created.status_code in (200, 201), created.text
    assert client.put("/api/alert-channels/webhook", headers=headers, json={"config": {"url": "https://hooks.example.com/x", "secret": "s3cr3t-webhook-secret-0123"}}).status_code in (200, 201)
    items = _by_key(client.get("/api/readiness", headers=headers).json())
    assert items["deployment"]["status"] == "ok" and "PAPER 1" in items["deployment"]["detail"]
    assert items["alerts"]["status"] == "ok" and "webhook" in items["alerts"]["detail"].lower()


def test_platform_checklist_is_super_admin_only_and_reads_config(monkeypatch):
    owner, _ = _owner("ab-owner@example.com")
    assert client.get("/api/admin/readiness", headers=owner).status_code == 403
    admin, _ = _admin("ab-admin@example.com")
    body = client.get("/api/admin/readiness", headers=admin).json()
    items = _by_key(body)
    assert body["target"] == "PLATFORM" and {"jwt_secret", "secrets_key", "smtp", "billing", "worker", "migrations", "redis", "cors", "instrument_master", "holidays"} <= set(items)
    assert items["billing"]["status"] == "info" and items["billing"]["detail"] == "manual"          # manual provider needs no keys
    assert items["smtp"]["status"] in ("ok", "warn") and items["push"]["status"] in ("ok", "info")
    scheme = config.DATABASE_URL.split("://", 1)[0]
    assert items["database"]["detail"] == scheme and items["database"]["status"] == ("ok" if scheme.startswith("postgresql") else "warn")

    # The environment guards are read live from config.
    monkeypatch.setattr(config, "JWT_SECRET_KEY", config._INSECURE_DEFAULT_JWT_SECRET)
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["*"])
    monkeypatch.setattr(config, "BILLING_PROVIDER", "razorpay")
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "")
    items = _by_key(client.get("/api/admin/readiness", headers=admin).json())
    assert items["jwt_secret"]["status"] == "todo" and items["jwt_secret"]["link"] == "JWT_SECRET_KEY"
    assert items["environment"]["status"] == "ok" and items["cors"]["status"] == "todo"
    assert items["billing"]["status"] == "todo" and "RAZORPAY_KEY_ID" in items["billing"]["fix"]
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "a-long-random-secret-for-the-test")
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://app.example.com"])
    items = _by_key(client.get("/api/admin/readiness", headers=admin).json())
    assert items["jwt_secret"]["status"] == "ok" and items["cors"]["status"] == "ok" and items["cors"]["detail"] == "https://app.example.com"
