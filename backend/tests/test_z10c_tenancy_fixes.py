"""Z10-c (G6): regression tests for the tenancy and scope bugs the sweep found (tests/test_z10c_tenancy_sweep.py).

1. A VIEWER (read-only by role) could write data every organisation shares: fundamentals companies / financials /
   refreshes and cited news events. Now a trading role of an active organisation is needed (`require_contributor`).
2. A VIEWER could run a broker reconciliation, which sets or clears the organisation's LIVE-entry block. Now
   `require_trader`.
3. The scope catalogue the Team page offers (`risk:write`, `alerts:write`, `strategies:write`, `brokers:write`,
   `billing:manage`, `marketplace:publish`) was not checked on those writes: denying a member one of them changed
   nothing. Now each write checks its scope (403 with `X-Missing-Scope`).
4. Bulk fundamentals refresh answered 500 when NSE could not be reached; a network failure is now a provider error
   reported per symbol.
"""
import asyncio
import json
from typing import Dict, Tuple

import httpx
import pytest

from app.auth.security import hash_password
from app.db.models import User
from app.fundamentals.providers.exceptions import FundamentalDataProviderError
from app.fundamentals.providers.nse import NSEProvider
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _upgrade_plan


def _owner(email: str) -> Tuple[Dict[str, str], dict]:
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"])
    return headers, me


def _member(email: str, tenant_id: int, role: str, deny: Tuple[str, ...] = ()) -> Dict[str, str]:
    async def create():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role,
                             scope_overrides=json.dumps({"deny": list(deny), "grant": []}) if deny else None))
            await session.commit()
    asyncio.run(create())
    response = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


_PROFILE = {"symbol": "Z10CCO", "name": "Z10c Test Ltd", "sector": "Energy", "industry": "Oil"}
_NEWS = {"category": "RBI_POLICY", "headline": "Repo rate unchanged", "event_date": "2026-10-09", "sentiment": "Neutral",
         "source": {"source": "RBI", "source_url": "https://www.rbi.org.in/"}}


def test_viewer_cannot_write_shared_fundamentals_but_a_trading_member_can():
    owner, me = _owner("z10c-fix-fund-owner@example.com")
    viewer = _member("z10c-fix-fund-viewer@example.com", me["tenant_id"], "VIEWER")
    assert client.post("/api/fundamentals/companies", headers=viewer, json=_PROFILE).status_code == 403
    assert client.post("/api/fundamentals/refresh", headers=viewer, json={"symbols": ["TCS"]}).status_code == 403
    created = client.post("/api/fundamentals/companies", headers=owner, json=_PROFILE)
    assert created.status_code == 201, created.text
    changed = client.put(f"/api/fundamentals/companies/{_PROFILE['symbol']}", headers=viewer, json={**_PROFILE, "name": "Defaced"})
    assert changed.status_code == 403
    assert client.get(f"/api/fundamentals/companies/{_PROFILE['symbol']}", headers=viewer).json()["name"] == "Z10c Test Ltd"


def test_viewer_cannot_enter_a_shared_news_event_but_a_trading_member_can():
    owner, me = _owner("z10c-fix-news-owner@example.com")
    viewer = _member("z10c-fix-news-viewer@example.com", me["tenant_id"], "VIEWER")
    assert client.post("/api/news-events", headers=viewer, json=_NEWS).status_code == 403
    created = client.post("/api/news-events", headers=owner, json=_NEWS)
    assert created.status_code == 201, created.text


def test_viewer_cannot_run_a_reconciliation():
    owner, me = _owner("z10c-fix-recon-owner@example.com")
    viewer = _member("z10c-fix-recon-viewer@example.com", me["tenant_id"], "VIEWER")
    assert client.post("/api/reconciliation/upstox", headers=viewer).status_code == 403
    assert client.post("/api/reconciliation/upstox", headers=owner).status_code == 404     # no credentials stored: the gate let it in


@pytest.mark.parametrize("scope,method,path,body", [
    ("risk:write", "PUT", "/api/risk-settings", {"capital": 100000.0, "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 2.0,
                                                 "max_trades_per_day": 5, "max_open_positions": 3, "max_consecutive_losses": 3,
                                                 "min_risk_reward": 1.5, "lot_size": 1}),
    ("alerts:write", "DELETE", "/api/alert-channels/telegram", None),
    ("strategies:write", "DELETE", "/api/custom-strategies/999999", None),
    ("brokers:write", "POST", "/api/accounts/999999/disable", None),
    ("billing:manage", "POST", "/api/billing/cancel", None),
    ("marketplace:publish", "POST", "/api/marketplace/listings/999999/unlist", None),
])
def test_a_denied_catalogue_scope_now_bites(scope, method, path, body):
    _, me = _owner(f"z10c-fix-scope-{scope.replace(':', '-')}@example.com")
    denied = _member(f"z10c-fix-denied-{scope.replace(':', '-')}@example.com", me["tenant_id"], "OWNER", (scope,))
    allowed = _member(f"z10c-fix-allowed-{scope.replace(':', '-')}@example.com", me["tenant_id"], "OWNER")
    refused = client.request(method, path, headers=denied, json=body)
    assert refused.status_code == 403 and refused.headers.get("X-Missing-Scope") == scope, refused.text
    passed = client.request(method, path, headers=allowed, json=body)
    assert passed.headers.get("X-Missing-Scope") is None, passed.text


def test_strategy_creator_follows_the_catalogue_and_no_longer_edits_risk_settings():
    """STRATEGY_CREATOR's role scopes never included `risk:write` (app/auth/scopes.py ROLE_SCOPES); the route now agrees."""
    _, me = _owner("z10c-fix-sc-owner@example.com")
    creator = _member("z10c-fix-sc@example.com", me["tenant_id"], "STRATEGY_CREATOR")
    response = client.put("/api/risk-settings", headers=creator, json={"capital": 100000.0})
    assert response.status_code == 403 and response.headers.get("X-Missing-Scope") == "risk:write"


def test_nse_unreachable_is_a_provider_error_not_a_crash():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)
    provider = NSEProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="https://www.nseindia.com"))
    with pytest.raises(FundamentalDataProviderError, match="NSE unreachable"):
        asyncio.run(provider.get_company_profile("TCS"))


def test_bulk_refresh_reports_an_unreachable_provider_per_symbol(monkeypatch):
    from app.fundamentals import routes as fundamentals_routes

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)
    offline = NSEProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="https://www.nseindia.com"))
    monkeypatch.setattr(fundamentals_routes, "_provider_or_400", lambda name: offline)
    owner, _ = _owner("z10c-fix-refresh@example.com")
    response = client.post("/api/fundamentals/refresh", headers=owner, json={"symbols": ["ZTEST1"], "create_missing": True})
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 0 and "NSE unreachable" in json.dumps(response.json()["results"])


def test_uploaded_option_chain_history_stays_with_the_uploading_organisation():
    """Z10c-3: an upload was platform-wide history - it priced other organisations' option backtests, they could read
    it, and an upload inside the recorder's interval stopped the live capture for everyone."""
    from datetime import datetime, timedelta, timezone

    from app.backtest import chain_recorder
    from app.brokers.models import OptionChain, OptionChainRow

    uploader, _ = _owner("z10c-fix-chain-a@example.com")
    other, _ = _owner("z10c-fix-chain-b@example.com")
    uploader_tenant = client.get("/api/auth/me", headers=uploader).json()["tenant_id"]
    viewer = _member("z10c-fix-chain-viewer@example.com", uploader_tenant, "VIEWER")
    now = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)
    row = {"timestamp": now.isoformat(), "expiry": "2026-09-30", "strike": 26000.0, "right": "CE", "ltp": 1.11}
    body = {"underlying": "Z10CIDX", "rows": [row]}
    assert client.post("/api/backtest/option-chain/snapshots", headers=viewer, json=body).status_code == 403
    uploaded = client.post("/api/backtest/option-chain/snapshots", headers=uploader, json=body)
    assert uploaded.status_code == 200 and uploaded.json()["written"] == 1, uploaded.text
    span = {"underlying": "Z10CIDX", "start": "2026-09-25T00:00:00Z", "end": "2026-09-26T00:00:00Z"}
    assert [r["ltp"] for r in client.get("/api/backtest/option-chain/snapshots", headers=uploader, params=span).json()] == [1.11]
    assert client.get("/api/backtest/option-chain/snapshots", headers=other, params=span).json() == []
    assert client.get("/api/backtest/option-chain/coverage", headers=other, params={"underlying": "Z10CIDX"}).json() == []
    # The other organisation uploads the same contract and time: its own row, not "a duplicate" of someone else's.
    assert client.post("/api/backtest/option-chain/snapshots", headers=other, json={"underlying": "Z10CIDX", "rows": [{**row, "ltp": 2.22}]}).json()["written"] == 1
    assert [r["ltp"] for r in client.get("/api/backtest/option-chain/snapshots", headers=other, params=span).json()] == [2.22]

    async def capture():
        async with _session_factory() as session:
            chain = OptionChain(underlying="Z10CIDX", expiry="2026-09-30", underlying_ltp=26000.0,
                                rows=[OptionChainRow(strike=26000.0, call_ltp=50.0, put_ltp=40.0)])
            return await chain_recorder.record_chain(session, "Z10CIDX", chain, now + timedelta(minutes=1), interval_minutes=5,
                                                     tenant_id=uploader_tenant)
    assert asyncio.run(capture()) == 2      # the uploads inside the interval did not stop the recorder's own capture
