import os

from dotenv import load_dotenv

load_dotenv()

# "production" enables the startup checks in validate_production_config() below - anything else
# (the default) is treated as local/dev and skips them so the app still runs with no .env at all.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://atp_user:atp_dev_password@localhost:5432/advance_trading_platform"
)

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

# Optional: caches short-lived, pure-computation results (option chain analysis, S/R zones).
# The app runs fine without Redis reachable - every cache call is wrapped to fail open.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

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
ALGO_ID_REQUIRED_FOR_LIVE = os.environ.get("ALGO_ID_REQUIRED_FOR_LIVE", "false").lower() in ("1", "true", "yes")

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


def validate_production_config() -> None:
    """Fails fast at startup rather than silently serving traffic with a known-insecure
    configuration. A very common way real deployments get compromised is a dev-only default
    secret nobody rotated before going live - refusing to boot is cheaper than that incident.
    """
    if ENVIRONMENT != "production":
        return

    problems = []
    if JWT_SECRET_KEY == _INSECURE_DEFAULT_JWT_SECRET:
        problems.append("JWT_SECRET_KEY is still the insecure default - set a real secret (see .env.example).")
    if not SECRETS_ENCRYPTION_KEY:
        problems.append(
            "SECRETS_ENCRYPTION_KEY is not set - broker credentials would be encrypted under a "
            "fixed, publicly-known dev-only key."
        )
    if ALLOWED_ORIGINS == ["*"]:
        problems.append("ALLOWED_ORIGINS is \"*\" - set it to your real frontend origin(s) in production.")

    if problems:
        raise RuntimeError(
            "Refusing to start with ENVIRONMENT=production and insecure configuration:\n- " + "\n- ".join(problems)
        )
