"""Master prompt Section 48: "rate limiting/circuit breakers". `tests/test_auth_api.py` disables
the register/login rate limiters for the whole suite by default (every request there shares one
fake TestClient IP, so the limiter would otherwise throttle the tests themselves). This file is
the one place that re-enables them, against a *separate* TestClient with its own distinct fake IP
so it can't be starved by - or interfere with - every other test's shared-IP traffic, and restores
the disabling override afterwards so no other test file is affected by import order.
"""
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.auth.routes import login_rate_limit, register_rate_limit
from app.core.rate_limit import _WINDOWS
from app.main import app
from tests.test_auth_api import _override_get_session
from app.db.session import get_session


@contextmanager
def _rate_limiting_enabled():
    """Temporarily removes the test-suite-wide disabling override so the real dependency runs."""
    saved_register = app.dependency_overrides.pop(register_rate_limit, None)
    saved_login = app.dependency_overrides.pop(login_rate_limit, None)
    try:
        yield
    finally:
        if saved_register is not None:
            app.dependency_overrides[register_rate_limit] = saved_register
        if saved_login is not None:
            app.dependency_overrides[login_rate_limit] = saved_login


def test_register_is_rate_limited_per_ip():
    _WINDOWS.clear()
    app.dependency_overrides[get_session] = _override_get_session
    isolated_client = TestClient(app, client=("203.0.113.10", 12345))

    with _rate_limiting_enabled():
        responses = [
            isolated_client.post(
                "/api/auth/register", json={"email": f"ratelimit-reg-{i}@example.com", "password": "S3cur3Pass!"},
            )
            for i in range(11)
        ]

    statuses = [r.status_code for r in responses]
    assert statuses[:10] == [201] * 10
    assert statuses[10] == 429


def test_login_is_rate_limited_per_ip_independently_of_register():
    _WINDOWS.clear()
    app.dependency_overrides[get_session] = _override_get_session
    isolated_client = TestClient(app, client=("203.0.113.20", 12345))

    with _rate_limiting_enabled():
        isolated_client.post(
            "/api/auth/register", json={"email": "ratelimit-login@example.com", "password": "S3cur3Pass!"},
        )
        responses = [
            isolated_client.post(
                "/api/auth/login", json={"email": "ratelimit-login@example.com", "password": "wrong-password"},
            )
            for _ in range(11)
        ]

    statuses = [r.status_code for r in responses]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_different_ips_are_not_cross_blocked():
    _WINDOWS.clear()
    app.dependency_overrides[get_session] = _override_get_session
    client_a = TestClient(app, client=("203.0.113.30", 1))
    client_b = TestClient(app, client=("203.0.113.31", 1))

    with _rate_limiting_enabled():
        for i in range(10):
            response = client_a.post(
                "/api/auth/register", json={"email": f"ip-a-{i}@example.com", "password": "S3cur3Pass!"},
            )
            assert response.status_code == 201

        exhausted = client_a.post(
            "/api/auth/register", json={"email": "ip-a-overflow@example.com", "password": "S3cur3Pass!"},
        )
        assert exhausted.status_code == 429

        still_allowed = client_b.post(
            "/api/auth/register", json={"email": "ip-b-first@example.com", "password": "S3cur3Pass!"},
        )
        assert still_allowed.status_code == 201
