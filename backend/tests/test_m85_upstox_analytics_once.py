"""M85: the Upstox Analytics Token (valid one year) is asked ONCE. While it serves market data, an ended daily login
never raises the "login ended" notice: the browser reads "prices refresh every few seconds" and the worker keeps the
organisation's stream on the analytics token. Without a working analytics token the renew notice stays as before."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.brokers import upstox_analytics
from app.market_data import hub as data_hub
from tests.test_tm1_data_hub import _clean, _email, _flags, run  # noqa: F401 - autouse fixture

UTC = timezone.utc


def _row(token_status="EXPIRED"):
    return SimpleNamespace(id=1, user_id=7, tenant_id=9, broker_name="upstox", account_label="primary",
                           token_status=token_status, token_expires_at=None, last_verified_at=None)


@pytest.fixture
def analytics(monkeypatch):
    state = {"works": True}
    monkeypatch.setattr(upstox_analytics, "analytics_usable", lambda record, now=None: state["works"])
    return state


def _conn(record, renew):
    return data_hub.UserConnection(namespace="ns-m85", broker="upstox", account_label="primary", byob=True, renew=renew, record=record)


def test_data_only_row_reads_polling_not_renew_even_after_a_published_renew(analytics):
    async def go():
        await data_hub.publish_status(data_hub.HubStatus(namespace="ns-m85", broker="upstox", state="renew_needed", since="x"))
        return await data_hub.connection_status(_conn(_row(), renew=False))
    status = run(go())
    assert status.state == "polling"
    assert status.public()["message"] == "Prices from Upstox refresh every few seconds."


def test_a_row_with_a_daily_login_still_waits_for_its_stream(analytics):
    async def go():
        await data_hub.publish_status(data_hub.HubStatus(namespace="ns-m85", broker="upstox", state="renew_needed", since="x"))
        row = _row("VALID")
        row.token_expires_at = datetime.now(UTC) + timedelta(hours=4)
        return await data_hub.connection_status(_conn(row, renew=False))
    assert run(go()).state == "connecting"


def test_worker_keeps_the_stream_on_the_analytics_token_and_never_says_renew(monkeypatch, analytics):
    from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _tenant, _worker
    from app.brokers.exceptions import BrokerAuthenticationError
    t = _tenant(_email("m85-data"), token_status="EXPIRED")
    _deploy(t, symbol="RELIANCE")
    _flags([t["tenant_id"]], data_hub=True)
    broker = _FakeBroker()

    async def rejecting_profile():
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)
    seen = []
    worker.hub = data_hub.DataHub(stream_factory=lambda adapter: seen.append(adapter) or None)   # no stream: REST
    run(worker.run_cycle(OPEN_NOW))
    status = run(data_hub.read_status(data_hub.org_namespace(t["tenant_id"])))
    assert seen, "the organisation's stream was asked for through the data-only row"
    assert status is not None and status.state == "polling"
    assert not worker.hub.prompts


def test_worker_without_a_working_analytics_token_still_says_renew_once(monkeypatch, analytics):
    from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _tenant, _worker
    from app.brokers.exceptions import BrokerAuthenticationError
    analytics["works"] = False
    t = _tenant(_email("m85-none"), token_status="EXPIRED")
    _deploy(t, symbol="RELIANCE")
    _flags([t["tenant_id"]], data_hub=True)
    broker = _FakeBroker()

    async def rejecting_profile():
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)
    worker.hub = data_hub.DataHub(stream_factory=lambda adapter: None)
    run(worker.run_cycle(OPEN_NOW))
    status = run(data_hub.read_status(data_hub.org_namespace(t["tenant_id"])))
    assert status is not None and status.state == "renew_needed" and len(worker.hub.prompts) == 1


def test_byob_user_with_only_the_analytics_token_gets_no_renew(monkeypatch, analytics):
    from app.market_data import entitlement
    from app.platform import controls

    async def on(*a, **k):
        return True

    async def records(session, user, *a, **k):
        return [_row()]
    monkeypatch.setattr(controls, "flag_enabled", on)
    monkeypatch.setattr(entitlement, "own_records", records)
    user = SimpleNamespace(id=7, tenant_id=9)
    conn = run(data_hub.user_connection(None, user))
    assert conn is not None and conn.renew is False and conn.byob
    analytics["works"] = False
    assert run(data_hub.user_connection(None, user)).renew is True


def test_token_status_says_data_usable_for_the_data_pages(analytics):
    from tests.test_auth_api import client
    from tests.test_trading_worker import _tenant
    t = _tenant(_email("m85-status"), token_status="EXPIRED")
    rows = client.get("/api/broker/token-status", headers=t["headers"]).json()
    assert rows[0]["needs_login"] is True and rows[0]["data_usable"] is True
    analytics["works"] = False
    rows = client.get("/api/broker/token-status", headers=t["headers"]).json()
    assert rows[0]["data_usable"] is False
