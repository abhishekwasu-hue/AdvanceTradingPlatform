"""Route 3 (BIZ-1, owner 14 Nov): "Trade in your broker app" for every user - the user places the order himself on his
broker's own page; ATP sends nothing to any broker.

* B2-B4: `GET /api/paper/fno/broker-app` says whether the feature is on (flag `broker_app_links`, off by default), the
  organisation's broker name and, for Zerodha only, the public Kite Publisher key. Reads only.
* B5: an entry signal's Telegram / push alert adds one line pointing to the button on the signal in ATP (flag on only),
  with no price, side or level beyond the alert's own text.
* B6: the `free` plan is shown as "Basic" (id unchanged); Pro carries `runner` (defined, not enforced).
"""
import asyncio
import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
from sqlalchemy import update

from app.alerts import deeplink, dispatcher
from app.core import config as app_config
from app.db.models import AiActionRecord, BrokerAccountRecord
from app.plans.registry import PLANS, get_plan
from tests.test_alerts import TELEGRAM, _auth, _put, _telegram_mock
from tests.test_auth_api import _session_factory, client
from tests.test_pw4_live import _n
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag


def _run(coro):
    return asyncio.run(coro)


def _member(tag: str) -> dict:
    t = _tenant(f"r3-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    _paper_flag(t["tenant_id"])
    return t


def _broker(t: dict, name: str) -> None:
    """His organisation's default broker account becomes `name`."""
    async def go():
        async with _session_factory() as session:
            await session.execute(update(BrokerAccountRecord).where(BrokerAccountRecord.tenant_id == t["tenant_id"]).values(is_default=False))
            session.add(BrokerAccountRecord(tenant_id=t["tenant_id"], broker_name=name, account_label="r3", status="ACTIVE", is_default=True))
            await session.commit()
    _run(go())


def _info(t: dict) -> dict:
    out = client.get("/api/paper/fno/broker-app", headers=t["headers"])
    assert out.status_code == 200, out.text
    return out.json()


# --- B2-B4: the broker-app facts ---------------------------------------------------------------------------------------

def test_off_by_default_and_says_nothing_about_the_broker():
    t = _member("off")
    _broker(t, "zerodha")
    assert _info(t) == {"on": False, "broker": None, "publisher_api_key": None}


def test_on_names_his_organisation_broker_and_the_kite_key_only_for_zerodha(monkeypatch):
    monkeypatch.setattr(app_config, "KITE_PUBLISHER_API_KEY", "publisher-key-for-tests")
    kite = _member("kite")
    _broker(kite, "zerodha")
    _flag("broker_app_links", kite["tenant_id"])
    assert _info(kite) == {"on": True, "broker": "zerodha", "publisher_api_key": "publisher-key-for-tests"}

    angel = _member("angel")
    _broker(angel, "angel_one")
    _flag("broker_app_links", angel["tenant_id"])
    assert _info(angel) == {"on": True, "broker": "angel_one", "publisher_api_key": None}     # never sent to another broker



def test_without_a_key_zerodha_has_no_prefill(monkeypatch):
    monkeypatch.setattr(app_config, "KITE_PUBLISHER_API_KEY", "")
    t = _member("nokey")
    _broker(t, "zerodha")
    _flag("broker_app_links", t["tenant_id"])
    assert _info(t)["publisher_api_key"] is None


def test_hidden_without_the_paper_pages():
    t = _tenant(f"r3-noui-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get("/api/paper/fno/broker-app", headers=t["headers"]).status_code == 404


# --- B5: the alert line ------------------------------------------------------------------------------------------------

def test_the_line_names_the_button_and_links_the_signal_only(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "https://atp.example.in", raising=False)
    note = _n(ai_action_id=12)
    assert deeplink.broker_app_line(note) == "Trade in your broker app: open this signal in ATP."
    assert deeplink.broker_app_line(note, html_link=True) == (
        '<a href="https://atp.example.in/signals?proposal=12">Trade in your broker app: open this signal in ATP.</a>')
    assert not any(w in deeplink.BROKER_APP_TEXT.lower() for w in ("buy", "sell", "target", "stop", "price"))
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "", raising=False)
    monkeypatch.setattr(app_config, "FRONTEND_URL", "/", raising=False)
    assert deeplink.broker_app_line(note, html_link=True) == deeplink.BROKER_APP_TEXT               # no address: no guessed link


def _entry_action(tenant_id: int, action: str = "ENTRY") -> int:
    async def go():
        async with _session_factory() as session:
            row = AiActionRecord(tenant_id=tenant_id, action=action, rule="r3", status="PROPOSED", reason="r3", evidence_json="{}",
                                 created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _telegram_text(me: dict, action_id: int) -> str:
    async def go():
        from sqlalchemy import select

        from app.db.models import AlertChannelRecord
        async with _session_factory() as session:
            record = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == me["tenant_id"]))
            seen: List[httpx.Request] = []
            note = _n(tenant_id=me["tenant_id"], ai_action_id=action_id, id=91, message="Higher low held at the buyer area")
            await dispatcher.send_via_channel(record, note, client=_telegram_mock([httpx.Response(200, json={"ok": True})], seen), session=session)
            return json.loads(seen[0].content)["text"]
    return _run(go())


def test_telegram_entry_signal_gets_the_line_only_with_the_flag(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "https://atp.example.in", raising=False)
    headers, me = _auth(f"r3-tg-{uuid.uuid4().hex[:6]}@example.com")
    assert _put(headers, "telegram", TELEGRAM, min_severity="INFO").status_code == 200
    entry = _entry_action(me["tenant_id"])
    assert "Trade in your broker app" not in _telegram_text(me, entry)                      # flag off: unchanged
    _flag("broker_app_links", me["tenant_id"])
    text = _telegram_text(me, entry)
    assert text.endswith(f'<a href="https://atp.example.in/signals?proposal={entry}">Trade in your broker app: open this signal in ATP.</a>')
    assert "Higher low held at the buyer area" in text                                        # the alert's own text, unchanged
    exit_id = _entry_action(me["tenant_id"], action="EXIT_POSITION")
    assert "Trade in your broker app" not in _telegram_text(me, exit_id)                    # only an entry signal


def test_push_body_line():
    note = _n(message="Higher low held")
    assert dispatcher.with_broker_app_line("Higher low held", note, True) == "Higher low held\nTrade in your broker app: open this signal in ATP."
    assert dispatcher.with_broker_app_line("Higher low held", note, False) == "Higher low held"


# --- B6: plans -------------------------------------------------------------------------------------------------------

def test_free_plan_shows_as_basic_with_its_id_kept_and_pro_has_runner():
    assert PLANS["free"].id == "free" and PLANS["free"].name == "Basic"
    assert get_plan("unknown").id == "free"
    assert {p.id: p.runner for p in PLANS.values()} == {"free": False, "pro": True, "business": False}
    assert dataclasses.fields(PLANS["pro"])[-1].name == "runner" and dataclasses.fields(PLANS["pro"])[-1].default is False
