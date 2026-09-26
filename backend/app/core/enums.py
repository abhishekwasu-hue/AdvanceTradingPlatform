from enum import Enum


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class SignalGrade(str, Enum):
    A1 = "A1"
    HIGH_QUALITY = "High Quality"
    VALID = "Valid"
    WEAK = "Weak"
    NO_TRADE = "No Trade"


class NotificationType(str, Enum):
    """In-app notification categories (spec section - notification engine). RISK_REJECTION and
    DAILY_LOSS_LIMIT are both risk-engine outcomes; DAILY_LOSS_LIMIT is split out specifically
    because breaching the daily loss limit warrants louder (CRITICAL) treatment than an ordinary
    risk rejection (e.g. R:R below minimum)."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REJECTION = "REJECTION"
    BROKER_DISCONNECT = "BROKER_DISCONNECT"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    RISK_REJECTION = "RISK_REJECTION"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"
    # Account security: login from a new device, MFA/password changes (Phase C4).
    SECURITY = "SECURITY"


class NotificationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AssetClass(str, Enum):
    """What kind of instrument a symbol is - drives position sizing in the risk engine
    (app/instruments/registry.py): equity/index options size in whole lots off the tenant's
    configured lot_size exactly as before, while COMMODITY and CRYPTO size off the instrument's
    own contract spec instead, and CRYPTO allows fractional quantities (you can buy 0.001 BTC;
    there is no such thing as "one lot" of a spot crypto pair).
    """

    EQUITY = "EQUITY"
    INDEX_OPTION = "INDEX_OPTION"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"


class RiskScope(str, Enum):
    """Phase I1 / V4.5: where a risk limit applies. Evaluated together; the strictest wins."""
    GLOBAL = "GLOBAL"          # platform-wide, SUPER_ADMIN
    TENANT = "TENANT"
    USER = "USER"              # scope_id = user id
    ACCOUNT = "ACCOUNT"        # scope_id = broker account id
    STRATEGY = "STRATEGY"      # scope_id = strategy id
    INSTRUMENT = "INSTRUMENT"  # scope_id = symbol


class RiskLimitType(str, Enum):
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"                    # currency, realised today
    MAX_STRATEGY_LOSS = "MAX_STRATEGY_LOSS"              # currency, realised today per strategy
    MAX_LOSS_PER_TRADE = "MAX_LOSS_PER_TRADE"            # currency at the stop
    MAX_ORDER_VALUE = "MAX_ORDER_VALUE"                  # currency, entry x quantity
    MAX_POSITION_QUANTITY = "MAX_POSITION_QUANTITY"      # units per order
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"            # count
    MAX_TRADES_PER_DAY = "MAX_TRADES_PER_DAY"            # count
    MAX_CAPITAL_ALLOCATION_PCT = "MAX_CAPITAL_ALLOCATION_PCT"  # % of capital in one order


class RiskAction(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK_ORDER = "BLOCK_ORDER"
    STOP_STRATEGY = "STOP_STRATEGY"
    EXIT_POSITION = "EXIT_POSITION"
    GLOBAL_KILL = "GLOBAL_KILL"


class KillSwitchScope(str, Enum):
    """Kill switches stop new order entries at three widening scopes (spec: strategy/user/
    global). GLOBAL is platform-wide (SUPER_ADMIN only); TENANT and STRATEGY are scoped to the
    caller's own tenant."""

    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    STRATEGY = "STRATEGY"


class UserRole(str, Enum):
    """Platform RBAC roles (spec section 5-6). SUPER_ADMIN is platform-wide (a manual DB flag for
    platform operators); the rest are tenant-scoped. A registration creates the tenant's OWNER;
    the owner invites teammates as USER (trader), STRATEGY_CREATOR or VIEWER (read-only).
    SUPPORT is platform support staff: read-only inside any tenant they are placed in.
    See app/auth/dependencies.py::require_trader / require_owner for the gates."""

    SUPER_ADMIN = "SUPER_ADMIN"
    OWNER = "OWNER"
    USER = "USER"
    STRATEGY_CREATOR = "STRATEGY_CREATOR"
    SUPPORT = "SUPPORT"
    VIEWER = "VIEWER"


class StrategyCategory(str, Enum):
    MULTI_TIMEFRAME = "multi_timeframe"
    INDICATOR_BASED = "indicator_based"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class DeploymentStatus(str, Enum):
    """Lifecycle of a StrategyDeploymentRecord (app/db/models.py) - the unit of work the
    autonomous trading worker (app/workers/trading_worker.py) picks up every cycle. Only ACTIVE
    deployments are evaluated; PAUSED ones keep their open positions monitored but take no new
    entries (the worker itself pauses a deployment on a broker token expiry or repeated
    failures, recording why in `pause_reason`); STOPPED is terminal for the row.
    """

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class AlertChannelType(str, Enum):
    """Out-of-app delivery channels for notifications (app/alerts/). In-app is always on; these
    are the ones that reach a phone/inbox while every browser is closed."""

    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"


class AlertDeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class BrokerTokenStatus(str, Enum):
    """What the platform currently knows about a stored broker session token
    (BrokerCredentialRecord.token_status). Upstox/Zerodha retail access tokens expire every
    trading day, so this is the state a LIVE deployment is gated on - see
    app/brokers/token_lifecycle.py.
    """

    UNKNOWN = "UNKNOWN"
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """The formal order lifecycle every paper/live execution attempt is recorded against
    (app/execution/order_state_machine.py enforces which transitions are legal). Terminal states
    are POSITION_OPEN, REJECTED, FAILED, CANCELLED - PENDING/PARTIAL_FILL are the only
    non-terminal states an order can sit in between submission and a final outcome.
    """

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RISK_CHECK = "RISK_CHECK"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"


class CapCategory(str, Enum):
    LARGE_CAP = "LARGE_CAP"
    MID_CAP = "MID_CAP"
    SMALL_CAP = "SMALL_CAP"
    MICRO_CAP = "MICRO_CAP"


class PeriodType(str, Enum):
    QUARTER = "QUARTER"
    ANNUAL = "ANNUAL"
    TTM = "TTM"


class QualityLabel(str, Enum):
    """Generic 5-band quality label reused across business quality, earnings quality, etc."""

    STRONG = "Strong"
    GOOD = "Good"
    AVERAGE = "Average"
    WEAK = "Weak"
    DETERIORATING = "Deteriorating"


class TrendLabel(str, Enum):
    IMPROVING = "Improving"
    STABLE = "Stable"
    DETERIORATING = "Deteriorating"
    HIGHLY_VOLATILE = "Highly Volatile"


class ValuationLabel(str, Enum):
    DEEPLY_UNDERVALUED = "Deeply Undervalued"
    UNDERVALUED = "Undervalued"
    FAIRLY_VALUED = "Fairly Valued"
    EXPENSIVE = "Expensive"
    EXTREMELY_EXPENSIVE = "Extremely Expensive"


class EarningsGrowthVisibility(str, Enum):
    VERY_HIGH = "Very High"
    HIGH = "High"
    MODERATE = "Moderate"
    LOW = "Low"
    DETERIORATING = "Deteriorating"


class FundamentalGrade(str, Enum):
    EXCEPTIONAL = "Exceptional"
    STRONG = "Strong"
    GOOD = "Good"
    AVERAGE = "Average"
    WEAK = "Weak"
    POOR = "Poor"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    EXTREME = "Extreme"


class Bias(str, Enum):
    BULLISH = "Bullish"
    NEUTRAL = "Neutral"
    BEARISH = "Bearish"


class FusionBias(str, Enum):
    A1_LONG_BIAS = "A1 LONG BIAS"
    A1_SHORT_BIAS = "A1 SHORT BIAS"
    WATCHLIST = "WATCHLIST"
    NO_TRADE = "NO TRADE"
    CAUTION = "CAUTION"


class InvestmentHorizon(str, Enum):
    INTRADAY = "Intraday"
    SWING = "Swing"
    POSITIONAL = "Positional"
    LONG_TERM = "Long Term"


class DataFreshness(str, Enum):
    LIVE = "LIVE"
    TODAY = "TODAY"
    THIS_WEEK = "THIS WEEK"
    RECENT = "RECENT"
    LAST_QUARTER = "LAST QUARTER"
    ANNUAL = "ANNUAL"
    HISTORICAL = "HISTORICAL"
    STALE = "STALE"


class ClaimType(str, Enum):
    """Fact vs Opinion (spec section 43): every claim the engines emit is tagged with one of
    these so the frontend never presents a computed estimate as if it were a filed fact.
    """

    FACT = "FACT"
    INTERPRETATION = "INTERPRETATION"
    ESTIMATE = "ESTIMATE"
    RISK = "RISK"


def grade_from_score(score: int) -> SignalGrade:
    if score >= 90:
        return SignalGrade.A1
    if score >= 80:
        return SignalGrade.HIGH_QUALITY
    if score >= 70:
        return SignalGrade.VALID
    if score >= 60:
        return SignalGrade.WEAK
    return SignalGrade.NO_TRADE


class InstrumentKind(str, Enum):
    """What a deployment trades when its strategy signals on the underlying (Phase F2):
    the underlying itself (cash equity - the original behaviour), an option on it, or its
    future. Indices can only be traded through OPTION or FUTURE."""

    UNDERLYING = "UNDERLYING"
    OPTION = "OPTION"
    FUTURE = "FUTURE"


class OptionPosition(str, Enum):
    """BUY: LONG signal buys a CE, SHORT buys a PE (loss capped at the premium).
    WRITE: LONG signal sells a PE, SHORT sells a CE (premium received, margin blocked, loss open-
    ended until the underlying-level stop or the premium ceiling exits)."""

    BUY = "BUY"
    WRITE = "WRITE"


class ExpiryRule(str, Enum):
    NEAREST = "NEAREST"   # first expiry on/after today (weekly where the underlying has weeklies)
    NEXT = "NEXT"         # the one after that
    MONTHLY = "MONTHLY"   # the last expiry of the nearest month that has one


class OptionStrategy(str, Enum):
    """Phase H2: what an OPTION deployment builds at signal time. SINGLE is the Phase F single
    leg; the spreads are defined-risk structures sold in the signal's direction (bull put on
    LONG, bear call on SHORT) and the iron condor sells both sides on either signal."""
    SINGLE = "SINGLE"
    BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
    BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"
    IRON_CONDOR = "IRON_CONDOR"


class StrikeRule(str, Enum):
    ATM = "ATM"
    ITM = "ITM"   # `strike_offset` steps in the money
    OTM = "OTM"   # `strike_offset` steps out of the money
