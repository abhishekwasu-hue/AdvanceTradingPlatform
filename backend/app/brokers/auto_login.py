"""BRK-1 B (owner order BRK-1 B, rule IN-SEBI.login.daily): the automatic daily login of the TOTP brokers.

Angel One, Shoonya, Kotak Neo, Groww and Dhan log in with details stored once (encrypted) and a TOTP secret, from which
ATP makes the six-digit code of the moment (app/brokers/totp.py). So nobody needs to press Renew: on every NSE trading
day, from `auto_login_minutes_before_open` before the open until the close (rule-set data), the trading worker logs each
active TOTP connection in by itself, through the very login the Renew button runs (`routes.run_authentication`: the
adapter's own login, the session stored encrypted and marked VALID, the attempt audited, the row written only if nobody
changed it meanwhile).

- Which rows: a broker whose catalog method is `totp_auto`; a row with a stored TOTP secret; proven at least once and not
  disconnected on purpose (`login_reminder.is_active_connection`); whose session will not last today's session.
- Idempotent: a session that lasts the day is left alone, so a second run (another cycle, a restarted worker, a second
  worker) does nothing. Each attempt is claimed on the row first (a conditional write of `auto_login` inside the encrypted
  payload: the IST day, the attempts made, when the last one ran), so two workers never log in twice at once.
- Bounded: one attempt per connection per morning plus `auto_login_retries` retries, `auto_login_retry_minutes` apart.
- A failure that uses up the attempts marks the row "Not working" for the day with ONE plain sentence (connect.py reads
  `failed_today`) and sends ONE reminder to the member who linked it (in-app and the organisation's channels such as
  Telegram), which also stands in for the pre-open reminder of that connection. Renew stays as the manual fallback, and
  any save of new details clears the state.
- Never a secret in a log line or a message: the adapters' login errors are reduced to fixed sentences
  (`connect.failure_message`), and this module logs only exception class names.

PAPER only: a login, nothing else - no order, no deployment change, no LIVE switch.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import catalog as cat
from app.brokers import login_reminder as lr
from app.brokers.models import BrokerCredentials
from app.brokers.token_lifecycle import load_credentials
from app.compliance import rules
from app.db.models import BrokerCredentialRecord, User
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day, load_holidays
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL

logger = logging.getLogger(__name__)


def _param(name: str, default: int) -> int:
    return int(rules.load().param(lr.RULE, name, default))


def auto_brokers() -> tuple[str, ...]:
    """The brokers ATP logs in to by itself: every catalog entry whose method is the TOTP login."""
    return tuple(sorted(e.broker for e in cat.CATALOG if e.method == cat.TOTP_AUTO))


def max_attempts() -> int:
    return 1 + max(0, _param("auto_login_retries", 1))


def login_time(day: date) -> datetime:
    return datetime.combine(day, MARKET_OPEN, tzinfo=IST) - timedelta(minutes=_param("auto_login_minutes_before_open", 45))


def in_window(now: datetime) -> bool:
    """From the automatic login time until the close (IST); a worker started mid-morning still logs in."""
    now_ist = now.astimezone(IST)
    return login_time(now_ist.date()) <= now_ist and now_ist.time() < MARKET_CLOSE


def _today(now: datetime) -> str:
    return now.astimezone(IST).date().isoformat()


def _state(creds: BrokerCredentials, now: datetime) -> dict[str, Any]:
    """Today's state ({"day", "attempts", "last_try", "failed", "reason"}); a fresh one on a new day."""
    state = creds.auto_login if isinstance(creds.auto_login, dict) else {}
    return dict(state) if state.get("day") == _today(now) else {"day": _today(now), "attempts": 0}


def failed_today(creds: BrokerCredentials, now: datetime) -> str | None:
    """The plain sentence of today's given-up automatic login, or None."""
    state = _state(creds, now)
    if not state.get("failed"):
        return None
    return str(state.get("reason") or "This morning's automatic login did not work; press Renew to log in now.")


def auto_state(record: BrokerCredentialRecord, now: datetime) -> tuple[bool, bool]:
    """(has a TOTP secret, ATP's own login is still going today) for one row - the reminder waits while it is."""
    if record.broker_name not in auto_brokers():
        return False, False
    try:
        creds = load_credentials(record)
    except Exception:  # noqa: BLE001 - an unreadable payload: nothing to log in with
        return False, False
    if not creds.totp_secret:
        return False, False
    state = _state(creds, now)
    return True, not state.get("failed") and int(state.get("attempts") or 0) < max_attempts()


def _retry_due(state: dict[str, Any], now: datetime) -> bool:
    last = state.get("last_try")
    if not last:
        return True
    try:
        last_at = datetime.fromisoformat(str(last))
    except ValueError:
        return True
    return now >= last_at + timedelta(minutes=_param("auto_login_retry_minutes", 10))


async def _write_state(session: AsyncSession, record: BrokerCredentialRecord, state: dict[str, Any]) -> bool:
    """Stores `state` on the row only if its payload is still the one just read (False: changed meanwhile). Commits."""
    from app.brokers.routes import _save_payload_if_unchanged
    creds = load_credentials(record)
    creds.auto_login = state
    payload = encrypt_text(creds.model_dump_json(), record.tenant_id, PURPOSE_BROKER_CREDENTIAL)
    if not await _save_payload_if_unchanged(session, record, payload):
        await session.rollback()
        return False
    await session.commit()
    await session.refresh(record)
    return True


async def _give_up(session: AsyncSession, record: BrokerCredentialRecord, entry: cat.BrokerEntry, sentence: str,
                   now: datetime) -> None:
    """Marks today's automatic login as given up with its plain reason and sends the ONE reminder for this connection."""
    await session.refresh(record)
    creds = load_credentials(record)
    state = {**_state(creds, now), "failed": True,
             "reason": f"This morning's automatic login did not work: {sentence}"}
    if not await _write_state(session, record, state):
        return                                  # the row changed (new details, a manual login): nothing to report
    if (record.broker_name, record.id) in await lr.reminded_today(session, record.tenant_id, now):
        return
    await lr.remind_connection(session, record, now, lr.connection_title(record) + ": the automatic login did not work",
                               f"{state['reason']} Broker page > Renew logs in now; until then nothing trades through this "
                               "connection.")


async def login_one(session: AsyncSession, record_id: int, now: datetime) -> bool | None:
    """One connection's automatic login: True logged in, False failed (retried later or given up), None not due."""
    from app.brokers.connect import failure_message
    from app.brokers.routes import run_authentication

    record = await session.get(BrokerCredentialRecord, record_id)
    entry = cat.entry(record.broker_name) if record is not None else None
    if record is None or entry is None or entry.method != cat.TOTP_AUTO or not lr.is_active_connection(record):
        return None
    if lr.lasts_the_session(record, now.astimezone(IST).date()):
        return None
    try:
        creds = load_credentials(record)
    except Exception:  # noqa: BLE001 - an unreadable payload: the connection view already says "Not working"
        return None
    if not creds.totp_secret:
        return None                             # a Dhan token pasted as the fallback: nothing to log in with
    state = _state(creds, now)
    attempts = int(state.get("attempts") or 0)
    if state.get("failed") or attempts >= max_attempts() or not _retry_due(state, now):
        return None
    attempts += 1
    if not await _write_state(session, record, {**state, "attempts": attempts, "last_try": now.isoformat()}):
        return None                             # claimed by another worker, or the row changed: not this run

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        await _give_up(session, record, entry, f"the member who connected {entry.name} can no longer sign in to ATP; connect it again.", now)
        return False
    label = record.account_label or "primary"
    broker, tenant_id = record.broker_name, record.tenant_id    # the login's conditional write expires the row
    try:
        _, exc = await run_authentication(session, user, record, label)
    except HTTPException:
        await session.rollback()
        return None                             # the row changed hands during the login: nothing was written
    except Exception as construct_exc:  # noqa: BLE001 - the adapter refused the stored fields before any call
        await session.rollback()
        exc = construct_exc
    if exc is None:
        logger.info("Automatic %s login succeeded for tenant %s (%s)", broker, tenant_id, label)
        return True
    logger.info("Automatic %s login failed for tenant %s (%s), attempt %d: %s", broker, tenant_id, label, attempts,
                type(exc).__name__)
    if attempts >= max_attempts():
        await _give_up(session, record, entry, failure_message(entry, exc, renewing=True), now)
    return False


async def run_due(session_factory, now: datetime, holidays=None) -> int:
    """Every due automatic login, each in its own session (one connection's trouble never stops the next). NSE trading
    days only, inside the window. Returns how many logged in."""
    if not in_window(now):
        return 0
    day = now.astimezone(IST).date()
    async with session_factory() as session:
        if holidays is None:
            holidays = await load_holidays(session, "NSE", day.year)
        if not is_trading_day(day, holidays):
            return 0
        ids = list(await session.scalars(select(BrokerCredentialRecord.id).where(
            BrokerCredentialRecord.broker_name.in_(auto_brokers())).order_by(BrokerCredentialRecord.id)))
    done = 0
    for record_id in ids:
        try:
            async with session_factory() as session:
                if await login_one(session, record_id, now):
                    done += 1
        except Exception as exc:  # noqa: BLE001 - one connection's failure never stops the others
            logger.warning("Automatic broker login failed for credential %s: %s", record_id, type(exc).__name__)
    return done
