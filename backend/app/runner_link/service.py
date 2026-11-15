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
- S4: the runner pulls his own saved rules (`rules_snapshot`: his strategies, his deployments' own settings, his trading
  settings - never a signal, an evaluation or a platform control) and the day's reference data (`reference`: the F&O ban
  list, the derivative contracts of his deployments' symbols); his own kill switch button creates a command signed with
  ATP's command key for his own device only (`create_kill_command`), delivered in the heartbeat answer and acked by the
  runner. The runner applies it; nothing in ATP sets its kill switch.
Everything is the caller's own: every user route filters by his user id; there is no admin path.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Annotated

from app.db.models import (
    CustomStrategyRecord,
    FoBanRecord,
    InstrumentRecord,
    RiskSettingsRecord,
    RunnerCommandRecord,
    RunnerDeviceRecord,
    RunnerMirrorAuditRecord,
    RunnerMirrorOrderRecord,
    RunnerMirrorPositionRecord,
    RunnerPairingCodeRecord,
    StrategyDeploymentRecord,
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
MAX_COMMANDS = 20                    # commands in one heartbeat answer / acks in one heartbeat (operational)
MAX_INSTRUMENTS = 5000               # derivative contracts in one reference answer (operational)
# S4: the user's own trading settings the runner's order path reads (prefs namespaces; app/execution/exit_settings.py,
# order_defaults.py, risk_engine/own_risk.py) - his own values, synced as they are.
SETTINGS_NAMESPACES: tuple[str, ...] = ("trade_settings", "trading_defaults", "smart_execution", "strategy_exits", "my_limits")
# Deployment columns that are the server's run state (evaluations, signals seen, errors) or server ids - never synced
# (rule R-2: ATP sends his saved rules and settings, never a signal or an evaluation).
DEPLOYMENT_NOT_SYNCED: frozenset[str] = frozenset({
    "tenant_id", "created_by", "last_evaluated_at", "last_signal_at", "last_error", "consecutive_failures", "wait_reason",
    "data_failures", "waiting_for_data_since", "last_route", "broker_account_id", "created_at", "updated_at"})
RISK_NOT_SYNCED: frozenset[str] = frozenset({"id", "tenant_id", "updated_by", "created_at", "updated_at"})
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
    # S4: the runner's daily login line (broker_login.status_line - never a value) and the commands it has applied.
    broker_login: Annotated[str, StringConstraints(max_length=200)] | None = None
    acked_commands: list[int] = Field(default_factory=list, max_length=MAX_COMMANDS)


async def heartbeat(session: AsyncSession, device: RunnerDeviceRecord, beat: Heartbeat, egress_ip: str | None,
                    now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    device.run_state = beat.run_state
    device.version = beat.version
    device.kill_switch_on = beat.kill_switch_on
    device.open_positions = beat.open_positions
    device.broker_session = beat.broker_session
    device.broker_login = beat.broker_login
    device.egress_ip = (egress_ip or "")[:45] or None
    device.last_heartbeat_at = now
    device.status = ONLINE
    device.offline_alerted_at = None                            # back online: the next offline spell alerts again
    prefs = await user_prefs(session, device.tenant_id, device.user_id)
    commands = await _commands_for(session, device, beat.acked_commands, now)
    etag = (await rules_snapshot(session, device))["etag"]
    return {"ok": True, "device_status": ONLINE, "server_time": now.isoformat(), "mirror_upload": bool(prefs["mirror_upload"]),
            "commands": commands, "rules_etag": etag, "state": await runner_state(session, device, now)}


# --- S4c: ATP's state for his runner (signed) ---------------------------------------------------------------------------

# The feature flags the runner's own pipeline reads (the runner loop's imported code, checked by a test that records every
# flag the loop asks for): ATP sends his organisation's answer for each, the runner writes them into its local store so
# the same code reads the same answer as on ATP's server. Nothing else of ATP's controls is sent (no admin kill switch,
# no maintenance block - rule R-1).
RUNNER_FLAGS: tuple[str, ...] = (
    "smart_execution",          # multi-leg: hedge legs first on the smart-execution engine (execution/smart_execution.py)
    "approval_only_entries",    # the approval-only gate (execution/approval.py)
    "risk_by_default",          # the risk defaults the pipeline applies (risk_engine/by_default.py)
    "risk_options",             # his own extra risk options (risk_engine/risk_options.py)
    "vix_gate",                 # his own VIX setting (risk_engine/vix_gate.py)
    "paper_account",            # the PAPER account's balance and charges (paper/service.py)
    "experience_levels",        # the experience ladder's limits (paper/experience.py)
    "discipline_engine",        # his discipline rules (journal, paper/service.py)
)


async def runner_state(session: AsyncSession, device: RunnerDeviceRecord, now: datetime) -> dict[str, Any]:
    """S4c: ATP's state for this device's user, signed with ATP's command key under its own scheme
    (`signing.state_message`): `live_open` for HIS organisation by the one rule every ATP path uses
    (`live_switch.live_allowed`: the live switch, and with B1 the owner's organisation only), its plain reason when
    closed, his organisation's answer for each of `RUNNER_FLAGS`, the server's F&O ban check setting and his plan's
    `runner` field (None while plans carry none). The runner applies it each tick while it is fresh; it decides no trade."""
    from app.core import config
    from app.db.models import Tenant
    from app.plans.registry import get_plan
    from app.platform import live_switch
    from app.platform.controls import features_for_tenant
    open_ = bool(await live_switch.live_allowed(session, device.tenant_id))
    reason = None if open_ else await live_switch.live_refusal(session, device.tenant_id)
    answers = await features_for_tenant(session, device.tenant_id)
    tenant = await session.get(Tenant, device.tenant_id)
    plan_runner = getattr(get_plan(tenant.plan), "runner", None) if tenant is not None else None
    payload = {"v": 1, "kind": "state", "device_id": device.device_uid, "user_ref": device.user_id,
               "tenant_ref": device.tenant_id, "issued_at": now.isoformat(), "live_open": open_, "live_reason": reason,
               "flags": {name: bool(answers.get(name, True)) for name in RUNNER_FLAGS},
               "fo_ban_check": bool(config.FO_BAN_CHECK_ENABLED),
               "plan_runner": None if plan_runner is None else bool(plan_runner)}
    return {"payload": payload, "signature": signing.sign(_command_key(), signing.state_message(payload))}


# --- S4: the user's own commands for his own runner (rule R-1) ---------------------------------------------------------

def _command_key() -> Ed25519PrivateKey:
    """ATP's command key: derived from the server's own secret (HKDF, its own label), never stored, never sent. Rotating
    that secret changes the key: the runner then refuses commands until it is paired again (it says so in its log)."""
    from app.core import config
    seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"atp-runner-command-key-1").derive(
        str(config.JWT_SECRET_KEY).encode())
    return Ed25519PrivateKey.from_private_bytes(seed)


def command_public_key() -> str:
    """The public half the runner pins at pairing (64 hex)."""
    return signing.public_hex(_command_key())


async def create_kill_command(session: AsyncSession, user: User, device_uid: str, on: bool,
                              now: datetime | None = None) -> RunnerCommandRecord:
    """The logged-in user's own kill switch button for HIS OWN runner (rule R-1): a command signed for that device and
    that user. Another member's, an admin's or a removed runner's device answers "No such runner" / refused. It stops new
    entries only on the runner; exits always run there (ADR-0004)."""
    now = now or datetime.now(UTC)
    device = await own_device(session, user, device_uid)                 # his own device only (tenant + user)
    if device.revoked_at is not None or device.status == REVOKED:
        raise RunnerError("revoked", "This runner was removed from your account. Pair it again on My runner.", 409)
    row = RunnerCommandRecord(tenant_id=device.tenant_id, user_id=user.id, device_id=device.id, kind="kill_switch",
                              payload_json="{}", signature="-", created_at=now)
    session.add(row)
    await session.flush()
    payload = {"v": 1, "command_id": row.id, "device_id": device.device_uid, "user_ref": user.id, "kind": "kill_switch",
               "on": bool(on), "origin": signing.COMMAND_ORIGIN, "issued_at": now.isoformat()}
    row.payload_json = signing.command_bytes(payload).decode()
    row.signature = signing.sign(_command_key(), signing.command_message(payload))
    await session.flush()
    return row


async def _commands_for(session: AsyncSession, device: RunnerDeviceRecord, acked: list[int], now: datetime) -> list[dict[str, Any]]:
    """Marks the device's own acked commands, then returns its pending ones (oldest first) as signed by ATP."""
    if acked:
        await session.execute(update(RunnerCommandRecord).where(
            RunnerCommandRecord.device_id == device.id, RunnerCommandRecord.id.in_(list(acked)),
            RunnerCommandRecord.acked_at.is_(None)).values(acked_at=now))
    pending = list(await session.scalars(select(RunnerCommandRecord).where(
        RunnerCommandRecord.device_id == device.id, RunnerCommandRecord.user_id == device.user_id,
        RunnerCommandRecord.acked_at.is_(None)).order_by(RunnerCommandRecord.id).limit(MAX_COMMANDS)))
    for row in pending:
        row.delivered_at = row.delivered_at or now
    return [{"id": row.id, "payload": json.loads(row.payload_json), "signature": row.signature} for row in pending]


# --- S4: his own saved rules and the day's reference data ---------------------------------------------------------------

def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return (_aware(value) or value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


async def rules_snapshot(session: AsyncSession, device: RunnerDeviceRecord) -> dict[str, Any]:
    """HIS saved rules for his runner (rules R-1 / R-2 / R-3): his own strategies, his own ACTIVE / PAUSED deployments with
    their own settings (lots, legs, strikes, stops, targets, exits) and his own trading settings. Never a signal, an
    evaluation, another member's row, a platform control, an admin kill switch or a maintenance block. `etag`: a hash of
    the rest, so the runner pulls only when something changed."""
    tenant_id, user_id = device.tenant_id, device.user_id
    strategies = [{"id": r.id, "name": r.name, "config_json": r.config_json, "origin": r.origin}
                  for r in await session.scalars(select(CustomStrategyRecord).where(
                      CustomStrategyRecord.tenant_id == tenant_id, CustomStrategyRecord.user_id == user_id)
                      .order_by(CustomStrategyRecord.id))]
    columns = [c.name for c in StrategyDeploymentRecord.__table__.columns if c.name not in DEPLOYMENT_NOT_SYNCED]
    deployments = [{name: _plain(getattr(r, name)) for name in columns}
                   for r in await session.scalars(select(StrategyDeploymentRecord).where(
                       StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.created_by == user_id,
                       StrategyDeploymentRecord.status.in_(("ACTIVE", "PAUSED"))).order_by(StrategyDeploymentRecord.id))]
    prefs = {ns: value for ns, value in (await session.execute(select(UserPrefsRecord.namespace, UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
        UserPrefsRecord.namespace.in_(SETTINGS_NAMESPACES)).order_by(UserPrefsRecord.namespace))).all()}
    risk_row = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
    risk = None if risk_row is None else {c.name: _plain(getattr(risk_row, c.name))
                                          for c in RiskSettingsRecord.__table__.columns if c.name not in RISK_NOT_SYNCED}
    body: dict[str, Any] = {"user_ref": user_id, "org_ref": tenant_id, "strategies": strategies, "deployments": deployments,
                            "settings": {"prefs": prefs, "risk": risk}}
    body["etag"] = hashlib.sha256(signing.command_bytes(body)).hexdigest()
    return body


async def reference(session: AsyncSession, device: RunnerDeviceRecord, now: datetime | None = None) -> dict[str, Any]:
    """The day's reference data the runner's entry checks read: the exchange's F&O ban list as ATP last stored it (None
    while ATP has none - the runner then keeps refusing LIVE F&O entries in plain words) and the live derivative contracts
    of the symbols in his own deployments. Market data is not sent (the runner reads his own broker)."""
    now = now or datetime.now(UTC)
    latest = await session.scalar(select(FoBanRecord.trade_date).order_by(FoBanRecord.trade_date.desc()).limit(1))
    ban = None
    if latest is not None:
        names = sorted(await session.scalars(select(FoBanRecord.underlying).where(FoBanRecord.trade_date == latest)))
        ban = {"trade_date": latest.isoformat(), "underlyings": names}
    symbols = sorted({s.upper() for s in await session.scalars(select(StrategyDeploymentRecord.symbol).where(
        StrategyDeploymentRecord.tenant_id == device.tenant_id, StrategyDeploymentRecord.created_by == device.user_id,
        StrategyDeploymentRecord.status.in_(("ACTIVE", "PAUSED"))))})
    today = now.astimezone(IST).date()
    fields = ("broker", "exchange", "segment", "instrument_key", "tradingsymbol", "name", "underlying", "instrument_type",
              "expiry", "strike", "lot_size", "tick_size", "weekly")
    instruments: list[dict[str, Any]] = []
    if symbols:
        rows = await session.scalars(select(InstrumentRecord).where(
            InstrumentRecord.underlying.in_(symbols), InstrumentRecord.instrument_type.in_(("FUT", "CE", "PE")),
            (InstrumentRecord.expiry.is_(None)) | (InstrumentRecord.expiry >= today))
            .order_by(InstrumentRecord.id).limit(MAX_INSTRUMENTS))
        instruments = [{f: _plain(getattr(r, f)) for f in fields} for r in rows]
    return {"day": today.isoformat(), "ban_list": ban, "instruments": instruments}


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
            "open_positions": device.open_positions, "broker_session": device.broker_session, "egress_ip": device.egress_ip,
            # S4: his own kill switch button relays to this runner (POST /api/runner/devices/{id}/kill-switch {"on": bool});
            # a removed runner takes no command. S4 also reports the daily broker login line.
            "kill_relay": device.revoked_at is None and device.status != REVOKED, "broker_login": device.broker_login}


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
