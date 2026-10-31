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


def api_docs_enabled(environment: str = ENVIRONMENT) -> bool:
    """AUD-G1.3 (owner audit 28 Oct): the interactive API docs (/docs, /redoc, /openapi.json) exist in development only;
    production and staging serve none of them (the edge already routes those paths to the web app)."""
    return environment not in HARDENED_ENVIRONMENTS
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
# LD-7: the connection pool of ONE process (an API worker process, the trading worker, the research worker). With
# API_WORKERS > 1 every API process has its own pool, so the database sees API_WORKERS x (size + overflow) from the API
# alone - keep that, plus the workers' pools, under Postgres `max_connections` (the Hostinger overlay does the sum).
DB_POOL_SIZE = max(1, int(os.environ.get("DB_POOL_SIZE", "5")))
DB_MAX_OVERFLOW = max(0, int(os.environ.get("DB_MAX_OVERFLOW", "10")))
DB_POOL_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("DB_POOL_TIMEOUT_SECONDS", "30")))
# LD-7: how many API processes uvicorn runs (scripts/serve_api.sh reads the same variable). Sizes per-process limits
# that are meant to be per host, such as the broker API budget (app/market_data/api_budget.py); not the bcrypt threads
# (LD-9: those follow the CPUs, app/auth/security.py).
API_WORKERS = max(1, int(os.environ.get("API_WORKERS", "1") or "1"))

_INSECURE_DEFAULT_JWT_SECRET = "dev-only-insecure-secret-change-me"
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", _INSECURE_DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
# Access tokens are short-lived on purpose (Phase C1): a stolen one is useful for minutes, and
# revocation (logout, removed member, password change) is checked against the session row on
# every request anyway. The browser silently refreshes with the long-lived, rotating refresh token.
JWT_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES", "15"))
# MO3-A1: how long an admin's read-only "View as user" session lasts (minutes, kept between 5 and 60).
VIEW_AS_MINUTES = max(5, min(60, int(os.environ.get("VIEW_AS_MINUTES", "30") or "30")))
# MO3-A1: how recent the admin's two-factor check must be to start a view (minutes, 1-60).
VIEW_AS_MFA_FRESH_MINUTES = max(1, min(60, int(os.environ.get("VIEW_AS_MFA_FRESH_MINUTES", "15") or "15")))
REFRESH_TOKEN_DAYS = int(os.environ.get("REFRESH_TOKEN_DAYS", "30"))


def _env_int_in(name: str, default: int, low: int, high: int) -> int:
    """An integer setting kept inside [low, high]; anything else (unset, not a number, out of range) is the default."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if low <= value <= high else default


# A4 (review R5 c, RDY-28): each refresh slides a session another REFRESH_TOKEN_DAYS, so a device that keeps opening the
# app would stay signed in for ever. SESSION_MAX_DAYS is the absolute cap counted from the sign-in itself: after it the
# person signs in again (and enters the authenticator code again, when two-factor is on), however active the device was.
SESSION_MAX_DAYS = _env_int_in("SESSION_MAX_DAYS", 90, 1, 3650)
# A4 (review R5 c, RDY-29): how recent the two-factor check on this session must be for a SUPER_ADMIN write (every route
# behind `require_mfa_session` that is not a GET). Admin reads need a checked session, not a recent one, so the console
# stays usable; a write after the window asks for the authenticator code once more and then carries on. The LIVE step-up
# (`ensure_live_step_up`) is unchanged: PAPER only in Phase 0, decided with LIVE.
MFA_STEP_UP_MINUTES = _env_int_in("MFA_STEP_UP_MINUTES", 30, 1, 24 * 60)
# M55 S1 (owner order): the one account that may switch LIVE on - turn `live_trading` on or the `phase0_paper_only` lock
# off (app/platform/live_owner.py), with a recent two-factor check and a typed confirmation. Any other SUPER_ADMIN gets
# a refusal. A user id; unset or not a positive number: 1.
LIVE_OWNER_USER_ID = _env_int_in("LIVE_OWNER_USER_ID", 1, 1, 2**31 - 1)
# AUD-G2.4 (activation / time to first value): the "first session" of a new person = the first
# ACTIVATION_FIRST_SESSION_HOURS hours after signing up. The admin activation funnel shows the share of people whose first
# paper trade came inside that window (target: first paper trade within the first session). Measurement only.
ACTIVATION_FIRST_SESSION_HOURS = _env_int_in("ACTIVATION_FIRST_SESSION_HOURS", 3, 1, 72)
# CP-UX CU6 (owner order 30 Oct): how many turns of a person's Copilot conversation the server keeps so it is still there
# after a reload (the newest ones; older turns are deleted as new ones are written). The page shows the same number.
COPILOT_CHAT_KEEP = _env_int_in("COPILOT_CHAT_KEEP", 20, 1, 200)

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
# Part D7: refuse new F&O entries on an underlying in the exchange's ban period (lake MWPL / OI data). Off by default.
FO_BAN_CHECK_ENABLED = os.environ.get("FO_BAN_CHECK_ENABLED", "false").lower() in ("1", "true", "yes")
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
                            "POST market-memory/refresh": 10, "POST plan": 3, "POST read": 3,
                            "POST support/ask": 1, "POST analyze": 8, "POST chat": 4,
                            "POST /api/option-chain/ask": 2}   # OC3: Ask AI may make a repair call (two AI calls)


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


def _byok_model(provider: str, default: str) -> str:
    return (os.environ.get(f"BYOK_{provider.upper()}_VISION_MODEL", "") or default).strip()


def _byok_choices(provider: str, default: str, extra: str) -> list:
    """The models a user may pick for one provider: the default first, then BYOK_<PROVIDER>_VISION_MODELS (comma list)."""
    raw = os.environ.get(f"BYOK_{provider.upper()}_VISION_MODELS", "").strip() or extra
    out = [_byok_model(provider, default)]
    for name in raw.split(","):
        name = name.strip()
        if name and name not in out:
            out.append(name)
    return out


# BYOK-V1 (docs/BYOK.md, flag `vision_byok`, off by default): "Vision with your own AI key". Per provider, the
# cost-efficient vision-capable model a user's key runs on unless they pick another from the choices. The user pays the
# provider directly; these are defaults only and every one can be overridden per deployment from the environment.
BYOK_VISION_DEFAULT_MODELS = {
    "anthropic": _byok_model("anthropic", "claude-haiku-5-5"),
    "openai": _byok_model("openai", "gpt-4.1-mini"),
    "gemini": _byok_model("gemini", "gemini-2.5-flash"),
}
BYOK_VISION_MODEL_CHOICES = {
    "anthropic": _byok_choices("anthropic", "claude-haiku-5-5", "claude-sonnet-5-5"),
    "openai": _byok_choices("openai", "gpt-4.1-mini", "gpt-4o-mini,gpt-4.1"),
    "gemini": _byok_choices("gemini", "gemini-2.5-flash", "gemini-2.5-flash-lite,gemini-2.5-pro"),
}
# A new user's spend caps (USD, user-editable) and the highest cap a user may set.
BYOK_DEFAULT_DAILY_CAP_USD = max(0.0, float(os.environ.get("BYOK_DEFAULT_DAILY_CAP_USD", "1") or 1))
BYOK_DEFAULT_MONTHLY_CAP_USD = max(0.0, float(os.environ.get("BYOK_DEFAULT_MONTHLY_CAP_USD", "10") or 10))
BYOK_MAX_CAP_USD = max(1.0, float(os.environ.get("BYOK_MAX_CAP_USD", "500") or 500))
# BYOK-V3: scan-alert audits per user per rolling hour (a noisy screen cannot burn the user's cap), and the daily
# review - after the close (IST, HH:MM) on NSE trading days, at most this many symbols per user, on this timeframe.
BYOK_SCAN_AUDITS_PER_HOUR = max(0, int(os.environ.get("BYOK_SCAN_AUDITS_PER_HOUR", "6") or 6))
BYOK_DAILY_REVIEW_AT = (os.environ.get("BYOK_DAILY_REVIEW_AT", "16:00") or "16:00").strip()
BYOK_DAILY_REVIEW_MAX_SYMBOLS = min(10, max(1, int(os.environ.get("BYOK_DAILY_REVIEW_MAX_SYMBOLS", "3") or 3)))
BYOK_DAILY_REVIEW_TIMEFRAME = (os.environ.get("BYOK_DAILY_REVIEW_TIMEFRAME", "1d") or "1d").strip()
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
# Part B2: streamed ticks -> 1-minute bars in the market data lake (md_candles, source stream:<broker>). Needs the stream.
LAKE_TICK_WRITER_ENABLED = os.environ.get("LAKE_TICK_WRITER_ENABLED", "false").lower() in ("1", "true", "yes")
# Part B3: data-quality thresholds for lake bars (events only - nothing is deleted or blocked).
LAKE_SPIKE_PCT = float(os.environ.get("LAKE_SPIKE_PCT", "10"))
LAKE_LATE_SECONDS = float(os.environ.get("LAKE_LATE_SECONDS", "300"))
LAKE_MISMATCH_PCT = float(os.environ.get("LAKE_MISMATCH_PCT", "0.5"))
# Part B4: also adjust prices for dividends in the adjusted view (splits and bonuses always are).
LAKE_ADJUST_DIVIDENDS = os.environ.get("LAKE_ADJUST_DIVIDENDS", "false").lower() in ("1", "true", "yes")
# Owner order 23 Oct (app/backtest/bar_source.py): the shared lake's Daily sources a backtest without a broker may read,
# as source prefixes. Default: the NSE daily files only (RDY-6: licensed EOD only, never the platform's or another user's
# broker feed). "r2:" adds the R2 daily import once its licence position allows it (OPEN_QUESTIONS BSH-2).
BACKTEST_STORED_SOURCES = tuple(p.strip() for p in os.environ.get("BACKTEST_STORED_SOURCES", "nse_bhav:").split(",") if p.strip())
# Part B5: most bars one /api/market-data/history request returns (narrow the window for more).
LAKE_HISTORY_MAX_BARS = int(os.environ.get("LAKE_HISTORY_MAX_BARS", "5000"))
# HB0: most 1-minute rows one tenant-store request may READ before resampling (checked with a count first), so a
# 60-minute request cannot pull 60 x LAKE_HISTORY_MAX_BARS rows into memory. 60,000 is about 160 sessions of 1-minute bars.
LAKE_TENANT_MAX_BASE_ROWS = int(os.environ.get("LAKE_TENANT_MAX_BASE_ROWS", "60000"))
# HB0 (docs/design/HB_HISTORY_BACKFILL.md): the tenant whose private store receives the owner's licensed 1-minute history
# from the R2 import. Unset: the importer refuses to load 1-minute data at all (daily still goes to the shared lake).
# The R2 credentials (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_ENDPOINT) are read only by the importer.
_history_owner = os.environ.get("HISTORY_OWNER_TENANT_ID", "").strip()
HISTORY_OWNER_TENANT_ID = int(_history_owner) if _history_owner.isdigit() and int(_history_owner) > 0 else None
# The confirmation: must equal that tenant's name, or the 1-minute import refuses (a mistyped id never reaches a customer).
HISTORY_OWNER_TENANT_NAME = os.environ.get("HISTORY_OWNER_TENANT_NAME", "").strip()
# HB1 (docs/design/HB_HISTORY_BACKFILL.md): the daily NSE bhavcopy backfill and nightly EOD job - both only while the
# `history_backfill` flag is on. Politeness: one request at a time, at least HISTORY_BACKFILL_PAUSE_SECONDS apart, and
# exponential backoff (honouring Retry-After) on 429 / 403 / 5xx / timeouts, HISTORY_BACKFILL_RETRIES attempts per URL.
HISTORY_BACKFILL_PAUSE_SECONDS = float(os.environ.get("HISTORY_BACKFILL_PAUSE_SECONDS", "1.5") or 1.5)
HISTORY_BACKFILL_RETRIES = max(1, int(os.environ.get("HISTORY_BACKFILL_RETRIES", "4") or 4))
HISTORY_BACKFILL_MAX_BACKOFF_SECONDS = float(os.environ.get("HISTORY_BACKFILL_MAX_BACKOFF_SECONDS", "120") or 120)
HISTORY_BACKFILL_USER_AGENT = os.environ.get("HISTORY_BACKFILL_USER_AGENT", "").strip()     # empty: a browser-like default
# Cash-market series loaded as equities (main board, trade-for-trade, SME); the first listed wins if a symbol has two.
HISTORY_BACKFILL_SERIES = tuple(s.strip().upper() for s in os.environ.get("HISTORY_BACKFILL_SERIES", "EQ,BE,BZ,SM,ST,SZ").split(",") if s.strip())
HISTORY_BACKFILL_FO_OPTIONS = os.environ.get("HISTORY_BACKFILL_FO_OPTIONS", "false").lower() in ("1", "true", "yes")
# F&O volume: "withhold" (default - stored as 0 until the legacy CONTRACTS vs UDiFF TtlTradgVol unit is confirmed on
# real files, so nothing downstream mixes units) or "as_printed". The unit is recorded per file either way.
HISTORY_BACKFILL_FO_VOLUME = os.environ.get("HISTORY_BACKFILL_FO_VOLUME", "withhold").strip().lower()
# Weekends are probed with the cash file only (a special session - budget Saturday, Muhurat - has one); index and F&O
# files are asked for only when the cash file of that day exists. "false" skips weekends entirely.
HISTORY_BACKFILL_PROBE_WEEKENDS = os.environ.get("HISTORY_BACKFILL_PROBE_WEEKENDS", "true").lower() in ("1", "true", "yes")
# A file with fewer rows than this share of the median of the last done days of its segment is refused (truncated).
HISTORY_BACKFILL_MIN_ROWS_RATIO = float(os.environ.get("HISTORY_BACKFILL_MIN_ROWS_RATIO", "0.5") or 0.5)
# Optional: keep every file as received under this directory ({segment}/{yyyy}/{name}); empty = not kept.
HISTORY_BACKFILL_ARCHIVE_DIR = os.environ.get("HISTORY_BACKFILL_ARCHIVE_DIR", "").strip()
# Nightly EOD job (trading worker): once per IST day from this hour, today's files plus a catch-up of the last N days.
HISTORY_BACKFILL_HOUR_IST = int(os.environ.get("HISTORY_BACKFILL_HOUR_IST", "19") or 19)
HISTORY_BACKFILL_CATCHUP_DAYS = int(os.environ.get("HISTORY_BACKFILL_CATCHUP_DAYS", "7") or 7)
# HB2 (docs/design/HB2_INTRADAY_HISTORY.md): the owner's 1-minute history, captured after the close and backfilled from
# the OWNER's own broker session (Tier A, read-only candle endpoints) into the owner's private store (md_tenant_candles,
# HISTORY_OWNER_TENANT_ID confirmed by HISTORY_OWNER_TENANT_NAME) - only while the `history_intraday` flag is on.
# Instruments: HISTORY_INTRADAY_SYMBOLS ("NSE:RELIANCE,NSE:NIFTY 50"; a bare symbol is NSE) plus, when
# HISTORY_INTRADAY_INCLUDE_DEPLOYMENTS, the symbols of the owner tenant's deployments (NSE-family exchanges only).
HISTORY_INTRADAY_SYMBOLS = [k.strip().upper() for k in os.environ.get("HISTORY_INTRADAY_SYMBOLS", "").split(",") if k.strip()]
HISTORY_INTRADAY_INCLUDE_DEPLOYMENTS = os.environ.get("HISTORY_INTRADAY_INCLUDE_DEPLOYMENTS", "true").lower() in ("1", "true", "yes")
# The oldest trade date the backfill asks for (the broker's 1-minute depth; older days come back empty -> no_data).
HISTORY_INTRADAY_START = os.environ.get("HISTORY_INTRADAY_START", "2022-01-03").strip() or "2022-01-03"
# Calendar days per historical-candle request (kept well under the broker's per-request range limit).
HISTORY_INTRADAY_WINDOW_DAYS = max(1, min(28, int(os.environ.get("HISTORY_INTRADAY_WINDOW_DAYS", "7") or 7)))
# The worker starts passes once per IST day from this hour (NSE closed; every bar of the day final) and repeats an
# unfinished pass at most every HISTORY_INTRADAY_RETRY_MINUTES; nothing runs on a trading day before
# HISTORY_INTRADAY_HOUR_IST or from HISTORY_INTRADAY_STOP_HOUR_IST in the morning (live reads have the API to themselves).
HISTORY_INTRADAY_HOUR_IST = int(os.environ.get("HISTORY_INTRADAY_HOUR_IST", "16") or 16)
HISTORY_INTRADAY_STOP_HOUR_IST = int(os.environ.get("HISTORY_INTRADAY_STOP_HOUR_IST", "8") or 8)
HISTORY_INTRADAY_RETRY_MINUTES = max(1, int(os.environ.get("HISTORY_INTRADAY_RETRY_MINUTES", "15") or 15))
HISTORY_INTRADAY_BUDGET_SECONDS = max(10, int(os.environ.get("HISTORY_INTRADAY_BUDGET_SECONDS", "1800") or 1800))
# Share of the owner's broker RateBudget (both buckets) a history request must leave untouched for the trading worker.
HISTORY_INTRADAY_RESERVE = min(0.95, max(0.0, float(os.environ.get("HISTORY_INTRADAY_RESERVE", "0.5") or 0.5)))
# Most HB2 broker requests in any rolling 30 minutes, counted from the database (Upstox caps a user at 2,000 standard
# API requests per 30 minutes; 600 leaves the rest to the trading worker and the owner's own apps). Resumed later.
HISTORY_INTRADAY_MAX_REQUESTS = max(1, int(os.environ.get("HISTORY_INTRADAY_MAX_REQUESTS", "600") or 600))
# The one user whose own broker session HB2 reads (must be an active user of HISTORY_OWNER_TENANT_ID). Unset: the
# lowest-id active OWNER of that tenant - never another co-owner, and no session at all if that user has none.
_intraday_user = os.environ.get("HISTORY_OWNER_USER_ID", "").strip()
HISTORY_OWNER_USER_ID = int(_intraday_user) if _intraday_user.isdigit() and int(_intraday_user) > 0 else None
# IST dates on which HB2 never calls the broker (special sessions the calendar does not know: Muhurat, a budget-day
# Saturday, a DR drill) - "2026-11-08,2027-02-01".
def _iso_dates(name: str) -> "tuple[frozenset, list]":
    """(dates, bad entries) of a comma-separated YYYY-MM-DD list; a bad entry is reported loudly, never dropped silently."""
    import datetime as _dt
    import logging as _logging
    good, bad = set(), []
    for raw in os.environ.get(name, "").split(","):
        if raw.strip():
            try:
                good.add(_dt.date.fromisoformat(raw.strip()))
            except ValueError:
                bad.append(raw.strip())
    if bad:
        _logging.getLogger(__name__).error("%s: not YYYY-MM-DD dates: %s - HB2 refuses to run until they are fixed", name, bad)
    return frozenset(good), bad


HISTORY_INTRADAY_BLACKOUT_DATES, HISTORY_INTRADAY_BLACKOUT_ERRORS = _iso_dates("HISTORY_INTRADAY_BLACKOUT_DATES")
# DATA-H (owner, 24 Oct; app/market_lake/index_history.py): the index history the chart reader and the Copilot read -
# Daily (Weekly is built from it) for LAKE_BACKFILL_DAILY_YEARS and 1-minute bars (15M / 1H are built from them) for
# LAKE_BACKFILL_INTRADAY_DAYS (at least the reader's longest intraday lookback, 1H = 180 days), read through HB2's
# owner session (HISTORY_OWNER_TENANT_ID + NAME, HISTORY_OWNER_USER_ID) into the owner's PRIVATE store only. The worker
# appends each closed session from LAKE_BACKFILL_HOUR_IST (off with LAKE_BACKFILL_AFTER_CLOSE=false).
LAKE_BACKFILL_INDICES = [k.strip().upper() for k in os.environ.get("LAKE_BACKFILL_INDICES", "NIFTY,BANKNIFTY,SENSEX,FINNIFTY").split(",")
                         if k.strip()]
LAKE_BACKFILL_DAILY_YEARS = max(1, min(25, int(os.environ.get("LAKE_BACKFILL_DAILY_YEARS", "10") or 10)))
LAKE_BACKFILL_INTRADAY_DAYS = max(45, min(730, int(os.environ.get("LAKE_BACKFILL_INTRADAY_DAYS", "200") or 200)))
LAKE_BACKFILL_AFTER_CLOSE = os.environ.get("LAKE_BACKFILL_AFTER_CLOSE", "true").lower() in ("1", "true", "yes")
LAKE_BACKFILL_HOUR_IST = max(16, min(23, int(os.environ.get("LAKE_BACKFILL_HOUR_IST", "16") or 16)))
LAKE_BACKFILL_BUDGET_SECONDS = max(10, int(os.environ.get("LAKE_BACKFILL_BUDGET_SECONDS", "900") or 900))
LAKE_BACKFILL_PAUSE_SECONDS = max(0.0, float(os.environ.get("LAKE_BACKFILL_PAUSE_SECONDS", "0.3") or 0.3))
TICK_MAX_AGE_SECONDS = int(os.environ.get("TICK_MAX_AGE_SECONDS", str(QUOTE_MAX_STALE_SECONDS)))
# M55 S2 (ADR-0004): an organisation with no usable broker session still gets its exits. Its price is then the newest
# stored tick of the symbol (its own data hub, else the shared stream keys) when younger than this many seconds;
# an older or missing one makes no exit decision this cycle, as with a stale broker quote.
EXIT_FALLBACK_TICK_MAX_AGE_SECONDS = int(os.environ.get("EXIT_FALLBACK_TICK_MAX_AGE_SECONDS", str(QUOTE_MAX_STALE_SECONDS)))
# M55 S2: of a per-day price-read allowance (rate_budget.DAILY_CAPS, Groww), this share is kept for exits: a price read
# for an exit may use it, any other read stops before it.
DAILY_EXIT_READ_RESERVE_SHARE = min(0.5, max(0.0, float(os.environ.get("DAILY_EXIT_READ_RESERVE_SHARE") or "0.1")))
# M55 S2: an exit order the broker answers REJECTED / CANCELLED is sent again up to this many more times in the same
# check, EXIT_REJECT_RETRY_SPACING_SECONDS apart. 0 (the default) keeps one send per check; the next check (the next
# cycle) always tries again while the exit rule still holds.
EXIT_REJECT_RETRIES = max(0, min(5, int(os.environ.get("EXIT_REJECT_RETRIES") or "0")))
EXIT_REJECT_RETRY_SPACING_SECONDS = max(0.0, min(5.0, float(os.environ.get("EXIT_REJECT_RETRY_SPACING_SECONDS") or "1.0")))
# BT1 (beta spec §2/§4): bring-your-own-broker data tier, read only with the `byob_data_tier` flag on (off by default).
# The ATP budget of symbols one user's broker session carries (positions > open charts > watchlist > screener universe);
# the broker's own per-connection cap (app/market_data/broker_data_terms.json) applies on top when it is recorded.
# 50 is the per-session figure of the beta capacity model (§4), not a broker limit.
BYOB_SESSION_SYMBOL_CAP = max(1, int(os.environ.get("BYOB_SESSION_SYMBOL_CAP", "50")))
# TM Part 1 (data hub, flag `data_hub`): an option-chain snapshot is shared within one broker connection for this many
# seconds (the chain page, a chart and a Copilot question in the same moment make one broker call). 0 = no sharing.
HUB_CHAIN_SNAPSHOT_SECONDS = max(0, int(os.environ.get("HUB_CHAIN_SNAPSHOT_SECONDS", "5")))

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
# M55 S7: a bot whose market-data read failed this many cycles in a row is "waiting for data" (a WARNING; it resumes by
# itself when a read works again). Code errors keep pausing it after MAX_CONSECUTIVE_FAILURES (trading_worker.py).
DEPLOYMENT_DATA_FAILURES_BEFORE_WAIT = _env_int_in("DEPLOYMENT_DATA_FAILURES_BEFORE_WAIT", 5, 1, 1000)
# M55 S7: the API (never the worker) tells each organisation with running bots, once (CRITICAL), when the worker's
# heartbeat is older than this many cycles; the same silence is not repeated within the cool-down.
WORKER_SILENT_CYCLES_ALERT = _env_int_in("WORKER_SILENT_CYCLES_ALERT", 3, 1, 1000)
WORKER_SILENCE_ALERT_COOLDOWN_SECONDS = _env_int_in("WORKER_SILENCE_ALERT_COOLDOWN_SECONDS", 3600, 60, 7 * 86400)
# TM2-4 (owner, 24 Oct): a waiting order the trigger engine sent and that is still "Sending" (FIRING) after this many
# seconds is looked up at its broker by its own client id / tag and settled from what is there (found: its state; not
# found: sent once with the same id). On worker start every FIRING order is settled this way before any new work.
TRIGGER_ACK_TIMEOUT_SECONDS = float(os.environ.get("TRIGGER_ACK_TIMEOUT_SECONDS", "5"))

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
    problems += broker_oauth_redirect_problems(ENVIRONMENT, BROKER_OAUTH_REDIRECT_BASE_URL)
    if problems:
        raise RuntimeError(
            f"Refusing to start with ENVIRONMENT={ENVIRONMENT} and insecure configuration:\n- " + "\n- ".join(problems)
        )
    for warning in broker_oauth_redirect_warnings(ENVIRONMENT, BROKER_OAUTH_REDIRECT_BASE_URL or PUBLIC_BASE_URL):
        logging.getLogger(__name__).warning("CONFIGURATION ACTION NEEDED: %s", warning)


def broker_oauth_redirect_problems(environment: str, base_url: str) -> List[str]:
    """UI2-5 review: in production and staging a BROKER_OAUTH_REDIRECT_BASE_URL that is set must be https - brokers send
    the one-time login code to it. (A new setting, so no running deployment has a non-https value to break.)"""
    if environment in HARDENED_ENVIRONMENTS and base_url and not base_url.lower().startswith("https://"):
        return ["BROKER_OAUTH_REDIRECT_BASE_URL must start with https:// - brokers send the one-time login code to it."]
    return []


def broker_oauth_redirect_warnings(environment: str, base_url: str) -> List[str]:
    """Unset in production or staging (and no PUBLIC_BASE_URL to fall back to, D2): the broker callback address (stored as
    an app's redirect_uri) is taken from the request's Host header. A loud warning for now, not a refusal to start, because existing deployments' .env files do
    not have the setting yet (docs/OPERATIONS.md, operator step)."""
    if environment in HARDENED_ENVIRONMENTS and not base_url:
        return ["BROKER_OAUTH_REDIRECT_BASE_URL is not set (nor PUBLIC_BASE_URL) - broker OAuth callback addresses come from the request's Host "
                "header. Set it to this deployment's public https origin (docs/OPERATIONS.md)."]
    return []

# --- Platform mailer + email verification (Phase N3) --------------------------------------------
# The platform's own SMTP account, used for account emails (verification links, and password
# resets when the tenant has no EMAIL alert channel yet). Unset = links are logged, not sent.
PLATFORM_SMTP_HOST = os.environ.get("PLATFORM_SMTP_HOST", "").strip()
PLATFORM_SMTP_PORT = int(os.environ.get("PLATFORM_SMTP_PORT", "587"))
PLATFORM_SMTP_USERNAME = os.environ.get("PLATFORM_SMTP_USERNAME", "")
PLATFORM_SMTP_PASSWORD = os.environ.get("PLATFORM_SMTP_PASSWORD", "")
PLATFORM_SMTP_FROM = os.environ.get("PLATFORM_SMTP_FROM", "")
PLATFORM_SMTP_STARTTLS = os.environ.get("PLATFORM_SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no"}
# Readiness C13: the first-week onboarding e-mails (flag `onboarding_emails`, OFF; nothing is sent without PLATFORM_SMTP_*
# and PUBLIC_BASE_URL). Hours after sign-up for "connect your broker", "start your first trading bot" and "review your first
# signal", in that order; each goes once, only inside the first week.
ONBOARDING_EMAIL_AFTER_HOURS = os.environ.get("ONBOARDING_EMAIL_AFTER_HOURS", "2,48,120").strip()
ONBOARDING_EMAIL_WINDOW_HOURS = int(os.environ.get("ONBOARDING_EMAIL_WINDOW_HOURS", "168"))
# Readiness C13: a new account may name the invite code it arrived with for this many days after signing up (flag
# `referrals`, OFF).
REFERRAL_CLAIM_DAYS = int(os.environ.get("REFERRAL_CLAIM_DAYS", "7"))
# When on, LIVE deployments and broker credential storage require a verified email address.
EMAIL_VERIFICATION_REQUIRED = os.environ.get("EMAIL_VERIFICATION_REQUIRED", "false").strip().lower() in {"1", "true", "yes"}

# --- Web Push (Phase O3) --------------------------------------------------------------------------
# VAPID key pair identifying this server to browser push services. Generate once with
# `python -m app.alerts.webpush` and keep it stable: rotating it invalidates every subscription.
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip() or None
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:ops@example.com").strip()

# --- Phone notifications for the Android app (readiness B8, docs/design/MOBILE_PUSH.md) ----------------------------
# Firebase Cloud Messaging HTTP v1. A Firebase service account key (JSON) authorises the server: either base64 on one
# line in .env (FCM_SERVICE_ACCOUNT_JSON_BASE64, the way on the Hostinger host) or a path to the file
# (FCM_SERVICE_ACCOUNT_FILE). FCM_PROJECT_ID is optional (the key's own project_id when empty). All empty = phone
# notifications are off: nothing is sent and the device routes say "not set up on this server yet".
FCM_PROJECT_ID = os.environ.get("FCM_PROJECT_ID", "").strip()
FCM_SERVICE_ACCOUNT_FILE = os.environ.get("FCM_SERVICE_ACCOUNT_FILE", "").strip()
FCM_SERVICE_ACCOUNT_JSON_BASE64 = os.environ.get("FCM_SERVICE_ACCOUNT_JSON_BASE64", "").strip()

# --- The platform's Telegram bot (UX2 fix 5: "Telegram in one tap") --------------------------------
# One bot for every organisation, created once by the operator with @BotFather. A user taps "Connect Telegram", the app
# opens t.me/<bot>?start=<one-time code>, the user presses Start and the chat is linked - no token or chat id to type.
# Unset = the Connect button says "Telegram alerts are not set up on this server yet" (the organisation's own bot under
# Advanced keeps working). The username is read from Telegram (getMe) when not given. With the webhook secret set the
# bot is reached by webhook (`POST /api/telegram/platform/webhook`, registered from the admin route); without it the
# app asks Telegram for new messages (getUpdates) while a user waits on the Connect screen.
ATP_TELEGRAM_BOT_TOKEN = os.environ.get("ATP_TELEGRAM_BOT_TOKEN", "").strip()
ATP_TELEGRAM_BOT_USERNAME = os.environ.get("ATP_TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
ATP_TELEGRAM_WEBHOOK_SECRET = os.environ.get("ATP_TELEGRAM_WEBHOOK_SECRET", "").strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# --- Production readiness A2: operator alerts (app/observability/ops_alerts.py) ---------------
# The server's scheduled check (`deploy/atp.sh ops-check`, every 5 minutes from the crontab `deploy` installs) judges
# these and messages the platform operator - never a user alert channel, so no user setting can mute it. Where: the
# operator Telegram chat registered from Admin > System > Alerts (one-time code with the platform bot; or the chat id
# in OPS_ALERT_TELEGRAM_CHAT_ID) and e-mail through PLATFORM_SMTP_* to OPS_ALERT_EMAIL (comma-separated; empty =
# SUPER_ADMIN_EMAILS). Thresholds: the Prometheus rules' own numbers where one exists (scripts/monitoring).
OPS_ALERT_TELEGRAM_CHAT_ID = os.environ.get("OPS_ALERT_TELEGRAM_CHAT_ID", "").strip()
OPS_ALERT_EMAIL = os.environ.get("OPS_ALERT_EMAIL", "").strip()
OPS_ALERT_AFTER_CHECKS = max(1, int(_env_float("OPS_ALERT_AFTER_CHECKS", 2)))        # the same verdict twice in a row
OPS_ALERT_REPEAT_HOURS = _env_float("OPS_ALERT_REPEAT_HOURS", 6)                     # a FAIL still there: remind
OPS_ERROR_RATE_MAX = _env_float("OPS_ERROR_RATE_MAX", 0.005)                         # rule ApiErrorBudgetBurn
OPS_ERROR_RATE_MIN_REQUESTS = int(_env_float("OPS_ERROR_RATE_MIN_REQUESTS", 50))
OPS_HEARTBEAT_MAX_SECONDS = int(_env_float("OPS_HEARTBEAT_MAX_SECONDS", 180))        # rule TradingWorkerStalled
OPS_FEED_STALE_REFUSALS = int(_env_float("OPS_FEED_STALE_REFUSALS", 5))              # rule MarketDataStale
OPS_TOKEN_EXPIRY_WARN_DAYS = int(_env_float("OPS_TOKEN_EXPIRY_WARN_DAYS", 7))
OPS_DISK_WARN_PERCENT = int(_env_float("OPS_DISK_WARN_PERCENT", 85))                 # the same lines as `atp.sh health`
OPS_DISK_FAIL_PERCENT = int(_env_float("OPS_DISK_FAIL_PERCENT", 95))
OPS_DB_SIZE_WARN_GB = _env_float("OPS_DB_SIZE_WARN_GB", 8)
OPS_DB_SIZE_FAIL_GB = _env_float("OPS_DB_SIZE_FAIL_GB", 15)
OPS_CERT_WARN_DAYS = int(_env_float("OPS_CERT_WARN_DAYS", 14))                        # Caddy renews at 30 days left
OPS_CERT_FAIL_DAYS = int(_env_float("OPS_CERT_FAIL_DAYS", 3))
OPS_OUTBOX_BACKLOG = int(_env_float("OPS_OUTBOX_BACKLOG", 20))                       # rule AlertOutboxBacklog
OPS_LOGIN_FAILURES_15M = int(_env_float("OPS_LOGIN_FAILURES_15M", 50))               # rule CredentialStuffing
OPS_HOST_FACTS_MAX_MINUTES = int(_env_float("OPS_HOST_FACTS_MAX_MINUTES", 30))
# Where the scheduled check reads the API's and the worker's Prometheus series (inside the compose network).
OPS_API_METRICS_URL = os.environ.get("OPS_API_METRICS_URL", "http://127.0.0.1:8000/metrics").strip()
OPS_WORKER_METRICS_URL = os.environ.get("OPS_WORKER_METRICS_URL", "http://worker:9102/metrics").strip()

# --- Production readiness A2: the error tracker (app/observability/error_tracker.py) ----------
# A Sentry-protocol DSN (GlitchTip or Sentry, from the owner's account). Empty = OFF: nothing is sent anywhere. Every
# event passes the PII scrubber first (no tokens, keys, e-mails, phone numbers, cookies, query strings, bodies).
ERROR_TRACKER_DSN = os.environ.get("ERROR_TRACKER_DSN", "").strip()
ERROR_TRACKER_ENVIRONMENT = os.environ.get("ERROR_TRACKER_ENVIRONMENT", "").strip()
ERROR_TRACKER_SAMPLE_RATE = min(1.0, max(0.0, _env_float("ERROR_TRACKER_SAMPLE_RATE", 1.0)))

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


# Screener addendum U1: NSE universe reference data (securities, symbol history, ...). Off until the operator has
# recorded each file's terms in docs/DATA_SOURCES.md. The URLs are the exchange's published download files.
UNIVERSE_SYNC_ENABLED = os.environ.get("UNIVERSE_SYNC_ENABLED", "false").lower() in ("1", "true", "yes")
UNIVERSE_SYNC_HOUR_IST = int(os.environ.get("UNIVERSE_SYNC_HOUR_IST", "18") or 18)
UNIVERSE_MIN_ROWS_RATIO = float(os.environ.get("UNIVERSE_MIN_ROWS_RATIO", "0.9") or 0.9)
# U1-b: an index file that would change more than this share of the members held in one day is refused for review.
UNIVERSE_MAX_CHURN_RATIO = float(os.environ.get("UNIVERSE_MAX_CHURN_RATIO", "0.2") or 0.2)
UNIVERSE_USER_AGENT = os.environ.get("UNIVERSE_USER_AGENT", "Mozilla/5.0 (compatible; ATP-reference-sync/1.0)")
_UNIVERSE_FILE_URLS_DEFAULT = {
    "equity_list": "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "sme_equity_list": "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
    "etf_list": "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
    "symbol_changes": "https://nsearchives.nseindia.com/content/equities/symbolchange.csv",
    # U1-c: {ddmmyyyy} is filled with the trade date.
    "index_close_all": "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv",
    "fo_lots": "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
    "fo_ban": "https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
}


def _universe_urls() -> dict:
    import json
    raw = os.environ.get("UNIVERSE_FILE_URLS", "").strip()
    try:
        extra = json.loads(raw) if raw else {}
    except ValueError:
        extra = {}
    return {**_UNIVERSE_FILE_URLS_DEFAULT, **(extra if isinstance(extra, dict) else {})}


UNIVERSE_FILE_URLS = _universe_urls()
# S1c (ADR-0021): which engine runs POST /api/scanner/run - "legacy" (app/screener/scanner/engine.py) or "screenql" (the same
# filters as one ScreenQL screen, parity-tested). Default legacy until the owner switches it; the response is identical.
SCANNER_ENGINE = os.environ.get("SCANNER_ENGINE", "legacy").strip().lower()
# S1d: the per-tenant ScreenQL cost cap (validator units; a screen over it is refused with the reason).
SCREENER_COST_CAP = float(os.environ.get("SCREENER_COST_CAP", "200"))
# S3b-2: the public https origin of this deployment (e.g. https://atp.example.com), used for the unsubscribe link in
# screen-alert emails. Empty = no link is added (the mails still go).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
# UI2-5: an optional override of the https origin at which brokers reach this API's OAuth callbacks (e.g.
# https://atp.example.com); the Redirect URL to register on the Upstox, Kite and Fyers developer consoles is this plus
# /api/broker/<broker>/oauth/callback. Empty = PUBLIC_BASE_URL (D2), else the address the request came in on.
BROKER_OAUTH_REDIRECT_BASE_URL = os.environ.get("BROKER_OAUTH_REDIRECT_BASE_URL", "").strip().rstrip("/")
# GS-1: Google sign-in (OpenID Connect). Both empty (the default) = the feature does not exist: GET /api/auth/providers says
# google: false, the sign-in page shows no Google button and the /api/auth/google/* routes answer 404. The redirect URI
# registered at Google is PUBLIC_BASE_URL + "/api/auth/google/callback". The secret is never returned or logged.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
# PW5: the Zerodha Kite Publisher api_key for "Trade in your broker app" basket links (docs/BROKER_DATA_TERMS.md). A
# public per-app identifier that sits in the page's HTML form, not a secret - but never committed with a value. Empty =
# no Zerodha link. Only read when the `broker_app_links` flag is on (off by default). ATP places no order with it.
KITE_PUBLISHER_API_KEY = os.environ.get("KITE_PUBLISHER_API_KEY", "").strip()

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


# AUD-G2.5 (flag `alert_webhooks`; app/alerts/signed_webhooks.py): how many signed webhooks one member may add, and how
# long one POST may take (connect, write and read each) before it counts as failed and is retried by the outbox.
ALERT_WEBHOOKS_MAX_PER_USER = _bounded_int("ALERT_WEBHOOKS_MAX_PER_USER", 5, 1, 50)
ALERT_WEBHOOKS_TIMEOUT_SECONDS = _bounded_int("ALERT_WEBHOOKS_TIMEOUT_SECONDS", 10, 1, 30)
# AUD-T5 ("Read this chart", flag `read_chart`; app/charts/read_chart.py): how many reads one member may ask for per
# minute (each runs the chart's structure read, the area replay and, once a day per symbol, the Daily trend read).
READ_CHART_PER_MINUTE = _bounded_int("READ_CHART_PER_MINUTE", 20, 1, 600)


# OI Banner (O2): the collector's slot and the strikes kept either side of the money (wider than any banner window,
# so each tenant's own ATM range applies on read), and the operator's default OIRegimeSettings fields (JSON).
OI_BANNER_SLOT_MINUTES = _bounded_int("OI_BANNER_SLOT_MINUTES", 5, 1, 60)
OI_BANNER_COLLECT_SPAN = _bounded_int("OI_BANNER_COLLECT_SPAN", 15, 1, 60)
OI_BANNER_DEFAULTS: dict = _json_object("OI_BANNER_DEFAULTS")
# M61 PCR-1 (data quality, not chart reading): a stored OI snapshot is "partial" when its count of strikes with OI is below
# this share of the median count of the session's earlier snapshots (0.9 = more than 10 % of the strikes missing).
OI_PARTIAL_SNAPSHOT_RATIO = min(1.0, max(0.0, _env_float("OI_PARTIAL_SNAPSHOT_RATIO", 0.9)))

# TM 9.4 chain history (docs/design/CHAIN_HISTORY.md): option-chain snapshots recorded through the OWNER's own broker
# session (HB2's owner: HISTORY_OWNER_TENANT_ID confirmed by HISTORY_OWNER_TENANT_NAME, the session of
# HISTORY_OWNER_USER_ID or the lowest-id active OWNER) into the owner's private store (md_tenant_chain_snapshots), only
# while the `chain_history` flag is on for that tenant. One slot every CHAIN_HISTORY_SLOT_MINUTES while the venue is open
# (the exchange calendar), for CHAIN_HISTORY_UNDERLYINGS and - when CHAIN_HISTORY_STOCKS_ENABLED - CHAIN_HISTORY_STOCKS,
# the nearest CHAIN_HISTORY_EXPIRIES expiries each, at most CHAIN_HISTORY_MAX_READS_PER_SLOT chain reads per slot (the
# indices first). Every read takes a token from the owner session's worker RateBudget through `acquire_spare`, leaving
# CHAIN_HISTORY_RESERVE of it for the trading worker; a slot without a token is recorded as a gap, never queued.
CHAIN_HISTORY_SLOT_MINUTES = _bounded_int("CHAIN_HISTORY_SLOT_MINUTES", 1, 1, 60)
CHAIN_HISTORY_UNDERLYINGS = [k.strip().upper() for k in os.environ.get("CHAIN_HISTORY_UNDERLYINGS", "NIFTY,BANKNIFTY,SENSEX").split(",") if k.strip()]
CHAIN_HISTORY_STOCKS_ENABLED = os.environ.get("CHAIN_HISTORY_STOCKS_ENABLED", "false").lower() in ("1", "true", "yes")
CHAIN_HISTORY_STOCKS = [k.strip().upper() for k in os.environ.get("CHAIN_HISTORY_STOCKS", "").split(",") if k.strip()]
CHAIN_HISTORY_EXPIRIES = _bounded_int("CHAIN_HISTORY_EXPIRIES", 2, 1, 6)
CHAIN_HISTORY_MAX_READS_PER_SLOT = _bounded_int("CHAIN_HISTORY_MAX_READS_PER_SLOT", 12, 1, 200)
CHAIN_HISTORY_RESERVE = min(0.95, max(0.0, float(os.environ.get("CHAIN_HISTORY_RESERVE", "0.5") or 0.5)))
# Seconds one read may wait for a spare token before the slot's remaining reads are skipped (a gap, asked next slot).
CHAIN_HISTORY_TOKEN_WAIT_SECONDS = _bounded_int("CHAIN_HISTORY_TOKEN_WAIT_SECONDS", 5, 0, 60)
# Retention: RETENTION_CHAIN_HISTORY_DAYS (app/retention/policy.py; TM-8: 0 = kept until the owner sets a limit).
# The read API: at most this many days per range request and this many stored chains per response.
CHAIN_HISTORY_READ_MAX_DAYS = _bounded_int("CHAIN_HISTORY_READ_MAX_DAYS", 31, 1, 366)
CHAIN_HISTORY_READ_MAX_CHAINS = _bounded_int("CHAIN_HISTORY_READ_MAX_CHAINS", 2000, 10, 50000)

# Z7a-2 market zones: the operator's ZoneSettings fields over the platform's all-daily defaults (JSON object; see
# app/price_action/zones/store.py PLATFORM_DEFAULTS), the lake instruments the nightly zone job always follows (comma list
# of keys like "NSE:BANKNIFTY"; tenants add their own in market_zone_settings), the IST hour from which it runs once a day
# (20: after HB1's 19:00 EOD ingest) and one pass's time budget (a pass that runs out resumes on the next cycle). Only with
# the `market_zones` flag (off by default).
MARKET_ZONES_DEFAULTS: dict = _json_object("MARKET_ZONES_DEFAULTS")
MARKET_ZONES_SYMBOLS = [k.strip().upper() for k in os.environ.get("MARKET_ZONES_SYMBOLS", "").split(",") if k.strip()]
MARKET_ZONES_HOUR_IST = _bounded_int("MARKET_ZONES_HOUR_IST", 20, 0, 23)
MARKET_ZONES_BUDGET_SECONDS = _bounded_int("MARKET_ZONES_BUDGET_SECONDS", 1800, 10, 6 * 3600)
# U6 BK5: breakout screen fitness - a nightly pass over the shared lake's daily bars (flag screener_breakouts_fitness, off).
# BREAKOUT_FITNESS_SYMBOLS: the universe ("NSE:RELIANCE,NSE:TCS,..."; empty = MARKET_ZONES_SYMBOLS).
BREAKOUT_FITNESS_SYMBOLS = [k.strip().upper() for k in os.environ.get("BREAKOUT_FITNESS_SYMBOLS", "").split(",") if k.strip()]
BREAKOUT_FITNESS_UNIVERSE = os.environ.get("BREAKOUT_FITNESS_UNIVERSE", "platform")[:32] or "platform"
BREAKOUT_FITNESS_HOUR_IST = _bounded_int("BREAKOUT_FITNESS_HOUR_IST", 21, 0, 23)
BREAKOUT_FITNESS_BUDGET_SECONDS = _bounded_int("BREAKOUT_FITNESS_BUDGET_SECONDS", 1800, 10, 6 * 3600)
BREAKOUT_FITNESS_HISTORY_DAYS = _bounded_int("BREAKOUT_FITNESS_HISTORY_DAYS", 1825, 400, 3650)

# PW6: the closed-beta cohort cap. With the `beta_cohort_gate` flag on, registration needs an invite code and stops once
# this many accounts have been created through invite codes (the sum of every code's used_count, revoked codes
# included). The flag is off by default, so the cap does nothing until the operator turns the gate on.
BETA_COHORT_CAP = _bounded_int("BETA_COHORT_CAP", 500, 1, 1_000_000)
# Z10-e (staged rollout 50 -> 200 -> 500): the cohort grows in stages and the gate stops at min(stage, BETA_COHORT_CAP).
# The SUPER_ADMIN moves the stage in the admin console (stored in platform_controls, every move audited); this setting is
# only the stage used until a stage has been stored. Any value outside BETA_COHORT_STAGES falls back to 50.
BETA_COHORT_STAGES = (50, 200, 500)
BETA_COHORT_STAGE = _bounded_int("BETA_COHORT_STAGE", 50, 1, 1_000_000)
if BETA_COHORT_STAGE not in BETA_COHORT_STAGES:
    BETA_COHORT_STAGE = 50


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if low <= value <= high else default


# Z3 / BT2 (beta spec, Phase 0): approval-only entries (flag `approval_only_entries`, off by default). An entry proposal
# expires after APPROVAL_ENTRY_TTL_MINUTES; an "Exit now?" proposal (a target reached) after APPROVAL_EXIT_TTL_MINUTES.
# On Approve the entry is re-quoted at the current price: a move from the proposal price beyond
# APPROVAL_REQUOTE_MAX_DRIFT_PCT (percent of the proposal price; 0 = no percent limit) or beyond
# APPROVAL_REQUOTE_MAX_DRIFT_TICKS (exchange ticks; 0 = no tick limit) is refused - whichever limit is stricter wins - and
# the proposal is re-issued at the new price, at most APPROVAL_MAX_REQUOTES times, then it expires.
APPROVAL_ENTRY_TTL_MINUTES = _bounded_int("APPROVAL_ENTRY_TTL_MINUTES", 15, 1, 24 * 60)
APPROVAL_EXIT_TTL_MINUTES = _bounded_int("APPROVAL_EXIT_TTL_MINUTES", 30, 1, 24 * 60)
APPROVAL_REQUOTE_MAX_DRIFT_PCT = _bounded_float("APPROVAL_REQUOTE_MAX_DRIFT_PCT", 0.5, 0.0, 20.0)
APPROVAL_REQUOTE_MAX_DRIFT_TICKS = _bounded_int("APPROVAL_REQUOTE_MAX_DRIFT_TICKS", 0, 0, 100_000)
APPROVAL_MAX_REQUOTES = _bounded_int("APPROVAL_MAX_REQUOTES", 2, 0, 10)

# N1 E1 (ADR-0014, flag `execution_core`, off by default): the execution core's intent queue. An intent not dispatched
# within EXECUTION_INTENT_TTL_SECONDS is EXPIRED (a setup is never proposed late); the worker claims at most
# EXECUTION_CORE_DRAIN_LIMIT per cycle; a claim older than EXECUTION_CORE_CLAIM_STALE_SECONDS (an interrupted worker) is
# queued again - safe, every route is idempotent on "intent:<id>" - and FAILED after EXECUTION_CORE_MAX_ATTEMPTS claims.
EXECUTION_INTENT_TTL_SECONDS = _bounded_int("EXECUTION_INTENT_TTL_SECONDS", 300, 30, 24 * 3600)
EXECUTION_CORE_DRAIN_LIMIT = _bounded_int("EXECUTION_CORE_DRAIN_LIMIT", 50, 1, 1000)
EXECUTION_CORE_CLAIM_STALE_SECONDS = _bounded_int("EXECUTION_CORE_CLAIM_STALE_SECONDS", 120, 30, 3600)
EXECUTION_CORE_MAX_ATTEMPTS = _bounded_int("EXECUTION_CORE_MAX_ATTEMPTS", 3, 1, 10)
# The drain stops claiming new intents once it has run this long in one cycle (the rest wait for the next cycle).
EXECUTION_CORE_DRAIN_SECONDS = _bounded_int("EXECUTION_CORE_DRAIN_SECONDS", 20, 1, 300)
# N1 X2 (flag scan_auto_paper, off; N1-3 is the owner's decision): limits on Auto PAPER entries from one armed scan
# template - at most this many per IST day, and a cool-down per symbol for the same person. Over either, the setup
# becomes a proposal instead.
SCAN_AUTO_PAPER_MAX_PER_DAY = _bounded_int("SCAN_AUTO_PAPER_MAX_PER_DAY", 3, 1, 20)
SCAN_AUTO_PAPER_COOLDOWN_MINUTES = _bounded_int("SCAN_AUTO_PAPER_COOLDOWN_MINUTES", 30, 0, 24 * 60)
# Review of X2: an arming lasts this many days (then the owner arms again), and no Auto PAPER entry is placed at or after
# this IST time (HH:MM; empty = the venue's own no-new-entries time, 15:00 on NSE, and never after the 15:15 square-off).
SCAN_AUTO_PAPER_ARM_DAYS = _bounded_int("SCAN_AUTO_PAPER_ARM_DAYS", 7, 1, 90)
SCAN_AUTO_PAPER_LAST_ENTRY = os.environ.get("SCAN_AUTO_PAPER_LAST_ENTRY", "").strip()

# Z3-b / BT3 (beta spec, Phase 0): the per-user PAPER account (flag `paper_account`, off by default). A new account starts
# with PAPER_STARTING_CAPITAL (the user may change it within the bounds). The progression ladder card shows the PAPER
# stage's criteria from the user's own PAPER trades: at least PAPER_LADDER_MIN_SESSIONS sessions with a PAPER trade, at
# least PAPER_LADDER_MIN_TRADES closed PAPER trades, and a largest drawdown of at most PAPER_LADDER_MAX_DRAWDOWN_PCT % of
# peak equity. Phase 0 has no LIVE stage to move to: the card shows the criteria, never a switch.
PAPER_STARTING_CAPITAL = _bounded_float("PAPER_STARTING_CAPITAL", 1_000_000.0, 10_000.0, 1_000_000_000.0)
PAPER_MIN_CAPITAL = 10_000.0
PAPER_MAX_CAPITAL = 100_000_000.0
PAPER_LADDER_MIN_SESSIONS = _bounded_int("PAPER_LADDER_MIN_SESSIONS", 20, 1, 1000)
PAPER_LADDER_MIN_TRADES = _bounded_int("PAPER_LADDER_MIN_TRADES", 30, 1, 10_000)
PAPER_LADDER_MAX_DRAWDOWN_PCT = _bounded_float("PAPER_LADDER_MAX_DRAWDOWN_PCT", 10.0, 0.5, 100.0)
# N2-1 (P1 §1.4 / §1.5, flag `ladder_evidence`, docs/design/N2_P1FGH.md): the ladder's discipline criterion is the mean
# Z6 weekly score of the last PAPER_LADDER_DISCIPLINE_WEEKS completed IST weeks with a PAPER trade (looking back at most
# PAPER_LADDER_DISCIPLINE_LOOKBACK_WEEKS weeks), met at PAPER_LADDER_MIN_DISCIPLINE or more. The "your figures" block
# needs PAPER_EVIDENCE_MIN_TRADES closed trades; its risk-of-ruin bound is for a fall of PAPER_EVIDENCE_RUIN_PCT % below
# the starting capital.
PAPER_LADDER_MIN_DISCIPLINE = _bounded_int("PAPER_LADDER_MIN_DISCIPLINE", 70, 0, 100)
PAPER_LADDER_DISCIPLINE_WEEKS = _bounded_int("PAPER_LADDER_DISCIPLINE_WEEKS", 4, 1, 12)
PAPER_LADDER_DISCIPLINE_LOOKBACK_WEEKS = _bounded_int("PAPER_LADDER_DISCIPLINE_LOOKBACK_WEEKS", 12, 1, 52)
PAPER_EVIDENCE_MIN_TRADES = _bounded_int("PAPER_EVIDENCE_MIN_TRADES", 20, 2, 10_000)
PAPER_EVIDENCE_RUIN_PCT = _bounded_float("PAPER_EVIDENCE_RUIN_PCT", 50.0, 1.0, 100.0)
# N2-2 (P1 §1.4, flag `experience_levels`): experience levels from the user's own PAPER record gate complexity. Beginner
# until the PAPER ladder's count criteria are met (sessions, closed trades, drawdown above); Intermediate opens futures;
# Advanced (EXPERIENCE_ADVANCED_MIN_TRADES closed trades over EXPERIENCE_ADVANCED_MIN_SESSIONS sessions, drawdown within
# the ladder's limit) opens naked short options. A logged opt-out with a reason lifts one gate for one IST day.
EXPERIENCE_ADVANCED_MIN_TRADES = _bounded_int("EXPERIENCE_ADVANCED_MIN_TRADES", 100, 1, 100_000)
EXPERIENCE_ADVANCED_MIN_SESSIONS = _bounded_int("EXPERIENCE_ADVANCED_MIN_SESSIONS", 60, 1, 10_000)
EXPERIENCE_OPT_OUT_MIN_REASON_CHARS = _bounded_int("EXPERIENCE_OPT_OUT_MIN_REASON_CHARS", 15, 1, 300)

# Z3 (review V2): "Exit now" without a live quote may book at the monitor's last price for the position only when that
# price is at most this many minutes old and from today's session (IST); otherwise it refuses (the stop and target keep
# working on their own).
EXIT_NOW_MAX_MARK_AGE_MINUTES = _bounded_int("EXIT_NOW_MAX_MARK_AGE_MINUTES", 5, 1, 60)
# Tax report (app/tax/report.py): how F&O turnover is counted. "ABS_PNL" (default) = the sum of the absolute profit or
# loss of every F&O trade (options and futures alike). "ABS_PNL_PLUS_PREMIUM" = the older method, which also adds the
# premium received on options sold. The report names the method it used; GET /api/tax/report?turnover_method=...
# picks the other one for a single report. docs/journal/OPEN_QUESTIONS.md JM-1 (a CA confirms the default).
TAX_FNO_TURNOVER_METHODS = ("ABS_PNL", "ABS_PNL_PLUS_PREMIUM")


def _choice(name: str, default: str, choices: tuple) -> str:
    raw = os.environ.get(name, "").strip().upper()
    if not raw:
        return default
    if raw in choices:
        return raw
    logging.getLogger(__name__).warning("%s=%r is not one of %s - using %s", name, raw, choices, default)
    return default


TAX_FNO_TURNOVER_METHOD = _choice("TAX_FNO_TURNOVER_METHOD", "ABS_PNL", TAX_FNO_TURNOVER_METHODS)

# Z5 / P1-d (P1 spec 1.1, 1.2, 2.2; Phase 0): risk by default and the plan-before-trade PAPER ticket (flag
# `risk_by_default`, off by default). TT-12 (owner decision, 24 Oct): every limit starts OFF for every user until he sets
# his own value; the RISK_DEFAULT_* values below are no longer applied to anyone. They are kept only to read a settings
# row saved before TT-12 (a save then wrote every key, so a stored value equal to one of these may not be the user's own
# choice and counts as unset - app/risk_engine/by_default.py `explicit_keys`). A user's own values stay within the
# bounds and the platform's risk ceilings (strictest wins - ADR-0003):
# - RISK_DEFAULT_RISK_PER_TRADE_PCT: the most a PAPER entry may lose at its stop, % of the user's PAPER capital basis
#   (starting capital + realised net P&L before today); RISK_DEFAULT_RISK_PER_TRADE_AMOUNT: the same cap in rupees
#   (0 = no rupee cap). The stricter of the two is the cap; the ticket sizes the quantity from it and the stop distance.
# - RISK_OVERRIDE_MAX_MULTIPLE: a deliberate, logged and counted override may size up to this many times the cap, never
#   more (and never past the platform ceiling); RISK_OVERRIDE_MIN_REASON_CHARS: the override needs a reason this long.
# - RISK_DEFAULT_DAILY_LOSS_PCT: today's PAPER net P&L (realised + today's open marks) at or below minus this % of the
#   capital basis locks new PAPER entries for the rest of the IST day. Exits are never locked (ADR-0004).
# - RISK_DEFAULT_MAX_TRADES_PER_DAY: new PAPER entries per IST day (0 = no limit).
# - RISK_DEFAULT_MIN_RR: a plan's reward:risk (target vs stop, from the entry) must be at least this (only when he set it).
# - The cost line's brokerage per order and slippage estimate are the paper broker's own (`PaperBroker`), so the ticket
#   shows what a PAPER fill is charged.
RISK_DEFAULT_RISK_PER_TRADE_PCT = _bounded_float("RISK_DEFAULT_RISK_PER_TRADE_PCT", 1.0, 0.05, 5.0)
RISK_DEFAULT_RISK_PER_TRADE_AMOUNT = _bounded_float("RISK_DEFAULT_RISK_PER_TRADE_AMOUNT", 0.0, 0.0, 10_000_000.0)
RISK_OVERRIDE_MAX_MULTIPLE = _bounded_float("RISK_OVERRIDE_MAX_MULTIPLE", 2.0, 1.0, 5.0)
RISK_OVERRIDE_MIN_REASON_CHARS = _bounded_int("RISK_OVERRIDE_MIN_REASON_CHARS", 10, 1, 200)
RISK_DEFAULT_DAILY_LOSS_PCT = _bounded_float("RISK_DEFAULT_DAILY_LOSS_PCT", 2.0, 0.1, 20.0)
RISK_DEFAULT_MAX_TRADES_PER_DAY = _bounded_int("RISK_DEFAULT_MAX_TRADES_PER_DAY", 10, 0, 500)
RISK_DEFAULT_MIN_RR = _bounded_float("RISK_DEFAULT_MIN_RR", 1.5, 0.0, 10.0)

# Z5 (Trade port, `check_vix_spike_halt.py` + `trading_engine.check_vix_spike_halt`): the India VIX gate (flag `vix_gate`,
# off by default). It reads the organisation's India VIX readings of the day (market memory, captured through its own
# broker session). VIX_GATE_SPIKE_PCT: a reading today at or above this % change against the previous close pauses new
# BULLISH (long) entries on VIX_GATE_UNDERLYINGS for the rest of the IST day, PAPER and LIVE alike (the Trade rule;
# bearish entries and other symbols are not touched). VIX_GATE_HIGH_LEVEL: the latest reading today at or above this
# level cuts the risk per trade of every new entry on those underlyings by VIX_GATE_SIZE_CUT_PCT % (0 level = off).
# A reading older than VIX_GATE_MAX_AGE_MINUTES is not used for the level. Exits are never gated (ADR-0004).
VIX_GATE_SPIKE_PCT = _bounded_float("VIX_GATE_SPIKE_PCT", 5.0, 0.5, 100.0)
VIX_GATE_HIGH_LEVEL = _bounded_float("VIX_GATE_HIGH_LEVEL", 20.0, 0.0, 200.0)
VIX_GATE_SIZE_CUT_PCT = _bounded_float("VIX_GATE_SIZE_CUT_PCT", 50.0, 0.0, 100.0)
VIX_GATE_MAX_AGE_MINUTES = _bounded_int("VIX_GATE_MAX_AGE_MINUTES", 45, 1, 24 * 60)
# VIX_GATE_CHECK_AT: the gate is open before this IST time (Trade's 09:20 check, five minutes after the open); readings
# are captured from then on every VIX_GATE_CAPTURE_MINUTES while NSE is open. After VIX_GATE_GRACE_MINUTES more, on a
# weekday, no valid reading today counts as a spike (Trade's fail-safe).
VIX_GATE_CHECK_AT = os.environ.get("VIX_GATE_CHECK_AT", "09:20").strip() or "09:20"
VIX_GATE_GRACE_MINUTES = _bounded_int("VIX_GATE_GRACE_MINUTES", 10, 0, 240)
VIX_GATE_CAPTURE_MINUTES = _bounded_int("VIX_GATE_CAPTURE_MINUTES", 15, 1, 240)
# A ticket's time stop must be later than the current IST time plus this many minutes (a time already past would close
# the position at once).
TICKET_TIME_STOP_MIN_LEAD_MINUTES = _bounded_int("TICKET_TIME_STOP_MIN_LEAD_MINUTES", 5, 0, 120)
VIX_GATE_UNDERLYINGS = tuple(s.strip().upper() for s in os.environ.get("VIX_GATE_UNDERLYINGS", "NIFTY,BANKNIFTY,SENSEX").split(",") if s.strip())

# AUD-G1.1 (owner audit 28 Oct; DPDP): a user's own "Delete my account" takes effect after this many days; until then
# the account works as before and the request can be cancelled from Settings -> Account.
ACCOUNT_ERASE_GRACE_DAYS = int(os.environ.get("ACCOUNT_ERASE_GRACE_DAYS", "7"))

# AUD-T3 ("Why no trade" timeline): how many closed bars each bot keeps a reason for (older rows are pruned on write).
WHY_NO_TRADE_KEEP_BARS = int(os.environ.get("WHY_NO_TRADE_KEEP_BARS", "50"))

# AUD-T4 (paper report card, flag `paper_report_card`): the calibration (Brier score) of a shown confidence appears only
# from PAPER_REPORT_CARD_MIN_TRADES_BRIER closed trades that carry one; the card reads at most PAPER_REPORT_CARD_MAX_TRADES
# of the user's newest closed PAPER trades.
PAPER_REPORT_CARD_MIN_TRADES_BRIER = _bounded_int("PAPER_REPORT_CARD_MIN_TRADES_BRIER", 20, 2, 10_000)
PAPER_REPORT_CARD_MAX_TRADES = _bounded_int("PAPER_REPORT_CARD_MAX_TRADES", 5000, 100, 100_000)
# AUD-G2.2 (live push, flag `live_push`; app/live/routes.py): the live stream's push settings, sent to the tab in its
# `ready` event.
# - LIVE_PUSH_STALE_SECONDS: a symbol whose last tick is older than this reads "Price delayed" on the chart, and its
#   view goes back to its timer until ticks come again.
# - LIVE_PUSH_HEARTBEAT_SECONDS: a `beat` event this often; a tab that hears nothing for two beats treats the push as
#   down and goes back to its timers.
# - LIVE_PUSH_BAR_SETTLE_SECONDS: after a tick in a new bar, the candles are read again this much later (the broker's
#   closed bar settles first).
# - LIVE_PUSH_MAX_STREAMS_PER_USER: push streams one user holds open at once (per server process); a further tab gets
#   the stream as before without the push and keeps its timers.
LIVE_PUSH_STALE_SECONDS = _bounded_int("LIVE_PUSH_STALE_SECONDS", 30, 1, 3600)
LIVE_PUSH_HEARTBEAT_SECONDS = _bounded_int("LIVE_PUSH_HEARTBEAT_SECONDS", 10, 1, 120)
LIVE_PUSH_BAR_SETTLE_SECONDS = _bounded_int("LIVE_PUSH_BAR_SETTLE_SECONDS", 5, 0, 120)
LIVE_PUSH_MAX_STREAMS_PER_USER = _bounded_int("LIVE_PUSH_MAX_STREAMS_PER_USER", 8, 1, 100)
# AUD-T6 (similar-setup recall, flag `setup_recall`; BR-M3): the words over the user's own similar closed trades (how
# many closed with a gain / a loss, average R, the most common way the losses closed) appear only from
# SETUP_RECALL_MIN_SAMPLE matches; below it the card says "not enough history yet" and only lists them. At most
# SETUP_RECALL_MAX_LISTED matches are listed (newest first); at most SETUP_RECALL_MAX_SCAN of his closed trades with the
# same setup are read.
SETUP_RECALL_MIN_SAMPLE = _bounded_int("SETUP_RECALL_MIN_SAMPLE", 10, 1, 10_000)
SETUP_RECALL_MAX_LISTED = _bounded_int("SETUP_RECALL_MAX_LISTED", 10, 1, 200)
SETUP_RECALL_MAX_SCAN = _bounded_int("SETUP_RECALL_MAX_SCAN", 2000, 50, 100_000)
# AUD-T1 ("My day in one card", flag `my_day_card`): the one state chip on each open position is read ONLY from the
# user's own stop on that position - how much of the room between his entry and his own stop is still left at the
# position's last mark (100 % = the mark is at the entry, 0 % = the mark has reached his stop). Information only: the
# chip never places, blocks or changes an order. A position with no stop gets no chip ("No stop set").
# - MY_DAY_WATCH_ROOM_LEFT_PCT: at or below this % of the room left the chip reads "Watch" (above it: "Safe").
# - MY_DAY_NEAR_ROOM_LEFT_PCT: at or below this % it reads "Near invalidation" (the daily rhythm's own 75 %-used line).
MY_DAY_WATCH_ROOM_LEFT_PCT = _bounded_float("MY_DAY_WATCH_ROOM_LEFT_PCT", 50.0, 0.0, 100.0)
MY_DAY_NEAR_ROOM_LEFT_PCT = _bounded_float("MY_DAY_NEAR_ROOM_LEFT_PCT", 25.0, 0.0, 100.0)
# M55 S8 (A16): operational numbers that were constants in code, now settings; every default equals the old constant
# (tests/test_m55_s8_settings.py keeps them equal). None of them is a trading rule.
# - TRIGGER_TICK_SECONDS: how often the trigger engine's tick pass checks waiting orders (0 turns the pass off; the
#   worker cycle still runs the engine). TRIGGER_TICK_MAX_AGE_SECONDS: a stored tick older than this is not used by it.
# - TRIGGER_STALL_SECONDS: no engine run for this long during market hours (with waiting orders) raises the stall alert.
TRIGGER_TICK_SECONDS = float(os.environ.get("TRIGGER_TICK_SECONDS", "1"))
TRIGGER_TICK_MAX_AGE_SECONDS = float(os.environ.get("TRIGGER_TICK_MAX_AGE_SECONDS", "5"))
TRIGGER_STALL_SECONDS = int(os.environ.get("TRIGGER_STALL_SECONDS", str(WORKER_CYCLE_SECONDS * 3)))
# - WORKER_MAX_CONSECUTIVE_FAILURES: a deployment that fails this many cycles in a row is paused (not retried forever).
# - WORKER_LOCK_TTL_MIN_SECONDS: the worker's replica lock lives two cycles, never less than this.
WORKER_MAX_CONSECUTIVE_FAILURES = _bounded_int("WORKER_MAX_CONSECUTIVE_FAILURES", 5, 1, 1000)
WORKER_LOCK_TTL_MIN_SECONDS = _bounded_int("WORKER_LOCK_TTL_MIN_SECONDS", 30, 5, 3600)
# - EXIT_CLAIM_INFLIGHT_SECONDS: an exit claim younger than this is an exit being placed right now (wait for it).
# - EXIT_ADOPT_CLOCK_SKEW_SECONDS: an order placed this long before the exit's claim can still be its exit (clock skew).
# - EXIT_WAIT_ALERT_CHECKS: checks in a row that find an earlier exit still working before the alert.
# - EXIT_ALERT_COOLDOWN_SECONDS: the position monitor's alerts repeat at most once per this many seconds.
EXIT_CLAIM_INFLIGHT_SECONDS = _bounded_int("EXIT_CLAIM_INFLIGHT_SECONDS", 120, 10, 3600)
EXIT_ADOPT_CLOCK_SKEW_SECONDS = _bounded_int("EXIT_ADOPT_CLOCK_SKEW_SECONDS", 5, 0, 120)
EXIT_WAIT_ALERT_CHECKS = _bounded_int("EXIT_WAIT_ALERT_CHECKS", 5, 1, 1000)
EXIT_ALERT_COOLDOWN_SECONDS = _bounded_int("EXIT_ALERT_COOLDOWN_SECONDS", 1800, 60, 86400)
# - STOP_GUARD_ALERT_COOLDOWN_SECONDS: the stop guard's failure alerts repeat at most once per this many seconds.
# - STOP_GUARD_MAX_EXIT_ATTEMPTS: immediate exits the stop guard sends for one position before it stops and alerts.
STOP_GUARD_ALERT_COOLDOWN_SECONDS = _bounded_int("STOP_GUARD_ALERT_COOLDOWN_SECONDS", 1800, 60, 86400)
STOP_GUARD_MAX_EXIT_ATTEMPTS = _bounded_int("STOP_GUARD_MAX_EXIT_ATTEMPTS", 3, 1, 20)
# - BROKER_DAILY_CAP_SHARE: of a broker's per-day call cap (rate_budget.DAILY_CAPS), ATP spends at most this share.
# - BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE: of the per-day order cap, this share is kept for exits (entries stop before it).
BROKER_DAILY_CAP_SHARE = _bounded_float("BROKER_DAILY_CAP_SHARE", 0.9, 0.1, 1.0)
BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE = _bounded_float("BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE", 0.1, 0.0, 0.5)
# M71 PL-2 (operational settings, not trading values; app/brokers/broker_limits.py):
# - BROKER_BUDGET_SAFETY_MARGIN: ATP stays this share below every published broker limit (all windows).
# - BROKER_ORDER_EXIT_RESERVE_SHARE: of every order-pool window, this share is kept for exits, stops, changes and cancels.
# - BROKER_DATA_PRIORITY_RESERVE_SHARE: of every data-pool window, this share is kept for an exit's own reads.
# - BROKER_DATA_MAX_WAIT_SECONDS: a data call waits at most this long for a slot, then gets a plain "busy" error.
# - BROKER_EXIT_MAX_WAIT_SECONDS: an exit waits at most this long for the next slot, then is sent (never refused).
# - BROKER_429_*: back-off after a broker "too many requests" (exponential, with jitter) and its total wait per call.
# - BROKER_UPSTOX_LIMIT_SET: "conservative" (default, the lower published figures) or "r33" (the owner's research).
BROKER_BUDGET_SAFETY_MARGIN = _bounded_float("BROKER_BUDGET_SAFETY_MARGIN", 0.10, 0.0, 0.5)
BROKER_ORDER_EXIT_RESERVE_SHARE = _bounded_float("BROKER_ORDER_EXIT_RESERVE_SHARE", 0.2, 0.0, 0.5)
BROKER_DATA_PRIORITY_RESERVE_SHARE = _bounded_float("BROKER_DATA_PRIORITY_RESERVE_SHARE", 0.1, 0.0, 0.5)
BROKER_DATA_MAX_WAIT_SECONDS = _bounded_float("BROKER_DATA_MAX_WAIT_SECONDS", 30.0, 0.0, 600.0)
BROKER_EXIT_MAX_WAIT_SECONDS = _bounded_float("BROKER_EXIT_MAX_WAIT_SECONDS", 2.0, 0.0, 60.0)
BROKER_429_BASE_SECONDS = _bounded_float("BROKER_429_BASE_SECONDS", 0.5, 0.05, 10.0)
BROKER_429_CAP_SECONDS = _bounded_float("BROKER_429_CAP_SECONDS", 8.0, 0.1, 120.0)
BROKER_429_DATA_MAX_WAIT_SECONDS = _bounded_float("BROKER_429_DATA_MAX_WAIT_SECONDS", 20.0, 0.0, 600.0)
BROKER_429_ORDER_MAX_WAIT_SECONDS = _bounded_float("BROKER_429_ORDER_MAX_WAIT_SECONDS", 60.0, 0.0, 600.0)
BROKER_UPSTOX_LIMIT_SET = (os.environ.get("BROKER_UPSTOX_LIMIT_SET") or "conservative").strip().lower()
