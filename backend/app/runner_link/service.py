"""Runner S3 (docs/design/USER_RUNNER.md section 4): pairing, the signed heartbeat, the mirror uploads, list / revoke and the
"runner offline" alert. Server side only; the runner side of the link is S4.

- Pairing: the logged-in user creates a one-time code (shown once, stored hashed, PAIRING_CODE_MINUTES); the runner presents
  it with its new Ed25519 public key and a proof signature, and gets its device id. A code pairs one device, once.
- Heartbeat / mirror: signed by the device (app/runner_link/signing.py). ATP stores only what the device reports; the kill
  switch state is the device's own report and nothing in ATP sets it.
- Mirror: optional (the user's `runner` prefs, `mirror_upload`, ON by default), idempotent (one row per device and runner
  reference / audit sequence; a re-upload changes nothing unless the runner's row is newer), size-capped.
- Offline: no heartbeat within the user's own `offline_after_minutes` -> OFFLINE and one alert (bell + his own Telegram);
  the next heartbeat re-arms it.
Everything is the caller's own: every user route filters by his user id; there is no admin path.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Annotated

from app.db.models import (
    RunnerDeviceRecord,
    RunnerMirrorAuditRecord,
    RunnerMirrorOrderRecord,
    RunnerMirrorPositionRecord,
    RunnerPairingCodeRecord,
    User,
    UserPrefsRecord,
)
from app.runner_link import signing

logger = logging.getLogger(__name__)

FLAG = "user_runner"
PAGE_FLAG = "ui_v2"
PREFS = "runner"
PAIRING_CODE_MINUTES = 10            # how long a pairing code lives (operational)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0 / O / 1 / I: read off a screen and typed on a VPS
_CODE_LENGTH = 8
MAX_MIRROR_BYTES = 256 * 1024        # one mirror upload's body (operational; the runner sends in pages)
MAX_HEARTBEAT_BYTES = 4 * 1024
MAX_MIRROR_ROWS = 500                # per kind in one upload
IST = ZoneInfo("Asia/Kolkata")

PAIRED, ONLINE, OFFLINE, REVOKED = "PAIRED", "ONLINE", "OFFLINE", "REVOKED"


class RunnerError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _utc(value: Any) -> Any:
    """A runner's timestamp (any offset) stored in UTC; any other value as it is."""
    return value.astimezone(UTC) if isinstance(value, datetime) and value.tzinfo is not None else value


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- pairing ----------------------------------------------------------------------------------------------------------

def normalise_code(text: str) -> str:
    return re.sub(r"[\s-]", "", text or "").upper()


def _hash(code: str) -> str:
    return hashlib.sha256(normalise_code(code).encode()).hexdigest()


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


async def create_code(session: AsyncSession, user: User, now: datetime | None = None) -> tuple[str, datetime]:
    """A new one-time pairing code for the user (the plain code is returned once and never stored). His earlier codes
    that are still unused expire now: one live code per user."""
    now = now or datetime.now(UTC)
    await session.execute(update(RunnerPairingCodeRecord).where(
        RunnerPairingCodeRecord.user_id == user.id, RunnerPairingCodeRecord.used_at.is_(None),
        RunnerPairingCodeRecord.expires_at > now).values(expires_at=now))
    code = _new_code()
    expires = now + timedelta(minutes=PAIRING_CODE_MINUTES)
    session.add(RunnerPairingCodeRecord(tenant_id=user.tenant_id, user_id=user.id, code_hash=_hash(code), expires_at=expires,
                                        created_at=now))
    await session.flush()
    return code, expires


async def pair(session: AsyncSession, *, code: str, public_key: str, proof: str, name: str, version: str | None,
               flags_on: Any, now: datetime | None = None) -> RunnerDeviceRecord:
    """Spends the code and registers the device. Refuses a wrong, expired or used code with the same answer (nothing tells
    a guesser which), a public key that is not Ed25519, and a proof that does not verify. `flags_on(tenant_id)` -> bool."""
    now = now or datetime.now(UTC)
    public_key = (public_key or "").strip().lower()
    normal = normalise_code(code)
    if signing.load_public_key(public_key) is None:
        raise RunnerError("bad_key", "The runner's key is not valid. Start the pairing again on the runner.")
    if not signing.verify(public_key, proof, signing.pair_message(normal, public_key)):
        raise RunnerError("bad_proof", "The runner's key could not be checked. Start the pairing again on the runner.")
    row = await session.scalar(select(RunnerPairingCodeRecord).where(RunnerPairingCodeRecord.code_hash == _hash(normal)))
    expires = _aware(row.expires_at) if row is not None else None
    if row is None or row.used_at is not None or expires is None or expires <= now:
        raise RunnerError("bad_code", "This pairing code is wrong or has expired. Create a new code on My runner.")
    if not await flags_on(row.tenant_id):
        raise RunnerError("feature_off", "Not found", 404)
    spent = await session.execute(update(RunnerPairingCodeRecord).where(
        RunnerPairingCodeRecord.id == row.id, RunnerPairingCodeRecord.used_at.is_(None)).values(used_at=now))
    if getattr(spent, "rowcount", 0) != 1:                       # another request spent it first
        raise RunnerError("bad_code", "This pairing code is wrong or has expired. Create a new code on My runner.")
    device = RunnerDeviceRecord(tenant_id=row.tenant_id, user_id=row.user_id, device_uid=secrets.token_hex(16),
                                name=name, public_key=public_key, status=PAIRED, version=version, paired_at=now,
                                last_signed_ms=0)
    session.add(device)
    await session.flush()
    row.device_id = device.id
    return device


# --- signed device requests -------------------------------------------------------------------------------------------

async def authenticate(session: AsyncSession, *, method: str, path: str, headers: Any, body: bytes,
                       now: datetime | None = None) -> RunnerDeviceRecord:
    """The device behind a signed request, or RunnerError 401. Checks the signature, the replay window and that the
    request is newer than the device's last accepted one (an atomic compare-and-set, so a replay can never pass twice)."""
    now = now or datetime.now(UTC)
    uid = str(headers.get(signing.HEADER_DEVICE) or "").strip().lower()
    stamp = str(headers.get(signing.HEADER_TIMESTAMP) or "").strip()
    signature = str(headers.get(signing.HEADER_SIGNATURE) or "")
    refused = RunnerError("bad_signature", "Signature not accepted", 401)
    if not re.fullmatch(r"[0-9a-f]{32}", uid) or not stamp.isdigit():
        raise refused
    timestamp_ms = int(stamp)
    if not signing.within_window(timestamp_ms, int(now.timestamp() * 1000)):
        raise RunnerError("stale", "Signature not accepted: the runner's clock is out of step", 401)
    device = await session.scalar(select(RunnerDeviceRecord).where(RunnerDeviceRecord.device_uid == uid))
    if device is None or not signing.verify(device.public_key, signature, signing.canonical(method, path, timestamp_ms, uid, body)):
        raise refused
    if device.status == REVOKED or device.revoked_at is not None:
        raise RunnerError("revoked", "This runner was removed from your account. Pair it again on My runner.", 401)
    moved = await session.execute(update(RunnerDeviceRecord).where(
        RunnerDeviceRecord.id == device.id, RunnerDeviceRecord.last_signed_ms < timestamp_ms).values(last_signed_ms=timestamp_ms))
    if getattr(moved, "rowcount", 0) != 1:
        raise RunnerError("replay", "Signature not accepted: this request was already received", 401)
    await session.refresh(device)
    return device


# --- the user's own runner prefs --------------------------------------------------------------------------------------

async def user_prefs(session: AsyncSession, tenant_id: int, user_id: int) -> dict[str, Any]:
    from app.me.prefs import stored_value
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id, UserPrefsRecord.namespace == PREFS))
    return stored_value(PREFS, raw)


# --- heartbeat ----------------------------------------------------------------------------------------------------------

Version = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")]


class Heartbeat(BaseModel):
    """What the runner reports. Its kill switch is its own (the user's): ATP only records the state."""
    model_config = ConfigDict(extra="forbid")
    run_state: Literal["running", "paused", "stopped"]
    version: Version
    kill_switch_on: bool
    open_positions: int = Field(ge=0, le=100_000)
    broker_session: Literal["valid", "expired", "none"] | None = None


async def heartbeat(session: AsyncSession, device: RunnerDeviceRecord, beat: Heartbeat, egress_ip: str | None,
                    now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    device.run_state = beat.run_state
    device.version = beat.version
    device.kill_switch_on = beat.kill_switch_on
    device.open_positions = beat.open_positions
    device.broker_session = beat.broker_session
    device.egress_ip = (egress_ip or "")[:45] or None
    device.last_heartbeat_at = now
    device.status = ONLINE
    device.offline_alerted_at = None                            # back online: the next offline spell alerts again
    prefs = await user_prefs(session, device.tenant_id, device.user_id)
    # S4 adds the user's own commands (start / pause / stop, kill switch, square-off) to this answer.
    return {"ok": True, "device_status": ONLINE, "server_time": now.isoformat(), "mirror_upload": bool(prefs["mirror_upload"]),
            "commands": []}


# --- mirror -------------------------------------------------------------------------------------------------------------

Ref = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")]
Symbol = Annotated[str, StringConstraints(min_length=1, max_length=60)]
Exchange = Annotated[str, StringConstraints(min_length=1, max_length=10, pattern=r"^[A-Z_]{2,10}$")]
Short = Annotated[str, StringConstraints(max_length=300)]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Number = Field(default=None, allow_inf_nan=False)


class _Row(BaseModel):
    # extra="forbid": a field the schema does not name (a key, a token, anything) refuses the whole upload.
    model_config = ConfigDict(extra="forbid")


class MirrorOrder(_Row):
    ref: Ref
    deployment_ref: Ref | None = None
    mode: Literal["PAPER", "LIVE"]
    symbol: Symbol
    exchange: Exchange
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"]
    quantity: float = Field(ge=0, allow_inf_nan=False)
    price: float | None = Number
    trigger_price: float | None = Number
    status: Annotated[str, StringConstraints(min_length=1, max_length=20, pattern=r"^[A-Z_]{1,20}$")]
    filled_quantity: float | None = Number
    average_price: float | None = Number
    reason: Short | None = None
    placed_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class MirrorPosition(_Row):
    ref: Ref
    deployment_ref: Ref | None = None
    mode: Literal["PAPER", "LIVE"]
    symbol: Symbol
    exchange: Exchange
    status: Literal["OPEN", "CLOSED"]
    quantity: float = Field(allow_inf_nan=False)
    average_price: float | None = Number
    last_price: float | None = Number
    unrealised_pnl: float | None = Number
    realised_pnl: float | None = Number
    exit_reason: Annotated[str, StringConstraints(max_length=120)] | None = None
    opened_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class MirrorAudit(_Row):
    seq: int = Field(ge=0)
    action: Annotated[str, StringConstraints(min_length=1, max_length=60, pattern=r"^[a-z0-9_.:-]{1,60}$")]
    summary: Short
    row_hash: Hash
    prev_hash: Hash | None = None
    occurred_at: AwareDatetime


class MirrorUpload(_Row):
    orders: list[MirrorOrder] = Field(default_factory=list, max_length=MAX_MIRROR_ROWS)
    positions: list[MirrorPosition] = Field(default_factory=list, max_length=MAX_MIRROR_ROWS)
    audit: list[MirrorAudit] = Field(default_factory=list, max_length=MAX_MIRROR_ROWS)


_ORDER_FIELDS = ("deployment_ref", "mode", "symbol", "exchange", "side", "order_type", "quantity", "price", "trigger_price",
                 "status", "filled_quantity", "average_price", "reason", "placed_at", "updated_at")
_POSITION_FIELDS = ("deployment_ref", "mode", "symbol", "exchange", "status", "quantity", "average_price", "last_price",
                    "unrealised_pnl", "realised_pnl", "exit_reason", "opened_at", "closed_at", "updated_at")


async def _upsert(session: AsyncSession, device: RunnerDeviceRecord, model: Any, rows: list[Any], fields: tuple[str, ...],
                  now: datetime, counts: dict[str, int]) -> None:
    latest: dict[str, Any] = {}
    for row in rows:                                            # the same ref twice in one upload: the newest wins
        if row.ref not in latest or row.updated_at > latest[row.ref].updated_at:
            latest[row.ref] = row
    if not latest:
        return
    stored = {r.runner_ref: r for r in await session.scalars(select(model).where(
        model.device_id == device.id, model.runner_ref.in_(list(latest))))}
    for ref, row in latest.items():
        existing = stored.get(ref)
        values = {f: _utc(getattr(row, f)) for f in fields}
        if existing is None:
            session.add(model(tenant_id=device.tenant_id, user_id=device.user_id, device_id=device.id, runner_ref=ref,
                              received_at=now, **values))
            counts["inserted"] += 1
        elif row.updated_at > (_aware(existing.updated_at) or row.updated_at):
            for field, value in values.items():
                setattr(existing, field, value)
            existing.received_at = now
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1


async def mirror(session: AsyncSession, device: RunnerDeviceRecord, upload: MirrorUpload, now: datetime | None = None) -> dict[str, int]:
    """Stores one upload. Idempotent: sending the same upload again inserts and changes nothing. An audit row already
    stored is never overwritten (the runner's log is append-only); a different hash for a stored sequence is counted
    in `conflicts` and kept out."""
    now = now or datetime.now(UTC)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 0}
    await _upsert(session, device, RunnerMirrorOrderRecord, upload.orders, _ORDER_FIELDS, now, counts)
    await _upsert(session, device, RunnerMirrorPositionRecord, upload.positions, _POSITION_FIELDS, now, counts)
    audit = {a.seq: a for a in upload.audit}
    if audit:
        stored = {r.seq: r.row_hash for r in await session.scalars(select(RunnerMirrorAuditRecord).where(
            RunnerMirrorAuditRecord.device_id == device.id, RunnerMirrorAuditRecord.seq.in_(list(audit))))}
        for seq, row in sorted(audit.items()):
            if seq in stored:
                counts["unchanged" if stored[seq] == row.row_hash else "conflicts"] += 1
                continue
            session.add(RunnerMirrorAuditRecord(tenant_id=device.tenant_id, user_id=device.user_id, device_id=device.id, seq=seq,
                                                action=row.action, summary=row.summary, row_hash=row.row_hash,
                                                prev_hash=row.prev_hash, occurred_at=_utc(row.occurred_at), received_at=now))
            counts["inserted"] += 1
    await session.flush()
    return counts


# --- the user's own devices -----------------------------------------------------------------------------------------------

def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


def device_dict(device: RunnerDeviceRecord) -> dict[str, Any]:
    return {"device_id": device.device_uid, "name": device.name, "status": device.status, "version": device.version,
            "paired_at": _iso(device.paired_at), "last_heartbeat_at": _iso(device.last_heartbeat_at),
            "revoked_at": _iso(device.revoked_at), "run_state": device.run_state, "kill_switch_on": device.kill_switch_on,
            "open_positions": device.open_positions, "broker_session": device.broker_session, "egress_ip": device.egress_ip}


async def list_devices(session: AsyncSession, user: User) -> list[RunnerDeviceRecord]:
    return list(await session.scalars(select(RunnerDeviceRecord).where(
        RunnerDeviceRecord.tenant_id == user.tenant_id, RunnerDeviceRecord.user_id == user.id).order_by(RunnerDeviceRecord.id)))


async def own_device(session: AsyncSession, user: User, device_uid: str) -> RunnerDeviceRecord:
    device = await session.scalar(select(RunnerDeviceRecord).where(
        RunnerDeviceRecord.device_uid == (device_uid or "").lower(), RunnerDeviceRecord.tenant_id == user.tenant_id,
        RunnerDeviceRecord.user_id == user.id))
    if device is None:
        raise RunnerError("not_found", "No such runner", 404)
    return device


async def revoke(session: AsyncSession, user: User, device_uid: str, now: datetime | None = None) -> RunnerDeviceRecord:
    """The user removes his own runner from his account: its signed requests are refused from now on. Nothing is sent to
    the runner and nothing on it is stopped (its exits keep running on his machine, ADR-0004)."""
    device = await own_device(session, user, device_uid)
    if device.revoked_at is None:
        device.revoked_at = now or datetime.now(UTC)
        device.status = REVOKED
    return device


async def mirror_view(session: AsyncSession, device: RunnerDeviceRecord, limit: int = 200) -> dict[str, Any]:
    def plain(row: Any, names: tuple[str, ...]) -> dict[str, Any]:
        out = {"ref": getattr(row, "runner_ref", None)}
        for name in names:
            value = getattr(row, name)
            out[name] = _iso(value) if isinstance(value, datetime) else value
        return out
    orders = await session.scalars(select(RunnerMirrorOrderRecord).where(RunnerMirrorOrderRecord.device_id == device.id)
                                   .order_by(RunnerMirrorOrderRecord.updated_at.desc()).limit(limit))
    positions = await session.scalars(select(RunnerMirrorPositionRecord).where(RunnerMirrorPositionRecord.device_id == device.id)
                                      .order_by(RunnerMirrorPositionRecord.updated_at.desc()).limit(limit))
    audit = await session.scalars(select(RunnerMirrorAuditRecord).where(RunnerMirrorAuditRecord.device_id == device.id)
                                  .order_by(RunnerMirrorAuditRecord.seq.desc()).limit(limit))
    return {"orders": [plain(r, _ORDER_FIELDS) for r in orders],
            "positions": [plain(r, _POSITION_FIELDS) for r in positions],
            "audit": [{"seq": r.seq, "action": r.action, "summary": r.summary, "row_hash": r.row_hash, "prev_hash": r.prev_hash,
                       "occurred_at": _iso(r.occurred_at)} for r in audit]}


# --- runner offline -----------------------------------------------------------------------------------------------------------

async def flags_on(session: AsyncSession, tenant_id: int) -> bool:
    """`ui_v2` and `user_runner` both on for the organisation."""
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, FLAG):
        if not await flag_enabled(session, name, tenant_id):
            return False
    return True


def offline_text(name: str, last_heartbeat_at: datetime | None) -> tuple[str, str]:
    since = _aware(last_heartbeat_at)
    when = since.astimezone(IST).strftime("%d %b %H:%M IST") if since is not None else "-"
    return (f"Runner offline: {name}"[:200],
            f"Your runner \"{name}\" has not reported since {when}. Check that its machine is on and online.")


async def check_offline(session: AsyncSession, now: datetime | None = None, client: Any = None) -> int:
    """The trading worker, every minute: each ONLINE device whose last heartbeat is older than its user's own
    `offline_after_minutes` turns OFFLINE and raises ONE alert (the move is a compare-and-set on the status and the heartbeat
    seen, so a second worker replica or the next run never alerts twice, and a heartbeat that lands in between wins).
    Returns how many alerts were raised."""
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    now = now or datetime.now(UTC)
    candidates = (await session.execute(select(
        RunnerDeviceRecord.id, RunnerDeviceRecord.tenant_id, RunnerDeviceRecord.user_id, RunnerDeviceRecord.name,
        RunnerDeviceRecord.last_heartbeat_at).where(
        RunnerDeviceRecord.status == ONLINE, RunnerDeviceRecord.last_heartbeat_at.is_not(None)).order_by(RunnerDeviceRecord.id))).all()
    flags: dict[int, bool] = {}
    raised = 0
    for device_id, tenant_id, user_id, name, seen in candidates:
        try:
            if tenant_id not in flags:
                flags[tenant_id] = await flags_on(session, tenant_id)
            if not flags[tenant_id]:
                continue
            prefs = await user_prefs(session, tenant_id, user_id)
            last = _aware(seen)
            if last is None or now - last < timedelta(minutes=int(prefs["offline_after_minutes"])):
                continue
            moved = await session.execute(update(RunnerDeviceRecord).where(
                RunnerDeviceRecord.id == device_id, RunnerDeviceRecord.status == ONLINE,
                RunnerDeviceRecord.last_heartbeat_at == seen).values(status=OFFLINE, offline_alerted_at=now))
            if getattr(moved, "rowcount", 0) != 1:
                await session.rollback()
                continue
            title, message = offline_text(name, seen)
            await notify(session, tenant_id, NotificationType.RUNNER_OFFLINE, title=title, message=message,
                         severity=NotificationSeverity.WARNING, user_id=user_id, channels=False)   # commits
            raised += 1
            if prefs.get("offline_telegram"):
                await _telegram(session, tenant_id, user_id, title, message, client)
        except Exception as exc:  # noqa: BLE001 - one device never stops the others; tried again on the next run
            logger.warning("Runner offline check for device %s failed: %s", device_id, type(exc).__name__)
            await session.rollback()
    return raised


async def _telegram(session: AsyncSession, tenant_id: int, user_id: int, title: str, message: str, client: Any) -> None:
    try:
        from app.alerts.channels import decrypt_config
        from app.alerts.dispatcher import send_telegram
        from app.rhythm.deliver import destination
        user = await session.get(User, user_id)
        target = await destination(session, user) if user is not None and user.tenant_id == tenant_id else None
        if target is None:
            return
        text = f"<b>{html.escape(title)}</b>\n{html.escape(message)}"
        await send_telegram(decrypt_config(target[0]), text, client, chat_id=target[1])  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the alert stays in the app; Telegram is best effort
        logger.info("Runner offline Telegram for user %s not sent: %s", user_id, type(exc).__name__)
