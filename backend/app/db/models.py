from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_TZ_DATETIME = DateTime(timezone=True)


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
    webhook_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="USER")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    broker_credentials: Mapped[list["BrokerCredentialRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class BrokerCredentialRecord(Base):
    """Broker credentials, encrypted at rest (see app/secrets_store/encryption.py). The DB only
    ever stores ciphertext - decryption happens in memory, on demand, right before an adapter
    is constructed. Scoped by tenant_id (a broker account belongs to the org, not to whichever
    user happened to add it); user_id is kept only for attribution.
    """

    __tablename__ = "broker_credentials"
    __table_args__ = (UniqueConstraint("tenant_id", "broker_name", name="uq_tenant_broker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
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
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    target1: Mapped[float] = mapped_column(Float, nullable=False)
    target2: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    charges: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
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
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED", index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


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
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


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
