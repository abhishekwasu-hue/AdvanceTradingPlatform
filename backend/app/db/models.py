from datetime import date, datetime, timezone

from sqlalchemy.sql import false, true
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_TZ_DATETIME = DateTime(timezone=True)

# P0.4 / S10: money is stored exactly. `Money` (2 decimals) for amounts - P&L, charges, balances, fees, invoices;
# `Price` (4 decimals) for traded prices and levels, so a sub-paisa crypto or option tick survives. Both come back
# as Python floats (`asdecimal=False`): the arithmetic in the engines is unchanged, the database no longer rounds
# a rupee total through binary floating point. Percentages, quantities and analytics stay Float.
# Aggregate these only with func.sum / func.coalesce / func.max (they inherit asdecimal=False); func.avg, func.abs,
# func.round and raw text() SQL return Decimal on Postgres - cast(..., Float) first or the float arithmetic breaks.
Money = Numeric(18, 2, asdecimal=False)
Price = Numeric(18, 4, asdecimal=False)


class Tenant(Base):
    """The isolation/billing boundary for the whole platform (spec section 5-6): every
    tenant-owned row across the schema carries a tenant_id, always derived server-side from the
    authenticated user's own tenant - never accepted from the client - so tenants can never read
    or write each other's data. V1 auto-creates one tenant per registration (see
    app/auth/routes.py::register); a multi-user invite flow onto an existing tenant is future work.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # Authenticates inbound TradingView (and future third-party) webhook alerts, which can't
    # carry a JWT/OAuth header - the token itself, embedded in the webhook URL, is the auth.
    # Generated once at tenant creation (app/auth/routes.py::register); rotatable via
    # POST /api/webhooks/tradingview/token/rotate if it ever leaks.
    # P0.3 / S13: the TradingView URL token is stored hashed; `webhook_token` (plaintext) is only still set for
    # organisations created before P0.3 and is cleared on the first rotation. A new organisation has the hash only.
    webhook_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    webhook_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    # Owner-set policy (Phase C3): LIVE deployments, broker credentials and the OAuth login
    # require the caller to have TOTP MFA enabled and verified on the current session.
    require_mfa_for_live: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Phase D1: the exchange-issued algo identifier the broker registered this tenant's algo
    # under (SEBI retail-algo framework). Prefixed onto the tag of every broker order.
    algo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Phase G1 (safety rule 8): set when a LIVE order FAILED - the broker call raised or timed
    # out, so the platform does not know whether the broker holds the position. New LIVE entries
    # are refused while set; a position reconciliation with zero mismatches clears it.
    broker_uncertain_since: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Phase P3 / section 57: the currency portfolio figures are reported in (app/fx/service.py).
    base_currency: Mapped[str] = mapped_column(String(4), nullable=False, default="INR", server_default="INR")
    # Phase T: the routing policy deployments use when they do not set their own.
    default_routing_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="EXPLICIT", server_default="EXPLICIT")
    broker_uncertain_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Phase K: why the status is what it is ("billing: grace expired", "admin: ...").
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="USER")
    # Deactivated (removed from the team) users keep their rows for attribution/audit history
    # but can no longer log in or use an existing token - see get_current_user.
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    # P0.9: the language the AI writes its answers in (chat, guide). The dashboard itself is English only.
    ai_language: Mapped[str] = mapped_column(String(4), nullable=False, default="en", server_default="en")
    # Phase M / V4.13: an OWNER or platform admin can stop one member from opening new positions
    # without deactivating the account (they can still watch, exit and report).
    trading_disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Phase N3: set when the address was confirmed through the emailed link (or by a platform
    # admin). LIVE deployments and broker credential storage require it when
    # EMAIL_VERIFICATION_REQUIRED is on. Users created before Phase N were backfilled as verified.
    email_verified_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Phase N2 / V4.12: fine-grained scope overrides on top of the role matrix, as JSON
    # {"deny": ["trading:live", ...], "grant": [...]} set by the tenant OWNER (app/auth/scopes.py).
    scope_overrides: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TOTP MFA (app/auth/mfa.py). The secret is Fernet-encrypted; a pending (not yet confirmed)
    # enrolment has a secret but mfa_enabled = False.
    mfa_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # P0.2 / S8: the last accepted TOTP step (30-second counter); a code for that step or an earlier one is a replay.
    mfa_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    broker_credentials: Mapped[list["BrokerCredentialRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class LoginEventRecord(Base):
    """Every login attempt, successful or not (app/auth/lockout.py). `user_id` is null when the
    email is unknown - the attempt is still recorded so per-IP lockout can count it. This is the
    user's own "who logged in as me" history and the input to lockout and new-device detection."""

    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class MfaBackupCodeRecord(Base):
    """One-time recovery codes for a user who lost their authenticator. Hash only; consumed on use."""

    __tablename__ = "mfa_backup_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class PasswordResetRecord(Base):
    """A one-hour, single-use password reset. Only the SHA-256 of the token is stored; the raw
    token travels in the link (emailed through the tenant's own SMTP channel when one exists, or
    handed over by the owner from the Team tab). Completing a reset revokes every session."""

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    requested_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class UserSessionRecord(Base):
    """One login = one session. Holds only a SHA-256 of the current refresh token; the access
    JWT carries the session id (`sid`) so every API request can check the session is still alive
    (not revoked, not expired) - which is what makes logout, "log out everywhere", member removal
    and password change take effect immediately instead of when a JWT happens to expire.
    Refresh tokens rotate on every use; presenting an already-rotated token is treated as theft
    and revokes the whole session (see app/auth/sessions.py)."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    previous_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    last_used_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Set when this session completed a TOTP/backup-code check (at login or by step-up).
    mfa_verified_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class TenantInviteRecord(Base):
    """An owner's invitation for someone to join their tenant with a given role. Only a SHA-256 of
    the one-time token is stored (the raw token lives in the invite link, shown to the owner once),
    so a DB read cannot mint a usable invite. Expires 48h after creation; accepting sets
    `accepted_at` and creates the User."""

    __tablename__ = "tenant_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="USER")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    accepted_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class NotificationReadRecord(Base):
    """Per-user read marker for a tenant-shared notification: one teammate reading an alert must
    not clear it for everyone else. Replaces the single `notifications.read_at` column (kept for
    history) now that a tenant can have more than one user."""

    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("notification_id", "user_id", name="uq_notification_read_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    read_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class BrokerCredentialRecord(Base):
    """Broker credentials, encrypted at rest (see app/secrets_store/encryption.py). The DB only
    ever stores ciphertext - decryption happens in memory, on demand, right before an adapter
    is constructed. Scoped by tenant_id (a broker account belongs to the org, not to whichever
    user happened to add it); user_id is kept only for attribution.
    """

    __tablename__ = "broker_credentials"
    __table_args__ = (UniqueConstraint("tenant_id", "broker_name", "account_label", name="uq_tenant_broker_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # Phase I2: several accounts at the same broker are several credential rows, told apart by
    # this label ("primary" is the one every existing caller means).
    account_label: Mapped[str] = mapped_column(String(50), nullable=False, default="primary")
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    # Broker session-token lifecycle (see app/brokers/token_lifecycle.py). Indian retail broker
    # access tokens (Upstox, Zerodha) expire every trading day around 03:30 IST with no refresh
    # token, so the platform tracks when the current one expires and when it last proved it
    # works - the autonomous worker refuses LIVE entries on anything but a VALID token.
    token_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    token_expires_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship(back_populates="broker_credentials")


class TradeRecord(Base):
    """A persisted paper (or, once wired, live) trade for a logged-in user - the storage layer
    a Trade Journal / Positions view reads from. Anonymous paper-execute calls (no logged-in
    user) are never written here, only ever the in-memory response.

    `exit_time`/`exit_price`/`pnl` stay null for as long as the position is open. Today nothing
    monitors live prices to close a paper-execute position automatically (that only happens
    inside the historical backtest engine's simulation) - closing one is a future write to this
    same row, not a new endpoint yet, so every row currently ends up "open" until that lands.
    """

    __tablename__ = "trades"
    # #221: the worker reads the open positions of every organisation every cycle (ADR-0004); this index keeps that read
    # on the open rows only, however many closed trades accumulate.
    __table_args__ = (
        Index("ix_trades_open", "tenant_id", postgresql_where=text("exit_time IS NULL"), sqlite_where=text("exit_time IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    entry_price: Mapped[float] = mapped_column(Price, nullable=False)
    # float, not int: a crypto fill sizes in fractional units (see app/instruments/registry.py) -
    # every equity/index-option/MCX fill still always lands on a whole multiple of its lot size.
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Price, nullable=False)
    # Nullable since Phase F3: a bought/written option has no target on its own price - the
    # strategy's targets are on the underlying (underlying_target1/2 below).
    target1: Mapped[float | None] = mapped_column(Price, nullable=True)
    target2: Mapped[float | None] = mapped_column(Price, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Price, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Money, nullable=True)
    charges: Mapped[float] = mapped_column(Money, nullable=False, default=0.0)
    # LIVE trades only (both stay null for PAPER): the broker's own id for the entry order and
    # for the protective stop-loss order placed right after the fill, so the position monitor
    # can cancel the SL when it exits on target, and reconciliation can match broker fills.
    broker_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sl_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # P0.5 / T6: the last price the position monitor saw and when, so the daily-loss limit can count the
    # marked-to-market loss of open positions, not only realised P&L.
    mark_price: Mapped[float | None] = mapped_column(Price, nullable=True)
    mark_time: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Which autonomous deployment opened this trade; null for trades entered by hand from the
    # console or via a TradingView webhook.
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_deployments.id", ondelete="SET NULL"), nullable=True
    )
    # Phase T: the broker account the LIVE position sits in (routing + FEWEST_POSITIONS + reconciliation).
    # Z3 (review M1/H2): where the trade's prices came from when not from the platform's own quotes - "client" for a
    # console paper-execute priced from candles the browser sent. The PAPER account and the ladder leave such trades out.
    price_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    broker_account_id: Mapped[int | None] = mapped_column(ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    # Phase D4: the broker's id for the order that closed a LIVE position (the SL when the
    # exchange closed us at the stop, else the market exit), so contract-note legs can be
    # matched to both sides of the trade.
    exit_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Re-exit guard (ADR-0004): when this position's exit was claimed / sent - an exit order missing from the broker's book
    # is treated as dead once it was sent on an earlier trading day, or a clean reconciliation since shows the position
    # still open. While the exit is being placed `exit_order_id` holds the pending marker (position_monitor.EXIT_PENDING).
    exit_sent_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Re-exit guard: when a reconciliation last found this position's symbol still open at the broker (quantities matched)
    # with no order working on it - the proof that an exit order missing from the book is over (set only while an exit
    # is recorded). And what earlier exits of this position already filled before the recorded one (quantity, average
    # price), so the booking blends every part.
    reconciled_open_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    exit_filled_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where `charges` came from: ESTIMATED (the platform's NSE cost model at close time) or
    # CONTRACT_NOTE (the broker's actual charges, applied from an uploaded contract note).
    charges_source: Mapped[str] = mapped_column(String(20), nullable=False, default="ESTIMATED")
    contract_note_id: Mapped[int | None] = mapped_column(
        ForeignKey("contract_notes.id", ondelete="SET NULL"), nullable=True
    )
    # Phase F3: derived-contract trades. `symbol` is the contract actually held (an option or
    # future tradingsymbol) and stop_loss/target* are on that contract's price; the strategy's own
    # levels live on the underlying, so the position monitor watches `underlying_symbol` against
    # them (F4) and uses the contract price only for P&L and the premium floor/ceiling.
    instrument_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="UNDERLYING")
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True)
    instrument_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    option_position: Mapped[str | None] = mapped_column(String(6), nullable=True)
    premium_stop_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    underlying_symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    underlying_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    underlying_stop_loss: Mapped[float | None] = mapped_column(Price, nullable=True)
    underlying_target1: Mapped[float | None] = mapped_column(Price, nullable=True)
    underlying_target2: Mapped[float | None] = mapped_column(Price, nullable=True)
    # Execution quality (master prompt V4.14): signal price vs fill, and entry latency.
    expected_price: Mapped[float | None] = mapped_column(Price, nullable=True)
    slippage: Mapped[float | None] = mapped_column(Price, nullable=True)   # fill - expected, signed against the trade
    # Phase AS: INTRADAY or SWING (held overnight: never squared off at the close; exits and
    # protective stops use the delivery / carry-forward product).
    holding: Mapped[str] = mapped_column(String(10), nullable=False, default="INTRADAY", server_default="INTRADAY")
    entry_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Phase H2: legs of one multi-leg structure share a leg_group_id; leg_role SHORT/LONG says
    # which side of the spread the leg is; group_meta (JSON) carries the structure's net credit,
    # max loss/profit, breakevens and the group exit levels every leg is judged by together.
    leg_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    leg_role: Mapped[str | None] = mapped_column(String(6), nullable=True)
    option_strategy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    group_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase J1: the exit rules this trade runs under, its initial stop (the stop_loss column moves
    # as rules tighten it) and the best price seen so far (trailing high-water mark).
    exit_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    initial_stop_loss: Mapped[float | None] = mapped_column(Price, nullable=True)
    best_price: Mapped[float | None] = mapped_column(Price, nullable=True)
    # Phase M / V4.14 trade journal: the regime read at entry (from the deployment's base frame),
    # free notes and comma-separated tags the trader adds afterwards.
    regime_at_entry: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class ContractNoteRecord(Base):
    """One uploaded broker contract note / tradebook file (Phase D4): who uploaded it, its
    SHA-256 (so the same file is recognised and the upload is evidence), and how many of its
    legs matched trades. The parsed legs live in ContractNoteLineRecord."""

    __tablename__ = "contract_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    note_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trades_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_charges: Mapped[float] = mapped_column(Money, nullable=False, default=0.0)
    uploaded_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class ContractNoteLineRecord(Base):
    """One leg (fill) from a contract note, as parsed, with the trade it was matched to."""

    __tablename__ = "contract_note_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_note_id: Mapped[int] = mapped_column(ForeignKey("contract_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True, index=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Price, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    charges: Mapped[float] = mapped_column(Money, nullable=False, default=0.0)
    breakdown_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    match_method: Mapped[str] = mapped_column(String(20), nullable=False, default="UNMATCHED")


class JournalEntryRecord(Base):
    """Z6 / P1-e (ADR-0026): the auto-captured journal of one trade, written by the capture hooks in
    `app/journal/capture.py` (entry fill, monitor marks, stop changes, exit) while the `discipline_engine` flag is on,
    and rebuilt from the trade row for anything a hook did not see. Personal: scoped by user, never shown to anyone else
    in the tenant. The JSON columns are data written by the platform; the user's own words stay on `trades.notes`."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")      # MANUAL/STRATEGY/SCAN/PROPOSAL/WEBHOOK
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    plan_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")             # the levels the trade was taken on
    fills_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")            # entry and exit fills
    stop_history_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")     # every stop change seen, with its source
    chart_entry_json: Mapped[str | None] = mapped_column(Text, nullable=True)              # a reproducible chart reference at entry
    chart_exit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    greeks_entry_json: Mapped[str | None] = mapped_column(Text, nullable=True)             # options: IV and Greeks at entry
    iv_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_exit: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_price: Mapped[float | None] = mapped_column(Price, nullable=True)                 # highest / lowest price seen while open
    low_price: Mapped[float | None] = mapped_column(Price, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


class JournalTagRecord(Base):
    """Z6 / P1-e: one behaviour tag on one trade. `source` AUTO (a detector found it) or USER (added by the trader);
    `status` SUGGESTED until the trader CONFIRMs or DISMISSes it. A detector re-run never overrides the trader's decision;
    a SUGGESTED tag whose rule no longer fires (a setting changed) is removed. Personal, like the journal entry."""

    __tablename__ = "journal_tags"
    __table_args__ = (UniqueConstraint("trade_id", "tag", name="uq_journal_tags_trade_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="AUTO")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="SUGGESTED")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    detected_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class JournalSettingsRecord(Base):
    """Z6 / P1-e: one trader's discipline-engine thresholds and score weights (JSON, merged over the documented defaults
    in `app/journal/settings.py`; a key that is absent means the default)."""

    __tablename__ = "journal_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


class StrategyDeploymentRecord(Base):
    """One "run this strategy on this symbol, in this mode, with this broker" instruction for
    the autonomous trading worker (app/workers/trading_worker.py) - the piece that turns the
    platform from a console someone has to click "Generate Signal" in into a system that trades
    on its own while every browser is closed. Tenant-scoped like everything else; `created_by`
    is attribution only.

    `strategy_id` is either an inbuilt registry id or "custom:<id>" (app/custom_strategies/
    resolver.py). `timeframe` is the base candle interval fetched from the broker (the strategy's
    own multi-timeframe needs are met by resampling up from it - app/market_data/service.py).
    `mode` PAPER routes fills to the paper broker; LIVE places real orders via `broker_name`, and
    is refused unless that broker's stored token is VALID (see BrokerTokenStatus).

    Only ACTIVE rows take new entries. The worker itself flips a row to PAUSED (with
    `pause_reason`) on a token expiry or repeated cycle failures rather than silently retrying
    forever; a person resumes it from the Deployments tab. `last_evaluated_at`/`last_signal_at`/
    `last_error` are the operator's window into "is this thing actually running".
    """

    __tablename__ = "strategy_deployments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "strategy_id", "symbol", "mode", "instrument_kind",
                         name="uq_deployment_tenant_strategy_symbol_mode_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, default="NSE")
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="1min")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    broker_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE", index=True)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_signal_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Phase F2: what to trade when the strategy signals on `symbol`. UNDERLYING keeps the original
    # behaviour; OPTION/FUTURE resolve a contract from the instrument master at signal time using
    # the rules below (app/instruments/contracts.py).
    instrument_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="UNDERLYING")
    option_position: Mapped[str | None] = mapped_column(String(6), nullable=True)   # BUY / WRITE
    expiry_rule: Mapped[str | None] = mapped_column(String(10), nullable=True)      # NEAREST / NEXT / MONTHLY
    strike_rule: Mapped[str | None] = mapped_column(String(6), nullable=True)       # ATM / ITM / OTM
    strike_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Bought option: exit when the premium falls this % below entry (safety net under the
    # underlying-level stop). Written option: exit when the premium rises this % above entry.
    premium_stop_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Phase H1: JSON strike-selection filters applied to the option chain at resolution time
    # (min OI/volume, max spread %, IV band, target delta, premium band) - see
    # app/instruments/strike_selection.py. NULL = rule strike only.
    strike_filters: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase H2: multi-leg structure (OptionStrategy), wing width in strike steps, and the
    # credit-based exit levels for spreads (take profit at target_credit_pct of the credit
    # captured; stop when the loss reaches stop_credit_pct of the credit).
    option_strategy: Mapped[str] = mapped_column(String(20), nullable=False, default="SINGLE")
    spread_width: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    target_credit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_credit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase U: the free-form structure's legs (JSON list of {right, role, strike_rule,
    # strike_offset, ratio}) when option_strategy is CUSTOM - see app/instruments/spreads.py.
    custom_legs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase I2: route this deployment's LIVE orders to one broker account (NULL = the broker's default).
    broker_account_id: Mapped[int | None] = mapped_column(ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True)
    # Phase T (V3.1-3.5): how the account is chosen at signal time - EXPLICIT / MOST_MARGIN /
    # LEAST_UTILISED / FEWEST_POSITIONS (NULL = the tenant's default policy); whether candidates
    # may come from every broker the tenant has a session for; and the last decision taken.
    routing_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    route_across_brokers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    last_route: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Phase J1: dynamic exit rules JSON (trailing %, break-even R, time exits) - app/trading/exit_rules.py.
    exit_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase L3: comma-separated regimes (TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE, QUIET) the
    # deployment may enter in; NULL = any. Judged on the base frame before the strategy runs.
    regime_filter: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # OI Banner O5: opt-in OI gates checked before a NEW entry (comma-separated names, app/option_chain/oi_gates.py);
    # fail-closed on missing/stale data; exits never consult them (ADR-0004). None = no OI gate.
    oi_gates: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Phase AS: INTRADAY (MIS, squared off at the close) or SWING (CNC / NRML, held overnight).
    holding: Mapped[str] = mapped_column(String(10), nullable=False, default="INTRADAY", server_default="INTRADAY")
    # P0.5 / T5: how LIVE entries are sent. MARKET (default, unchanged behaviour) or PROTECTED_LIMIT - a marketable
    # limit `market_protection_pct` past the signal price (default 0.5%), so a thin book cannot fill a market
    # order far from the price the strategy sized on. Unfilled remainder is cancelled by the fill check (T1).
    order_style: Mapped[str] = mapped_column(String(20), nullable=False, default="MARKET", server_default="MARKET")
    market_protection_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Z3 / BT2: AUTO (the worker enters on its own, as before) or APPROVAL (every entry signal becomes an ENTRY proposal and
    # only an approved one places a PAPER order - app/execution/approval.py). With the `approval_only_entries` flag on
    # every deployment of the organisation is approval-only whatever this says; APPROVAL never reverts to AUTO by itself.
    entry_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="AUTO", server_default="AUTO")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class WorkerHeartbeatRecord(Base):
    """Liveness record for each long-running background worker, upserted at the end of every
    cycle (one row per `worker_name`). The API reads it (GET /api/system/worker-status) so the
    Dashboard can show "worker last seen 12s ago" and so the ops runbook has a single place to
    answer "is the engine actually running?" - a stale `last_seen_at` on a trading day is the
    CRITICAL alert, not a quiet log line.
    """

    __tablename__ = "worker_heartbeats"

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    cycle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_cycle_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketHolidayRecord(Base):
    """Exchange trading holidays (app/market_data/calendar.py consults these plus the fixed
    09:15-15:30 IST Mon-Fri session to decide whether the worker should evaluate at all). Shared
    reference data, not tenant-scoped: an NSE holiday is a fact for every tenant. Seeded with the
    NSE capital-market list for 2026 from the exchange's own circular; each following year's
    list is added by a SUPER_ADMIN via the API once NSE publishes it (usually December).
    """

    __tablename__ = "market_holidays"
    __table_args__ = (UniqueConstraint("exchange", "holiday_date", name="uq_market_holiday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, default="NSE")
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class MarketEventRecord(Base):
    """Phase V1 (rule M8): a scheduled market event the guardian acts on - budget, RBI policy,
    expiry, results. `tenant_id` NULL = global (kept by the platform operator), else the
    tenant's own. `underlying` NULL/"*" = every symbol, "INDEX" = the index bucket, else one
    underlying. A BLOCK event refuses entries in its window; SIZE_CUT scales the risk per trade
    by `size_cut_pct` (NULL = the tenant's default)."""

    __tablename__ = "market_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    underlying: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)   # "HH:MM" IST
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="OTHER")
    action: Mapped[str] = mapped_column(String(10), nullable=False, default="SIZE_CUT")
    size_cut_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class OrderRecord(Base):
    """The formal order lifecycle record every paper/live execution *attempt* by a logged-in
    user creates (app/execution/order_state_machine.py enforces its transitions) - unlike
    TradeRecord, which only ever gets a row once an order actually fills, this gets one for
    every attempt, rejected or not, so the platform has a real order audit trail rather than
    only a record of successful fills. `idempotency_key` is unique per tenant when supplied
    (NULL is never deduplicated), so a client retrying the same submission - a network retry, a
    duplicated webhook delivery - replays the original outcome instead of double-executing.
    """

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED", index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Phase D1: the exact order tag sent to the broker (algo id + strategy + leg), for
    # reconciling this trail against the exchange's algo-order records.
    algo_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)
    signal_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class OrderEventRecord(Base):
    """Append-only order state-transition audit trail - one row per transition, oldest first,
    never mutated or deleted. `from_status` is null on the first (CREATED) event.
    """

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class SignalHistoryRecord(Base):
    """Every enriched signal generated by a logged-in user, whether or not it was ever paper- or
    live-executed - a record of "what the engine said, and when" for later review, independent
    of the Trade Journal (which only has rows for signals that actually got filled).
    """

    __tablename__ = "signal_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_time: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target1: Mapped[float | None] = mapped_column(Float, nullable=True)
    target2: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grade: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    timeframe_combo: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class CustomStrategyRecord(Base):
    """A user-authored strategy built with the no-code Strategy Builder - a serialized
    CustomStrategyConfig (app/strategy_engine/declarative.py) that gets rehydrated into a
    DeclarativeStrategy on demand. Referenced elsewhere in the API as strategy id "custom:<id>".

    `config_json`/`name` always mirror whichever `StrategyVersionRecord` is currently pinned as
    `live_version_id` - kept denormalized here so every existing reader (the resolver, execution
    pipeline, list/get endpoints) needs no changes to pick up a version change; the immutable
    history itself lives in `strategy_versions`, never mutated once written.
    """

    __tablename__ = "custom_strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    live_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    # Phase L2: where the strategy came from ("user", "ai:<draft id>", "marketplace:<listing id>")
    # and, for AI-generated ones, who approved it after review (safety rule 16).
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="user")
    ai_approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class StrategyVersionRecord(Base):
    """One immutable snapshot of a custom strategy's config - never updated or deleted once
    written. Editing a strategy (PUT /api/custom-strategies/{id}) or rolling it back
    (POST .../versions/{version_number}/rollback) always appends a new version and repoints
    `CustomStrategyRecord.live_version_id`; it never rewrites an existing row's `config_json`.
    `status` distinguishes the currently-pinned version (LIVE) from every earlier one (ARCHIVED) -
    bookkeeping metadata about supersession, not a mutation of the version's actual content.
    """

    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("custom_strategy_id", "version_number", name="uq_strategy_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    custom_strategy_id: Mapped[int] = mapped_column(
        ForeignKey("custom_strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="LIVE")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class RiskSettingsRecord(Base):
    """A tenant's risk parameters (position sizing, daily loss/trade-count/consecutive-loss
    guards), persisted so paper-execute uses the org's configured limits by default instead of
    the hardcoded RiskConfig() every anonymous call falls back to. One row per tenant (matches
    the spec's risk_limits.scope=tenant); `updated_by` is attribution only, not the scope key.
    """

    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    capital: Mapped[float] = mapped_column(Float, nullable=False, default=100_000.0)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    min_risk_reward: Mapped[float] = mapped_column(Float, nullable=False, default=1.2)
    # Phase V1: Risk Guardian rules (see app/risk_engine/guardian.py).
    max_portfolio_risk_pct: Mapped[float] = mapped_column(Float, nullable=False, default=6.0, server_default="6.0")
    stop_cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    dd_level_1_pct: Mapped[float] = mapped_column(Float, nullable=False, default=5.0, server_default="5.0")
    dd_level_2_pct: Mapped[float] = mapped_column(Float, nullable=False, default=10.0, server_default="10.0")
    event_size_cut_pct: Mapped[float] = mapped_column(Float, nullable=False, default=50.0, server_default="50.0")
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class RiskLimitRecord(Base):
    """Phase I1 (V3.4 / V4.5): one configurable limit at one scope. Limits of the same type at
    different scopes are all evaluated for an order and the strictest applies. GLOBAL rows have
    tenant_id NULL and are set by SUPER_ADMIN; every other scope belongs to a tenant."""

    __tablename__ = "risk_limits"
    __table_args__ = (UniqueConstraint("tenant_id", "scope", "scope_id", "limit_type", name="uq_risk_limit_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(12), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")   # "" for GLOBAL/TENANT
    limit_type: Mapped[str] = mapped_column(String(40), nullable=False)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class RiskEventRecord(Base):
    """Append-only record of every risk-hierarchy check (V4.5 risk_event fields): what was
    measured, against which limit, and what the engine did about it. Never updated or deleted
    (retention policy: NEVER_DELETED alongside audit logs)."""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(12), nullable=False)
    current_value: Mapped[float] = mapped_column(Float, nullable=False)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)     # INFO / WARNING / CRITICAL
    action: Mapped[str] = mapped_column(String(20), nullable=False)       # RiskAction
    status: Mapped[str] = mapped_column(String(10), nullable=False)       # PASS / WARN / BLOCK
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class EgressIpRecord(Base):
    """Part D4 (rule IN-SEBI.static_ip.registered): an IP the organisation registered with a broker for API orders -
    one PRIMARY and optionally one BACKUP per broker. The platform's own egress IP (SERVER_EGRESS_IP) must be one of
    them before a LIVE entry goes out (when STATIC_IP_REQUIRED_FOR_LIVE)."""

    __tablename__ = "egress_ips"
    __table_args__ = (UniqueConstraint("tenant_id", "broker_name", "role", name="uq_egress_ip_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="PRIMARY")      # PRIMARY / BACKUP
    ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    registered_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)   # when the broker accepted it
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class EgressIpChangeRecord(Base):
    """Part D4: every change of a registered IP (append-only) - the weekly-change rule counts these."""

    __tablename__ = "egress_ip_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    old_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    new_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class ComplianceEvidenceRecord(Base):
    """Part D6: evidence recorded for one go-live checklist item (append-only; the latest row per item counts).
    `reference` is where the evidence lives (document id, register page, URL) - never the document or a secret."""

    __tablename__ = "compliance_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    reference: Mapped[str] = mapped_column(String(500), nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class BrokerAccountRecord(Base):
    """Phase I2 (V3.14 rule 3): one trading account at a broker - the credential it authenticates
    with, the broker's own identifier, and the last synced balance/margin/P&L. Deployments may
    route to a specific account; a disabled account refuses new LIVE entries."""

    __tablename__ = "broker_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "broker_name", "account_label", name="uq_broker_account_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("broker_credentials.id", ondelete="SET NULL"), nullable=True)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    account_label: Mapped[str] = mapped_column(String(50), nullable=False, default="primary")
    broker_account_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE")   # ACTIVE / DISABLED
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    available_balance: Mapped[float | None] = mapped_column(Money, nullable=True)
    used_margin: Mapped[float | None] = mapped_column(Money, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Money, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Money, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class BacktestRunRecord(Base):
    """Phase J2 (V4.8, section 41): one backtest a logged-in user ran - the strategy, its
    parameters, the data it saw (source label, bar count, span) and the headline metrics - so a
    result on a screen can always be traced to what produced it."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    base_timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_source: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    bars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_from: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    data_to: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1")
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class SubscriptionRecord(Base):
    """Phase K1 (V3.6-3.8): why a tenant is on its plan. One per tenant; `tenants.plan` mirrors
    the entitlement so every existing limit check keeps reading one column."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(10), nullable=False, default="MONTHLY")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="TRIALING")   # TRIALING / ACTIVE / PAST_DUE / CANCELLED
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    trial_end: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    grace_until: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(nullable=False, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Phase K1b: the gateway's hosted checkout / autopay-mandate link for this subscription.
    checkout_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class BillingGatewayPlanRecord(Base):
    """Phase K1b: our (plan, cycle) -> the gateway's plan object, created lazily once."""

    __tablename__ = "billing_gateway_plans"
    __table_args__ = (UniqueConstraint("provider", "plan_id", "billing_cycle", name="uq_gateway_plan"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(10), nullable=False)
    gateway_plan_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class BillingWebhookEventRecord(Base):
    """Phase K1b: every gateway webhook delivery, keyed by the gateway's event id so a redelivery
    is a no-op. Payloads are kept for dispute handling and audit."""

    __tablename__ = "billing_webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str | None] = mapped_column(String(300), nullable=True)
    received_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class BillingTransactionRecord(Base):
    """Phase K1 (section 43 billing_transactions): invoices, payments, refunds, failed payments."""

    __tablename__ = "billing_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)      # INVOICE / PAYMENT / REFUND / FAILED_PAYMENT
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(10), nullable=False)    # OPEN / PAID / VOID / FAILED
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class UsageRecord(Base):
    """Phase K1 (V3.14 rule 7): metered usage per tenant, daily buckets."""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    period_start: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="api")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class MarketplaceListingRecord(Base):
    """Phase K2 (V3.9-3.10): a strategy version a creator offers to other tenants. Publishing goes
    through review; the documented performance is a saved backtest run, never a promise."""

    __tablename__ = "marketplace_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)   # creator
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    custom_strategy_id: Mapped[int] = mapped_column(ForeignKey("custom_strategies.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)     # frozen copy of the published version
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    methodology: Mapped[str | None] = mapped_column(Text, nullable=True)
    backtest_run_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_runs.id", ondelete="SET NULL"), nullable=True)
    performance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")   # DRAFT / PENDING_REVIEW / PUBLISHED / REJECTED / UNLISTED
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subscriber_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)
    # Phase X: a one-time price (0 = free) and the platform's share, frozen when the listing is
    # published so a later change of terms never re-prices a live listing.
    price: Mapped[float] = mapped_column(Money, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    platform_fee_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketplaceSubscriptionRecord(Base):
    __tablename__ = "marketplace_subscriptions"
    __table_args__ = (UniqueConstraint("listing_id", "tenant_id", name="uq_marketplace_subscriber"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)   # subscriber
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    custom_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("custom_strategies.id", ondelete="SET NULL"), nullable=True)  # the subscriber's copy
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")   # PENDING_PAYMENT / ACTIVE / CANCELLED
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class MarketplaceChargeRecord(Base):
    """Phase X (V3.9-3.10 revenue share): one purchase of a paid listing - what the subscriber
    owes or paid, split into the platform's fee and the creator's net. A PAID charge is the
    creator's earning row; `payout_id` says which payout carried it to them."""

    __tablename__ = "marketplace_charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id", ondelete="CASCADE"), nullable=False, index=True)
    creator_tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)   # the buyer
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_subscriptions.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    platform_fee_pct: Mapped[float] = mapped_column(Float, nullable=False)
    platform_fee: Mapped[float] = mapped_column(Money, nullable=False)
    creator_net: Mapped[float] = mapped_column(Money, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="OPEN")   # OPEN / PAID / VOID
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)    # the gateway's payment link id
    checkout_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payment_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)     # the payment id / transfer reference
    paid_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_payouts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class MarketplacePayoutRecord(Base):
    """Phase X: a creator's request to be paid its available earnings, settled by the operator
    (bank/UPI transfer outside the platform; the reference is recorded here). The destination
    the creator typed is stored encrypted under the tenant's key; only a hint is kept in clear."""

    __tablename__ = "marketplace_payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)   # the creator
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="REQUESTED")   # REQUESTED / PAID / REJECTED
    destination_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    destination_hint: Mapped[str] = mapped_column(String(40), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    settled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class ApiKeyRecord(Base):
    """Phase K3 (V3.11-3.12): a public API key. The secret is shown once and stored hashed."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")   # comma-separated
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    expires_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class AiProviderConfigRecord(Base):
    """Phase L1: one LLM provider per tenant. The API key is entered on the Settings page only,
    stored Fernet-encrypted (like broker credentials) and never returned by the API."""

    __tablename__ = "ai_provider_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)          # anthropic / openai / rule_based
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class AiStrategyDraftRecord(Base):
    """Phase L2: an AI-generated strategy on its way through the review gate (master prompt
    section 56, safety rule 16): DRAFT -> BACKTESTED (a saved run attached) -> APPROVED (a human
    OWNER/USER saved it as a custom strategy) or REJECTED. The raw model output is kept for
    lineage (V4 data governance)."""

    __tablename__ = "ai_strategy_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Phase V2: the compliance validator's report (checks, fixes, user-must-accept, evidence).
    compliance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase V3: which prompt template answered, the runtime context it was filled with, and the
    # deployment settings the model suggested (mapped onto the Autopilot form).
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT")   # DRAFT / FAILED / BACKTESTED / APPROVED / REJECTED
    backtest_run_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_runs.id", ondelete="SET NULL"), nullable=True)
    custom_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("custom_strategies.id", ondelete="SET NULL"), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class TraderProfileRecord(Base):
    """Phase AQ: what the AI Copilot remembers about one trader - the interview answers, the
    preferences learnt from the options they turned down (and why), and the options they chose -
    so the next plan starts from them instead of from scratch."""

    __tablename__ = "trader_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    answers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    preferences_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class MarketSnapshotRecord(Base):
    """Phase AR: the Copilot's market memory - the market read (trend, regime, structure,
    support/resistance, bias) of a watched symbol, or a market cue (India VIX, index day change),
    captured by the worker every 15 minutes through the tenant's own broker session. Tenant-scoped:
    the data comes through the tenant's broker licence. Reference data, pruned by retention."""

    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_snapshots_lookup", "tenant_id", "symbol", "captured_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="SYMBOL")   # SYMBOL | CUE
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE")
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="5min")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias: Mapped[str | None] = mapped_column(String(10), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    higher_regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    structure: Mapped[str | None] = mapped_column(String(12), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    captured_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class AiActionRecord(Base):
    """Phase L4: the monitoring agent's action-state machine (V4.1): the agent PROPOSES, a human
    APPROVES or REJECTS, the system EXECUTES; unanswered proposals EXPIRE. Nothing here ever
    places or closes a position without the approval row filled in."""

    __tablename__ = "ai_actions"
    # P0.8 / A5: at most one *open* (PROPOSED/APPROVED) proposal per tenant, deployment and rule - a concurrent raise from
    # two worker passes or two feed items is refused by the database, not only by the in-app check.
    __table_args__ = (
        Index("uq_ai_actions_open_rule", "tenant_id", "deployment_id", "rule", unique=True,
              postgresql_where=text("status IN ('PROPOSED', 'APPROVED')"), sqlite_where=text("status IN ('PROPOSED', 'APPROVED')")),
        # Z3 / BT2: one ENTRY proposal per signal (a redelivered webhook or a second worker pass is refused by the database).
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_ai_actions_tenant_idempotency_key"),
        # LD-7: the dashboard's proposal list (tenant, status, newest first by id).
        Index("ix_ai_actions_tenant_status_id", "tenant_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_deployments.id", ondelete="CASCADE"), nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)          # PAUSE_DEPLOYMENT / EXIT_POSITION / REDUCE_RISK / REVIEW_STRATEGY
    rule: Mapped[str] = mapped_column(String(40), nullable=False)            # which observation fired
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PROPOSED", index=True)   # PROPOSED / APPROVED / EXECUTED / REJECTED / EXPIRED / FAILED
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    result: Mapped[str | None] = mapped_column(String(300), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)
    # PW5: a PROPOSED row hidden from the pending list until this time, then announced once more (the worker clears it
    # when it wakes). Never later than expires_at - a snooze never extends the expiry - and never consulted by an exit.
    snoozed_until: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # PW4 (ported from the Trade bot's vision/tg.py): the Telegram message that carries this proposal's buttons, so a
    # decision taken in the app - or the proposal expiring - can take the buttons off and say what happened there too.
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Z3 / BT2: the signal an ENTRY proposal was raised for ("deployment:<id>:<bar>", "tv:<alert id>", "requote:<id>");
    # NULL for the monitoring agent's own proposals.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Z3 (review L7/L8): the person a proposal is for - an entry proposal's deployment creator / alert owner / sender, an
    # "Exit now?" question's position holder. Only they (or the organisation OWNER) decide it; NULL = the monitoring
    # agent's organisation-wide proposals, decided as before.
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)


class ScanThenTemplateRecord(Base):
    """N1 U4 X1 (flag `scan_then`, off by default): a scan alert rule's "Then" template. Once the rule matches a symbol, the
    level gate (Trade's level-bot rules: Daily trend first -> a Z7a zone as the area -> handover study -> a dominant
    commitment candle on higher volume) watches it on closed `confirm_tf` bars for the rest of the session; a CONFIRMED
    setup becomes an execution-core intent for `user_id` in `mode` (NOTIFY / ONE_CLICK / AUTO_PAPER, which the core turns
    into a proposal). Never an order. One template per rule."""

    __tablename__ = "scan_then_templates"
    __table_args__ = (UniqueConstraint("rule_id", name="uq_scan_then_templates_rule_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(12), nullable=False, default="NOTIFY")
    confirm_tf: Mapped[str] = mapped_column(String(4), nullable=False, default="5m")
    gate_settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    last_bar_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)      # the last confirm bar evaluated
    last_status_json: Mapped[str | None] = mapped_column(Text, nullable=True)              # per symbol: the gate's code + line
    last_problem: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class ExecutionIntentRecord(Base):
    """N1 E1 (ADR-0014): one entry intent on the execution core's queue - "this setup, for this person, in this mode".
    A producer (a scan's "Then" template, X1) only writes the row; the trading worker claims it and the core's policy
    routes it: NOTIFY (a notification), PROPOSE (a Z3 entry proposal, decided by a person) or REFUSE (with why). The
    row never holds a broker or an order: in N1 nothing on this queue reaches a broker (PAPER only, ADR-0006). Personal
    (tenant + user); kept as the trail of what the core decided and why."""

    __tablename__ = "execution_intents"
    # Per person (review M1): two members' intents for the same organisation-wide setup are two rows, never one shared row.
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "idempotency_key", name="uq_execution_intents_tenant_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)                 # scan (E1); more with review
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)            # e.g. "rule:12:event:345"
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_mode: Mapped[str] = mapped_column(String(12), nullable=False)         # NOTIFY / ONE_CLICK / AUTO_PAPER / AUTO_LIVE
    route: Mapped[str | None] = mapped_column(String(10), nullable=True)            # NOTIFY / PROPOSE / PAPER / REFUSE
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="QUEUED", index=True)   # QUEUED / CLAIMED / DONE / REFUSED / FAILED / EXPIRED
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(12), nullable=False, default="NSE")
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(80), nullable=False)
    signal_json: Mapped[str] = mapped_column(Text, nullable=False)                  # the Signal (levels, reasons, bar time)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # e.g. the level gate's ticket (L3)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)          # the policy's / the outcome's why
    ai_action_id: Mapped[int | None] = mapped_column(ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True)
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False, index=True)


class AiCandidateRecord(Base):
    """P0.8 / A3: a strategy candidate the *server* produced and validated - the strategist's rule sets with their
    in-sample / out-of-sample simulation, or the interview's pick with its evidence - held here so "adopt" and
    "deploy in PAPER" take a candidate id, never a config the browser typed. Adoption runs the compliance checklist
    and needs the human's risk acceptance, like an AI draft."""

    __tablename__ = "ai_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)          # strategist / interview
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(100), nullable=True)   # a built-in id (interview) when config_json is empty
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")   # the server-side simulation / evidence
    deployment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN", index=True)   # OPEN / ADOPTED / DEPLOYED
    adopted_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("custom_strategies.id", ondelete="SET NULL"), nullable=True)
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_deployments.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, index=True)


class FxRateRecord(Base):
    """Phase P3: operator-maintained FX rates (base/quote -> rate). One row per pair; the
    conversion helper also uses the inverse and the INR pivot."""

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(4), nullable=False)
    quote: Mapped[str] = mapped_column(String(4), nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    as_of: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (UniqueConstraint("base", "quote", name="uq_fx_rates_pair"),)


class TenantKeyRecord(Base):
    """Phase N1 / section 48: one Fernet data key per tenant, stored wrapped by the master key
    (`SECRETS_ENCRYPTION_KEY`). Secrets written as `t1:<tenant_id>:<token>` decrypt only with this
    key; rotating the master re-wraps these rows and touches no credential (app/secrets_store/envelope.py)."""

    __tablename__ = "tenant_keys"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    wrapped_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class EmailVerificationRecord(Base):
    """Phase N3: a 24-hour, single-use email verification token (SHA-256 stored, raw token in
    the link). Verifying stamps `users.email_verified_at`."""

    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class PlatformControlRecord(Base):
    """Phase M / V4.13: platform-wide operator switches as key -> JSON value: `maintenance_mode`
    ({"on": bool, "message": str}), `disabled_brokers` ({"names": [..]}). Read on every entry."""

    __tablename__ = "platform_controls"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class IncidentRecord(Base):
    """Phase M / V4.10: the incident record the DR runbook asks for - opened by the operator or
    automatically (global kill switch), closed with a root cause and the audit-log range it covers."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="WARNING")   # WARNING / CRITICAL / EMERGENCY
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN", index=True)   # OPEN / MITIGATED / RESOLVED
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="operator")   # operator / kill_switch / circuit_breaker
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=_utcnow)
    mitigated_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_log_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_log_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_loss_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)     # measured RPO
    downtime_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)      # measured RTO
    opened_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class KillSwitchRecord(Base):
    """One row per kill-switch scope key (GLOBAL; TENANT within a tenant; STRATEGY within a
    tenant+strategy_id), upserted on every engage/disengage rather than appended - the *current*
    state is what execution checks on every order, and `updated_at`/`engaged_at`/`disengaged_at`
    give enough history for "when was this last touched" without a separate event table.
    GLOBAL rows have `tenant_id=None` (platform-wide, SUPER_ADMIN only); TENANT/STRATEGY rows are
    always scoped to the caller's own tenant, `tenant_id` never accepted from the client.
    """

    __tablename__ = "kill_switches"
    __table_args__ = (UniqueConstraint("tenant_id", "scope", "strategy_id", name="uq_kill_switch_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    engaged: Mapped[bool] = mapped_column(nullable=False, default=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    engaged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    engaged_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    disengaged_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class AuditLogRecord(Base):
    """Security-relevant events (register, login, credential stored, broker authenticated, ...).
    Per the platform's audit-trail requirement: every credential/auth action leaves a row here.

    `prev_hash`/`hash` form a tamper-evident hash chain (master prompt Section 48) across every
    row in this table in `id` order: `hash` is a SHA-256 of this row's own fields concatenated
    with the previous row's `hash` (or `GENESIS` for the first row ever written). Altering or
    deleting any row, or inserting one out of band, breaks every subsequent row's hash - see
    `app/audit/log.py::write_audit_log`/`verify_audit_chain`, which are the only sanctioned way to
    append to or check this table; never construct/add an `AuditLogRecord` directly.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # P0.3 / S7: RESTRICT, never SET NULL - tenant_id/user_id are inside the hash, so nulling them would break the chain.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class AuditAnchorRecord(Base):
    """P0.3 / S7: the audit chain's head, recorded once a day by the worker (`app/audit/log.py::record_anchor`).
    A verification can start from the latest anchor instead of row 1, and anchors copied off-site let a
    rewritten prefix be detected even if every hash in the table was recomputed."""

    __tablename__ = "audit_anchors"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_id: Mapped[int] = mapped_column(Integer, nullable=False)
    head_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    anchored_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)



class NotificationRecord(Base):
    """An in-app notification event (entry/exit/rejection/broker-disconnect/token-expired/
    risk-rejection/daily-loss/emergency-exit/system-failure), visible to the whole tenant like
    every other tenant-shared resource. `read_at` is a simple per-row read marker - an honest v1
    approximation; a real per-user read-state join table is future work once a tenant can have
    more than its one original user (see the Multi-Tenancy Foundation notes on this same
    limitation for GET /api/audit-logs).
    """

    __tablename__ = "notifications"
    # LD-7: the feed every page polls - one tenant's rows, newest first.
    __table_args__ = (Index("ix_notifications_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="INFO")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    related_trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)
    related_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Phase BE: the monitoring-agent proposal this notification announces (approve/reject buttons on Telegram).
    ai_action_id: Mapped[int | None] = mapped_column(ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True)
    # OI Banner O4: a structured payload for webhook receivers (versioned JSON); None for every older event type.
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class AlertChannelRecord(Base):
    """One out-of-app delivery channel per tenant per type (Telegram chat, SMTP mailbox). The
    channel's secrets (bot token, SMTP password) live only in `encrypted_config`, decrypted in
    memory at send time, never returned by the API. `min_severity` is the floor a notification
    must reach to be queued for this channel - the default WARNING keeps routine ENTRY/EXIT chatter
    in-app only while every TOKEN_EXPIRED / SYSTEM_FAILURE / DAILY_LOSS_LIMIT reaches a human.
    """

    __tablename__ = "alert_channels"
    __table_args__ = (UniqueConstraint("tenant_id", "channel_type", name="uq_alert_channel_tenant_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    min_severity: Mapped[str] = mapped_column(String(10), nullable=False, default="WARNING")
    encrypted_config: Mapped[str] = mapped_column(Text, nullable=False)
    last_delivered_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class AlertDeliveryRecord(Base):
    """Outbox row: one notification x one channel. `notify()` enqueues these; the trading worker
    drains them every cycle (app/alerts/dispatcher.py) with exponential backoff up to a fixed
    attempt count, after which the row is FAILED with the last error kept for the operator. The
    outbox is what makes delivery auditable ("was the 10:31 SYSTEM_FAILURE actually sent?") and
    what keeps a slow/broken SMTP server from ever blocking the pipeline that raised the alert.
    """

    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("alert_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # S3a (ADR-0022): the Notification Service grows this outbox - priority routing, the burst group or digest bucket a
    # delivery belongs to, and a reason code next to every non-sent status.
    priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    digest_bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # S3b-2: a per-user destination on the channel (a linked Telegram chat); NULL = the channel's own address.
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Z10-b (flag alerts_reliable_outbox): one key per delivery (notification x channel x address), unique, so a
    # duplicate cycle cannot queue the same delivery twice; sent to receivers that can dedupe (webhook / SMS
    # `Idempotency-Key`, email `Message-ID`). A worker claims a row with a lease before sending it, so two workers never
    # send the same row; a worker killed mid-send leaves a lease that expires, and the row is claimed again.
    idem_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CompanyRecord(Base):
    """Reference data about a listed company - shared, not user-private (like an instrument
    master), but every write is attributed to the user who entered it since nothing here is
    fetched automatically (no live SEBI/NSE/BSE feed is wired in - see
    app/fundamentals/providers/). `source_json` holds the SourceCitation for the profile itself.
    """

    __tablename__ = "companies"
    # A symbol is unique per scope (security LOW follow-up of #234): one shared profile (a partial unique index) and at most
    # one per organisation - an organisation's private profile never blocks or shadows the shared one for the others.
    __table_args__ = (
        UniqueConstraint("symbol", "tenant_id", name="uq_companies_symbol_tenant"),
        Index("uq_companies_symbol_shared", "symbol", unique=True, postgresql_where=text("tenant_id IS NULL"),
              sqlite_where=text("tenant_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bse_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    sub_industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    cap_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    promoter_holding_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_holding_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_holding_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    public_holding_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_pledge_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    face_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    headquarters: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_segments_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    business_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domestic_revenue_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    international_revenue_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cyclical: Mapped[bool | None] = mapped_column(nullable=True)
    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)


class FinancialPeriodRecord(Base):
    """One reported quarter or year's raw statement figures for a company. Ratios/margins are
    always derived live by the analysis engines from these raw numbers - never stored - so
    there is exactly one place a figure can be wrong: the cited source it was entered from.
    """

    __tablename__ = "financial_periods"
    __table_args__ = (UniqueConstraint("company_id", "period_type", "period_label", "tenant_id", name="uq_company_period_tenant"),
                      # NULLs differ in a unique key: the shared periods get their own partial unique index
                      Index("uq_company_period_shared", "company_id", "period_type", "period_label", unique=True,
                            postgresql_where=text("tenant_id IS NULL"), sqlite_where=text("tenant_id IS NULL")))

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)
    period_label: Mapped[str] = mapped_column(String(20), nullable=False)
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)

    revenue: Mapped[float] = mapped_column(Float, nullable=False)
    cogs: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebitda: Mapped[float] = mapped_column(Float, nullable=False)
    depreciation: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebit: Mapped[float | None] = mapped_column(Float, nullable=True)
    interest_expense: Mapped[float | None] = mapped_column(Float, nullable=True)
    other_income: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    exceptional_items: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_expense: Mapped[float | None] = mapped_column(Float, nullable=True)
    pat: Mapped[float] = mapped_column(Float, nullable=False)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)

    cfo: Mapped[float | None] = mapped_column(Float, nullable=True)
    cfi: Mapped[float | None] = mapped_column(Float, nullable=True)
    cff: Mapped[float | None] = mapped_column(Float, nullable=True)
    capex: Mapped[float | None] = mapped_column(Float, nullable=True)

    total_debt: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_and_equivalents: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_assets: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_liabilities: Mapped[float | None] = mapped_column(Float, nullable=True)
    receivables: Mapped[float | None] = mapped_column(Float, nullable=True)
    inventory: Mapped[float | None] = mapped_column(Float, nullable=True)
    payables: Mapped[float | None] = mapped_column(Float, nullable=True)
    contingent_liabilities: Mapped[float | None] = mapped_column(Float, nullable=True)
    shareholders_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_assets: Mapped[float | None] = mapped_column(Float, nullable=True)

    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class ShareholdingSnapshotRecord(Base):
    __tablename__ = "shareholding_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    promoter_pct: Mapped[float] = mapped_column(Float, nullable=False)
    promoter_pledge_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fii_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    public_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class CorporateActionRecord(Base):
    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    announced_date: Mapped[date] = mapped_column(Date, nullable=False)
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_revenue_impact: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expected_margin_impact: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expected_eps_impact: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class EarningsCalendarEventRecord(Base):
    """A scheduled company event (spec section 30) - results date, AGM, board meeting, dividend/
    bonus/split/buyback, record date, investor day, product launch, regulatory decision. Always
    user-entered (there's no live corporate-actions feed wired in), so it's exactly as reliable
    as whoever entered it - cite the source.
    """

    __tablename__ = "earnings_calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class SectorMetricRecord(Base):
    """A sector-specific KPI (NIM/CASA/GNPA for banking, utilization/attrition for IT, volume
    growth for auto, US-generics mix for pharma, GRM for oil & gas, capacity utilization for
    cement, ...). Always user-entered and cited - there is no live regulatory/industry-body feed
    wired in, so SectorSpecificEngine only ever classifies numbers someone actually supplied.
    """

    __tablename__ = "sector_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    period_label: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class QualitativeFactorRecord(Base):
    """A human-entered judgement (business-quality moat factor rating, management-quality note,
    SWOT bullet) - deliberately never computed or invented by an engine, only ever supplied and
    cited (spec sections 2, 9, 21).
    """

    __tablename__ = "qualitative_factors"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class NewsEventRecord(Base):
    """A structured, cited macro/market news entry - RBI monetary policy decisions, Union Budget
    announcements, government/regulatory policy changes, broad corporate news, global macro
    events, or sector-wide developments. Always user-entered and cited: there is no live news
    feed wired in, so this is exactly as reliable as whoever entered it and the source they cite
    - unlike the fundamentals module's optional `source`, the citation here is mandatory since
    the whole point of this table is a sourced claim, not a raw number. Shared reference data
    (like the fundamentals company/corporate-action tables), not tenant-private: a real RBI
    policy decision is a fact for every tenant, not a per-tenant judgement call.
    """

    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Contributed-data scope (security fix): NULL = shared reference data (provider-ingested / platform); set = entered by
    # that organisation and visible to it only (app/fundamentals/visibility.py).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    affected_symbols_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False)
    source_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    # Phase BB: where the row came from. MANUAL rows are a person's cited entry (verified by that
    # person); FEED rows come from a public feed (app/news_feed), carry the item's URL, are never
    # verified by the platform and are shown as "unverified feed". `dedupe_hash` keeps one row per
    # feed item; `classification_json` is the shared keyword classification (no tenant key used).
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="MANUAL", server_default="MANUAL", index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    dedupe_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    feed_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    classification_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsClassificationRecord(Base):
    """Phase BB: one organisation's AI classification of one feed item, made with that
    organisation's own provider key and kept for it alone (tenant-scoped, unlike the shared
    keyword classification on the news row). Unique per (tenant, item) so a headline is never
    sent to the provider twice for the same organisation."""

    __tablename__ = "news_classifications"
    __table_args__ = (UniqueConstraint("tenant_id", "news_event_id", name="uq_news_classification_tenant_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    news_event_id: Mapped[int] = mapped_column(ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    severity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    classification_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class TelegramCallbackRecord(Base):
    """Phase BE: one Telegram button = one nonce. The button carries only `p:<nonce>` (Telegram's
    64-byte callback_data limit); this row holds what it means (tenant, proposal, decision, the chat it
    was sent to), its HMAC and expiry, and whether it was used. Single use, tenant-scoped."""

    __tablename__ = "telegram_callbacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("ai_actions.id", ondelete="CASCADE"), nullable=False, index=True)
    nonce: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)        # approve / reject
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class InstrumentRecord(Base):
    """One row of a broker's instrument master (Phase F1): every tradable contract the broker
    knows - equities, indices, futures and options - with the fields F&O routing needs (lot size,
    expiry, strike, right, underlying). Replaced wholesale per (broker, exchange) by the daily
    sync; platform-wide, not tenant-scoped (a NIFTY option is the same contract for everyone).
    `underlying` is the master's own underlying name (NIFTY, BANKNIFTY, RELIANCE); index candles
    are fetched under the index symbol (NIFTY 50), and app/instruments/master.py maps between
    the two."""

    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("broker", "exchange", "instrument_key", name="uq_instrument_broker_key"),
        Index("ix_instruments_lookup", "broker", "underlying", "instrument_type", "expiry", "strike"),
        Index("ix_instruments_symbol", "broker", "exchange", "tradingsymbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    segment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    instrument_key: Mapped[str] = mapped_column(String(100), nullable=False)
    tradingsymbol: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    underlying: Mapped[str | None] = mapped_column(String(50), nullable=True)
    instrument_type: Mapped[str] = mapped_column(String(10), nullable=False)  # EQ, INDEX, FUT, CE, PE
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tick_size: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    weekly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synced_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class OptionChainSnapshotRecord(Base):
    """Phase W: one quoted option at one moment, sampled from the chains the worker already
    fetches for its option deployments (app/backtest/chain_recorder.py) so option backtests
    can be priced from what the market actually quoted. Platform-wide reference data (a NIFTY
    quote is the same fact for every tenant), sampled every few minutes for the strikes around
    the money, trimmed by retention after a bounded number of days."""

    __tablename__ = "option_chain_snapshots"
    __table_args__ = (
        Index("ix_option_chain_snapshots_lookup", "underlying", "expiry", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    underlying: Mapped[str] = mapped_column(String(30), nullable=False)          # master name: NIFTY
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, index=True)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    right: Mapped[str] = mapped_column(String(2), nullable=False)                # CE / PE
    ltp: Mapped[float] = mapped_column(Float, nullable=False)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    underlying_ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="worker")   # worker / <broker> / upload

class AgentRunRecord(Base):
    """H-C2 (ADR-0019): one Copilot agent request - who asked (the question only as a hash; its text is in llm_calls,
    masked), the prompt version and model, the limits, and how it ended. Append-only; never deleted (like llm_calls)."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    question_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    limits_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class AgentStepRecord(Base):
    """H-C2: one tool call inside an agent run - the arguments, a hash of the output (untrusted payloads by hash only),
    success and duration."""

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(60), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    untrusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))


class OISnapshotRecord(Base):
    """OI Banner O2: one collector slot of one underlying's option chain (platform-wide reference data, like
    `option_chain_snapshots`). The per-strike OI lives in `strike_oi_snapshots`; every banner reading is computed from
    those rows with the reader's own settings (app/option_chain/oi_regime.py), so nothing tenant-specific is stored
    here. One row per (underlying, slot): the collector is idempotent per slot."""

    __tablename__ = "oi_snapshots"
    __table_args__ = (
        UniqueConstraint("underlying", "slot_start", name="uq_oi_snapshot_slot"),
        Index("ix_oi_snapshots_day", "underlying", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    underlying: Mapped[str] = mapped_column(String(30), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)                 # the exchange's (IST) trading day
    slot_start: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)    # when the chain was read (data time)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    underlying_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    strikes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="worker")


class StrikeOISnapshotRecord(Base):
    """OI Banner O2: one strike of one slot - call/put OI and premium (the inputs of every banner number)."""

    __tablename__ = "strike_oi_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_id", "strike", name="uq_strike_oi_snapshot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("oi_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    call_oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_iv: Mapped[float | None] = mapped_column(Float, nullable=True)


class OIDayBaselineRecord(Base):
    """OI Banner O2: the first OI the collector saw for a strike on a trading day - the baseline of the OI-wall check
    (change in OI since the day began). Written once per (underlying, day, strike), never updated."""

    __tablename__ = "oi_day_baselines"
    __table_args__ = (UniqueConstraint("underlying", "trade_date", "strike", name="uq_oi_day_baseline"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    underlying: Mapped[str] = mapped_column(String(30), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    call_oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)


class OIBannerSettingRecord(Base):
    """OI Banner O2: a tenant's banner settings for one underlying ("*" = the tenant's default for every underlying).
    `enabled` asks the collector to follow the underlying; `overrides` is a JSON object of OIRegimeSettings fields
    layered over the platform defaults."""

    __tablename__ = "oi_banner_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "underlying", name="uq_oi_banner_setting"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    overrides: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))
    snoozed_until: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)     # O4: alerts paused until


class OIBannerStateRecord(Base):
    """OI Banner O4: a tenant's banner state per slot (its own settings), kept so changes are detectable and auditable."""

    __tablename__ = "oi_banner_states"
    __table_args__ = (UniqueConstraint("tenant_id", "underlying", "slot_start", name="uq_oi_banner_state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(30), nullable=False)
    slot_start: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    stable_direction: Mapped[str] = mapped_column(String(10), nullable=False)
    stable_strength: Mapped[str | None] = mapped_column(String(12), nullable=True)
    pcr_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    max_pain: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_pain_ref: Mapped[float | None] = mapped_column(Float, nullable=True)       # max pain at the last move alert
    dte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wall: Mapped[str | None] = mapped_column(String(40), nullable=True)            # e.g. "CE@25000" or None
    message: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))


class OIAlertLogRecord(Base):
    """OI Banner O4: every alert decision - sent, or held back (cooldown, quiet hours, snoozed) - with its dedupe key."""

    __tablename__ = "oi_alert_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(30), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False)
    old_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_state: Mapped[str] = mapped_column(String(40), nullable=False)
    slot_start: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)               # SENT / COOLDOWN / QUIET / SNOOZED
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))


class ThesisRecord(Base):
    """Phase BD-lite: one market thesis of one symbol at one moment (direction, confidence, agreement,
    scenarios, inputs) with the *shadow* size multiplier the reduce-only overlay would have used - stored
    so the next session can score it. Nothing in execution, risk or the guardian reads this table."""

    __tablename__ = "thesis_records"
    __table_args__ = (Index("ix_thesis_records_lookup", "tenant_id", "symbol", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)                 # IST day the thesis was made
    direction: Mapped[str] = mapped_column(String(10), nullable=False)                # BULLISH | BEARISH | NEUTRAL
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agreement: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)      # share of factors agreeing
    shadow_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lang: Mapped[str] = mapped_column(String(5), nullable=False, default="mr")
    narrative_source: Mapped[str] = mapped_column(String(10), nullable=False, default="rules")   # rules | model
    thesis_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    scored_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)            # BULL | BEAR | RANGE | UNKNOWN
    score: Mapped[float | None] = mapped_column(Float, nullable=True)                 # +1 right, -1 wrong, 0 neither
    score_detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsFeedbackRecord(Base):
    """Phase BD-2: one member's verdict on one feed item - was it useful, noise, or classified in the
    wrong direction. Tenant-scoped (the item itself is shared); one verdict per member per item,
    re-voting replaces it. Feeds the organisation's "news trust" that scales the thesis news factor."""

    __tablename__ = "news_feedback"
    __table_args__ = (UniqueConstraint("tenant_id", "news_event_id", "user_id", name="uq_news_feedback_member_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    news_event_id: Mapped[int] = mapped_column(ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)             # useful | noise | wrong_direction
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)



class AiAcknowledgementRecord(Base):
    """P0.8-D: a versioned acceptance - the AI Copilot first-use acknowledgement (per user: the Copilot is not a
    SEBI-registered adviser, it explains rules and data, the decision is the trader's) and the organisation's
    data-sharing consent before anything goes to an external LLM provider (per tenant, by the owner). Kept for
    good (`retention.NEVER_DELETED`)."""

    __tablename__ = "ai_acknowledgements"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)     # copilot_terms / data_consent
    version: Mapped[str] = mapped_column(String(24), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(4), nullable=False, default="en")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class ResearchStudyRecord(Base):
    """H-C3c: one strategy research study as a job - queued by the API, run by the research worker (a separate process,
    so a study's model calls and backtests never share a loop with trading or the API). Its trials live in
    `research_trials`; this row carries the request and the job state."""

    __tablename__ = "research_studies"

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    idea: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE")
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_drafts: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued", index=True)   # queued / running / done / failed / interrupted
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class ResearchTrialRecord(Base):
    """H-C3 (ADR-0019/0020): one trial of a strategy research study - the DSL tried, the in-sample window, its metrics and
    its daily returns. Append-only and never deleted: the study report deflates the chosen result by EVERY trial tried,
    so dropping a trial would make the search look better than it was."""

    __tablename__ = "research_trials"
    __table_args__ = (UniqueConstraint("study_id", "seq", name="uq_research_trials_study_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    study_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    dsl_json: Mapped[str] = mapped_column(Text, nullable=False)
    dsl_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE")
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    data_from: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    data_to: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)                 # ok / invalid / error; oos = the study's check
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    returns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")   # daily returns over the window
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class LlmCallRecord(Base):
    """P0.8-D: every LLM input and output - the system prompt, the user text, the answer (or the error), with hashes,
    the prompt version, provider, model, tokens, cost, tenant, user and feature. Append-only and never deleted
    (`retention.NEVER_DELETED`), so an answer a trader acted on can be audited years later."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    feature: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    system_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    user_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system_text: Mapped[str] = mapped_column(Text, nullable=False)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(300), nullable=False, default="ok")     # ok / error: <reason>
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False, index=True)
    # BYOK-V1: True when the call ran on the user's own provider key (the user pays the provider, not the plan budget);
    # `app.ai.byok.byok_spend` sums these per user against the user's caps.
    billed_to_user_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class UserAiKeyRecord(Base):
    """BYOK-V1 (docs/BYOK.md): one user's own AI provider API key, one row per (user, provider). The key is encrypted with
    the tenant's data key (purpose `byok_ai_key`) and the sealed plaintext also carries the tenant, the user and the
    provider, so a ciphertext copied to another user's or organisation's row refuses to open. Only `key_hint` (first 4
    and last 4 characters) is ever shown; the key itself is never returned, logged or audited."""

    __tablename__ = "user_ai_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_ai_keys_user_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)               # anthropic / openai / gemini
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")       # "" = the configured default
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(200), nullable=True)   # sanitised, never the key


class UserVisionPrefsRecord(Base):
    """BYOK-V1: one user's "Vision chart review" settings - on/off, the provider their charts go to, daily and monthly
    spend caps in USD (hard stop), which charts to audit and where reports go. Vision only comments (ADR-0006)."""

    __tablename__ = "user_vision_prefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)          # None = the first provider with a key
    daily_cap_usd: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    monthly_cap_usd: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    audit_proposals: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    audit_scan_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    audit_daily_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    channel_telegram: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    channel_in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


class VisionReportRecord(Base):
    """BYOK-V2 (docs/BYOK.md): one user's Vision report on one proposal, made with THAT user's own key and shown to that
    user only. Queued as `pending` when the proposal is raised (never blocking it), audited later by the worker:
    `ok` (report written), `skipped` (no call made: vision off, no key, a cap, no chart data, proposal already decided)
    or `failed` (the provider call failed; `reason` is sanitised and never contains the key). The chart image is not
    stored - only its SHA-256. The report only comments: nothing here approves, rejects or changes anything.

    BYOK-V3: the subject is a kind - `proposal` (V2, `ai_action_id`), `scan_alert` (one screen-alert notification of a
    rule this user created: `notification_id`, `alert_rule_id`, the matched symbol) or `daily_review` (one symbol of this
    user's own after-close review, `subject_date` in IST). `subject_key` ("proposal:<id>", "scan:<notification id>",
    "daily:<date>:<EXCHANGE>:<SYMBOL>") is unique per user, so a subject is never audited twice for the same user."""

    __tablename__ = "vision_reports"
    __table_args__ = (UniqueConstraint("user_id", "ai_action_id", name="uq_vision_reports_user_action"),
                      UniqueConstraint("user_id", "subject_key", name="uq_vision_reports_user_subject"))

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ai_action_id: Mapped[int | None] = mapped_column(ForeignKey("ai_actions.id", ondelete="CASCADE"), nullable=True, index=True)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="proposal", server_default="proposal", index=True)
    subject_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True, index=True)
    alert_rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(10), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subject_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", index=True)   # pending/running/ok/skipped/failed
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)            # why skipped / failed (sanitised)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)              # <= 12 lines, numbers from the engine
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)       # the chart sent; the image is not kept
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    telegram_status: Mapped[str] = mapped_column(String(10), nullable=False, default="none", server_default="none")   # none/sent/failed
    telegram_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class PaperAccountRecord(Base):
    """Z3-b / BT3 (beta spec, Phase 0): one PAPER account per **user** (not per organisation): the starting capital the
    balance and the progression ladder are measured from, and the PAPER-scope pause (Telegram /pause, /resume) that
    stops that user's new PAPER entries and entry proposals - exits never (ADR-0004). Balance and P&L are not stored:
    they are summed from the user's own PAPER trades (`trades.user_id`), so the account can never drift from them."""

    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    starting_capital: Mapped[float] = mapped_column(Money, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    paused_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    # Z5 / P1-d: the user's own risk-by-default settings (risk per trade, daily loss limit, trades per day, minimum
    # reward:risk) as JSON; NULL = the platform defaults (app/risk_engine/by_default.py).
    risk_settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Z5 / P1-d: the entries-per-day slots taken on `entries_day` (IST), updated atomically per entry (by_default.reserve_entry).
    entries_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    entries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


class ExperienceOptOutRecord(Base):
    """N2-2 (P1 spec 1.4, flag `experience_levels`): a user's explicit opt-out of one experience gate (naked short options,
    futures) for one IST day, with the reason they typed. Logged (audit) and counted on the level card; it lets that
    user's own entries of that kind through until the day ends. Personal: tenant + user; exits never consult it."""

    __tablename__ = "experience_opt_outs"
    __table_args__ = (UniqueConstraint("user_id", "gate", "day", name="uq_experience_opt_outs_user_gate_day"),
                      Index("ix_experience_opt_outs_user_day", "user_id", "day"))

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    gate: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)     # the user's level when they opted out
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)              # IST day it covers
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class TradePlanRecord(Base):
    """Z5 / P1-d (P1 spec 1.2): the plan behind a PAPER ticket - setup, direction, entry / stop / target, reward:risk,
    time stop, invalidation note and the pre-trade checklist - with the sizing it was checked against (the user's risk
    cap, the quantity, a deliberate override and its reason) and the cost line. Stored with the order and the position it
    produced (or the reasons it was refused), so the plan can be compared with what happened. Personal: tenant + user."""

    __tablename__ = "trade_plans"
    __table_args__ = (Index("ix_trade_plans_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    setup: Mapped[str] = mapped_column(String(30), nullable=False)
    entry: Mapped[float] = mapped_column(Price, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Price, nullable=False)
    target: Mapped[float] = mapped_column(Price, nullable=False)
    reward_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_stop: Mapped[str | None] = mapped_column(String(10), nullable=True)          # "HH:MM" IST
    invalidation: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checklist_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    checklist_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    risk_amount: Mapped[float] = mapped_column(Money, nullable=False)
    risk_cap: Mapped[float] = mapped_column(Money, nullable=False)
    override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    override_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    costs_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")     # PENDING | PLACED | PROPOSED | REFUSED | FAILED
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Under approval-only mode the ticket raises an ENTRY proposal; Approve places the PAPER order (Z3-a).
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class RiskLockoutRecord(Base):
    """Z5 / P1-d (P1 spec 1.1): a user's daily loss lockout - recorded the first time their PAPER net P&L of the IST day
    reaches the limit, and kept for the rest of that day even if the P&L recovers. It stops new PAPER entries and entry
    proposals of that user only; exits are never locked (ADR-0004). One row per user, mode, day and kind."""

    __tablename__ = "risk_lockouts"
    __table_args__ = (UniqueConstraint("user_id", "mode", "trading_day", "kind", name="uq_risk_lockouts_user_day_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    trading_day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="DAILY_LOSS")
    pnl: Mapped[float] = mapped_column(Money, nullable=False)
    limit_amount: Mapped[float] = mapped_column(Money, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


# --- Part B1: market data lake (ADR-0013). Every row carries `ingested_at` (when the platform learned it) and, where a
# source can correct itself, a `version`: a query "as of T" sees only rows ingested at or before T and, per key, the
# highest such version. Times are bar END / event time in UTC. On TimescaleDB the four time-series tables become
# hypertables (migration b1c2d3e4f5a6, only when the extension is available); on plain Postgres/SQLite they are tables.

class MdCandleRecord(Base):
    __tablename__ = "md_candles"

    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_TZ_DATETIME, primary_key=True)          # bar END (realism C1)
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    open: Mapped[float] = mapped_column(Price, nullable=False)
    high: Mapped[float] = mapped_column(Price, nullable=False)
    low: Mapped[float] = mapped_column(Price, nullable=False)
    close: Mapped[float] = mapped_column(Price, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class MdTickRecord(Base):
    __tablename__ = "md_ticks"

    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_TZ_DATETIME, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=0)       # order within the same timestamp
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    ltp: Mapped[float] = mapped_column(Price, nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class MdOptionChainRecord(Base):
    __tablename__ = "md_option_chain_snapshots"

    underlying: Mapped[str] = mapped_column(String(40), primary_key=True)
    expiry: Mapped[date] = mapped_column(Date, primary_key=True)
    strike: Mapped[float] = mapped_column(Price, primary_key=True)
    option_type: Mapped[str] = mapped_column(String(2), primary_key=True)          # CE / PE
    ts: Mapped[datetime] = mapped_column(_TZ_DATETIME, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    ltp: Mapped[float | None] = mapped_column(Price, nullable=True)
    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class MdPositionLimitRecord(Base):
    """Market-wide position limit and open interest per underlying per day (NSE F&O) - the data D7 (FutEq / MWPL) needs."""

    __tablename__ = "md_position_limits"

    underlying: Mapped[str] = mapped_column(String(40), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mwpl: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open_interest: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class InstrumentMasterVersionRecord(Base):
    """An instrument's contract terms over time (lot size, tick size, ...): valid from `valid_from` until the next version."""

    __tablename__ = "instrument_master_versions"
    __table_args__ = (UniqueConstraint("instrument_key", "valid_from", "source", name="uq_instrument_master_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    tradingsymbol: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)                  # EQ / FUT / CE / PE / INDEX ...
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tick_size: Mapped[float] = mapped_column(Price, nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Price, nullable=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class MdCorporateActionRecord(Base):
    """Market-data lake (not the fundamentals `corporate_actions` announcements table): splits, bonuses and dividends with their ex-date; `ratio_new / ratio_old` is the share multiplier (1:2 split -> 2/1)."""

    __tablename__ = "md_corporate_actions"
    __table_args__ = (UniqueConstraint("symbol", "exchange", "ex_date", "action", "version", name="uq_md_corporate_action_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    action: Mapped[str] = mapped_column(String(12), nullable=False)                # SPLIT / BONUS / DIVIDEND
    ratio_new: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratio_old: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Money, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class DataQualityEventRecord(Base):
    """One detected problem in the lake (gap / spike / duplicate / late / mismatch) - B3 writes these, nothing deletes them."""

    __tablename__ = "data_quality_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    instrument_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ts: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detected_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    # HB0: NULL = an event about the shared lake (everyone may count it); set = an event about a tenant's private store
    # (md_tenant_candles) - only that tenant sees it, like the bars themselves.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)


class MdTenantCandleRecord(Base):
    """HB0: a tenant-PRIVATE candle store (the owner's licensed 1-minute history). Same shape and as-of/version rules as
    `md_candles`, plus `tenant_id` in the key. Kept as a separate table on purpose: no shared read path (history API's
    shared store, quality scans, backtests) ever queries it, so a missing filter can never leak licensed bars; the only
    reader is `app.market_lake.tenant_store`, which requires a tenant id."""

    __tablename__ = "md_tenant_candles"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_TZ_DATETIME, primary_key=True)          # bar END, UTC
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    open: Mapped[float] = mapped_column(Price, nullable=False)
    high: Mapped[float] = mapped_column(Price, nullable=False)
    low: Mapped[float] = mapped_column(Price, nullable=False)
    close: Mapped[float] = mapped_column(Price, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class HistoryImportFileRecord(Base):
    """HB0: one object of the R2 history store and what the importer did with it - the resume point (a file is skipped
    once `done` with the same sha256) and the progress source for the HB4 admin page (per dataset / symbol: done,
    pending, refused, last error). Status: pending | running | done | refused | failed."""

    __tablename__ = "history_import_files"
    __table_args__ = (Index("ix_history_import_files_dataset_symbol", "dataset", "exchange", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    manifest_key: Mapped[str] = mapped_column(String(512), nullable=False)
    dataset: Mapped[str] = mapped_column(String(8), nullable=False)               # daily | m1
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)                # yyyy (daily) | yyyy-mm (m1)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)  # m1 only
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)               # as listed in the manifest
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rows_expected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_read: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_in_lake: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    first_ts: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_ts: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


# --- Z7a-2: market zones (demand / supply, order blocks, S/R, gaps) with lifecycle and level memory ------------------------
class MarketZoneSettingRecord(Base):
    """Z7a-2: a tenant's market-zone settings for one instrument ("*" = the tenant's default for every instrument), the
    OI banner pattern. `enabled` asks the nightly zone job to follow the instrument; `overrides` is a JSON object of
    ZoneSettings fields layered over the platform defaults (zones/store.py PLATFORM_DEFAULTS + MARKET_ZONES_DEFAULTS)."""

    __tablename__ = "market_zone_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "instrument_key", name="uq_market_zone_setting"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    instrument_key: Mapped[str] = mapped_column(String(64), nullable=False)                    # lake key "NSE:BANKNIFTY" or "*"
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    overrides: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=lambda: datetime.now(timezone.utc))


class MarketZoneRunRecord(Base):
    """Z7a-2: one stored snapshot of an instrument's zones, computed from the shared lake bars that closed by `as_of`
    (never later bars, never a tenant's private data). scope "platform" = the default settings (shared by every tenant
    without overrides); "tenant:<id>" = that tenant's own settings (`settings_hash` tells them apart). Append-only: the
    zones as of T are the run with the latest as_of <= T."""

    __tablename__ = "market_zone_runs"
    __table_args__ = (UniqueConstraint("scope", "instrument_key", "as_of", name="uq_market_zone_run"),)   # also the as-of lookup index

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    instrument_key: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)                      # close of the last bar used
    computed_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False, default=_utcnow)
    settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    bars: Mapped[str] = mapped_column(Text, nullable=False, default="{}")                       # JSON {timeframe: bars read}
    zone_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    previous_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)                 # the run level memory merged with
    source_last_ts: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)        # newest bar END it read
    source_ingested_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)    # newest ingest it read


class MarketZoneRecord(Base):
    """Z7a-2: one zone of a stored snapshot - its bounds, role by price, ATP lifecycle state (fresh / tested / flipped /
    invalidated) and counters, and the tracker the next run continues from (JSON)."""

    __tablename__ = "market_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("market_zone_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    zone_key: Mapped[str] = mapped_column(String(200), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    zone_low: Mapped[float] = mapped_column(Float, nullable=False)
    zone_high: Mapped[float] = mapped_column(Float, nullable=False)
    strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)                        # RESISTANCE_SUPPLY / SUPPORT_DEMAND
    origin_role: Mapped[str] = mapped_column(String(12), nullable=False)                       # SUPPORT / RESISTANCE
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    formed_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    first_touch_event: Mapped[str | None] = mapped_column(String(12), nullable=True)          # Trade's FILLED moment (parity)
    first_touch_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    touches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flips: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    structure_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    remembered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    tracker: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class HistoryBackfillFileRecord(Base):
    """HB1: one NSE daily file - a segment (`cm` cash bhavcopy, `fo` F&O bhavcopy, `index` all-indices closes) for one
    trade date - and what the backfill did with it. The resume point (a `done` day is never fetched again) and the
    progress source of the admin page. Status: pending | running | done | no_file (the archive has no file: a holiday,
    a weekend, or not published yet) | blocked (the archive refused after its retries - upload the file by hand) |
    refused (the file failed a check; nothing written) | failed (unexpected error; retried on the next run)."""

    __tablename__ = "history_backfill_files"
    __table_args__ = (UniqueConstraint("segment", "trade_date", name="uq_history_backfill_file"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    segment: Mapped[str] = mapped_column(String(8), nullable=False)                # cm | fo | index
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)          # nse_archives | local_dir | upload
    file_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    file_format: Mapped[str | None] = mapped_column(String(16), nullable=True)     # cm_legacy | cm_udiff | cm_full | fo_legacy | ...
    # What the bars' volume counts: shares (cash), contracts (legacy F&O), unconfirmed (UDiFF F&O TtlTradgVol),
    # withheld (F&O volume stored as 0 until the unit is settled), none (indices).
    volume_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)          # of the bytes as received (zip or csv)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_read: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_loaded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


class MdIsinSymbolRecord(Base):
    """HB1: which symbol an ISIN traded under, as OBSERVED in the exchange's own daily cash files - one row per
    contiguous run (first and last trade date seen), so a rename and back (X -> Y -> X) is two X runs. Survivorship-free
    by construction - a delisted or suspended name keeps its rows - and the join that stitches one company's daily
    history across symbol changes and ISIN changes (`app.market_lake.by_isin`). Kept apart from U1's `symbol_history`
    (derived from the exchange's symbol-change file) so neither rewrites the other."""

    __tablename__ = "md_isin_symbols"
    __table_args__ = (UniqueConstraint("exchange", "isin", "symbol", "first_date", name="uq_md_isin_symbol_run"),
                      Index("ix_md_isin_symbols_symbol", "exchange", "symbol"))

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, default="NSE")
    isin: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    series: Mapped[str] = mapped_column(String(4), nullable=False)               # the series of the latest row seen
    first_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_date: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


class HistoryIntradayDayRecord(Base):
    """HB2: one (tenant, session holder, instrument, trade date) of the owner's 1-minute history and what the capture / backfill did with
    it - the resume point (a `done` day is never asked for again) and the progress source. Status: pending | running |
    done | no_data (the broker returned no bar: beyond its depth, not traded, or a holiday without a calendar) | refused
    (a check failed; nothing written) | failed (unexpected error or a broker error; retried on the next pass). The bars
    themselves live in `md_tenant_candles` (tenant-private); `credential_id` records whose session they came from."""

    __tablename__ = "history_intraday_days"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "instrument_key", "trade_date", name="uq_history_intraday_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    instrument_key: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)            # broker:<name>
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("broker_credentials.id", ondelete="SET NULL"), nullable=True)
    # The account holder whose own session the bars came from: the only member who may read `broker:*` rows (BT1b).
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    endpoint: Mapped[str | None] = mapped_column(String(16), nullable=True)          # intraday (today) | historical
    bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dropped_forming: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # bars not closed at fetch time
    quality_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_ts: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_ts: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow, nullable=False)


# --- Screener addendum U1: NSE universe reference data (as-of)------------------------------------------------------------
class SecurityRecord(Base):
    """U1-a: one listed security, keyed by ISIN (stable across renames). The broker `instruments` master stays the
    tradability layer and joins on ISIN/symbol. `last_seen_on` is the last equity-list file that carried it; a security
    missing from today's file is not marked delisted on that alone (delisting comes from the exchange's own list)."""

    __tablename__ = "securities"

    isin: Mapped[str] = mapped_column(String(12), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    series: Mapped[str] = mapped_column(String(4), nullable=False)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    face_value: Mapped[float | None] = mapped_column(Money, nullable=True)
    market_lot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchanges: Mapped[str] = mapped_column(String(20), nullable=False, default="NSE")          # "NSE", "NSE,BSE"
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ACTIVE")         # ACTIVE / SUSPENDED / DELISTED
    is_sme: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_etf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    underlying: Mapped[str | None] = mapped_column(String(100), nullable=True)                 # ETFs: the tracked index/asset
    last_seen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class SymbolHistoryRecord(Base):
    """U1-a: which symbol an ISIN traded under, as half-open ranges [valid_from, valid_to); valid_to NULL = still current.
    valid_from NULL = since before the first record we hold (never invented). A change closes the open range and opens
    a new one; nothing is overwritten."""

    __tablename__ = "symbol_history"
    __table_args__ = (UniqueConstraint("isin", "symbol", "valid_from", name="uq_symbol_history_range"),
                      Index("ix_symbol_history_symbol", "symbol", "valid_from"))

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class IndexRecord(Base):
    """U1-b: one index (broad / sectoral / thematic / strategy). The catalogue is data (app/universe/data/indices.json);
    `broker_symbols` maps the index to each broker's spelling (JSON object)."""

    __tablename__ = "indices"

    index_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    family: Mapped[str] = mapped_column(String(20), nullable=False)          # BROAD / SECTORAL / THEMATIC / STRATEGY / FIXED_INCOME
    base_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    method: Mapped[str | None] = mapped_column(String(60), nullable=True)
    broker_symbols: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    has_derivatives: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class IndexMembershipRecord(Base):
    """U1-b: an ISIN's membership of an index as a half-open range [valid_from, valid_to). A rebalance closes ranges and
    opens new ones; nothing is overwritten. `start_observed` = the start is only the first file we saw it in (the real
    inclusion may be earlier - backfill U1-e extends it from older files, never by guessing). `weight` is the latest
    published weight, not a history."""

    __tablename__ = "index_membership"
    __table_args__ = (UniqueConstraint("index_code", "isin", "valid_from", name="uq_index_membership_range"),
                      Index("ix_index_membership_lookup", "index_code", "valid_from", "valid_to"))

    id: Mapped[int] = mapped_column(primary_key=True)
    index_code: Mapped[str] = mapped_column(String(40), nullable=False)
    isin: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class ClassificationRecord(Base):
    """U1-b: an ISIN's industry classification over time (scheme NSE: macro-economic sector -> sector -> industry ->
    basic industry). A level the source does not give stays NULL - never inferred."""

    __tablename__ = "classifications"
    __table_args__ = (UniqueConstraint("isin", "scheme", "valid_from", name="uq_classification_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    scheme: Mapped[str] = mapped_column(String(12), nullable=False, default="NSE")
    macro_sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    basic_industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class IndexEodRecord(Base):
    """U1-c: one index's daily close from the exchange's all-indices file, with the valuation ratios it publishes."""

    __tablename__ = "index_eod"

    index_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Price, nullable=True)
    high: Mapped[float | None] = mapped_column(Price, nullable=True)
    low: Mapped[float | None] = mapped_column(Price, nullable=True)
    close: Mapped[float] = mapped_column(Price, nullable=False)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    div_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class FoMembershipRecord(Base):
    """U1-c: an underlying's F&O eligibility and market lot over time, half-open [valid_from, valid_to). Index
    underlyings have no ISIN. A lot-size change closes the range and opens a new one."""

    __tablename__ = "fo_membership"
    __table_args__ = (UniqueConstraint("underlying", "valid_from", name="uq_fo_membership_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    underlying: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    is_index: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class FoBanRecord(Base):
    """U1-c: the exchange's own F&O ban list, one row per (underlying, trade date). D7's computed ban status is checked
    against it."""

    __tablename__ = "fo_ban_history"

    underlying: Mapped[str] = mapped_column(String(40), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class ScreenRecord(Base):
    """S1d (ADR-0021): a saved ScreenQL screen - the canonical text and the AST the validator accepted, its base timeframe
    and default parameters. Tenant-scoped; archived, never hard-deleted (a run may point at it)."""

    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)              # canonical ScreenQL text
    ast_json: Mapped[str] = mapped_column(Text, nullable=False)
    ast_version: Mapped[str] = mapped_column(String(20), nullable=False, default="screenql/1")
    base_tf: Mapped[str] = mapped_column(String(4), nullable=False, default="1d")
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class ScreenRunRecord(Base):
    """S1d (ADR-0021 §6): one screen run - what ran (AST hash and version), on which universe and data source, and what
    matched. Append-only: a match list someone acted on can be reproduced."""

    __tablename__ = "screen_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    screen_id: Mapped[int | None] = mapped_column(ForeignKey("screens.id", ondelete="SET NULL"), nullable=True, index=True)
    ast_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ast_version: Mapped[str] = mapped_column(String(20), nullable=False)
    base_tf: Mapped[str] = mapped_column(String(4), nullable=False)
    universe_json: Mapped[str] = mapped_column(Text, nullable=False)            # the symbols asked for
    data_source: Mapped[str] = mapped_column(String(60), nullable=False)        # broker:<name> / lake (S2)
    scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # [{symbol, matched, reason}]
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False, index=True)


class ScreenEvidenceRecord(Base):
    """U8 EV1/EV2: the evidence pack (`evidence/1`) of one matched symbol - stored with the screen run or the alert
    event that produced it, in the same pass as the match, so "Show on chart", the verify strip and the Telegram image
    show exactly what the engine used. Read by the run's organisation (and, with BT1 `byob_data_tier` on, by the user
    whose broker session read the bars). Kept for `RETENTION_SCREEN_EVIDENCE_DAYS`. Never an order or a proposal."""

    __tablename__ = "screen_evidence"
    __table_args__ = (UniqueConstraint("run_id", "symbol", name="uq_screen_evidence_run_symbol"),
                      UniqueConstraint("alert_event_id", name="uq_screen_evidence_alert_event"))

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("screen_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    alert_event_id: Mapped[int | None] = mapped_column(ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    base_tf: Mapped[str] = mapped_column(String(4), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)        # start of the matched bar (UTC)
    schema: Mapped[str] = mapped_column(String(20), nullable=False, default="evidence/1")
    pack_json: Mapped[str] = mapped_column(Text, nullable=False)
    pack_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # BT1b: the bars came through the user's own broker session (byob_data_tier on at store time) - such a pack stays
    # owner-only even if the flag is turned off later
    per_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False, index=True)


class AlertRuleRecord(Base):
    """S3a (ADR-0022): a screen or instrument alert rule - what fires (a saved screen, or a ScreenQL condition on one
    symbol), how loud (priority), how often (cooldown), and instant or digest delivery. Rules produce `alert_events`;
    events produce notifications and outbox deliveries. A rule never places or changes an order."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)                 # screen / instrument
    screen_id: Mapped[int | None] = mapped_column(ForeignKey("screens.id", ondelete="SET NULL"), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    condition_text: Mapped[str | None] = mapped_column(Text, nullable=True)        # canonical ScreenQL (instrument rules)
    base_tf: Mapped[str] = mapped_column(String(4), nullable=False, default="1d")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")   # critical / normal / low
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="instant")      # instant / digest
    digest_every: Mapped[str] = mapped_column(String(10), nullable=False, default="hourly")  # hourly / eod
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")     # active / paused
    expires_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    # S4a: the bar-close engine - a screen rule's own symbols (JSON list, at most 50), the exchange, the last closed bar
    # this rule was evaluated on, when the engine last looked (data not in yet -> retried after a pause), and why it
    # last could not evaluate.
    universe_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE", server_default="NSE")
    last_bar_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    last_problem: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # S4b-2: when the rule fires - "bar_close" (closed bars only, the default) or "intrabar" (on the forming bar, at most
    # once per bar and symbol; needs the `screener_intrabar` flag, off by default).
    fire_on: Mapped[str] = mapped_column(String(10), nullable=False, default="bar_close", server_default="bar_close")


class AlertEventRecord(Base):
    """S3a (ADR-0022): one firing of a rule for one symbol on one bar. `idem_key` (rule, symbol, condition hash, bar
    time) is unique, so a re-run of the same bar never fires twice. Status: pending -> sent / suppressed / held (quiet
    hours, hourly cap, digest), always with a reason code."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    condition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    idem_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Z10-b: the rule's timeframe when the event was recorded - the stale-data check at send time judges the bar by it,
    # not by whatever the rule says later. NULL for events recorded before this column.
    base_tf: Mapped[str | None] = mapped_column(String(5), nullable=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    values_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")     # the trigger values, with their timestamps
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class NotificationLinkRecord(Base):
    """S3b-2 (ADR-0022): a user's own Telegram chat, linked by a one-time code sent to the organisation's bot as
    `/start <code>`. Only the code's hash is stored; a code expires and works once. A linked chat receives the screen
    alerts of rules that user created - it gains no command rights (those stay on the channel's approver list)."""

    __tablename__ = "notification_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="telegram")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")          # pending | linked | revoked
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    linked_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)


class EmailOptOutRecord(Base):
    """S3b-2: an address that unsubscribed (signed link in the mail, or List-Unsubscribe one-click) from an
    organisation's screen-alert emails. Risk and system emails are not affected. A trader can remove the row."""

    __tablename__ = "email_opt_outs"
    __table_args__ = (UniqueConstraint("tenant_id", "address", "scope", name="uq_email_opt_outs_tenant_address_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="screen_alerts")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class NotificationPolicyRecord(Base):
    """S3a (ADR-0022): an organisation's delivery policy - its timezone and quiet hours, the hourly cap, and the burst
    grouping window. One row per tenant; missing = the defaults in app/alerts/engine.py."""

    __tablename__ = "notification_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False, default="Asia/Kolkata")
    quiet_start: Mapped[str | None] = mapped_column(String(5), nullable=True)      # HH:MM in `timezone`
    quiet_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    max_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    group_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    eod_digest_time: Mapped[str] = mapped_column(String(5), nullable=False, default="15:45")
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class ChartDrawingRecord(Base):
    """CH1 (ADR-0023): one user drawing on a symbol's chart, in our own engine-neutral JSON (`drawing/1`: time/price
    anchors, so every timeframe and every chart engine shows the same drawing). Versioned for optimistic concurrency
    (undo/redo is client-side over versions), lockable, soft-deleted."""

    __tablename__ = "chart_drawings"
    __table_args__ = (Index("ix_chart_drawings_user_symbol", "user_id", "symbol", "exchange"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE")
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    drawing_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class ChartLayoutRecord(Base):
    """CH5: one saved multi-chart workspace layout of one user (`layout/1`: grid of 1/2/4/6/8 charts, each with its
    symbol, timeframe, chart type and link group; which link groups sync symbol / interval / crosshair). Per user and
    tenant, named, versioned for optimistic concurrency. A picture of the screen: nothing in it places an order."""

    __tablename__ = "chart_layouts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_chart_layouts_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    layout_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class WatchlistRecord(Base):
    """AMW-2: one named watchlist of one user - an ordered list of symbols the Screener and the Scanner can load as
    their universe. Per user and tenant (BT1b), unique by name per user. A list of names only: it places nothing.
    Behind the `watchlists` flag (off by default)."""

    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "name", name="uq_watchlists_tenant_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    symbols_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class OptionStrategyRecord(Base):
    """Z5 / P1-c (ADR-0025): a strategy saved from the Options Builder - the instrument inputs and the legs as the page
    held them (`strategy_json`), shared within the tenant. Research data only: it places nothing. Archived, never
    hard-deleted. Behind the `options_builder_saved` flag (off by default)."""

    __tablename__ = "option_strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    underlying: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    strategy_json: Mapped[str] = mapped_column(Text, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class BetaInviteCodeRecord(Base):
    """PW6 (beta spec 4a): a closed-beta invite code, issued by a SUPER_ADMIN. Only the SHA-256 of the normalised code is
    stored - the plain code is shown once, at creation. With the `beta_cohort_gate` flag on, each registration spends one
    use; the cohort size is the sum of `used_count` over every code (app/beta/invites.py)."""

    __tablename__ = "beta_invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False)


class FeedbackRecord(Base):
    """PW6: a "Send feedback" message from a logged-in user, with the app version and the screen path it was sent from
    (query string stripped). No market data, request bodies, tokens or secrets - only what the user typed and those two
    labels, plus a truncated User-Agent. A SUPER_ADMIN reads them in the admin console."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str] = mapped_column(String(40), nullable=False)
    screen: Mapped[str] = mapped_column(String(200), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, nullable=False, index=True)
