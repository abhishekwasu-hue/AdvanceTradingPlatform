"""Owner 3 Nov (UPX-Q1): the Upstox Analytics Token is never asked for again because of a bad moment at Upstox.

- A check that does not settle it (429 / 5xx / timeout) never undoes a class that worked; a 401 / 403 still counts.
- A saved, unexpired token whose classes are not all `works` checks itself again (read-only GETs, at most once per
  HEAL_INTERVAL_SECONDS per row): when Upstox accepts it again the connection reads Connected with nothing pasted.
"""
import asyncio
from datetime import UTC, datetime, timedelta

from app.brokers import upstox_analytics as ua
from app.brokers.connect import connection_view
from app.brokers.models import BrokerCredentials
from app.brokers.token_lifecycle import load_credentials
from tests.broker_fakes import UPSTOX_KEY, UPSTOX_SECRET
from tests.test_auth_api import _register, _session_factory, client
from tests.test_upstox_analytics_token import ANALYTICS, NOW, _FakeWs, _jwt, _mock_client, _record, _Report, _tenant_of


def test_an_unsettled_check_never_undoes_a_class_that_worked_but_a_refusal_does():
    meta = ua.new_meta(ANALYTICS, NOW)
    meta["coverage"] = {c: {"status": ua.WORKS} for c in ua.CLASSES}
    creds = BrokerCredentials(api_key="k", analytics_token=ANALYTICS, analytics_meta=meta)
    ua.apply_probe(creds, {ua.HISTORICAL: {"status": ua.UNVERIFIED, "error_code": "timeout"},
                           ua.INTRADAY: {"status": ua.REFUSED, "http_status": 401}}, NOW)
    coverage = creds.analytics_meta["coverage"]
    assert coverage[ua.HISTORICAL]["status"] == ua.WORKS            # Upstox busy: still works
    assert coverage[ua.INTRADAY]["status"] == ua.REFUSED            # a real refusal is recorded
    fresh = BrokerCredentials(api_key="k", analytics_token=ANALYTICS, analytics_meta=ua.new_meta(ANALYTICS, NOW))
    ua.apply_probe(fresh, {ua.HISTORICAL: {"status": ua.UNVERIFIED}}, NOW)
    assert fresh.analytics_meta["coverage"][ua.HISTORICAL]["status"] == ua.UNVERIFIED   # never worked: stays unsettled


def test_a_saved_token_refused_overnight_heals_itself_without_pasting(monkeypatch):
    async def fake_ws(url):
        return _FakeWs()
    monkeypatch.setattr(ua, "_default_ws_connect", fake_ws)

    async def fake_smoke(adapter, account_label="primary"):
        return _Report()
    monkeypatch.setattr("app.brokers.routes.run_smoke", fake_smoke)
    seen: list = []
    refused = {ua.HISTORICAL: (401, "UDAPI100050"), ua.INTRADAY: (401, "UDAPI100050")}
    monkeypatch.setattr(ua, "_client", lambda: _mock_client(refused, seen))
    bearer = _register("upx-heal@example.com")
    tenant_id = _tenant_of(bearer)
    token = _jwt(datetime.now(UTC) + timedelta(days=300))
    res = client.post("/api/broker/upstox/credentials", headers={"Authorization": f"Bearer {bearer}"},
                      json={"api_key": UPSTOX_KEY, "api_secret": UPSTOX_SECRET, "analytics_token": token})
    assert res.status_code == 204, res.text
    record = asyncio.run(_record(tenant_id))
    probed = datetime.fromisoformat(load_credentials(record).analytics_meta["probed_at"])
    assert connection_view(record, probed)["status"] == "not_working"          # what made him paste it again

    seen.clear()
    monkeypatch.setattr(ua, "_client", lambda: _mock_client({}, seen))       # Upstox accepts it again

    async def heal(at):
        async with _session_factory() as session:
            return await ua.heal_coverage(session, at)
    soon = probed + timedelta(seconds=ua.HEAL_INTERVAL_SECONDS - 60)
    asyncio.run(heal(soon))
    assert not seen and connection_view(asyncio.run(_record(tenant_id)), soon)["status"] == "not_working"
    seen.clear()
    later = probed + timedelta(seconds=ua.HEAL_INTERVAL_SECONDS + 60)
    asyncio.run(heal(later))
    assert seen and {r.method for r in seen} == {"GET"}                      # read-only
    healed = asyncio.run(_record(tenant_id))
    view = connection_view(healed, later)
    assert view["status"] == "connected" and view["data_only"] is True       # nothing pasted
    assert load_credentials(healed).analytics_token == token                  # the same saved token
    seen.clear()
    asyncio.run(heal(later + timedelta(hours=5)))                             # every class works: nothing to check
    assert not seen
