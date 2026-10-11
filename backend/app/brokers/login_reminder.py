"""Part D5 (rule IN-SEBI.login.daily): the daily broker login, the same for every adapter, and a pre-open reminder.

Retail broker sessions die every night (TOKEN_DAILY_EXPIRY_IST) and SEBI's framework expects a fresh 2FA/OAuth login
each trading day - no refresh tokens. How each broker logs in (`login_method`):
- `oauth`        the platform drives the redirect end to end (Upstox);
- `login_code`   the platform opens the broker's page, the operator pastes the one-time code (Fyers, Kite);
- `api_key`      a long-lived key with no daily session (crypto venues, PERMANENT_KEY_BROKERS);
- `manual`       the token is pasted in Settings (everything else).
On a trading day, `reminder_minutes_before_open` before the open (rule-set data), every organisation with an ACTIVE
deployment on a broker whose session will not last the day gets one WARNING naming the broker and how to log in.

BT1 (flag `byob_data_tier`, off by default): live data comes only through each user's own broker session, so each user
whose own credential is at a broker with confirmed data terms (Upstox, Dhan) and whose session will not last the day
gets their own pre-open reminder too ("Live data: log in to ..."), once per user per IST day. Flag off: nothing new.
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import token_lifecycle as tl
from app.compliance import rules
from app.core.enums import DeploymentStatus, NotificationSeverity, NotificationType
from app.db.models import BrokerCredentialRecord, NotificationRecord, StrategyDeploymentRecord
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day, load_holidays
from app.notifications.service import notify

RULE = "IN-SEBI.login.daily"
TITLE_PREFIX = "Log in to "
DATA_TITLE_PREFIX = "Live data: log in to "   # BT1: the per-user reminder for the bring-your-own-broker data tier
HOW = {
    "oauth": "Settings > Brokers > Log in (the broker's page opens and returns here)",
    "login_code": "Settings > Brokers > Open login page, then paste the code from the address bar",
    "api_key": "no daily login (API key)",
    "manual": "Settings > Brokers > paste today's access token",
}


def login_method(broker_name: str) -> str:
    name = (broker_name or "").lower()
    if name in tl.PERMANENT_KEY_BROKERS:
        return "api_key"
    if name in tl.OAUTH_BROKERS:
        return "oauth"
    if name in tl.LOGIN_URL_BROKERS:
        return "login_code"
    return "manual"


def reminder_time(day: date) -> datetime:
    minutes = int(rules.load().param(RULE, "reminder_minutes_before_open", 30))
    return datetime.combine(day, MARKET_OPEN, tzinfo=IST) - timedelta(minutes=minutes)


def lasts_the_session(record: Optional[BrokerCredentialRecord], day: date) -> bool:
    """Usable now *and* until today's close (a token that dies at 03:30 tomorrow is fine; one that is EXPIRED is not)."""
    close = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    return tl.token_is_usable(record, now=close)


def in_window(now: datetime) -> bool:
    """Between the reminder time and the close (IST). A worker started mid-morning still reminds; after the close, not."""
    now_ist = now.astimezone(IST)
    return reminder_time(now_ist.date()) <= now_ist and now_ist.time() < MARKET_CLOSE


async def due_reminders(session: AsyncSession, now: datetime, holidays=None) -> Dict[int, List[str]]:
    """tenant id -> brokers needing a login today, inside the window on an NSE trading day; else {}."""
    day = now.astimezone(IST).date()
    if holidays is None:
        holidays = await load_holidays(session, "NSE", day.year)
    if not in_window(now) or not is_trading_day(day, holidays):
        return {}
    wanted = await session.execute(select(StrategyDeploymentRecord.tenant_id, StrategyDeploymentRecord.broker_name).where(
        StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value, StrategyDeploymentRecord.broker_name.is_not(None)).distinct())
    due: Dict[int, List[str]] = {}
    for tenant_id, broker in wanted.all():
        if login_method(broker) == "api_key":
            continue
        records = list(await session.scalars(select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker)))
        if not any(lasts_the_session(r, day) for r in records):
            due.setdefault(tenant_id, []).append(broker)
    return {t: sorted(set(b)) for t, b in due.items()}


async def due_data_reminders(session: AsyncSession, now: datetime, holidays=None) -> Dict[Tuple[int, int], List[str]]:
    """BT1: (tenant id, user id) -> brokers with confirmed data terms whose session the user linked will not last
    today, for organisations with `byob_data_tier` on; inside the reminder window on an NSE trading day; else {}."""
    from app.market_data.data_terms import confirmed_brokers
    from app.market_data.fanin import FLAG
    from app.platform.controls import feature_flags

    flag = (await feature_flags(session)).get(FLAG) or {}
    if not (flag.get("on") or flag.get("tenants")):
        return {}
    day = now.astimezone(IST).date()
    if holidays is None:
        holidays = await load_holidays(session, "NSE", day.year)
    if not in_window(now) or not is_trading_day(day, holidays):
        return {}
    allowed = set(flag.get("tenants") or [])
    lasting: Dict[Tuple[int, int, str], bool] = {}
    for record in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name.in_(sorted(confirmed_brokers())))):
        if not (flag.get("on") or record.tenant_id in allowed) or login_method(record.broker_name) == "api_key":
            continue
        key = (record.tenant_id, record.user_id, record.broker_name)
        lasting[key] = lasting.get(key, False) or lasts_the_session(record, day)
    due: Dict[Tuple[int, int], List[str]] = {}
    for (tenant_id, user_id, broker), ok in lasting.items():
        if not ok:
            due.setdefault((tenant_id, user_id), []).append(broker)
    return {k: sorted(set(v)) for k, v in due.items()}


async def _data_reminder_sent(session: AsyncSession, tenant_id: int, user_id: int, now: datetime) -> bool:
    day_start = datetime.combine(now.astimezone(IST).date(), datetime.min.time(), tzinfo=IST)
    found = await session.scalar(select(NotificationRecord.id).where(
        NotificationRecord.tenant_id == tenant_id, NotificationRecord.user_id == user_id,
        NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value,
        NotificationRecord.title.startswith(DATA_TITLE_PREFIX), NotificationRecord.created_at >= day_start).limit(1))
    return found is not None


async def _already_sent(session: AsyncSession, tenant_id: int, now: datetime) -> bool:
    """One reminder per organisation per IST day, even across a worker restart."""
    day_start = datetime.combine(now.astimezone(IST).date(), datetime.min.time(), tzinfo=IST)
    found = await session.scalar(select(NotificationRecord.id).where(
        NotificationRecord.tenant_id == tenant_id, NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value,
        NotificationRecord.title.startswith(TITLE_PREFIX), NotificationRecord.created_at >= day_start).limit(1))
    return found is not None


async def send_reminders(session: AsyncSession, now: datetime, holidays=None) -> int:
    sent = 0
    for tenant_id, brokers in (await due_reminders(session, now, holidays)).items():
        if await _already_sent(session, tenant_id, now):
            continue
        lines = [f"{b}: {HOW[login_method(b)]}" for b in brokers]
        await notify(session, tenant_id, NotificationType.TOKEN_EXPIRED,
                     f"{TITLE_PREFIX}{', '.join(brokers)} before the open",
                     "Today's broker session is not active yet - deployments on it will not trade until you log in. "
                     + " | ".join(lines), severity=NotificationSeverity.WARNING)
        sent += 1
    for (tenant_id, user_id), brokers in (await due_data_reminders(session, now, holidays)).items():
        if await _data_reminder_sent(session, tenant_id, user_id, now):
            continue
        lines = [f"{b}: {HOW[login_method(b)]}" for b in brokers]
        await notify(session, tenant_id, NotificationType.TOKEN_EXPIRED,
                     f"{DATA_TITLE_PREFIX}{', '.join(brokers)} before the open",
                     "Your live prices, charts and alerts in ATP come through your own broker session; until you log in "
                     "today they show end-of-day data. " + " | ".join(lines), severity=NotificationSeverity.WARNING, user_id=user_id)
        sent += 1
    return sent
