"""U8 EV1/EV2 through the API and the alert pipeline (flag `evidence_charts`, off by default):

* flag off: a run stores no evidence and carries no evidence ids; every evidence route answers 404 (hidden);
* flag on: a run stores one pack per MATCHED symbol (same bars, parameters and ranks as the run); the pack, its image
  and "send to my Telegram" are readable by the run's organisation only - another organisation gets 404; with BT1
  `byob_data_tier` on, only the user who ran it (a teammate gets 404);
* Telegram: only to the caller's own linked private chat (never the organisation's group chat), never with the token;
* alerts: a fired bar-close rule stores the pack with the event; its Telegram delivery to the rule creator's linked chat
  carries the image after the text; flag off -> text only; the organisation's group chat never gets the image;
* retention: evidence ages out after `screen_evidence_days`.
"""
import asyncio
import re
import types
from datetime import datetime, timedelta, timezone
from typing import List
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.alerts import barclose, dispatcher, engine
from app.alerts.channels import TelegramConfig, encrypt_config
from app.core.models import OHLCVBar
from app.db.models import (AlertChannelRecord, AlertEventRecord, AlertRuleRecord, NotificationLinkRecord, NotificationPolicyRecord,
                           ScreenEvidenceRecord)
from app.platform import controls
from app.retention import service as retention
from app.retention.policy import load_policy
from app.screener.runtime import SymbolData
from app.secrets_store.envelope import ensure_tenant_key
from tests.test_auth_api import _session_factory, client
from tests.test_passwords import _headers, _tenant_pro
from tests.test_phase_l_ai import _owner

BOT_TOKEN = "123456:EVIDENCE-test-token"
GROUP_CHAT = "-100777"
USER_CHAT = "4242"


def _run(coro):
    return asyncio.run(coro)


def _set(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def screener_on():
    _set(screener_v2=True, evidence_charts=False, byob_data_tier=False)
    yield
    _set(screener_v2=False, evidence_charts=False, byob_data_tier=False)


def _bars(step: float, days: int = 80) -> List[OHLCVBar]:
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    closes = 100 + np.arange(days) * step + np.sin(np.arange(days) / 4)
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=c - 0.5, high=c + 1, low=c - 1, close=float(c), volume=1000.0 + i)
            for i, (t, c) in enumerate(zip(idx, closes))]


def _serve_bars(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        return _bars(1.0 if symbol.startswith("UP") else -1.0)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


SCREEN = "close > SMA(close, 20) AND RSI(14) > 50"


def _screen_run(headers, symbols=("UP", "DOWN", "UP2"), source=SCREEN):
    out = client.post("/api/screener/run", json={"source": source, "symbols": list(symbols)}, headers=headers)
    assert out.status_code == 200, out.text
    return out.json()


def test_the_flag_exists_is_off_by_default_and_hides_everything(screener_on, monkeypatch):
    assert "evidence_charts" in controls.FEATURE_FLAGS and "evidence_charts" in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS["evidence_charts"]
    headers, me = _owner("u8-off@example.com")
    _serve_bars(monkeypatch)
    body = _screen_run(headers)
    assert body["matched"] == ["UP", "UP2"] and all("evidence_id" not in r for r in body["results"])

    async def count():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(ScreenEvidenceRecord).where(ScreenEvidenceRecord.tenant_id == me["tenant_id"]))))
    assert _run(count()) == 0
    for path in ("/api/screener/evidence/1", "/api/screener/evidence/1/image.png", f"/api/screener/evidence/runs/{body['run_id']}"):
        assert client.get(path, headers=headers).status_code == 404
    assert client.post("/api/screener/evidence/1/telegram", headers=headers).status_code == 404


def test_a_run_stores_one_pack_per_matched_symbol_readable_by_its_organisation_only(screener_on, monkeypatch):
    _set(evidence_charts=True)
    headers, me = _owner("u8-run@example.com")
    other, _ = _owner("u8-run-other@example.com")
    _serve_bars(monkeypatch)
    body = _screen_run(headers)
    ids = {r["symbol"]: r.get("evidence_id") for r in body["results"]}
    assert ids["DOWN"] is None and ids["UP"] and ids["UP2"] and ids["UP"] != ids["UP2"]
    stored = client.get(f"/api/screener/runs/{body['run_id']}", headers=headers).json()
    assert {r["symbol"]: r.get("evidence_id") for r in stored["results"]} == ids             # kept with the run
    listing = client.get(f"/api/screener/evidence/runs/{body['run_id']}", headers=headers).json()
    assert listing["evidence"] == {"UP": ids["UP"], "UP2": ids["UP2"]}

    pack = client.get(f"/api/screener/evidence/{ids['UP']}", headers=headers).json()
    assert pack["schema"] == "evidence/1" and pack["symbol"] == "UP" and pack["matched"] is True and pack["run_id"] == body["run_id"]
    assert pack["screen"]["text"] == body["text"] and pack["data"]["source"] == "broker:fake" and pack["can_send"] is True
    assert [r["text"] for r in pack["rows"]] == ["close > SMA(close, 20)", "RSI(14) > 50"] and all(r["passed"] for r in pack["rows"])
    assert len(pack["pack_sha256"]) == 64 and "recommendation" in pack["disclaimer"]
    # the numbers are the run's: the last close of the served bars
    assert pack["rows"][0]["terms"][0]["value"] == round(_bars(1.0)[-1].close, 4)

    png = client.get(f"/api/screener/evidence/{ids['UP']}/image.png?theme=dark", headers=headers)
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "no-store" in png.headers["cache-control"]
    # another organisation: 404 everywhere, the run listing is empty
    assert client.get(f"/api/screener/evidence/{ids['UP']}", headers=other).status_code == 404
    assert client.get(f"/api/screener/evidence/{ids['UP']}/image.png", headers=other).status_code == 404
    assert client.post(f"/api/screener/evidence/{ids['UP']}/telegram", headers=other).status_code == 404
    assert client.get(f"/api/screener/evidence/runs/{body['run_id']}", headers=other).status_code == 404
    # a preview is not stored and builds no evidence
    preview = client.post("/api/screener/run", json={"source": SCREEN, "symbols": ["UP"], "preview": True}, headers=headers).json()
    assert preview["run_id"] is None and "results" not in preview


def _teammate(owner_headers, email):
    _tenant_pro(owner_headers)
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": email, "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()
    return _headers(member["access_token"])


def test_bt1_with_the_data_tier_on_only_the_runner_reads_the_evidence(screener_on, monkeypatch):
    _set(evidence_charts=True)
    headers, me = _owner("u8-bt1@example.com")
    member = _teammate(headers, "u8-bt1-member@example.com")
    _serve_bars(monkeypatch)
    eid = _screen_run(headers, symbols=["UP"])["results"][0]["evidence_id"]
    assert client.get(f"/api/screener/evidence/{eid}", headers=member).status_code == 200        # flag off: the organisation's data
    assert client.get(f"/api/screener/evidence/{eid}", headers=member).json()["can_send"] is False
    assert client.post(f"/api/screener/evidence/{eid}/telegram", headers=member).status_code == 409   # never to a teammate's chat
    _set(byob_data_tier=True)                                                                        # BT1: a broker entitlement is personal
    assert client.get(f"/api/screener/evidence/{eid}", headers=member).status_code == 404
    assert client.get(f"/api/screener/evidence/{eid}/image.png", headers=member).status_code == 404
    assert client.get(f"/api/screener/evidence/{eid}", headers=headers).status_code == 200


class Telegram:
    def __init__(self):
        self.sent: List[dict] = []

    def client(self):
        def handler(request):
            body = request.content
            if request.url.path.endswith("/sendPhoto"):
                chat = re.search(rb'name="chat_id"\r\n\r\n([^\r]*)', body)
                caption = re.search(rb'name="caption"\r\n\r\n(.*?)\r\n--', body, re.S)
                self.sent.append({"method": "sendPhoto", "chat_id": chat.group(1).decode() if chat else None,
                                  "caption": caption.group(1).decode() if caption else "", "png": b"\x89PNG\r\n\x1a\n" in body})
            else:
                import json
                data = json.loads(body or b"{}")
                self.sent.append({"method": "sendMessage", "chat_id": str(data.get("chat_id")), "text": data.get("text", "")})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _bot(tenant_id: int, link_user_id=None):
    async with _session_factory() as session:
        await ensure_tenant_key(session, tenant_id)
        session.add(AlertChannelRecord(tenant_id=tenant_id, channel_type="TELEGRAM", enabled=True, min_severity="INFO",
                                       encrypted_config=encrypt_config(TelegramConfig(bot_token=BOT_TOKEN, chat_id=GROUP_CHAT), tenant_id)))
        if link_user_id is not None:
            session.add(NotificationLinkRecord(tenant_id=tenant_id, user_id=link_user_id, channel="telegram", status="linked", chat_id=USER_CHAT,
                                               linked_at=datetime.now(timezone.utc)))
        await session.commit()


def test_send_to_my_telegram_goes_to_the_users_own_linked_chat_only(screener_on, monkeypatch):
    _set(evidence_charts=True)
    headers, me = _owner("u8-tg@example.com")
    _serve_bars(monkeypatch)
    eid = _screen_run(headers, symbols=["UP"])["results"][0]["evidence_id"]
    no_bot = client.post(f"/api/screener/evidence/{eid}/telegram", headers=headers)        # SCR-FIX C4: a reason code + sentence
    assert no_bot.status_code == 409 and no_bot.json()["detail"]["reason"] == "bot_not_configured"
    _run(_bot(me["tenant_id"], link_user_id=me["id"]))
    tg = Telegram()
    from app.ai import vision_audit
    monkeypatch.setattr(vision_audit, "telegram_client", tg.client)
    sent = client.post(f"/api/screener/evidence/{eid}/telegram", headers=headers)
    assert sent.status_code == 200 and sent.json()["sent"] is True and BOT_TOKEN not in sent.text
    assert [(s["method"], s["chat_id"], s["png"]) for s in tg.sent] == [("sendPhoto", USER_CHAT, True)]   # never the group chat
    caption = tg.sent[0]["caption"]
    assert caption.startswith("UP · 1d · bar of ") and "✅ close" in caption and caption.rstrip().endswith("Not a recommendation.")


# ------------------------------------------------------------------------------------------------------------- alerts
def _frame(days=80, step=1.0):
    bars = _bars(step, days)
    idx = pd.DatetimeIndex([b.timestamp for b in bars])
    return pd.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars], "low": [b.low for b in bars],
                         "close": [b.close for b in bars], "volume": [b.volume for b in bars]}, index=idx)


class FakeFetch:
    def __init__(self, frames):
        self.frames = frames

    async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback):
        return [SymbolData(s, {base_tf: self.frames[s]}) for s in symbols if s in self.frames], {}, "test"


def _alert_setup(email):
    headers, me = _owner(email)
    _run(_bot(me["tenant_id"], link_user_id=me["id"]))

    async def go():
        async with _session_factory() as session:
            session.add(NotificationPolicyRecord(tenant_id=me["tenant_id"], timezone="Asia/Kolkata", max_per_hour=100, group_window_seconds=0,
                                                 eod_digest_time="15:45", updated_at=datetime.now(timezone.utc)))
            rule = AlertRuleRecord(tenant_id=me["tenant_id"], created_by=me["id"], name="Trend evidence", kind="instrument", symbol="TCS",
                                   condition_text=SCREEN, base_tf="1d", priority="normal", cooldown_minutes=0, mode="instant",
                                   digest_every="hourly", status="active", created_at=datetime.now(timezone.utc),
                                   updated_at=datetime.now(timezone.utc))
            session.add(rule)
            await session.commit()
            return rule.id
    return headers, me, _run(go())


def _fire_and_dispatch(tenant_id, rule_id, evidence_enabled):
    frame = _frame()
    last = frame.index[-1]
    now = (last + timedelta(days=1, hours=12)).to_pydatetime()            # after the last daily bar closed (IST)
    tg = Telegram()

    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            state = await barclose.evaluate_rule(session, rule, now, FakeFetch({"TCS": frame}), evidence_enabled=evidence_enabled)
            await session.commit()
            await engine.flush(session, now + timedelta(seconds=5), tenant_id=tenant_id)
            sent = await dispatcher.dispatch_pending(session, client=tg.client(), tenant_id=tenant_id,
                                                     now=datetime.now(timezone.utc) + timedelta(minutes=1))
            events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule_id)))
            evidence = list(await session.scalars(select(ScreenEvidenceRecord).where(ScreenEvidenceRecord.tenant_id == tenant_id)))
            return state, sent, events, evidence
    return (*_run(go()), tg)


def test_a_fired_alert_stores_its_evidence_and_sends_the_image_to_the_creators_chat(screener_on):
    _set(evidence_charts=True)
    _h, me, rid = _alert_setup("u8-alert@example.com")
    state, _sent, events, evidence, tg = _fire_and_dispatch(me["tenant_id"], rid, evidence_enabled=True)
    assert state == ("done", 1) and len(events) == 1
    assert len(evidence) == 1 and evidence[0].alert_event_id == events[0].id and evidence[0].user_id == me["id"]
    assert evidence[0].bar_time.replace(tzinfo=timezone.utc) == events[0].bar_time.replace(tzinfo=timezone.utc)
    texts = [(s["method"], s["chat_id"]) for s in tg.sent if s["method"] == "sendMessage"]
    photos = [s for s in tg.sent if s["method"] == "sendPhoto"]
    assert sorted(texts) == [("sendMessage", GROUP_CHAT), ("sendMessage", USER_CHAT)]
    assert [(p["chat_id"], p["png"]) for p in photos] == [(USER_CHAT, True)]                # the creator's chat only, after the text
    assert tg.sent.index(photos[0]) > [i for i, s in enumerate(tg.sent) if s["chat_id"] == USER_CHAT][0]
    assert "Trend evidence" in photos[0]["caption"] and "TCS · 1d" in photos[0]["caption"]


def test_with_the_flag_off_alerts_stay_text_only(screener_on):
    _h, me, rid = _alert_setup("u8-alert-off@example.com")
    state, _sent, events, evidence, tg = _fire_and_dispatch(me["tenant_id"], rid, evidence_enabled=False)
    assert state == ("done", 1) and len(events) == 1 and evidence == []
    assert not [s for s in tg.sent if s["method"] == "sendPhoto"] and len(tg.sent) == 2


def test_evidence_ages_out_with_retention(screener_on, monkeypatch):
    _set(evidence_charts=True)
    headers, me = _owner("u8-retention@example.com")
    _serve_bars(monkeypatch)
    eid = _screen_run(headers, symbols=["UP"])["results"][0]["evidence_id"]
    policy = load_policy()
    assert policy.screen_evidence_days == 90 and policy.as_dict()["screen_evidence_days"] == 90

    async def go():
        async with _session_factory() as session:
            row = await session.get(ScreenEvidenceRecord, eid)
            row.created_at = datetime.now(timezone.utc) - timedelta(days=91)
            await session.commit()
            report = await retention.run_retention(session, policy=policy)
            await session.commit()
            return report.deleted.get("screen_evidence"), await session.get(ScreenEvidenceRecord, eid)
    deleted, row = _run(go())
    assert deleted >= 1 and row is None


def test_an_evidence_failure_never_fails_the_run(screener_on, monkeypatch):
    _set(evidence_charts=True)
    headers, _me = _owner("u8-fail@example.com")
    _serve_bars(monkeypatch)
    from app.screener import evidence as ev

    def boom(*args, **kwargs):
        raise RuntimeError("renderer bug")
    monkeypatch.setattr(ev, "build_pack", boom)
    body = _screen_run(headers, symbols=["UP", "DOWN"])
    assert body["matched"] == ["UP"] and body["results"][0].get("evidence_id") is None
    assert client.get(f"/api/screener/runs/{body['run_id']}", headers=headers).json()["matched"] == 1


def test_only_deliverable_events_get_a_pack_and_the_daily_cap_holds(screener_on, monkeypatch):
    """M1: a cooldown-suppressed event is never sent, so it gets no pack; an organisation stores at most
    MAX_PER_TENANT_DAY packs a day (runs and alerts together)."""
    from app.screener import evidence_store
    _set(evidence_charts=True)
    _h, me, rid = _alert_setup("u8-pending@example.com")
    frame = _frame()

    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rid)
            events = []
            for i, status in enumerate(("pending", "suppressed", "pending")):
                e = AlertEventRecord(tenant_id=me["tenant_id"], rule_id=rid, symbol=f"S{i}", condition_hash="h", bar_time=frame.index[-1].to_pydatetime(),
                                     idem_key=f"u8-pending-{i}", priority="normal", values_json="{}", status=status,
                                     created_at=datetime.now(timezone.utc))
                session.add(e)
                events.append(e)
            await session.flush()
            from app.screener import compile_screen
            ast, validated = compile_screen(SCREEN, base_tf="1d")
            data = [SymbolData(f"S{i}", {"1d": frame}) for i in range(3)]
            stored = await evidence_store.store_for_events(session, rule, list(zip(events, data)), ast=ast, validated=validated,
                                                           universe=data, params={}, data_source="broker:organisation")
            await session.commit()
            rows = list(await session.scalars(select(ScreenEvidenceRecord).where(ScreenEvidenceRecord.tenant_id == me["tenant_id"])))
            return stored, sorted(r.symbol for r in rows), {e.status for e in events}
    stored, symbols, _ = _run(go())
    assert stored == 2 and symbols == ["S0", "S2"]                                   # the suppressed S1 has none

    monkeypatch.setattr(evidence_store, "MAX_PER_TENANT_DAY", 3)                     # 2 already stored today: room for 1
    _serve_bars(monkeypatch)
    body = _screen_run(_h, symbols=["UP", "UP2"])
    assert sum(1 for r in body["results"] if r.get("evidence_id")) == 1
    again = _screen_run(_h, symbols=["UP", "UP2"])
    assert not any(r.get("evidence_id") for r in again["results"]) and again["matched"] == ["UP", "UP2"]   # the run itself is unchanged


def test_per_user_packs_stay_owner_only_after_the_flag_is_turned_off(screener_on, monkeypatch):
    """L3: a pack read through the runner's own broker session (BT1b) is recorded as such and stays owner-only."""
    _set(evidence_charts=True)
    headers, _me = _owner("u8-peruser@example.com")
    member = _teammate(headers, "u8-peruser-member@example.com")
    _serve_bars(monkeypatch)
    body = _screen_run(headers, symbols=["UP"])
    eid = body["results"][0]["evidence_id"]

    async def mark():
        async with _session_factory() as session:
            row = await session.get(ScreenEvidenceRecord, eid)
            assert row.per_user is False                                              # flag off: the organisation's session
            row.per_user = True                                                       # as stored with byob_data_tier on
            await session.commit()
    _run(mark())
    assert client.get(f"/api/screener/evidence/{eid}", headers=member).status_code == 404   # flag off now, still owner-only
    assert client.get(f"/api/screener/evidence/runs/{body['run_id']}", headers=member).json()["evidence"] == {}
    assert client.get(f"/api/screener/evidence/{eid}", headers=headers).status_code == 200
