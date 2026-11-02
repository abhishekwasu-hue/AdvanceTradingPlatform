"""Part D5 (rule IN-SEBI.login.daily): the daily broker login, the same for every adapter, and a pre-open reminder.

Retail broker sessions die every night (TOKEN_DAILY_EXPIRY_IST) and SEBI's framework expects a fresh 2FA/OAuth login
each trading day - no refresh tokens. How each broker logs in (`login_method`, BRK-1 B):
- `analytics`    Upstox: the pasted Analytics Token (one year, read-only) - no Upstox OAuth flow;
- `oauth`        Zerodha (Kite), Fyers: the broker's page opens and returns to ATP's callback, nothing is pasted;
- `totp`         Angel One, Shoonya, Kotak Neo, Groww, Dhan: ATP logs in by itself before the open (app/brokers/auto_login.py);
- `api_key`      a long-lived key with no daily session (crypto venues, PERMANENT_KEY_BROKERS);
- `manual`       the token is pasted on the Broker page (anything else);
- `login_code`   (kept for older rows' wording) the operator pastes the one-time code from the address bar.
On a trading day, `reminder_minutes_before_open` before the open (rule-set data), every organisation with an ACTIVE
deployment on a broker whose session will not last the day gets one WARNING naming the broker and how to log in.

BRK-1 B, every broker connection: one reminder (in-app and the organisation's channels such as Telegram) to the member who
linked it, BEFORE its session ends - `reminder_minutes_before_open` before the open when the session will already have
ended by then, or that long before a session that ends during market hours - once per connection per IST day, on NSE
trading days only (`send_connection_reminders`). A TOTP connection is reminded only once ATP's own login has given up
(that give-up is the reminder); a connection disconnected on purpose, never proven, or with no daily login is not.

BT1 (flag `byob_data_tier`, off by default): live data comes only through each user's own broker session, so each user
whose own credential is at a broker with confirmed data terms (Upstox, Dhan) and whose session will not last the day
gets their own pre-open reminder too ("Live data: log in to ..."), once per user per IST day. Flag off: nothing new.
"""
import json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import catalog as cat
from app.brokers import token_lifecycle as tl
from app.compliance import rules
from app.core.enums import BrokerTokenStatus, DeploymentStatus, NotificationSeverity, NotificationType
from app.db.models import BrokerCredentialRecord, NotificationRecord, StrategyDeploymentRecord
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day, load_holidays
from app.notifications.service import notify

RULE = "IN-SEBI.login.daily"
TITLE_PREFIX = "Log in to "
DATA_TITLE_PREFIX = "Live data: log in to "   # BT1: the per-user reminder for the bring-your-own-broker data tier
CONNECTION_REMINDER = "broker_session_reminder"   # BRK-1 B: the per-connection reminder's metadata kind (its dedupe key)
HOW = {
    "analytics": "Broker page > paste your Upstox Analytics Token (valid one year)",
    "oauth": "Broker page > Log in, then sign in on the broker's page (ATP finishes the login)",
    "totp": "ATP logs in for you before the open; if it could not, Broker page > Renew",
    "login_code": "Broker page > Open login page, then paste the code from the address bar",
    "api_key": "no daily login (API key)",
    "manual": "Broker page > paste today's access token",
}


def login_method(broker_name: str) -> str:
    name = (broker_name or "").lower()
    if name in tl.PERMANENT_KEY_BROKERS:
        return "api_key"
    entry = cat.entry(name)
    if entry is not None and entry.method == cat.ANALYTICS_TOKEN:
        return "analytics"
    if name in tl.OAUTH_CALLBACK_BROKERS:
        return "oauth"
    if entry is not None and entry.method == cat.TOTP_AUTO:
        return "totp"
    if name in tl.LOGIN_URL_BROKERS:
        return "login_code"
    return "manual"


def is_active_connection(record: BrokerCredentialRecord) -> bool:
    """BRK-1 B: a connection ATP keeps logging in to and reminds about - proven at least once, and not disconnected on
    purpose (POST /disconnect leaves it EXPIRED with no expiry; a rejected login keeps its expiry)."""
    if record.last_verified_at is None:
        return False
    return not (record.token_status == BrokerTokenStatus.EXPIRED.value and record.token_expires_at is None)


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
    """One reminder per organisation per IST day, even across a worker restart. (BRK-1 B: the organisation-wide one only,
    which is addressed to nobody; a per-connection reminder is addressed to its holder.)"""
    day_start = datetime.combine(now.astimezone(IST).date(), datetime.min.time(), tzinfo=IST)
    found = await session.scalar(select(NotificationRecord.id).where(
        NotificationRecord.tenant_id == tenant_id, NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value,
        NotificationRecord.title.startswith(TITLE_PREFIX), NotificationRecord.user_id.is_(None),
        NotificationRecord.created_at >= day_start).limit(1))
    return found is not None


async def send_reminders(session: AsyncSession, now: datetime, holidays=None) -> int:
    sent = 0
    for tenant_id, due_brokers in (await due_reminders(session, now, holidays)).items():
        if await _already_sent(session, tenant_id, now):
            continue
        # BRK-1 B: a broker whose connection was already reminded today (or whose automatic login gave up and said so) is
        # not named again - one reminder per broker session.
        covered = {broker for broker, _ in await reminded_today(session, tenant_id, now)}
        brokers = [b for b in due_brokers if b not in covered]
        if not brokers:
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


# --- BRK-1 B: one reminder per broker connection, before its session ends ----------------------------------------------

def _lead() -> timedelta:
    return timedelta(minutes=int(rules.load().param(RULE, "reminder_minutes_before_open", 30)))


def connection_due_at(record: BrokerCredentialRecord, day: date) -> Optional[datetime]:
    """When today's reminder for this connection is due: the lead time before the open when its session will not be
    usable at the open, or before its end when it ends during market hours; None when it lasts the session."""
    if lasts_the_session(record, day):
        return None
    open_at = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    expires = tl._as_utc(record.token_expires_at)
    target = expires.astimezone(IST) if tl.token_is_usable(record, now=open_at) and expires is not None else open_at
    return target - _lead()


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(IST).date(), datetime.min.time(), tzinfo=IST)


async def reminded_today(session: AsyncSession, tenant_id: int, now: datetime) -> Set[Tuple[str, int]]:
    """(broker, credential id) of every connection reminder this organisation got today (IST), across restarts."""
    today = now.astimezone(IST).date().isoformat()
    rows = await session.scalars(select(NotificationRecord.metadata_json).where(
        NotificationRecord.tenant_id == tenant_id, NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value,
        NotificationRecord.title.startswith(TITLE_PREFIX), NotificationRecord.metadata_json.is_not(None),
        NotificationRecord.created_at >= _day_start(now)))
    out: Set[Tuple[str, int]] = set()
    for raw in rows:
        try:
            meta = json.loads(raw or "")
        except ValueError:
            continue
        if isinstance(meta, dict) and meta.get("kind") == CONNECTION_REMINDER and meta.get("day") == today:
            out.add((str(meta.get("broker") or ""), int(meta.get("credential_id") or 0)))
    return out


def connection_title(record: BrokerCredentialRecord) -> str:
    label = record.account_label or "primary"
    return f"{TITLE_PREFIX}{cat.display_name(record.broker_name)}" + ("" if label == "primary" else f" ({label})")


async def remind_connection(session: AsyncSession, record: BrokerCredentialRecord, now: datetime, title: str,
                            message: str) -> None:
    """The one reminder for this connection today: in-app for its holder and on the organisation's channels (Telegram
    and the like, by their severity floors). Commits."""
    meta = {"kind": CONNECTION_REMINDER, "credential_id": int(record.id), "broker": record.broker_name,
            "account_label": record.account_label or "primary", "day": now.astimezone(IST).date().isoformat()}
    await notify(session, record.tenant_id, NotificationType.TOKEN_EXPIRED, title[:200], message,
                 severity=NotificationSeverity.WARNING, user_id=record.user_id, metadata_json=json.dumps(meta))


async def send_connection_reminders(session: AsyncSession, now: datetime, holidays=None) -> int:
    """Every active connection with a daily login whose session will not last today's session gets one reminder before
    it ends (see the module notes). NSE trading days only, until the close. Returns how many were sent."""
    from app.brokers import auto_login

    now_ist = now.astimezone(IST)
    day = now_ist.date()
    if now_ist.time() >= MARKET_CLOSE:
        return 0
    if holidays is None:
        holidays = await load_holidays(session, "NSE", day.year)
    if not is_trading_day(day, holidays):
        return 0
    daily = sorted(e.broker for e in cat.CATALOG if e.daily_renewal)
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name.in_(daily))
                                         .order_by(BrokerCredentialRecord.id)))
    done: Dict[int, Set[int]] = {}
    sent = 0
    for record in records:
        if not is_active_connection(record):
            continue
        due_at = connection_due_at(record, day)
        if due_at is None or now < due_at:
            continue
        has_secret, trying = auto_login.auto_state(record, now)
        if trying:
            continue                          # ATP's own login is still going; its give-up is the reminder
        if record.tenant_id not in done:
            done[record.tenant_id] = {cid for _, cid in await reminded_today(session, record.tenant_id, now)}
        if record.id in done[record.tenant_id]:
            continue
        name = cat.display_name(record.broker_name)
        method = login_method(record.broker_name)
        how = HOW[method]
        if method == "totp" and not has_secret:
            # No TOTP secret on this row: Dhan's pasted token (paste today's), or Groww's approval key (approve, then Renew).
            entry = cat.entry(record.broker_name)
            how = HOW["manual"] if entry is not None and entry.fallback_renew_fields else "Broker page > Renew"
        expires = tl._as_utc(record.token_expires_at)
        if tl.token_is_usable(record, now=now) and expires is not None:
            text = f"Your {name} session ends at {expires.astimezone(IST).strftime('%H:%M')} IST today. {how}."
        else:
            text = f"Your {name} session is not active for today's market yet; nothing trades through it until you log in. {how}."
        await remind_connection(session, record, now, connection_title(record) + " before the session ends", text)
        done[record.tenant_id].add(record.id)
        sent += 1
    return sent
