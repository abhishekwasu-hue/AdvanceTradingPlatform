"""Part D5 (rule IN-SEBI.login.daily): one daily-login model for every broker and a pre-open reminder.

- every broker maps to a login method (oauth / login_code / api_key / manual) from token_lifecycle's sets, not a list here;
- the reminder fires at MARKET_OPEN minus `reminder_minutes_before_open` (rule-set data), on NSE trading days, until the close;
- an organisation is reminded when an ACTIVE deployment's broker has no credential whose session lasts to today's close
  (EXPIRED, or VALID but expiring before the close); a session lasting the day, an api-key venue, or a stopped deployment
  is not reminded; one reminder per organisation per day, even across a worker restart;
- the worker raises it from its housekeeping, market closed or not (it is pre-open).
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.brokers import login_reminder as lr
from app.brokers import token_lifecycle as tl
from app.compliance import rules
from app.db.models import BrokerCredentialRecord, NotificationRecord
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import _deploy, _FakeBroker, _tenant, _worker


def _run(coro):
    return asyncio.run(coro)


def _past_monday() -> date:
    """A weekday in the past (created_at of the reminders is real time, so the test day must not be in the future)."""
    today = datetime.now(IST).date()
    return today - timedelta(days=today.weekday() + 7)


def _at(day: date, minutes_from_open: int) -> datetime:
    return datetime.combine(day, MARKET_OPEN, tzinfo=IST) + timedelta(minutes=minutes_from_open)


def _set_token(tenant_id: int, status: str, expires_at):
    async def go():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))
            # stored in UTC: SQLite drops the offset and token_lifecycle reads a naive value as UTC (Postgres keeps it)
            record.token_status, record.token_expires_at = status, expires_at and expires_at.astimezone(timezone.utc)
            await session.commit()
    _run(go())


def _due(now, holidays=()):
    async def go():
        async with _session_factory() as session:
            return await lr.due_reminders(session, now, holidays)
    return _run(go())


def _reminders(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == tenant_id, NotificationRecord.title.startswith(lr.TITLE_PREFIX))))
    return _run(go())


def test_every_broker_has_a_login_method_from_the_token_lifecycle_sets():
    for broker in tl.PERMANENT_KEY_BROKERS:
        assert lr.login_method(broker) == "api_key"
    for broker in tl.OAUTH_BROKERS:
        assert lr.login_method(broker.upper()) == "oauth"
    for broker in tl.LOGIN_URL_BROKERS:
        assert lr.login_method(broker) == "login_code"
    assert lr.login_method("some-new-broker") == "manual"
    assert set(lr.HOW) == {"oauth", "login_code", "api_key", "manual"}


def test_reminder_time_and_window_come_from_the_rule_set():
    day = _past_monday()
    minutes = rules.load().param(lr.RULE, "reminder_minutes_before_open")
    assert lr.reminder_time(day) == _at(day, -minutes)
    assert not lr.in_window(_at(day, -minutes) - timedelta(minutes=1))
    assert lr.in_window(_at(day, -minutes))
    assert lr.in_window(_at(day, 60))                                   # a worker started mid-morning still reminds
    assert not lr.in_window(datetime.combine(day, MARKET_CLOSE, tzinfo=IST))


def test_expired_or_short_session_is_reminded_a_full_day_session_is_not():
    t = _tenant("d5-due@example.com", token_status="EXPIRED")
    _deploy(t)
    day = _past_monday()
    now = _at(day, -10)
    assert _due(now).get(t["tenant_id"]) == ["upstox"]
    assert _due(now, holidays={day}) == {}                              # NSE holiday: no reminder
    assert _due(_at(day + timedelta(days=5), -10)) == {}                # Saturday
    close = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    _set_token(t["tenant_id"], "VALID", close - timedelta(hours=1))    # dies mid-session
    assert _due(now).get(t["tenant_id"]) == ["upstox"]
    _set_token(t["tenant_id"], "VALID", close + timedelta(hours=12))   # lasts the session
    assert t["tenant_id"] not in _due(now)


def test_stopped_deployments_and_api_key_venues_are_not_reminded():
    t = _tenant("d5-quiet@example.com", token_status="EXPIRED")
    _deploy(t, status="STOPPED")
    now = _at(_past_monday(), -10)
    assert t["tenant_id"] not in _due(now)
    crypto = next(iter(tl.PERMANENT_KEY_BROKERS))
    _deploy(t, broker_name=crypto, symbol="BTCINR")
    assert t["tenant_id"] not in _due(now)


def test_one_reminder_per_day_naming_how_to_log_in():
    t = _tenant("d5-once@example.com", token_status="EXPIRED")
    _deploy(t)
    now = _at(_past_monday(), -10)

    async def send():
        async with _session_factory() as session:
            return await lr.send_reminders(session, now, holidays=())
    assert _run(send()) >= 1
    _run(send())                                                        # a restarted worker: no second reminder
    sent = _reminders(t["tenant_id"])
    assert len(sent) == 1 and sent[0].severity == "WARNING" and "upstox" in sent[0].title
    assert lr.HOW["oauth"] in sent[0].message


def test_worker_raises_the_reminder_before_the_open(monkeypatch):
    t = _tenant("d5-worker@example.com", token_status="EXPIRED")
    _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker())

    async def no_holidays(session, exchange="NSE", year=None):
        return set()
    monkeypatch.setattr(lr, "load_holidays", no_holidays)
    day = _past_monday()
    before = _run(worker.run_cycle(_at(day, -45)))                      # before the reminder time
    assert before.login_reminders == 0 and not _reminders(t["tenant_id"])
    report = _run(worker.run_cycle(_at(day, -10)))
    assert report.login_reminders >= 1 and len(_reminders(t["tenant_id"])) == 1
    again = _run(worker.run_cycle(_at(day, -9)))
    assert again.login_reminders == 0                                   # once per IST day


def test_tokens_without_expiry_but_valid_last_the_session():
    t = _tenant("d5-noexpiry@example.com", token_status="VALID")
    _deploy(t)
    _set_token(t["tenant_id"], "VALID", None)
    assert t["tenant_id"] not in _due(_at(_past_monday(), -10))
