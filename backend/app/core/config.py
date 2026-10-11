import logging
import os
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

# "production" enables the startup checks in validate_production_config() below - anything else
# (the default) is treated as local/dev and skips them so the app still runs with no .env at all.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
# P0.1: environments that must boot with a hardened configuration and never create tables outside Alembic.
HARDENED_ENVIRONMENTS = ("production", "staging")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
# P0.3 / S14: access tokens carry iss/aud and a key id; JWT_PREVIOUS_SECRET_KEYS (comma-separated) keeps tokens
# signed with an older secret verifiable through a rotation. JWT_ACCEPT_LEGACY=true (default) also accepts tokens
# minted before P0.3 (no iss/aud) until they expire - minutes for access tokens - so a deploy logs nobody out.
JWT_ISSUER = os.environ.get("JWT_ISSUER", "atp")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "atp-web")
JWT_PREVIOUS_SECRET_KEYS = [k.strip() for k in os.environ.get("JWT_PREVIOUS_SECRET_KEYS", "").split(",") if k.strip()]
JWT_ACCEPT_LEGACY = os.environ.get("JWT_ACCEPT_LEGACY", "true").strip().lower() in {"1", "true", "yes"}
# P0.3 / S6: the refresh token travels in an HttpOnly cookie (path /api, SameSite=Strict, Secure in hardened
# environments). REFRESH_TOKEN_IN_BODY=true (default this release) also returns it in the JSON for clients
# built before P0.3; switch it off once every browser has reloaded the new UI.
REFRESH_COOKIE_NAME = os.environ.get("REFRESH_COOKIE_NAME", "atp_refresh")
REFRESH_TOKEN_IN_BODY = os.environ.get("REFRESH_TOKEN_IN_BODY", "true").strip().lower() in {"1", "true", "yes"}
# P0.2 / S1: login protection. After LOGIN_DELAY_AFTER_FAILURES failed attempts on one email the next attempt
# must wait 2^(n-3) seconds (capped) since the last failure - a brute force slows to a crawl while the real
# owner is never locked out by someone spamming their email. A CAPTCHA (Cloudflare Turnstile / hCaptcha) can be
# demanded after LOGIN_CAPTCHA_AFTER_FAILURES failures when CAPTCHA_PROVIDER and CAPTCHA_SECRET are set.
LOGIN_DELAY_AFTER_FAILURES = int(os.environ.get("LOGIN_DELAY_AFTER_FAILURES", "3"))
LOGIN_DELAY_MAX_SECONDS = int(os.environ.get("LOGIN_DELAY_MAX_SECONDS", "60"))
LOGIN_CAPTCHA_AFTER_FAILURES = int(os.environ.get("LOGIN_CAPTCHA_AFTER_FAILURES", "0"))      # 0 = never
CAPTCHA_PROVIDER = os.environ.get("CAPTCHA_PROVIDER", "").strip().lower()                   # "", turnstile, hcaptcha
CAPTCHA_SECRET = os.environ.get("CAPTCHA_SECRET", "")
# P0.2 / S1: the request limiter counts in Redis (shared across API replicas) in hardened environments or when
# RATE_LIMIT_BACKEND=redis; otherwise in-process (one replica, as in dev/test).
RATE_LIMIT_BACKEND = os.environ.get("RATE_LIMIT_BACKEND", "").strip().lower()
# P0.2 / S5: outbound HTTP from alert channels (webhooks, SMS gateways, push services). Private, loopback,
# link-local and reserved destinations are refused after DNS resolution in hardened environments; an optional
# comma-separated allowlist (exact host or ".suffix") narrows it further.
EGRESS_ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get("EGRESS_ALLOWED_HOSTS", "").split(",") if h.strip()]
# P0.2 / S9: a refresh token presented again within this many seconds of its rotation is honoured (two tabs
# refreshing at once); after that, reuse revokes the session as before.
REFRESH_REUSE_GRACE_SECONDS = int(os.environ.get("REFRESH_REUSE_GRACE_SECONDS", "30"))
# P0.1 / S3: the largest request body the API accepts (candle arrays for backtests and scans are the
# big ones; 8 MB is ~100k bars). Enforced from Content-Length before the body is read.
MAX_REQUEST_BODY_BYTES = int(os.environ.get("MAX_REQUEST_BODY_BYTES", str(8 * 1024 * 1024)))

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://atp_user:atp_dev_password@localhost:5432/advance_trading_platform"
)
# Phase O1 / section 48: least privilege. The API and worker run as a DML-only role (DATABASE_URL);
# schema migrations run as the owning role through this URL (scripts/db_roles.sql creates both).
# Unset = same URL as the app, which is how a single-role dev database keeps working.
MIGRATION_DATABASE_URL = os.environ.get("MIGRATION_DATABASE_URL", "").strip() or DATABASE_URL

_INSECURE_DEFAULT_JWT_SECRET = "dev-only-insecure-secret-change-me"
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", _INSECURE_DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
# Access tokens are short-lived on purpose (Phase C1): a stolen one is useful for minutes, and
# revocation (logout, removed member, password change) is checked against the session row on
# every request anyway. The browser silently refreshes with the long-lived, rotating refresh token.
JWT_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.environ.get("REFRESH_TOKEN_DAYS", "30"))

# Fernet key for encrypting broker credentials at rest. Must be set via env in any real
# deployment - a process-local fallback is generated here only so the app still runs for local
# development, but anything encrypted with it becomes unreadable across restarts.
SECRETS_ENCRYPTION_KEY = os.environ.get("SECRETS_ENCRYPTION_KEY")
# P0.4 / S11: format new ciphertexts are written in. "fernet" (default this release) = the per-tenant Fernet
# format every image since Phase N reads, so an image rollback never strands a secret; "aesgcm" = AES-256-GCM
# under a key derived from the tenant data key, bound to the tenant and the column's purpose (AAD). Both are
# always *read*; `scripts/reencrypt_secrets.py reencrypt` moves existing rows to the configured format.
SECRETS_WRITE_FORMAT = os.environ.get("SECRETS_WRITE_FORMAT", "fernet").strip().lower() or "fernet"

# Optional: caches short-lived, pure-computation results (option chain analysis, S/R zones).
# The app runs fine without Redis reachable - every cache call is wrapped to fail open.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _ratio(name: str, default: float) -> float:
    """A fraction in (0, 1]; anything else (typo, 70 instead of 0.70) falls back to the default."""
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if 0 < value <= 1 else default


# H-1 (OPEN_QUESTIONS): Redis runs with a memory cap and `volatile-lru` - at the cap it evicts keys that carry a TTL,
# and the worker's replica lock is one of them. The worker warns the operators at this share of `maxmemory`
# (checked every REDIS_MEMORY_CHECK_SECONDS) and a lost lock makes it fail closed (app/workers/redis_guard.py).
REDIS_MEMORY_WARN_RATIO = _ratio("REDIS_MEMORY_WARN_RATIO", 0.70)
REDIS_MEMORY_CHECK_SECONDS = max(30, int(float(os.environ.get("REDIS_MEMORY_CHECK_SECONDS", "300") or 300)))
# Every cache entry is short-lived: cache_set refuses a TTL longer than this (an hour), so a cache can never crowd
# out the lock under memory pressure with keys that outlive their use.
CACHE_MAX_TTL_SECONDS = 3600

# Comma-separated list of allowed frontend origins for CORS, e.g. "https://app.example.com".
# Defaults to "*" (any origin) so the dev server and API docs "try it out" work with zero
# config - see validate_production_config(), which refuses to start with this default set in
# ENVIRONMENT=production.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Where a browser is sent back to after a broker OAuth login round-trip (Upstox). Defaults to
# "/" - a same-origin redirect, correct for the docker-compose setup where nginx serves the UI
# and proxies /api. For the split dev setup (Vite on :5173, API on :8000) set it to the Vite URL.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "/")

# Comma-separated emails that are platform administrators (SUPER_ADMIN). Promoted at startup and
# on registration; never demoted automatically. Keep this to the people who operate the platform.
SUPER_ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("SUPER_ADMIN_EMAILS", "").split(",") if e.strip()}

# Phase D1: refuse LIVE orders for tenants that have not entered their exchange-issued algo id
# (SEBI retail-algo framework). Off by default so a PAPER-only or pre-registration deployment
# keeps working; turn on once live trading is offered to customers.
# H-C1 a: AI approval evidence (draft backtests, interview options) must run on server-fetched candles; client-posted
# candles are recorded as "sample" and cannot approve or deploy. On by default (a safety rule, not a feature).
AI_EVIDENCE_SERVER_ONLY = os.environ.get("AI_EVIDENCE_SERVER_ONLY", "true").lower() in ("1", "true", "yes")
# H-C1 b: AI rate limit (app/ai/rate_limit.py). Units per window for each plan, per user and per organisation; a call
# costs 1 unit unless AI_RATE_WEIGHTS names its route ("METHOD path-after-/api/ai/"). Both are JSON objects in the
# environment; an empty or invalid value keeps the defaults below. 0 = no limit for that scope.
AI_RATE_WINDOW_SECONDS = max(1, int(os.environ.get("AI_RATE_WINDOW_SECONDS", "60") or 60))
_AI_RATE_LIMITS_DEFAULT = {"free": {"user": 60, "tenant": 120}, "pro": {"user": 180, "tenant": 600}, "business": {"user": 360, "tenant": 3000}}
_AI_RATE_WEIGHTS_DEFAULT = {"POST strategist/build": 20, "POST strategist/study": 10, "POST interview/plan": 10, "POST interview/refine": 10,
                            "POST drafts": 5, "POST drafts/{draft_id}/backtest": 5, "POST copilot": 3, "POST ask": 3,
                            "POST market-memory/refresh": 10, "POST plan": 3, "POST read": 3}


def _json_table(name: str, default: dict) -> dict:
    import json
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except ValueError:
        return default
    return value if isinstance(value, dict) else default


AI_RATE_LIMITS = _json_table("AI_RATE_LIMITS", _AI_RATE_LIMITS_DEFAULT)
AI_RATE_WEIGHTS = _json_table("AI_RATE_WEIGHTS", _AI_RATE_WEIGHTS_DEFAULT)
ALGO_ID_REQUIRED_FOR_LIVE = os.environ.get("ALGO_ID_REQUIRED_FOR_LIVE", "false").lower() in ("1", "true", "yes")
# Part D2 (rule IN-SEBI.ops.throttle): orders per second per client and exchange, exits first. Off by default; the
# rate is the rule-set's `ops_per_second` unless OPS_PER_SECOND overrides it for this deployment.
OPS_THROTTLE_ENABLED = os.environ.get("OPS_THROTTLE_ENABLED", "false").lower() in ("1", "true", "yes")
OPS_PER_SECOND = float(os.environ.get("OPS_PER_SECOND", "0") or 0) or None
# Part D4 (rule IN-SEBI.static_ip.registered): the public IP this server's API orders leave from (deploy/hostinger/status.sh
# prints it as "egress IP"), and whether a LIVE entry is refused unless that IP is registered for the deployment's broker.
SERVER_EGRESS_IP = os.environ.get("SERVER_EGRESS_IP", "").strip() or None
STATIC_IP_REQUIRED_FOR_LIVE = os.environ.get("STATIC_IP_REQUIRED_FOR_LIVE", "false").lower() in ("1", "true", "yes")

# Phase E1: observability. METRICS_TOKEN protects GET /metrics on the API (empty = open, fine
# behind a private network); WORKER_METRICS_PORT serves the worker's own metrics (0 = off).
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")
WORKER_METRICS_PORT = int(os.environ.get("WORKER_METRICS_PORT", "9102"))

# Phase F1: which Upstox public instrument masters the worker syncs daily (source exchanges;
# NSE carries NSE_EQ/NSE_INDEX/NSE_FO, BSE carries SENSEX/BANKEX derivatives). Empty disables.
INSTRUMENT_SYNC_EXCHANGES = [e.strip().upper() for e in os.environ.get("INSTRUMENT_SYNC_EXCHANGES", "NSE").split(",") if e.strip()]
INSTRUMENT_SYNC_HOUR_IST = int(os.environ.get("INSTRUMENT_SYNC_HOUR_IST", "8"))

# Phase G1: market-data staleness gate (safety rule 7). No signal is evaluated when the newest
# base candle is more than MARKET_DATA_MAX_STALE_BARS bars behind the clock, and no exit decision
# is taken on a broker quote whose exchange timestamp is older than QUOTE_MAX_STALE_SECONDS.
# 0 disables either check (not recommended outside tests).
MARKET_DATA_MAX_STALE_BARS = int(os.environ.get("MARKET_DATA_MAX_STALE_BARS", "3"))
QUOTE_MAX_STALE_SECONDS = int(os.environ.get("QUOTE_MAX_STALE_SECONDS", "120"))

# Phase S: streaming quotes. Off by default: the worker polls REST as before. On, the worker keeps
# one websocket per broker session (Upstox Market Data Feed V3, Kite ticker) subscribed to the
# symbols its deployments and open positions need; get_ltp uses a tick younger than
# TICK_MAX_AGE_SECONDS (default: the quote staleness limit) and falls back to REST otherwise.
STREAMING_QUOTES_ENABLED = os.environ.get("STREAMING_QUOTES_ENABLED", "false").lower() in ("1", "true", "yes")
TICK_MAX_AGE_SECONDS = int(os.environ.get("TICK_MAX_AGE_SECONDS", str(QUOTE_MAX_STALE_SECONDS)))

# Phase G2: per-broker circuit breaker on call health (distinct from the kill switch). When more
# than BROKER_CIRCUIT_FAILURE_RATIO of the broker calls in the last BROKER_CIRCUIT_WINDOW_SECONDS
# failed (timeouts, 5xx, 429 - not business 4xx), after at least BROKER_CIRCUIT_MIN_CALLS, new
# LIVE entries to that broker are refused platform-wide for BROKER_CIRCUIT_OPEN_SECONDS, then one
# probe entry is allowed. Exits are never refused.
BROKER_CIRCUIT_WINDOW_SECONDS = float(os.environ.get("BROKER_CIRCUIT_WINDOW_SECONDS", "60"))
BROKER_CIRCUIT_MIN_CALLS = int(os.environ.get("BROKER_CIRCUIT_MIN_CALLS", "5"))
BROKER_CIRCUIT_FAILURE_RATIO = float(os.environ.get("BROKER_CIRCUIT_FAILURE_RATIO", "0.5"))
BROKER_CIRCUIT_OPEN_SECONDS = float(os.environ.get("BROKER_CIRCUIT_OPEN_SECONDS", "120"))

# Autonomous trading worker (app/workers/trading_worker.py) cadence in seconds. 60 matches the
# 1-minute base candle; anything shorter mostly re-reads the same cached candles.
WORKER_CYCLE_SECONDS = int(os.environ.get("WORKER_CYCLE_SECONDS", "60"))

# The risk-free rate used to discount option payoffs in the Black-Scholes Greeks engine
# (app/option_chain/greeks.py) - approximates the short-term Indian G-Sec/repo yield. Configurable
# since the "right" rate drifts with the rate cycle and reasonable people disagree on which
# tenor to use; the default is a reasonable long-run approximation, not a live rate feed.
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.07"))


def tables_created_at_startup(environment: str) -> bool:
    """P0.1 / S12: `create_all` at boot is a dev/test convenience only. Production and staging get their
    schema from Alembic alone (the Dockerfile runs the migration guard before uvicorn), so a model that
    is ahead of its migration surfaces as a failing `alembic check`, never as a silently created table."""
    return environment not in HARDENED_ENVIRONMENTS


def config_problems(*, environment: str, jwt_secret: str, secrets_key: Optional[str], allowed_origins: List[str], metrics_token: str,
                    email_verification_required: bool, smtp_host: str) -> List[str]:
    """The insecure-configuration findings for `environment` (empty outside the hardened ones)."""
    if environment not in HARDENED_ENVIRONMENTS:
        return []
    problems = []
    if jwt_secret == _INSECURE_DEFAULT_JWT_SECRET:
        problems.append("JWT_SECRET_KEY is still the insecure default - set a real secret (see .env.example).")
    if not secrets_key:
        problems.append(
            "SECRETS_ENCRYPTION_KEY is not set - broker credentials would be encrypted under a "
            "fixed, publicly-known dev-only key."
        )
    if allowed_origins == ["*"]:
        problems.append("ALLOWED_ORIGINS is \"*\" - set it to your real frontend origin(s).")
    if not metrics_token:
        problems.append("METRICS_TOKEN is not set - GET /metrics (counts of orders, logins, tenants) would be public.")
    if email_verification_required and not smtp_host:
        problems.append("EMAIL_VERIFICATION_REQUIRED is on but PLATFORM_SMTP_HOST is unset - nobody could ever verify.")
    return problems


def validate_production_config() -> None:
    """Fails fast at startup rather than silently serving traffic with a known-insecure
    configuration. A very common way real deployments get compromised is a dev-only default
    secret nobody rotated before going live - refusing to boot is cheaper than that incident.
    P0.1 / S4: staging is held to the same checks as production.
    """
    problems = config_problems(
        environment=ENVIRONMENT, jwt_secret=JWT_SECRET_KEY, secrets_key=SECRETS_ENCRYPTION_KEY, allowed_origins=ALLOWED_ORIGINS,
        metrics_token=METRICS_TOKEN,
        email_verification_required=os.environ.get("EMAIL_VERIFICATION_REQUIRED", "").strip().lower() in {"1", "true", "yes"},
        smtp_host=os.environ.get("PLATFORM_SMTP_HOST", "").strip(),
    )
    if problems:
        raise RuntimeError(
            f"Refusing to start with ENVIRONMENT={ENVIRONMENT} and insecure configuration:\n- " + "\n- ".join(problems)
        )

# --- Platform mailer + email verification (Phase N3) --------------------------------------------
# The platform's own SMTP account, used for account emails (verification links, and password
# resets when the tenant has no EMAIL alert channel yet). Unset = links are logged, not sent.
PLATFORM_SMTP_HOST = os.environ.get("PLATFORM_SMTP_HOST", "").strip()
PLATFORM_SMTP_PORT = int(os.environ.get("PLATFORM_SMTP_PORT", "587"))
PLATFORM_SMTP_USERNAME = os.environ.get("PLATFORM_SMTP_USERNAME", "")
PLATFORM_SMTP_PASSWORD = os.environ.get("PLATFORM_SMTP_PASSWORD", "")
PLATFORM_SMTP_FROM = os.environ.get("PLATFORM_SMTP_FROM", "")
PLATFORM_SMTP_STARTTLS = os.environ.get("PLATFORM_SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no"}
# When on, LIVE deployments and broker credential storage require a verified email address.
EMAIL_VERIFICATION_REQUIRED = os.environ.get("EMAIL_VERIFICATION_REQUIRED", "false").strip().lower() in {"1", "true", "yes"}

# --- Web Push (Phase O3) --------------------------------------------------------------------------
# VAPID key pair identifying this server to browser push services. Generate once with
# `python -m app.alerts.webpush` and keep it stable: rotating it invalidates every subscription.
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip() or None
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:ops@example.com").strip()

# --- Billing gateway (Phase K1b) ----------------------------------------------------------
# "manual" = the operator records bank/UPI payments; "razorpay" = Razorpay Subscriptions with
# hosted checkout and webhooks. These are the operator's own gateway secrets (platform-level,
# like JWT_SECRET_KEY) - never a tenant's. Missing keys fall back to manual with a warning.
BILLING_PROVIDER = os.environ.get("BILLING_PROVIDER", "manual").strip().lower()
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_BASE_URL = os.environ.get("RAZORPAY_BASE_URL", "https://api.razorpay.com/v1")

# Trade port (order_safety): exchange market protection on MARKET / SL-M orders (SEBI retail-algo framework, April
# 2026; Upstox `market_protection`). Unset = the field is not sent (today's behaviour; Upstox then applies its own
# automatic protection). 1-25 = that percentage on every Upstox MARKET / SL-M order (entries, stops, exits).
ORDER_MARKET_PROTECTION_PCT = os.environ.get("ORDER_MARKET_PROTECTION_PCT", "").strip() or None
# G-LIVE (all default off = today's orders, unchanged until the operator turns them on):
# - every MARKET / SL-M order to Zerodha and Upstox carries `market_protection`: ORDER_MARKET_PROTECTION_PCT when set
#   (1-25), else -1 = the broker's automatic band. Kite rejects an API market order whose protection is 0 / absent.
LIVE_MARKET_PROTECTION = os.environ.get("LIVE_MARKET_PROTECTION", "false").lower() in ("1", "true", "yes")
# - Upstox protective stops on option contracts go as SL (stop-limit) instead of SL-M (the exchanges stopped SL-M on
#   index options); the limit sits STOP_LIMIT_BAND_PCT past the trigger.
LIVE_UPSTOX_OPTION_STOP_LIMIT = os.environ.get("LIVE_UPSTOX_OPTION_STOP_LIMIT", "false").lower() in ("1", "true", "yes")
# - a LIVE position whose broker-side stop the broker clearly rejected (at entry, or on a stop-guard re-arm) is closed
#   at once - never after a timeout / 5xx, while broker-uncertain or with the market shut; at most 3 tries.
LIVE_EXIT_IF_NO_STOP = os.environ.get("LIVE_EXIT_IF_NO_STOP", "false").lower() in ("1", "true", "yes")
# Stop guard: after this many re-armed stops in a row that the broker accepted and then REJECTED, the guard stops
# re-arming that position (it would loop forever) - with LIVE_EXIT_IF_NO_STOP the position is closed, else a CRITICAL.
def _positive_int(raw: str, default: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


STOP_REARM_MAX_REJECTS = _positive_int(os.environ.get("STOP_REARM_MAX_REJECTS", "3"), 3)
# How far past the trigger a stop-limit's limit sits (% of the trigger; Zerodha options today, Upstox options with
# the flag above). Unset = today's stop-limit (1%, nearest tick). Set = that band, rounded outward to the 0.05 tick
# and always at least one tick past the trigger. An unreadable value is ignored (today's behaviour) with a warning.
def _band_pct(raw: str) -> Optional[float]:
    if not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if 0 <= value <= 20:
        return value
    logging.getLogger(__name__).warning("STOP_LIMIT_BAND_PCT=%r is not a percentage between 0 and 20 - ignored", raw)
    return None


STOP_LIMIT_BAND_PCT: Optional[float] = _band_pct(os.environ.get("STOP_LIMIT_BAND_PCT", ""))
# Multi-leg LIVE entries: a short leg is sent only after its wings filled IN FULL. Off = today's behaviour (any
# confirmed wing fill lets the shorts go at the full quantity). Default off while LIVE changes are gated (G-LIVE).
LIVE_STRICT_WING_FILL = os.environ.get("LIVE_STRICT_WING_FILL", "false").lower() in ("1", "true", "yes")

# S1c (ADR-0021): which engine runs POST /api/scanner/run - "legacy" (app/scanner/engine.py) or "screenql" (the same
# filters as one ScreenQL screen, parity-tested). Default legacy until the owner switches it; the response is identical.
SCANNER_ENGINE = os.environ.get("SCANNER_ENGINE", "legacy").strip().lower()
# S1d: the per-tenant ScreenQL cost cap (validator units; a screen over it is refused with the reason).
SCREENER_COST_CAP = float(os.environ.get("SCREENER_COST_CAP", "200"))

def _json_object(name: str) -> dict:
    """An env var holding a JSON object; anything else is ignored with a warning."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        import json
        value = json.loads(raw)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value
    logging.getLogger(__name__).warning("%s is not a JSON object - ignored", name)
    return {}


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if low <= value <= high else default


# OI Banner (O2): the collector's slot and the strikes kept either side of the money (wider than any banner window,
# so each tenant's own ATM range applies on read), and the operator's default OIRegimeSettings fields (JSON).
OI_BANNER_SLOT_MINUTES = _bounded_int("OI_BANNER_SLOT_MINUTES", 5, 1, 60)
OI_BANNER_COLLECT_SPAN = _bounded_int("OI_BANNER_COLLECT_SPAN", 15, 1, 60)
OI_BANNER_DEFAULTS: dict = _json_object("OI_BANNER_DEFAULTS")
