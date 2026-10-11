from datetime import date, datetime, timezone

from sqlalchemy.sql import false, true
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
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
    broker_account_id: Mapped[int | None] = mapped_column(ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    # Phase D4: the broker's id for the order that closed a LIVE position (the SL when the
    # exchange closed us at the stop, else the market exit), so contract-note legs can be
    # matched to both sides of the trade.
    exit_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
    # Phase AS: INTRADAY (MIS, squared off at the close) or SWING (CNC / NRML, held overnight).
    holding: Mapped[str] = mapped_column(String(10), nullable=False, default="INTRADAY", server_default="INTRADAY")
    # P0.5 / T5: how LIVE entries are sent. MARKET (default, unchanged behaviour) or PROTECTED_LIMIT - a marketable
    # limit `market_protection_pct` past the signal price (default 0.5%), so a thin book cannot fill a market
    # order far from the price the strategy sized on. Unfilled remainder is cancelled by the fill check (T1).
    order_style: Mapped[str] = mapped_column(String(20), nullable=False, default="MARKET", server_default="MARKET")
    market_protection_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
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


class CompanyRecord(Base):
    """Reference data about a listed company - shared, not user-private (like an instrument
    master), but every write is attributed to the user who entered it since nothing here is
    fetched automatically (no live SEBI/NSE/BSE feed is wired in - see
    app/fundamentals/providers/). `source_json` holds the SourceCitation for the profile itself.
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
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
    __table_args__ = (UniqueConstraint("company_id", "period_type", "period_label", name="uq_company_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
