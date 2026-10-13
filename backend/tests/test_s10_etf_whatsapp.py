"""S10 (SCREENER_SPEC §5, Phase 0 slice): the ETF screen (J) on the shared end-of-day history (flag `screener_etf`) and
the messaging seam - a WhatsApp Business channel (approved template + opt-in, paid plans) and India's DLT fields on the
SMS channel (flag `alerts_whatsapp_dlt`). Both flags are off by default."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from app.alerts import dispatcher
from app.alerts.channels import SmsConfig, WhatsAppConfig, parse_config
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import MdCandleRecord, SecurityRecord
from app.market_lake.bhavcopy_backfill import bar_end
from app.notifications.service import notify
from app.platform import controls
from app.screener import etf
from tests.test_alerts import _dispatch, _put
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _upgrade_plan

SOURCE = "test_s10"


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _user(email, plan=None):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if plan:
        _upgrade_plan(me["tenant_id"], plan)
    return headers, me


# --- the ETF screen -----------------------------------------------------------------------------------------------------

def test_index_keys_try_the_underlying_name_and_its_plain_form():
    keys = etf.index_keys("Nifty Bank TRI")
    assert keys[0].startswith("NSE_INDEX:") and len(keys) == 2
    assert etf.index_keys("NIFTY 50")[0] in etf.index_keys("NIFTY 50 Index")
    assert etf.index_keys(None) == [] and etf.index_keys("  ") == []


def _frame(closes, days, volume=1000.0):
    return pd.DataFrame({"close": closes, "volume": [volume] * len(closes)}, index=days)


def test_measure_gives_change_index_change_and_tracking_difference():
    days = [d.date() for d in pd.bdate_range(end="2019-12-30", periods=30)]
    etf_frame = _frame(np.linspace(100, 110, 30), days)                       # +10% over 29 sessions
    index_frame = _frame(np.linspace(1000, 1105, 30), days)
    row = etf.measure(etf.EtfRow("ETFX", "X ETF", "NIFTY 50", index_key="NSE_INDEX:NIFTY50"), etf_frame, index_frame, 29)
    assert row.session == "2019-12-30" and row.close == 110.0 and row.change_pct == 10.0 and row.index_change_pct == 10.5
    assert row.tracking_diff_pct == -0.5 and row.traded_value == round(float((etf_frame["close"].iloc[-20:] * 1000).mean()), 2)
    short = etf.measure(etf.EtfRow("S", "S", None), etf_frame.iloc[:10], None, 20)
    assert short.change_pct is None and "sessions in the shared history" in short.notes[0]
    alone = etf.measure(etf.EtfRow("A", "A", "Gold"), etf_frame, None, 5)
    assert alone.change_pct is not None and alone.tracking_diff_pct is None and "not in the shared history" in alone.notes[0]
    assert etf.measure(etf.EtfRow("E", "E", None), etf_frame.iloc[:0], None, 5).notes == ["no daily bars in the shared history"]


def test_screen_filters_and_sorts_without_ranking_quality():
    rows = [etf.EtfRow("AAA", "Alpha Nifty ETF", "NIFTY 50", traded_value=5e7, change_pct=2.0, tracking_diff_pct=-0.1),
            etf.EtfRow("BBB", "Beta Bank ETF", "NIFTY BANK", traded_value=9e7, change_pct=-1.0, tracking_diff_pct=-0.6),
            etf.EtfRow("CCC", "Gold ETF", "Gold", traded_value=1e6)]
    assert [r.symbol for r in etf.screen(rows)] == ["BBB", "AAA", "CCC"]
    assert [r.symbol for r in etf.screen(rows, min_traded_value=1e7)] == ["BBB", "AAA"]
    assert [r.symbol for r in etf.screen(rows, query="bank")] == ["BBB"]
    assert [r.symbol for r in etf.screen(rows, max_abs_tracking=0.5)] == ["AAA"]
    assert [r.symbol for r in etf.screen(rows, sort="tracking_diff_pct")] == ["AAA", "BBB", "CCC"]


@pytest.fixture
def seeded():
    def clear():
        async def go():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.source == SOURCE))
                await session.execute(delete(SecurityRecord).where(SecurityRecord.source == SOURCE))
                await session.commit()
        _run(go())
    clear()
    end = (datetime.now(timezone.utc) - timedelta(days=10)).date()
    days = [d.date() for d in pd.bdate_range(end=end, periods=40)]

    async def go():
        from app.universe.fo_eod import code_for
        async with _session_factory() as session:
            session.add(SecurityRecord(isin="INS10ETF0001", symbol="TXSTENETF", name="Test Nifty ETF", series="EQ", is_etf=True,
                                       underlying="NIFTY 50 Index", source=SOURCE, checksum="t"))
            session.add(SecurityRecord(isin="INS10EQ00001", symbol="TXSTENEQ", name="Not an ETF", series="EQ", is_etf=False, source=SOURCE, checksum="t"))
            for key, closes in (("NSE:TXSTENETF", np.linspace(100, 120, 40)), (f"NSE_INDEX:{code_for('NIFTY 50')}", np.linspace(1000, 1210, 40))):
                for d, c in zip(days, closes):
                    session.add(MdCandleRecord(instrument_key=key, timeframe="day", ts=bar_end(d), source=SOURCE, version=1, open=float(c),
                                               high=float(c) + 1, low=float(c) - 1, close=float(c), volume=5000,
                                               ingested_at=bar_end(d) + timedelta(hours=3)))
            await session.commit()
    _run(go())
    yield days
    clear()


def test_etf_rows_from_the_shared_history(seeded):
    async def go():
        async with _session_factory() as session:
            return await etf.etf_rows(session, sessions=20)
    rows = {r.symbol: r for r in _run(go())}
    assert "TXSTENEQ" not in rows
    row = rows["TXSTENETF"]
    assert row.session == seeded[-1].isoformat() and row.index_key and row.tracking_diff_pct is not None
    assert row.change_pct > 0 and row.index_change_pct > 0


def test_the_etf_route_is_flagged_and_member_readable(seeded):
    from app.screener import etf_routes
    etf_routes._CACHE.clear()
    headers, _ = _user("s10-etf@example.com")
    _set_flags(screener_v2=True)
    try:
        off = client.get("/api/screener/etf", headers=headers)
        assert off.status_code == 503 and off.headers.get("X-Feature-Disabled") == etf.FLAG
        _set_flags(**{etf.FLAG: True})
        body = client.get("/api/screener/etf?q=TXSTEN&sessions=20", headers=headers).json()
        assert [r["symbol"] for r in body["rows"]] == ["TXSTENETF"] and body["note"] == etf.NOTE
        assert client.get("/api/screener/etf?sort=best", headers=headers).status_code == 422
        loads = []
        real = etf.load

        async def counting(session, now=None):
            loads.append(1)
            await asyncio.sleep(0.05)                          # a slow load: the concurrent requests wait for it
            return await real(session, now=now)
        etf_routes._CACHE.clear()
        import unittest.mock as um
        with um.patch.object(etf, "load", counting):
            seven = client.get("/api/screener/etf?sessions=7", headers=headers).json()
            twenty = client.get("/api/screener/etf?sessions=20&q=TXSTEN", headers=headers).json()
        assert loads == [1]                                                               # one lake read, every window
        assert seven["sessions"] == 7 and twenty["rows"][0]["change_pct"] == body["rows"][0]["change_pct"]

        async def concurrent():
            from app.screener.etf_routes import _history
            etf_routes._CACHE.clear()
            loads.clear()

            async def one():
                async with _session_factory() as session:
                    return await _history(session)
            with um.patch.object(etf, "load", counting):
                got = await asyncio.gather(*(one() for _ in range(5)))
            return got
        got = _run(concurrent())
        assert loads == [1] and all(g is got[0] for g in got)                             # single flight
        assert any(d.dependency is etf_routes.etf_rate_limit for d in etf_routes.router.routes[0].dependencies)   # the per-user limit
    finally:
        _set_flags(screener_v2=False, **{etf.FLAG: False})
    from app.screener.taxonomy import advice_words
    assert advice_words(etf.NOTE) == []
    assert etf.FLAG in controls.DEFAULT_OFF_FLAGS and "alerts_whatsapp_dlt" in controls.DEFAULT_OFF_FLAGS


# --- WhatsApp and SMS DLT -----------------------------------------------------------------------------------------------

WA = {"url": "https://graph.facebook.com/v19.0/123/messages", "headers": {"Authorization": "Bearer SECRET"},
      "body_template": '{"messaging_product":"whatsapp","to":"{to}","type":"template","template":{"name":"{template}",'
                       '"language":{"code":"{language}"},"components":[{"type":"body","parameters":[{"type":"text","text":"{text}"}]}]}}',
      "template_name": "atp_alert", "to_numbers": ["+91 98123 45678"], "opt_in_confirmed": True}
SMS_DLT = {"url": "https://control.msg91.com/api/v5/flow/", "headers": {"authkey": "K"}, "dlt_template_id": "1107160000000000001",
           "dlt_entity_id": "1201160000000000001", "sender_id": "ATPALR",
           "body_template": '{"template_id":"{dlt_template_id}","sender":"{sender_id}","recipients":[{"mobiles":"{to}","message":"{text}"}]}',
           "to_numbers": ["+919812345678"]}


class _N:
    title = 'Stop "hit"'
    message = "m"
    severity = "CRITICAL"
    event_type = "STOP_LOSS_HIT"
    id = 1
    created_at = datetime.now(timezone.utc)


def test_whatsapp_config_needs_an_approved_template_and_opt_in():
    cfg = parse_config("WHATSAPP", WA)
    assert isinstance(cfg, WhatsAppConfig) and cfg.to_numbers == ["+919812345678"]
    for bad in ({**WA, "opt_in_confirmed": False}, {**WA, "body_template": '{"text":"{text}"}'}, {**WA, "template_name": "Bad Name"},
                {**WA, "url": "http://insecure.example/x"}, {**WA, "to_numbers": ["x"]}):
        with pytest.raises(ValueError):
            parse_config("WHATSAPP", bad)
    body, _ = dispatcher.render_sms(cfg, _N(), "+919812345678")
    parsed = json.loads(body)
    assert parsed["template"]["name"] == "atp_alert" and parsed["template"]["language"]["code"] == "en"
    assert 'Stop "hit"' in parsed["template"]["components"][0]["parameters"][0]["text"]


def test_sms_dlt_fields_fill_their_placeholders_and_a_used_one_must_be_set():
    cfg = parse_config("SMS", SMS_DLT)
    assert isinstance(cfg, SmsConfig) and cfg.uses_dlt()
    parsed = json.loads(dispatcher.render_sms(cfg, _N(), "+919812345678")[0])
    assert parsed["template_id"] == "1107160000000000001" and parsed["sender"] == "ATPALR"
    with pytest.raises(ValueError):
        parse_config("SMS", {**SMS_DLT, "dlt_template_id": None})
    plain = parse_config("SMS", {k: v for k, v in SMS_DLT.items() if k not in ("dlt_template_id", "dlt_entity_id", "sender_id")}
                         | {"body_template": '{"m":"{text}"}'})
    assert not plain.uses_dlt()


def test_the_channels_are_gated_by_the_flag_and_whatsapp_by_a_paid_plan():
    free, _ = _user("s10-free@example.com")
    paid, me = _user("s10-paid@example.com", plan="pro")
    assert _put(paid, "whatsapp", WA).status_code == 403                                  # flag off
    assert _put(paid, "sms", SMS_DLT).status_code == 403
    assert _put(paid, "sms", {**SMS_DLT, "dlt_template_id": None, "dlt_entity_id": None, "sender_id": None,
                              "body_template": '{"m":"{text}"}'}).status_code == 200      # a plain SMS channel is untouched
    _set_flags(alerts_whatsapp_dlt=True)
    try:
        assert _put(free, "whatsapp", WA).status_code == 402
        r = _put(paid, "whatsapp", WA, min_severity="WARNING")
        assert r.status_code == 200, r.text
        assert r.json()["config"]["template_name"] == "atp_alert" and r.json()["config"]["opt_in_confirmed"] is True
        assert "SECRET" not in r.text
        assert _put(paid, "sms", SMS_DLT).status_code == 200

        seen: List[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True})

        async def raise_alert():
            async with _session_factory() as session:
                await notify(session, me["tenant_id"], NotificationType.DAILY_LOSS_LIMIT, title="Daily loss limit", message="-2.1%",
                             severity=NotificationSeverity.CRITICAL)
        _run(raise_alert())
        sent, failed = _dispatch(me["tenant_id"], httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        assert failed == 0 and sent == 2
        wa = next(json.loads(r.content) for r in seen if "graph.facebook.com" in str(r.url))
        assert wa["template"]["name"] == "atp_alert" and wa["to"] == "+919812345678"
        sms = next(json.loads(r.content) for r in seen if "msg91" in str(r.url))
        assert sms["template_id"] == "1107160000000000001"
    finally:
        _set_flags(alerts_whatsapp_dlt=False)


def test_whatsapp_opt_in_is_required_not_defaulted():
    missing = {k: v for k, v in WA.items() if k != "opt_in_confirmed"}
    with pytest.raises(ValueError):
        parse_config("WHATSAPP", missing)
    with pytest.raises(ValueError):
        parse_config("WHATSAPP", {**WA, "headers": {"Authorization": "Bearer x\r\nX: y"}})
    for bad in ({"url": "https://evil.example\\@graph.facebook.com/v19.0/1/messages"}, {"content_type": "application/json\r\nX: y"},
                {"headers": {"Host": "graph.facebook.com"}}):
        with pytest.raises(ValueError):
            parse_config("WHATSAPP", {**WA, **bad})
    from app.alerts.channels import merge_secrets
    moved = merge_secrets("WHATSAPP", {**WA, "url": "https://collector.example/x", "headers": {}}, dict(WA))
    assert moved["headers"] == {}                                    # the stored token never follows a moved gateway


def test_a_plan_downgrade_or_the_flag_turned_off_stops_delivery_with_a_reason():
    paid, me = _user("s10-kill@example.com", plan="pro")
    _set_flags(alerts_whatsapp_dlt=True)
    try:
        assert _put(paid, "whatsapp", WA).status_code == 200
        assert _put(paid, "sms", SMS_DLT).status_code == 200
    finally:
        _set_flags(alerts_whatsapp_dlt=False)
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async def raise_alert():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="T", message="m", severity=NotificationSeverity.CRITICAL)

    async def suppressed():
        from sqlalchemy import select

        from app.db.models import AlertDeliveryRecord
        async with _session_factory() as session:
            return [(d.status, d.reason_code, d.last_error) for d in await session.scalars(
                select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == me["tenant_id"]).order_by(AlertDeliveryRecord.id))]
    _run(raise_alert())                                                    # flag off: the kill switch
    _dispatch(me["tenant_id"], httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rows = _run(suppressed())
    assert not seen and len(rows) == 2 and all(s == "SUPPRESSED" and code == "feature_off" and "not turned on" in why for s, code, why in rows)
    assert client.post("/api/alert-channels/whatsapp/test", headers=paid).status_code == 403

    _set_flags(alerts_whatsapp_dlt=True)                                   # flag on, plan downgraded to Free
    try:
        _upgrade_plan(me["tenant_id"], "free")
        _run(raise_alert())
        _dispatch(me["tenant_id"], httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        rows = _run(suppressed())[2:]
        whatsapp = [r for r in rows if "paid plan" in (r[2] or "")]
        assert len(whatsapp) == 1 and whatsapp[0][0] == "SUPPRESSED"
        assert [h for h in seen if "graph.facebook.com" in str(h.url)] == []   # WhatsApp sent nothing; DLT SMS may go
        assert client.post("/api/alert-channels/whatsapp/test", headers=paid).status_code == 402
    finally:
        _set_flags(alerts_whatsapp_dlt=False)


def test_one_undecryptable_channel_never_starves_another_organisation_s_deliveries():
    """S10 review MED: the S10 gate (and the config decrypt it needs) runs inside the per-row try - a corrupt SMS channel
    of organisation A becomes a retry on A's row; organisation B's webhook delivery in the same drain is still SENT, and
    A's Settings test button answers ok=False instead of a 500."""
    a, me_a = _user("s10-corrupt-a@example.com", plan="pro")
    b, me_b = _user("s10-corrupt-b@example.com", plan="pro")
    assert _put(a, "sms", {"url": "https://control.msg91.com/api/v5/flow/", "headers": {"authkey": "K"}, "content_type": "application/json",
                           "body_template": '{"to":"{to}","text":"{text}"}', "to_numbers": ["+919812345678"]}).status_code == 200
    assert _put(b, "webhook", {"url": "https://hooks.example.com/s10", "secret": "s" * 32}).status_code == 200

    async def corrupt_and_raise():
        from sqlalchemy import select

        from app.db.models import AlertChannelRecord
        async with _session_factory() as session:
            row = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == me_a["tenant_id"],
                                                                        AlertChannelRecord.channel_type == "SMS"))
            row.encrypted_config = "not-a-ciphertext"
            await session.commit()
            for me in (me_a, me_b):
                await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="T", message="m",
                             severity=NotificationSeverity.CRITICAL)
    _run(corrupt_and_raise())
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async def drain_everyone():
        async with _session_factory() as session:            # platform-wide, as the worker drains it
            return await dispatcher.dispatch_pending(session, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), limit=500)
    _run(drain_everyone())                                   # never raises

    async def rows(tenant_id):
        from sqlalchemy import select

        from app.db.models import AlertDeliveryRecord
        async with _session_factory() as session:
            return [(d.status, d.attempts, d.last_error) for d in await session.scalars(
                select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tenant_id))]
    assert [r[0] for r in _run(rows(me_b["tenant_id"]))] == ["SENT"]
    assert any("hooks.example.com" in str(r.url) for r in seen)
    [(status, attempts, error)] = _run(rows(me_a["tenant_id"]))
    assert status == "PENDING" and attempts == 1 and error                      # a retry with the reason, not a crash
    test = client.post("/api/alert-channels/sms/test", headers=a)
    assert test.status_code == 200 and test.json()["ok"] is False
