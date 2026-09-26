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
JWT_EXPIRE_MINUTES = 60 * 24

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
