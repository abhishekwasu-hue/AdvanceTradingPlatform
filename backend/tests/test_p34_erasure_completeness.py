"""P3-4-B9: account erasure also covers the organisation name (sign-up sets it to the e-mail), the member's own alert
channel entries and Telegram link, their journal, and - for the last member of an organisation - its alert channels and
broker credentials. Trades and broker accounts stay (regulatory retention)."""
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.ai import memory as copilot_memory
from app.alerts.channels import FcmConfig, FcmDevice, TelegramApprover, TelegramConfig, decrypt_raw, encrypt_config
from app.chart_analyze import storage
from app.db.models import (
    AlertChannelRecord,
    AlertDeliveryRecord,
    BrokerAccountRecord,
    BrokerCredentialRecord,
    CopilotChatSessionRecord,
    CopilotChatTurnRecord,
    JournalAttachmentRecord,
    JournalEntryRecord,
    JournalManualTradeRecord,
    JournalSettingsRecord,
    JournalTagRecord,
    NotificationLinkRecord,
    NotificationRecord,
    Tenant,
    TradeRecord,
    User,
    UserAiKeyRecord,
    UserPrefsRecord,
)
from app.retention.service import erase_user
from app.secrets_store.envelope import ensure_tenant_key
from tests.test_auth_api import _register, _session_factory, client
from tests.test_team_api import _join, _owner

NOW = datetime.now(UTC)
PASSWORD = "S3cur3Pass!"


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"p34-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _count(model, **where) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).filter_by(**where)) or 0)
    return _run(go())


def _seed_journal(tenant_id: int, user_id: int, tag: str) -> int:
    """A trade with its journal entry, a tag, an outside-ATP trade, an attached image and journal settings."""
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="P34TEST", strategy_id="s",
                                direction="LONG", entry_time=NOW - timedelta(hours=2), entry_price=100.0, quantity=1.0,
                                stop_loss=98.0, target1=104.0)
            session.add(trade)
            await session.flush()
            manual = JournalManualTradeRecord(tenant_id=tenant_id, user_id=user_id, symbol="OUTSIDE", direction="LONG", quantity=1.0,
                                              entry_time=NOW - timedelta(hours=3), entry_price=50.0, notes="my own words")
            session.add(manual)
            await session.flush()
            session.add_all([
                JournalEntryRecord(tenant_id=tenant_id, user_id=user_id, trade_id=trade.id, user_tags_json='["fomo"]'),
                JournalTagRecord(tenant_id=tenant_id, user_id=user_id, trade_id=trade.id, tag="LATE_ENTRY", source="USER"),
                JournalAttachmentRecord(tenant_id=tenant_id, user_id=user_id, manual_trade_id=manual.id, image_path=f"{tenant_id}/{tag}.png",
                                        size_bytes=10, width=1, height=1, sha256="0" * 64),
                JournalSettingsRecord(tenant_id=tenant_id, user_id=user_id, settings_json="{}"),
            ])
            await session.commit()
            return trade.id
    return _run(go())


def _device(user_id: int, n: int) -> FcmDevice:
    return FcmDevice(device_id=f"{user_id:08x}{n:04x}", token=f"fcm-token-{user_id}-{n}-" + "x" * 20, user_id=user_id)


def _seed_org_channels(tenant_id: int, members: list) -> None:
    """A Telegram channel with one approver per member, a phone per member, a delivery, and a member's Telegram link."""
    async def go():
        async with _session_factory() as session:
            await ensure_tenant_key(session, tenant_id)
            telegram = TelegramConfig(bot_token="123456:" + "A" * 30, chat_id="-100200",
                                      approvers=[TelegramApprover(telegram_user_id=str(900 + m["id"]), user_id=m["id"], email=m["email"])
                                                 for m in members])
            tg = AlertChannelRecord(tenant_id=tenant_id, channel_type="TELEGRAM", encrypted_config=encrypt_config(telegram, tenant_id))
            fcm = AlertChannelRecord(tenant_id=tenant_id, channel_type="FCM",
                                     encrypted_config=encrypt_config(FcmConfig(devices=[_device(m["id"], 1) for m in members]), tenant_id))
            session.add_all([tg, fcm])
            note = NotificationRecord(tenant_id=tenant_id, user_id=members[0]["id"], event_type="ENTRY", title="t")
            session.add(note)
            await session.flush()
            session.add(AlertDeliveryRecord(tenant_id=tenant_id, notification_id=note.id, channel_id=tg.id))
            for m in members:
                session.add(NotificationLinkRecord(tenant_id=tenant_id, user_id=m["id"], channel="telegram", status="linked",
                                                   chat_id=str(700 + m["id"]), linked_at=NOW))
            await session.commit()
    _run(go())


def _seed_broker(tenant_id: int, user_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            cred = BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name="upstox", encrypted_payload="ciphertext")
            session.add(cred)
            await session.flush()
            session.add(BrokerAccountRecord(tenant_id=tenant_id, user_id=user_id, credential_id=cred.id, broker_name="upstox"))
            await session.commit()
    _run(go())


def _erase(user_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            user.is_active = False
            await erase_user(session, user, actor_id=user_id, reason="test")
            await session.commit()
    _run(go())


def _tenant_name(tenant_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            return (await session.get(Tenant, tenant_id)).name
    return _run(go())


def _channel_entries(tenant_id: int, channel_type: str, key: str) -> list:
    async def go():
        async with _session_factory() as session:
            await ensure_tenant_key(session, tenant_id)
            record = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                           AlertChannelRecord.channel_type == channel_type))
            return [e["user_id"] for e in decrypt_raw(record)[key]]
    return _run(go())


def _broker_account_credential(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(BrokerAccountRecord.credential_id).where(BrokerAccountRecord.tenant_id == tenant_id))
    return _run(go())


def test_sole_member_erasure_clears_org_name_channels_credentials_and_journal(monkeypatch):
    removed = []
    monkeypatch.setattr(storage, "delete", lambda path: removed.append(path) or True)
    email = _email("sole")
    headers = {"Authorization": f"Bearer {_register(email, PASSWORD)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    tid, uid = me["tenant_id"], me["id"]
    assert _tenant_name(tid) == email                                      # sign-up names the organisation after the e-mail
    trade_id = _seed_journal(tid, uid, "sole")
    _seed_org_channels(tid, [me])
    _seed_broker(tid, uid)

    _erase(uid)

    assert _tenant_name(tid) == f"Erased organisation {tid}"
    for model in (JournalEntryRecord, JournalTagRecord, JournalManualTradeRecord, JournalAttachmentRecord, JournalSettingsRecord):
        assert _count(model, user_id=uid) == 0, model.__tablename__
    assert removed == [f"{tid}/sole.png"]                                 # the image file goes with its row
    assert _count(AlertChannelRecord, tenant_id=tid) == 0 and _count(AlertDeliveryRecord, tenant_id=tid) == 0
    assert _count(NotificationLinkRecord, user_id=uid) == 0
    assert _count(BrokerCredentialRecord, tenant_id=tid) == 0
    # Regulatory records stay: the trade, and the broker account (orders point at it) without its deleted credential.
    assert _count(TradeRecord, id=trade_id) == 1
    assert _count(BrokerAccountRecord, tenant_id=tid) == 1 and _broker_account_credential(tid) is None


def test_member_erasure_in_a_shared_org_keeps_org_data_and_drops_only_their_entries(monkeypatch):
    monkeypatch.setattr(storage, "delete", lambda path: True)
    owner_email = _email("owner")
    owner_headers, owner = _owner(owner_email)
    _, member = _join(owner_headers, _email("member"), role="USER")
    tid = owner["tenant_id"]
    _seed_journal(tid, member["id"], "member")
    owner_trade = _seed_journal(tid, owner["id"], "owner")
    _seed_org_channels(tid, [owner, member])
    _seed_broker(tid, owner["id"])

    assert client.delete(f"/api/team/members/{member['id']}", headers=owner_headers).status_code == 204
    assert client.post(f"/api/team/members/{member['id']}/erase", json={}, headers=owner_headers).status_code == 204

    assert _count(JournalEntryRecord, user_id=member["id"]) == 0 and _count(JournalManualTradeRecord, user_id=member["id"]) == 0
    assert _count(JournalEntryRecord, user_id=owner["id"]) == 1 and _count(TradeRecord, id=owner_trade) == 1
    assert _channel_entries(tid, "TELEGRAM", "approvers") == [owner["id"]]
    assert _channel_entries(tid, "FCM", "devices") == [owner["id"]]
    assert _count(NotificationLinkRecord, user_id=member["id"]) == 0 and _count(NotificationLinkRecord, user_id=owner["id"]) == 1
    assert _count(AlertChannelRecord, tenant_id=tid) == 2 and _count(BrokerCredentialRecord, tenant_id=tid) == 1
    assert _tenant_name(tid) == owner_email                               # the remaining owner's organisation keeps its name

    # The owner is now the last member with personal data: his erasure clears the organisation's own data too.
    _erase(owner["id"])
    assert _tenant_name(tid) == f"Erased organisation {tid}"
    assert _count(AlertChannelRecord, tenant_id=tid) == 0 and _count(BrokerCredentialRecord, tenant_id=tid) == 0
    assert _count(JournalEntryRecord, user_id=owner["id"]) == 0 and _count(TradeRecord, id=owner_trade) == 1


def test_org_named_after_an_erased_owner_is_renamed_even_while_members_remain(monkeypatch):
    monkeypatch.setattr(storage, "delete", lambda path: True)
    owner_email = _email("named")
    owner_headers, owner = _owner(owner_email)
    _, member = _join(owner_headers, _email("stays"), role="USER")
    tid = owner["tenant_id"]
    _seed_org_channels(tid, [owner, member])
    _seed_broker(tid, owner["id"])

    _erase(owner["id"])

    assert _tenant_name(tid) == f"Erased organisation {tid}"              # the name was the erased e-mail
    assert _channel_entries(tid, "TELEGRAM", "approvers") == [member["id"]]
    assert _channel_entries(tid, "FCM", "devices") == [member["id"]]
    assert _count(AlertChannelRecord, tenant_id=tid) == 2 and _count(BrokerCredentialRecord, tenant_id=tid) == 1
    raw = _run(_raw_approver_emails(tid))
    assert owner_email not in json.dumps(raw)


async def _raw_approver_emails(tenant_id: int):
    async with _session_factory() as session:
        await ensure_tenant_key(session, tenant_id)
        record = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                       AlertChannelRecord.channel_type == "TELEGRAM"))
        return decrypt_raw(record)


# P3-4: the Copilot memory (user_prefs `ai_memory`), every other user_prefs row, the Copilot conversation and the
# member's own AI keys go with the account; the other member's rows and the organisation's broker credential stay.
_PREF_NAMESPACES = (copilot_memory.PREFS, "chart", "ai", "signals_day_one")


def _seed_ai_data(tenant_id: int, user_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            for ns in _PREF_NAMESPACES:
                session.add(UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=ns, value_json='{"instruments": ["NIFTY"]}',
                                            version=1, updated_at=NOW))
            chat = CopilotChatSessionRecord(tenant_id=tenant_id, user_id=user_id, title="my question")
            session.add(chat)
            await session.flush()
            session.add(CopilotChatTurnRecord(tenant_id=tenant_id, user_id=user_id, session_id=chat.id, question="my question",
                                              answer_json="{}"))
            session.add(UserAiKeyRecord(tenant_id=tenant_id, user_id=user_id, provider="anthropic", encrypted_key="ciphertext",
                                        key_hint="...abcd"))
            await session.commit()
    _run(go())


def _ai_counts(user_id: int) -> dict:
    return {"prefs": _count(UserPrefsRecord, user_id=user_id),
            "memory": _count(UserPrefsRecord, user_id=user_id, namespace=copilot_memory.PREFS),
            "chat_sessions": _count(CopilotChatSessionRecord, user_id=user_id),
            "chat_turns": _count(CopilotChatTurnRecord, user_id=user_id),
            "ai_keys": _count(UserAiKeyRecord, user_id=user_id)}


def test_member_erasure_removes_copilot_memory_all_prefs_conversation_and_ai_keys(monkeypatch):
    monkeypatch.setattr(storage, "delete", lambda path: True)
    owner_headers, owner = _owner(_email("aiowner"))
    _, member = _join(owner_headers, _email("aimember"), role="USER")
    tid = owner["tenant_id"]
    for person in (owner, member):
        _seed_ai_data(tid, person["id"])
    _seed_broker(tid, owner["id"])
    seeded = {"prefs": len(_PREF_NAMESPACES), "memory": 1, "chat_sessions": 1, "chat_turns": 1, "ai_keys": 1}
    assert _ai_counts(member["id"]) == seeded

    assert client.delete(f"/api/team/members/{member['id']}", headers=owner_headers).status_code == 204
    assert client.post(f"/api/team/members/{member['id']}/erase", json={}, headers=owner_headers).status_code == 204

    assert _ai_counts(member["id"]) == {k: 0 for k in seeded}
    assert _ai_counts(owner["id"]) == seeded                               # another member's rows are untouched
    assert _count(BrokerCredentialRecord, tenant_id=tid) == 1              # the organisation's credential stays with it


def test_sole_member_erasure_removes_copilot_memory_and_the_organisations_analytics_token(monkeypatch):
    monkeypatch.setattr(storage, "delete", lambda path: True)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_register(_email('aisole'), PASSWORD)}"}).json()
    tid, uid = me["tenant_id"], me["id"]
    _seed_ai_data(tid, uid)
    _seed_broker(tid, uid)                                                 # an Upstox credential (Analytics Token) of the org

    _erase(uid)

    assert _ai_counts(uid) == {"prefs": 0, "memory": 0, "chat_sessions": 0, "chat_turns": 0, "ai_keys": 0}
    assert _count(BrokerCredentialRecord, tenant_id=tid) == 0
