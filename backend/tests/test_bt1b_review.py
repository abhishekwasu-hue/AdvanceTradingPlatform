"""BT1b review follow-ups (flag `byob_data_tier`): per-member alert rules, events, runs and notifications; the OI banner,
shared intraday lake bars and recorded chains off; smoke-test / login-url / delete gates; the holder rule only with the
flag on (flag off: a member may do the daily login on keys the owner stored, as before); race-safe credential writes in
both flag states; the worker stops every org stream and waits for confirmed data (exits still run); Greeks / preview /
exit-price details; /ltp body staleness. No network."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.alerts import engine
from app.brokers import routes as broker_routes
from app.brokers import token_lifecycle
from app.db.models import (AlertRuleRecord, BrokerCredentialRecord, NotificationRecord, ScreenRunRecord, StrategyDeploymentRecord, TradeRecord,
                           User)
from app.notifications.service import notify
from app.core.enums import NotificationType
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
from tests.broker_fakes import KITE_KEY, KITE_SECRET, UPSTOX_KEY, UPSTOX_SECRET
from tests.test_auth_api import _session_factory, client
from tests.test_bt1b_guard_all_reads import _Feed, _byob, _flags, _member, _route
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
run = asyncio.run


def _email(tag: str) -> str:
    return f"bt1br-{tag}-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture(autouse=True)
def _reset_flags():
    yield
    _flags(byob_data_tier=[], screener_v2=[])


def _row(tenant_id, broker="upstox"):
    async def go():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker))
            return rec.user_id, token_lifecycle.load_credentials(rec), rec.token_status
    return run(go())


def _take_over_row(tenant_id, member_id, broker="upstox"):
    """What the store route commits when a member stores over the row - done from another session mid-login."""
    async def go():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker))
            rec.user_id = member_id
            rec.encrypted_payload = encrypt_text('{"api_key": "mk"}', tenant_id, PURPOSE_BROKER_CREDENTIAL)
            rec.token_status = "UNKNOWN"
            await session.commit()
    return go()


class _Login:
    """A broker adapter whose login runs `during` (a concurrent change) before handing back a fresh token."""

    def __init__(self, during=None, token="OWNERS-FRESH-TOKEN"):
        self.during, self.token, self.access_token = during, token, None

    async def authenticate(self):
        if self.during is not None:
            await self.during
        self.access_token = self.token
        return SimpleNamespace(broker="upstox", user_id="U1")


# --- M2: flag off = the daily login by any trader works as before ------------------------------------------------------

def test_flag_off_a_member_does_the_daily_login_on_keys_the_owner_stored(monkeypatch):
    owner_headers, owner = _owner(_email("dl"))
    _store_credentials(owner)
    member_headers, member = _member(owner_headers)
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda name, creds: _Login(token="TODAY"))
    res = client.post("/api/broker/upstox/authenticate", headers=member_headers)
    assert res.status_code == 200, res.text
    holder, creds, status = _row(owner["tenant_id"])
    assert holder == owner["id"] and creds.access_token == "TODAY" and status == "VALID"
    assert client.get("/api/broker/upstox/oauth/start", headers=member_headers).status_code != 403
    # With the flag on the same login is the holder's only, and the message does not suggest another account label.
    _byob(owner["tenant_id"])
    res = client.post("/api/broker/upstox/authenticate", headers=member_headers)
    assert res.status_code == 403 and "account label" not in res.json()["detail"]


# --- M1: race-safe writes, both flag states ------------------------------------------------------------------------------

@pytest.mark.parametrize("flag_on", [False, True])
def test_oauth_callback_never_writes_a_fresh_token_onto_a_row_that_changed_hands(monkeypatch, flag_on):
    owner_headers, owner = _owner(_email("race-oa"))
    _store_credentials(owner)
    member_headers, member = _member(owner_headers)
    if flag_on:
        _byob(owner["tenant_id"])
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda name, creds: _Login(during=_take_over_row(owner["tenant_id"], member["id"])))
    state = token_lifecycle.create_oauth_state(owner["tenant_id"], owner["id"], "upstox")
    r = client.get(f"/api/broker/upstox/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and "error=changed_during_login" in r.headers["location"]
    holder, creds, status = _row(owner["tenant_id"])
    assert holder == member["id"] and creds.access_token is None and status == "UNKNOWN"     # the member's store stands, no token


@pytest.mark.parametrize("flag_on", [False, True])
def test_authenticate_and_login_code_are_race_safe(monkeypatch, flag_on):
    owner_headers, owner = _owner(_email("race-auth"))
    _store_credentials(owner)
    _store_credentials(owner, broker="zerodha")
    member_headers, member = _member(owner_headers)
    if flag_on:
        _byob(owner["tenant_id"])
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda name, creds: _Login(during=_take_over_row(owner["tenant_id"], member["id"])))
    res = client.post("/api/broker/upstox/authenticate", headers=owner_headers)
    assert res.status_code == 409 and "changed while the login" in res.json()["detail"]
    assert _row(owner["tenant_id"])[1].access_token is None
    monkeypatch.setattr(broker_routes, "get_broker_adapter",
                        lambda name, creds: _Login(during=_take_over_row(owner["tenant_id"], member["id"], broker="zerodha")))
    res = client.post("/api/broker/zerodha/login-code", headers=owner_headers, json={"code": "request_token=abc"})
    assert res.status_code == 409
    holder, creds, _status = _row(owner["tenant_id"], "zerodha")
    assert holder == member["id"] and creds.access_token is None


def test_store_route_is_race_safe(monkeypatch):
    headers, me = _owner(_email("race-store"))
    _store_credentials(me)
    other_headers, other = _member(headers)
    original = broker_routes._merge_with_stored

    def merge_then_race(incoming, existing):
        merged = original(incoming, existing)
        broker_routes._race = _take_over_row(me["tenant_id"], other["id"])
        return merged
    monkeypatch.setattr(broker_routes, "_merge_with_stored", merge_then_race)
    real_write = broker_routes._write_if_unchanged

    async def write(session, record, expected_user_id, expected_payload, **kw):
        await broker_routes._race               # the concurrent store commits between the read and this write
        return await real_write(session, record, expected_user_id, expected_payload, **kw)
    monkeypatch.setattr(broker_routes, "_write_if_unchanged", write)
    res = client.post("/api/broker/upstox/credentials", headers=headers, json={"access_token": "mine"})
    assert res.status_code == 409 and "changed while saving" in res.json()["detail"]
    holder, creds, _status = _row(me["tenant_id"])
    assert holder == other["id"] and creds.access_token is None


# --- H3 / L1: smoke test, login URL, delete / disconnect -----------------------------------------------------------------

def test_smoke_test_is_the_holders_and_hides_unconfirmed_prices(monkeypatch):
    owner_headers, owner = _owner(_email("smk"))
    _store_credentials(owner)
    _store_credentials(owner, broker="zerodha")
    member_headers, member = _member(owner_headers)

    async def smoke(adapter, account_label="primary"):
        steps = [{"name": n, "status": "ok", "detail": f"{n} 25,000.00"} for n in ("profile", "funds", "quote", "contract_quote", "option_chain", "positions")]
        return SimpleNamespace(ok=True, summary="ok", as_dict=lambda: {"steps": [dict(s) for s in steps]})
    monkeypatch.setattr(broker_routes, "run_smoke", smoke)
    monkeypatch.setattr(broker_routes, "build_adapter", lambda record: object())
    assert client.post("/api/broker/upstox/smoke-test", headers=member_headers).status_code == 200      # flag off: as before
    _byob(owner["tenant_id"])
    assert client.post("/api/broker/upstox/smoke-test", headers=member_headers).status_code == 403     # the holder's session
    up = client.post("/api/broker/upstox/smoke-test", headers=owner_headers).json()
    assert next(s for s in up["steps"] if s["name"] == "quote")["detail"] == "quote 25,000.00"          # confirmed: shown
    zd = client.post("/api/broker/zerodha/smoke-test", headers=owner_headers).json()
    details = {s["name"]: s["detail"] for s in zd["steps"]}
    assert all("details hidden" in details[n] for n in ("quote", "contract_quote", "option_chain", "positions"))
    assert details["profile"] == "profile 25,000.00" and "25,000" not in json.dumps([details[n] for n in ("quote", "positions")])


def test_login_url_delete_and_disconnect_are_for_the_holder_or_an_owner_with_the_flag_on(monkeypatch):
    owner_headers, owner = _owner(_email("del"))
    _store_credentials(owner, broker="zerodha")
    member_headers, member = _member(owner_headers)
    _byob(owner["tenant_id"])
    assert client.get("/api/broker/zerodha/login-url", headers=member_headers).status_code == 403          # no App ID for others
    assert client.get("/api/broker/zerodha/login-url", headers=owner_headers).status_code == 200
    assert client.post("/api/broker/zerodha/disconnect", headers=member_headers).status_code == 403
    assert client.delete("/api/broker/zerodha/credentials", headers=member_headers).status_code == 403
    assert _row(owner["tenant_id"], "zerodha")[0] == owner["id"]
    # The member's own connection: an owner may remove it.
    assert client.post("/api/broker/upstox/credentials", headers=member_headers, json={"api_key": UPSTOX_KEY, "api_secret": UPSTOX_SECRET}).status_code == 204
    assert client.delete("/api/broker/upstox/credentials", headers=owner_headers).status_code == 204


# --- H1: rules, events, runs and notifications are the member's own -------------------------------------------------------

def _rule(tenant_id, created_by):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(UTC)
            row = AlertRuleRecord(tenant_id=tenant_id, created_by=created_by, name="mine", kind="instrument", symbol="X", condition_text="close > 1",
                                  base_tf="5m", priority="normal", cooldown_minutes=60, mode="instant", digest_every="hourly", status="active",
                                  created_at=now, updated_at=now)
            session.add(row)
            await session.commit()
            return row.id
    return run(go())


def test_rules_events_runs_and_notifications_are_per_member_with_the_flag_on():
    owner_headers, owner = _owner(_email("h1"))
    member_headers, member = _member(owner_headers)
    t = owner["tenant_id"]
    _flags(screener_v2=[t])
    mine, theirs = _rule(t, owner["id"]), _rule(t, member["id"])
    bar = datetime(2026, 3, 10, 4, 0, tzinfo=UTC)

    async def fire():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, mine)
            await engine.record_event(session, rule, "X", bar, {"close": 101.5, "as_of": bar.isoformat()}, now=bar + timedelta(minutes=1))
            await session.commit()
        async with _session_factory() as session:
            await engine.flush(session, bar + timedelta(minutes=5), tenant_id=t)
            session.add(ScreenRunRecord(tenant_id=t, user_id=owner["id"], ast_sha256="x" * 64, ast_version="1", base_tf="1d", universe_json="[]",
                                        data_source="broker:upstox", scanned=0, matched=0, result_json="[]", duration_ms=1))
            await notify(session, t, NotificationType.SYSTEM_FAILURE, "for everyone")
            await session.commit()
            note = await session.scalar(select(NotificationRecord).where(NotificationRecord.tenant_id == t,
                                                                         NotificationRecord.event_type == "SCREEN_ALERT"))
            run_id = await session.scalar(select(ScreenRunRecord.id).where(ScreenRunRecord.tenant_id == t))
            return note, run_id
    note, run_id = run(fire())
    assert note is not None and note.user_id == owner["id"]                       # addressed to the rule's creator
    # Flag off: the organisation-wide view, as before.
    assert {r["id"] for r in client.get("/api/alerts/rules", headers=member_headers).json()} == {mine, theirs}
    assert client.get(f"/api/alerts/rules/{mine}/events", headers=member_headers).status_code == 200
    assert client.get(f"/api/screener/runs/{run_id}", headers=member_headers).status_code == 200
    assert note.id in {n["id"] for n in client.get("/api/notifications", headers=member_headers).json()}
    _byob(t)
    assert {r["id"] for r in client.get("/api/alerts/rules", headers=member_headers).json()} == {theirs}
    assert client.get(f"/api/alerts/rules/{mine}/events", headers=member_headers).status_code == 404
    events = client.get(f"/api/alerts/rules/{mine}/events", headers=owner_headers).json()
    assert events and events[0]["values"]["close"] == 101.5
    assert client.get(f"/api/screener/runs/{run_id}", headers=member_headers).status_code == 404
    assert client.get(f"/api/screener/runs/{run_id}", headers=owner_headers).status_code == 200
    member_feed = {n["id"] for n in client.get("/api/notifications", headers=member_headers).json()}
    owner_feed = {n["id"] for n in client.get("/api/notifications", headers=owner_headers).json()}
    assert note.id not in member_feed and note.id in owner_feed and member_feed       # the org-wide one is still everyone's
    assert client.post(f"/api/notifications/{note.id}/read", headers=member_headers).status_code == 404


# --- H2: OI banner, OI alerts, shared intraday lake bars, recorded chains ---------------------------------------------------

def test_oi_banner_shared_intraday_lake_and_recorded_chains_are_off_with_the_flag_on(monkeypatch):
    from app.option_chain import oi_alerts
    headers, me = _owner(_email("h2"))
    t = me["tenant_id"]
    assert client.get("/api/option-chain/banners", headers=headers).status_code == 200
    _byob(t)
    for path in ("/api/option-chain/banners", "/api/option-chain/NIFTY/banner", "/api/option-chain/NIFTY/history",
                 "/api/option-chain/NIFTY/strikes"):
        res = client.get(path, headers=headers)
        assert res.status_code == 409 and res.headers["X-Data-Tier"] == "C", path
    start, end = "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"
    res = client.get(f"/api/market-data/history?instrument_key=NSE:TCS&timeframe=1m&start={start}&end={end}", headers=headers)
    assert res.status_code == 409 and res.headers["X-Data-Tier"] == "C"
    assert client.get(f"/api/market-data/history?instrument_key=NSE:TCS&timeframe=1d&start={start}&end={end}", headers=headers).status_code == 200
    res = client.get(f"/api/backtest/option-chain/snapshots?underlying=NIFTY&start={start}&end={end}", headers=headers)
    assert res.status_code == 409

    called = []

    async def settings_for(session, tenant_id, underlying):
        called.append(tenant_id)
        return SimpleNamespace(alerts=SimpleNamespace(enabled=True, types=[]))
    monkeypatch.setattr(oi_alerts.snapshots, "settings_for", settings_for)

    async def go():
        async with _session_factory() as session:
            return await oi_alerts.evaluate_tenant(session, t, "NIFTY", datetime.now(UTC), market_open=True)
    assert run(go()) == [] and called == [t]


def test_vision_audit_reads_only_daily_lake_bars_with_the_flag_on(monkeypatch):
    from app.ai import vision_audit
    asked = []

    async def candles_as_of(session, key, tf, start, end):
        asked.append(tf)
        return []
    monkeypatch.setattr("app.market_lake.asof.candles_as_of", candles_as_of)
    run(vision_audit.lake_bars(None, "NSE", "TCS", "5min", datetime.now(UTC), daily_only=True))
    assert asked == ["1d"]
    asked.clear()
    run(vision_audit.lake_bars(None, "NSE", "TCS", "5min", datetime.now(UTC)))
    assert asked == ["5m", "1m"]


# --- M3 / M4: the worker --------------------------------------------------------------------------------------------------

class _FakeStream:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _zerodha_tenant(email):
    from tests.test_trading_worker import _tenant
    t = _tenant(email, with_credentials=False)
    client.post("/api/broker/zerodha/credentials", headers=t["headers"], json={"api_key": KITE_KEY, "api_secret": KITE_SECRET, "access_token": "t"})

    async def mark():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == t["tenant_id"]))
            rec.token_status, rec.token_expires_at = "VALID", datetime.now(UTC) + timedelta(hours=8)
            await session.commit()
    run(mark())
    return t


def test_worker_drops_every_org_stream_and_waits_for_confirmed_data_while_exits_run(monkeypatch):
    from app.market_data.fanin import SessionSymbols, UserSessionFanIn
    from app.workers import trading_worker as tw
    from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _force_signal, _signal, _trades, _worker
    t = _zerodha_tenant(_email("m4"))
    dep_id = _deploy(t, broker_name="zerodha")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    worker.byob = UserSessionFanIn(symbols=SessionSymbols(), stream_factory=lambda a: None, streaming=False)
    _force_signal(monkeypatch, _signal)
    run(worker.run_cycle(OPEN_NOW))                                     # flag off: enters as before
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1 and trades[0].exit_time is None
    streams = {f"{t['tenant_id']}:zerodha": _FakeStream(), f"{t['tenant_id']}:zerodha@b": _FakeStream(), "999999:upstox": _FakeStream()}
    worker.streams._streams.update(streams)
    _byob(t["tenant_id"])
    broker.ltp = 97.0                                                   # below the 98 stop
    run(worker.run_cycle(OPEN_NOW + timedelta(minutes=1)))
    trades = _trades(t["tenant_id"])
    assert trades[0].exit_time is not None                              # the exit still ran
    assert len(trades) == 1                                             # no new entry on Zerodha data
    dep = run(_get_dep(dep_id))
    assert dep.last_error == tw.WAITING_FOR_CONFIRMED_DATA
    assert streams[f"{t['tenant_id']}:zerodha"].stopped and streams[f"{t['tenant_id']}:zerodha@b"].stopped
    assert not streams["999999:upstox"].stopped and "999999:upstox" in worker.streams._streams     # another organisation's
    worker.streams._streams.clear()


def test_worker_drops_org_streams_also_on_the_no_session_early_return(monkeypatch):
    from app.market_data.fanin import SessionSymbols, UserSessionFanIn
    from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _tenant, _worker
    t = _tenant(_email("m3"), token_status="EXPIRED")
    _deploy(t)
    broker = _FakeBroker()

    async def rejecting_profile():
        from app.brokers.exceptions import BrokerAuthenticationError
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)
    worker.byob = UserSessionFanIn(symbols=SessionSymbols(), stream_factory=lambda a: None, streaming=False)
    stale = _FakeStream()
    worker.streams._streams[f"{t['tenant_id']}:upstox"] = stale
    _byob(t["tenant_id"])
    run(worker.run_cycle(OPEN_NOW))
    assert stale.stopped and f"{t['tenant_id']}:upstox" not in worker.streams._streams


async def _get_dep(dep_id):
    async with _session_factory() as session:
        return await session.get(StrategyDeploymentRecord, dep_id)


# --- L2 / L3 / L4 ---------------------------------------------------------------------------------------------------------

def test_greeks_spot_and_preview_legs_respect_the_budget(monkeypatch):
    from app.core import config as app_config
    from app.deployments import routes as dep_routes
    monkeypatch.setattr(app_config, "BYOB_SESSION_SYMBOL_CAP", 1)
    headers, me = _owner(_email("l2"))
    _store_credentials(me)
    feed = _Feed(10.0)
    _route(monkeypatch, {me["tenant_id"]: feed})
    _byob(me["tenant_id"])

    async def add_trade():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="NIFTY26OCT25000CE", strategy_id="s",
                                    direction="LONG", entry_time=datetime.now(UTC), entry_price=100.0, quantity=50, stop_loss=90.0, exchange="NFO",
                                    instrument_kind="OPTION", underlying_symbol="NIFTY 50", expiry=date(2026, 10, 27)))
            await session.commit()
    run(add_trade())
    body = client.get("/api/positions/greeks", headers=headers).json()
    # Budget of one symbol: the contract takes it, so the spot (the underlying) is outside - skipped with the reason, not read.
    assert body["skipped"] and "outside your Upstox session budget" in body["skipped"][0]["reason"]
    assert not any(r.startswith("ltp:NIFTY 50") for r in feed.reads)

    async def legs():
        async with _session_factory() as session:
            return await dep_routes._legs_refusal(session, await session.get(User, me["id"]), ["NIFTY26OCT25000CE", "NIFTY26OCT25100CE"])
    assert "outside your Upstox session budget" in run(legs())


def test_exit_price_lookup_uses_the_positions_exchange(monkeypatch):
    from app.ai.worker_bridge import price_lookup_for_tenant
    headers, me = _owner(_email("l3"))
    _store_credentials(me)
    _byob(me["tenant_id"])
    seen = []

    async def get_ltp(self, symbol, exchange="NSE", now=None):
        seen.append((symbol, exchange))
        return 42.0
    monkeypatch.setattr("app.market_data.service.MarketDataService.get_ltp", get_ltp)
    _route(monkeypatch, {me["tenant_id"]: _Feed(1.0)})

    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="BANKNIFTY26OCTFUT", strategy_id="s",
                                    direction="LONG", entry_time=datetime.now(UTC), entry_price=1.0, quantity=1, stop_loss=0.5, exchange="NFO",
                                    instrument_kind="FUTURE"))
            await session.commit()
            lookup = await price_lookup_for_tenant(session, me["tenant_id"], user=await session.get(User, me["id"]))
            return await lookup("BANKNIFTY26OCTFUT"), await lookup("RELIANCE")
    assert run(go()) == (42.0, 42.0)
    assert seen == [("BANKNIFTY26OCTFUT", "NFO"), ("RELIANCE", "NSE")]


def test_ltp_body_is_stale_when_the_time_is_unknown(monkeypatch):
    headers, me = _owner(_email("l4"))
    _store_credentials(me)
    _route(monkeypatch, {me["tenant_id"]: _Feed(10.0, quote_ts=None)})
    flag_off = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()
    assert flag_off["stale"] is False and "data_tier" not in flag_off                  # flag off: as before
    _byob(me["tenant_id"])
    body = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()
    assert body["timestamp"] is None and body["stale"] is True and body["stale_reason"] == body["data_tier"]["stale_reason"]


# --- round 3: only data-bearing notifications are private; deliveries; remaining OI / coverage routes ---------------------

def test_safety_notifications_stay_organisation_wide_and_deliveries_follow_the_feed():
    from app.brokers.login_reminder import DATA_TITLE_PREFIX
    from app.db.models import AlertChannelRecord
    owner_headers, owner = _owner(_email("r3"))
    member_headers, member = _member(owner_headers)
    t = owner["tenant_id"]

    async def seed():
        async with _session_factory() as session:
            session.add(AlertChannelRecord(tenant_id=t, channel_type="webhook", enabled=True, min_severity="INFO", encrypted_config="x"))
            await session.commit()
            emergency = await notify(session, t, NotificationType.EMERGENCY_EXIT, "Emergency exit: daily loss limit", user_id=member["id"])
            failure = await notify(session, t, NotificationType.SYSTEM_FAILURE, "Deployment auto-paused", user_id=member["id"])
            alert = await notify(session, t, NotificationType.SCREEN_ALERT, "Breakout: TCS 4,012.5", user_id=member["id"])
            reminder = await notify(session, t, NotificationType.TOKEN_EXPIRED, f"{DATA_TITLE_PREFIX}upstox before the open", user_id=member["id"])
            await session.commit()
            return emergency.id, failure.id, alert.id, reminder.id
    emergency, failure, alert, reminder = run(seed())
    _byob(t)
    owner_feed = {n["id"] for n in client.get("/api/notifications", headers=owner_headers).json()}
    member_feed = {n["id"] for n in client.get("/api/notifications", headers=member_headers).json()}
    assert {emergency, failure} <= owner_feed                       # a member-triggered emergency exit reaches the OWNER
    assert alert not in owner_feed and reminder not in owner_feed   # the member's own data stays theirs
    assert {emergency, failure, alert, reminder} <= member_feed
    owner_deliveries = {d["notification_id"] for d in client.get("/api/alert-channels/deliveries?limit=200", headers=owner_headers).json()}
    assert emergency in owner_deliveries and alert not in owner_deliveries
    member_deliveries = {d["notification_id"] for d in client.get("/api/alert-channels/deliveries?limit=200", headers=member_headers).json()}
    assert alert in member_deliveries


def test_oi_settings_alert_history_and_chain_coverage_are_gated():
    headers, me = _owner(_email("r3oi"))
    assert client.get("/api/backtest/option-chain/coverage", headers=headers).status_code == 200
    _byob(me["tenant_id"])
    for path in ("/api/option-chain/NIFTY/settings", "/api/option-chain/NIFTY/alerts", "/api/backtest/option-chain/coverage"):
        res = client.get(path, headers=headers)
        assert res.status_code == 409 and res.headers["X-Data-Tier"] == "C", path
    res = client.put("/api/option-chain/NIFTY/settings", headers=headers, json={"enabled": True})
    assert res.status_code == 409
