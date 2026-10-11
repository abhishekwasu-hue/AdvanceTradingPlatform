"""OI Banner O4b: read-only Telegram buttons under OI alerts (links + snooze / mute callbacks, never an order), the
day's banner digest, and the test alert."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.alerts import dispatcher
from app.core import config as app_config
from app.db.models import NotificationRecord, OIAlertLogRecord, OIBannerSettingRecord, OIBannerStateRecord
from app.option_chain import oi_alerts
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_o2_oi_snapshots import T0, UND
from tests.test_phase_be_telegram_inbound import _Telegram, _no_shared_rate_limit, _post, _setup  # noqa: F401 - fixture


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            for model in (OIAlertLogRecord, OIBannerStateRecord, OIBannerSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(go())
    yield


def test_keyboard_is_read_only_links_and_snooze_only():
    assert oi_alerts.telegram_keyboard(UND, "NSE", "/", inbound=False) is None                  # relative URL, no inbound
    links = oi_alerts.telegram_keyboard("NIFTY BANK", "NSE", "https://app.example.com/", inbound=False)
    assert links["inline_keyboard"] == [[{"text": "Open option chain", "url": "https://app.example.com/option-chain?underlying=NIFTY%20BANK"},
                                         {"text": "Open chart", "url": "https://app.example.com/?chart=NIFTY%20BANK&tf=5min&exchange=NSE"}]]
    both = oi_alerts.telegram_keyboard(UND, "BSE", "https://app.example.com", inbound=True)
    callbacks = [b["callback_data"] for row in both["inline_keyboard"] for b in row if "callback_data" in b]
    assert callbacks == [f"oi:s:{UND}", f"oi:m:{UND}"] and all(len(c) <= 64 for c in callbacks)
    texts = " ".join(b["text"] for row in both["inline_keyboard"] for b in row).lower()
    for word in ("buy", "sell", "order", "exit", "approve", "trade"):
        assert word not in texts


def test_snooze_and_mute_from_telegram():
    async def go(data, now):
        async with _session_factory() as session:
            text = await oi_alerts.snooze_from_telegram(session, 1, data, None, now)
            row = await session.scalar(select(OIBannerSettingRecord).where(OIBannerSettingRecord.tenant_id == 1, OIBannerSettingRecord.underlying == UND))
            return text, row.snoozed_until if row else None
    text, until = _run(go(f"oi:s:{UND}", T0))
    assert "1 hour" in text and until.replace(tzinfo=timezone.utc) == T0 + timedelta(hours=1)
    text, until = _run(go(f"oi:m:{UND}", T0))
    assert "rest of today" in text and until.replace(tzinfo=timezone.utc) == datetime(2026, 3, 10, 18, 30, tzinfo=timezone.utc)   # next IST midnight
    assert _run(go("oi:x:" + UND, T0))[0] == "This button is not valid." and _run(go("oi:s:BAD;X", T0))[0] == "This button is not valid."


def test_a_telegram_snooze_button_needs_a_whitelisted_authorised_sender(monkeypatch):
    telegram = _Telegram()
    t = _setup("o4b-tg@example.com", monkeypatch, telegram)

    def press(chat, sender):
        return {"update_id": 9, "callback_query": {"id": "cb1", "data": f"oi:s:{UND}", "from": {"id": int(sender)},
                                                   "message": {"message_id": 7, "chat": {"id": int(chat)}, "text": "alert"}}}
    stranger = _post(t, press("4242", "4242")).json()
    assert stranger["handled"] == "ignored"
    ok = _post(t, press("987654", "987654")).json()
    assert ok["handled"] == "callback" and "snoozed for 1 hour" in ok["reply"]

    async def snoozed():
        async with _session_factory() as session:
            row = await session.scalar(select(OIBannerSettingRecord).where(OIBannerSettingRecord.tenant_id == t["tenant_id"]))
            return row.snoozed_until
    assert _run(snoozed()) is not None


def test_an_oi_alert_goes_to_telegram_with_the_buttons(monkeypatch):
    telegram = _Telegram()
    t = _setup("o4b-dispatch@example.com", monkeypatch, telegram)
    monkeypatch.setattr(app_config, "FRONTEND_URL", "https://app.example.com")

    async def go():
        async with _session_factory() as session:
            await oi_alerts.send_test(session, t["tenant_id"], UND)
        async with _session_factory() as session:
            return await dispatcher.dispatch_pending(session, client=telegram.client(), tenant_id=t["tenant_id"])
    sent, failed = _run(go())
    assert (sent, failed) == (1, 0)
    msg = [c for c in telegram.calls if c["method"] == "sendMessage"][-1]
    rows = msg["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["url"].endswith(f"/option-chain?underlying={UND}") and rows[1][0]["callback_data"] == f"oi:s:{UND}"
    assert "OI alerts: test" in msg["text"]


def test_the_day_digest_is_sent_once_after_its_time():
    from tests.test_phase_l_ai import _owner
    headers, me = _owner("o4b-digest@example.com")
    body = {"enabled": True, "overrides": {"alerts": {"enabled": True, "digest_time": "15:45"}}}
    assert client.put(f"/api/option-chain/{UND}/settings", json=body, headers=headers).status_code == 200

    async def seed():
        async with _session_factory() as session:
            for i, d in enumerate(["NEUTRAL", "BULLISH", "BULLISH", "MIXED"]):
                session.add(OIBannerStateRecord(tenant_id=me["tenant_id"], underlying=UND, slot_start=T0 + timedelta(minutes=5 * i), direction=d,
                                                stable_direction="BULLISH", stable_strength="Strong", pcr_band="SIDEWAYS", max_pain=24500.0,
                                                max_pain_ref=24500.0, dte=16, wall=None, message=f"m{i}", created_at=T0))
            await session.commit()
    _run(seed())

    async def digest(now):
        async with _session_factory() as session:
            return await oi_alerts.send_digests(session, now)
    assert _run(digest(datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc))) == 0                    # 15:30 IST: before its time
    assert _run(digest(datetime(2026, 3, 10, 10, 20, tzinfo=timezone.utc))) == 1                   # 15:50 IST
    assert _run(digest(datetime(2026, 3, 10, 11, 0, tzinfo=timezone.utc))) == 0                    # once a day

    async def note():
        async with _session_factory() as session:
            return await session.scalar(select(NotificationRecord).where(NotificationRecord.tenant_id == me["tenant_id"]).order_by(NotificationRecord.id.desc()))
    n = _run(note())
    assert "09:20 NEUTRAL → 09:25 BULLISH → 09:35 MIXED" in n.message and json.loads(n.metadata_json)["alert_type"] == "DIGEST"


def test_test_alert_endpoint_is_for_members():
    from tests.test_phase_l_ai import _owner
    headers, me = _owner("o4b-test@example.com")
    r = client.post(f"/api/option-chain/{UND}/alerts/test", headers=headers)
    assert r.status_code == 200 and r.json()["notification_id"]
    assert client.post(f"/api/option-chain/{UND}/alerts/test").status_code == 401
