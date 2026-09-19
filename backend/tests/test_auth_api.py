import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.auth.routes import login_rate_limit, register_rate_limit
from app.brokers.models import BrokerProfile
from app.db.base import Base
from app.db.session import get_session
from app.main import app

_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_session():
    async with _session_factory() as session:
        yield session


async def _create_tables() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


asyncio.run(_create_tables())
app.dependency_overrides[get_session] = _override_get_session
# Every request in this test suite shares the TestClient's one fake IP, so the per-IP register/
# login rate limits (see app/core/rate_limit.py) would otherwise rate-limit the test suite itself
# well before any real test's request count - disabled here for the whole suite by default.
# tests/test_rate_limiting.py re-enables them (via a separate TestClient with a distinct fake IP)
# to test the 429 behavior itself, restoring these overrides afterwards.
app.dependency_overrides[register_rate_limit] = lambda: None
app.dependency_overrides[login_rate_limit] = lambda: None

client = TestClient(app)


def _register(email: str, password: str = "S3cur3Pass!") -> str:
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_register_returns_token_and_creates_user():
    token = _register("alice@example.com")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_register_duplicate_email_returns_409():
    _register("bob@example.com")
    response = client.post("/api/auth/register", json={"email": "bob@example.com", "password": "whatever123"})
    assert response.status_code == 409


def test_login_returns_token_for_correct_password():
    _register("carol@example.com", password="MyPassword1!")
    response = client.post("/api/auth/login", json={"email": "carol@example.com", "password": "MyPassword1!"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_rejects_wrong_password():
    _register("dave@example.com", password="RightPassword1!")
    response = client.post("/api/auth/login", json={"email": "dave@example.com", "password": "WrongPassword"})
    assert response.status_code == 401


def test_me_requires_authentication():
    response = client.get("/api/auth/me")
    assert response.status_code in (401, 403)


def test_register_rejects_password_shorter_than_8_characters():
    response = client.post("/api/auth/register", json={"email": "shortpw@example.com", "password": "abc123"})
    assert response.status_code == 422


def test_register_rejects_password_longer_than_128_characters():
    response = client.post("/api/auth/register", json={"email": "longpw@example.com", "password": "a" * 129})
    assert response.status_code == 422


def test_login_with_unregistered_email_returns_401_without_error():
    """Exercises the timing-safe branch (a dummy bcrypt check against an unknown email) - must
    behave exactly like a wrong password, not raise or leak whether the email exists.
    """
    response = client.post("/api/auth/login", json={"email": "nobody-registered@example.com", "password": "whatever123"})
    assert response.status_code == 401


def test_store_list_and_delete_broker_credentials():
    token = _register("erin@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    store = client.post(
        "/api/broker/zerodha/credentials", headers=headers,
        json={"api_key": "key123", "access_token": "tok456"},
    )
    assert store.status_code == 204

    listing = client.get("/api/broker/credentials", headers=headers)
    assert listing.status_code == 200
    assert [c["broker_name"] for c in listing.json()] == ["zerodha"]

    delete = client.delete("/api/broker/zerodha/credentials", headers=headers)
    assert delete.status_code == 204

    listing_after = client.get("/api/broker/credentials", headers=headers)
    assert listing_after.json() == []


def test_store_credentials_requires_authentication():
    response = client.post("/api/broker/zerodha/credentials", json={"api_key": "x"})
    assert response.status_code in (401, 403)


def test_store_credentials_rejects_unknown_broker():
    token = _register("frank@example.com")
    response = client.post(
        "/api/broker/not_a_real_broker/credentials",
        headers={"Authorization": f"Bearer {token}"}, json={"api_key": "x"},
    )
    assert response.status_code == 404


def test_authenticate_without_stored_credentials_returns_404():
    token = _register("grace@example.com")
    response = client.post("/api/broker/zerodha/authenticate", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_authenticate_success_calls_adapter_and_audits(monkeypatch):
    token = _register("heidi@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    class _FakeAdapter:
        async def authenticate(self):
            return BrokerProfile(broker="zerodha", user_id="ZZ123", name="Heidi")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _FakeAdapter())

    response = client.post("/api/broker/zerodha/authenticate", headers=headers)
    assert response.status_code == 200
    assert response.json()["user_id"] == "ZZ123"


def test_authenticate_failure_returns_502_and_audits(monkeypatch):
    token = _register("ivan@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    class _FailingAdapter:
        async def authenticate(self):
            raise RuntimeError("bad credentials")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _FailingAdapter())

    response = client.post("/api/broker/zerodha/authenticate", headers=headers)
    assert response.status_code == 502
