"""BYOK-V1 (docs/BYOK.md): "Vision with your own AI key".

Each user may store their OWN provider API key (Anthropic, OpenAI or Google Gemini) to turn on Vision chart review. The
user pays the provider directly; the platform stores the key encrypted, never bills for it and never uses one user's key
for anybody else. Behind the `vision_byok` operator flag (off by default).

* **Keys** - one row per (user, provider) in `user_ai_keys`. The key is sealed as JSON together with the tenant, the user
  and the provider and encrypted with the tenant's data key (purpose `byok_ai_key`, the envelope encryption every other
  secret uses). Opening checks all three against the row and against the caller, so a ciphertext copied to another
  user's or organisation's row is refused instead of used. Only a hint (first 4 + last 4 characters) is ever shown.
* **API keys only** - consumer chat subscriptions (Claude Pro/Max and the like) cannot be used: their terms do not allow
  sign-in from third-party apps. Session cookies, OAuth tokens and JWTs are refused at the door.
* **Spend** - every call on a user's key is logged in `llm_calls` with `billed_to_user_key = true` (and never in the
  plan's usage rows). `byok_spend` sums them; `byok_allowed` is the gate V2/V3 call before every audit: vision off,
  no key, daily cap reached or monthly cap reached (hard stop). Caps reset at 00:00 UTC (day) and on the 1st (month).
* **Errors** - the text recorded for a failed test is sanitised: the key, and anything that looks like a key or token,
  is removed before it is stored or returned.

Vision only comments; it never places, blocks or changes an order (ADR-0006).
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import pricing
from app.ai import providers as ai_providers
from app.ai.metering import log_call, month_start
from app.ai.providers import BYOK_PROVIDERS, Completion, ProviderError
from app.audit.log import write_audit_log
from app.core import config
from app.db.models import LlmCallRecord, User, UserAiKeyRecord, UserVisionPrefsRecord
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_BYOK_AI_KEY

logger = logging.getLogger(__name__)

FLAG = "vision_byok"
FEATURE_TEST = "byok_test"
TEST_PROMPT_VERSION = "byok-test-v1"
TEST_SYSTEM = "You are a connectivity check for a trading dashboard. Answer with the single word OK."
TEST_USER = "Reply with OK."
TEST_MAX_TOKENS = 16

PROVIDER_LABELS = {"anthropic": "Anthropic (Claude)", "openai": "OpenAI", "gemini": "Google Gemini"}
CONSOLE_URLS = {"anthropic": "https://console.anthropic.com/settings/keys", "openai": "https://platform.openai.com/api-keys",
                "gemini": "https://aistudio.google.com/app/apikey"}

REASON_OK = "ok"
REASON_VISION_OFF = "vision off"
REASON_NO_KEY = "no key"
REASON_DAILY_CAP = "daily cap reached"
REASON_MONTHLY_CAP = "monthly cap reached"

VISION_OFF_NO_KEY = "Vision off - add your API key in Settings"
SUBSCRIPTION_NOTE = ("Subscription sign-in (for example Claude Pro or Max) is not supported - API keys only. Create an API key in the "
                     "provider's developer console; the provider bills you directly for what you use.")
PRIVACY_NOTE = ("When Vision reviews a chart, the chart image and the minimal context needed (symbol, timeframe, whether it is a "
                "proposal, a scan alert or your daily review) are sent only to the provider you chose, under your own key. Your key "
                "is never used for anyone else. Chart images are not kept; you can delete any report.")
SESSION_TOKEN_MESSAGE = ("This looks like a sign-in session, cookie or OAuth token, not an API key. Subscription sign-in is not "
                         "supported - create an API key in the provider's developer console and paste that.")

MIN_KEY_LENGTH = 20
MAX_KEY_LENGTH = 256
_KEY_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")
# Prefixes of things people paste that are not API keys: Claude.ai session keys and OAuth tokens (subscription sign-in),
# Google OAuth access / refresh tokens, legacy OpenAI browser session tokens.
_SESSION_PREFIXES = ("sk-ant-sid", "sk-ant-oat", "sk-ant-ort", "ya29.", "1//", "sess-", "eyj", "bearer ")
_SESSION_MARKERS = ("session", "cookie", "oauth", "refresh_token", "access_token")
_ADMIN_PREFIXES = ("sk-ant-admin", "sk-admin-")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{4,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{8,}"),
    re.compile(r"ya29\.[0-9A-Za-z_\-.]+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)\b(x-goog-api-key|x-api-key|api[_-]?key|key|token)\s*[=:]\s*\S+"),
)
ERROR_MAX = 200


class ByokError(ValueError):
    """A refusal the user can act on. The message never contains the key."""


class KeyBindingError(ValueError):
    """A stored key that does not belong to the caller (copied row, other tenant, other provider) - never used."""


class CapReached(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- key shape, hint, sealing -------------------------------------------------------------------------------------------

def _looks_like_session_token(key: str) -> bool:
    lower = key.lower()
    if lower.startswith(_SESSION_PREFIXES) or any(marker in lower for marker in _SESSION_MARKERS):
        return True
    if "=" in key or ";" in key or key.count(".") >= 2:          # a cookie header, a JWT
        return True
    return False


def validate_key_shape(provider: str, raw: Optional[str]) -> str:
    """The cleaned key, or ByokError. Refuses session cookies / OAuth / subscription tokens, admin keys, the wrong
    provider's key and anything outside the length or character limits."""
    provider = (provider or "").lower()
    if provider not in BYOK_PROVIDERS:
        raise ByokError(f"Unknown provider; one of {', '.join(BYOK_PROVIDERS)}")
    key = (raw or "").strip()
    if not key:
        raise ByokError("Paste an API key")
    if _looks_like_session_token(key):
        raise ByokError(SESSION_TOKEN_MESSAGE)
    if len(key) < MIN_KEY_LENGTH or len(key) > MAX_KEY_LENGTH:
        raise ByokError(f"An API key is {MIN_KEY_LENGTH} to {MAX_KEY_LENGTH} characters long")
    if not _KEY_CHARS.match(key):
        raise ByokError("An API key contains only letters, digits, '-' and '_' - paste the key alone, without quotes or spaces")
    lower = key.lower()
    if lower.startswith(_ADMIN_PREFIXES):
        raise ByokError("This is an organisation admin key. Use a standard API key from the provider's console")
    if provider == "anthropic" and not key.startswith("sk-ant-"):
        raise ByokError("An Anthropic API key starts with 'sk-ant-'")
    if provider == "openai" and (not key.startswith("sk-") or key.startswith("sk-ant-")):
        raise ByokError("An OpenAI API key starts with 'sk-'")
    if provider == "gemini" and key.startswith("sk-"):
        raise ByokError("This looks like an Anthropic or OpenAI key, not a Google Gemini key")
    return key


def key_hint(key: str) -> str:
    """First 4 and last 4 characters only."""
    return f"{key[:4]}...{key[-4:]}"


def seal_key(key: str, *, tenant_id: int, user_id: int, provider: str) -> str:
    payload = json.dumps({"v": 1, "t": int(tenant_id), "u": int(user_id), "p": provider, "k": key}, separators=(",", ":"))
    return encrypt_text(payload, tenant_id, PURPOSE_BYOK_AI_KEY)


def open_key(record: UserAiKeyRecord, *, user: User) -> str:
    """The plaintext key of the caller's own row. Refuses a row of another user / organisation and a ciphertext that was
    sealed for another user, organisation or provider (a copied row)."""
    if int(record.user_id) != int(user.id) or int(record.tenant_id) != int(user.tenant_id):
        raise KeyBindingError("This key belongs to another user")
    try:
        plain = decrypt_text(record.encrypted_key, PURPOSE_BYOK_AI_KEY, tenant_id=record.tenant_id)
        data = json.loads(plain)
    except ValueError as exc:
        raise KeyBindingError("The stored key could not be opened - add it again") from exc
    if (not isinstance(data, dict) or data.get("t") != int(record.tenant_id) or data.get("u") != int(record.user_id)
            or data.get("p") != record.provider or not isinstance(data.get("k"), str) or not data.get("k")):
        raise KeyBindingError("The stored key is bound to another user, organisation or provider - refusing to use it")
    return str(data["k"])


def sanitize_error(text: Any, secrets: Iterable[str] = ()) -> str:
    """Short, single-line error text with the key (and anything key- or token-shaped) removed."""
    out = str(text or "")
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[redacted]", out)
    out = " ".join(out.split())
    return out[:ERROR_MAX]


# --- models, caps --------------------------------------------------------------------------------------------------------

def default_model(provider: str) -> str:
    return str(config.BYOK_VISION_DEFAULT_MODELS.get(provider) or "")


def model_choices(provider: str) -> List[str]:
    return list(config.BYOK_VISION_MODEL_CHOICES.get(provider) or [default_model(provider)])


def effective_model(record: Optional[UserAiKeyRecord], provider: str) -> str:
    return (record.model if record is not None and record.model else "") or default_model(provider)


def clean_model(provider: str, model: Optional[str]) -> Optional[str]:
    """None = leave unchanged; "" = the configured default; else one of the listed choices."""
    if model is None:
        return None
    value = model.strip()
    if not value or value == default_model(provider):
        return ""
    if value not in model_choices(provider):
        raise ByokError(f"Pick one of the listed models for {PROVIDER_LABELS.get(provider, provider)}")
    return value


def day_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _caps(prefs: Optional[UserVisionPrefsRecord]) -> Tuple[float, float]:
    if prefs is None:
        return float(config.BYOK_DEFAULT_DAILY_CAP_USD), float(config.BYOK_DEFAULT_MONTHLY_CAP_USD)
    return float(prefs.daily_cap_usd), float(prefs.monthly_cap_usd)


# --- queries (always the caller's user id AND tenant id) -----------------------------------------------------------------

async def get_prefs(session: AsyncSession, user: User) -> Optional[UserVisionPrefsRecord]:
    return await session.scalar(select(UserVisionPrefsRecord).where(UserVisionPrefsRecord.user_id == user.id,
                                                                   UserVisionPrefsRecord.tenant_id == user.tenant_id))


async def list_keys(session: AsyncSession, user: User) -> List[UserAiKeyRecord]:
    return list(await session.scalars(select(UserAiKeyRecord).where(UserAiKeyRecord.user_id == user.id,
                                                                     UserAiKeyRecord.tenant_id == user.tenant_id)))


async def get_key(session: AsyncSession, user: User, provider: str) -> Optional[UserAiKeyRecord]:
    return await session.scalar(select(UserAiKeyRecord).where(UserAiKeyRecord.user_id == user.id, UserAiKeyRecord.tenant_id == user.tenant_id,
                                                              UserAiKeyRecord.provider == provider))


async def byok_spend(session: AsyncSession, user_id: int, provider: Optional[str] = None, since: Optional[datetime] = None, *,
                     tenant_id: Optional[int] = None) -> float:
    """USD this user spent on their OWN keys (calls marked `billed_to_user_key`) since `since`, for one provider or all.
    The organisation is taken from the user row when not given, so a stray row under another tenant never counts."""
    if tenant_id is None:
        owner = await session.get(User, user_id)
        if owner is None:
            return 0.0
        tenant_id = owner.tenant_id
    query = select(func.coalesce(func.sum(LlmCallRecord.cost_usd), 0.0)).where(
        LlmCallRecord.user_id == user_id, LlmCallRecord.tenant_id == tenant_id, LlmCallRecord.billed_to_user_key.is_(True))
    if provider:
        query = query.where(LlmCallRecord.provider == provider)
    if since is not None:
        query = query.where(LlmCallRecord.created_at >= since)
    return float(await session.scalar(query) or 0.0)


def active_provider(prefs: Optional[UserVisionPrefsRecord], keys: List[UserAiKeyRecord], requested: Optional[str] = None) -> Optional[str]:
    """The provider the user's charts go to: the one asked for, else the one chosen in the prefs, else the first with a
    key. None when that provider has no key."""
    have = {k.provider for k in keys}
    wanted = requested or (prefs.provider if prefs is not None else None)
    if wanted:
        return wanted if wanted in have else None
    return next((p for p in BYOK_PROVIDERS if p in have), None)


async def cap_block(session: AsyncSession, user: User, prefs: Optional[UserVisionPrefsRecord], *, now: Optional[datetime] = None,
                    estimate_usd: float = 0.0) -> Optional[str]:
    """`daily cap reached` / `monthly cap reached` when this user's own-key spend (plus the next call's estimate) is at the
    cap; None otherwise. A hard stop: nothing runs past the cap until the day / month rolls over."""
    daily_cap, monthly_cap = _caps(prefs)
    extra = max(0.0, float(estimate_usd or 0.0))
    today = await byok_spend(session, user.id, None, day_start(now), tenant_id=user.tenant_id)
    if today >= daily_cap or today + extra > daily_cap:
        return REASON_DAILY_CAP
    month = await byok_spend(session, user.id, None, month_start(now), tenant_id=user.tenant_id)
    if month >= monthly_cap or month + extra > monthly_cap:
        return REASON_MONTHLY_CAP
    return None


async def byok_allowed(session: AsyncSession, user_id: int, provider: Optional[str] = None, *, now: Optional[datetime] = None,
                       estimate_usd: float = 0.0) -> Tuple[bool, str]:
    """The gate before every Vision audit (V2/V3): (True, "ok") or (False, "vision off" | "no key" | "daily cap reached" |
    "monthly cap reached"). Vision off covers the operator flag, the user's own switch and an inactive account."""
    from app.platform.controls import flag_enabled
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return False, REASON_VISION_OFF
    if not await flag_enabled(session, FLAG, user.tenant_id):
        return False, REASON_VISION_OFF
    prefs = await get_prefs(session, user)
    if prefs is None or not prefs.enabled:
        return False, REASON_VISION_OFF
    if active_provider(prefs, await list_keys(session, user), provider) is None:
        return False, REASON_NO_KEY
    blocked = await cap_block(session, user, prefs, now=now, estimate_usd=estimate_usd)
    if blocked:
        return False, blocked
    return True, REASON_OK


# --- writes ---------------------------------------------------------------------------------------------------------------

async def _prefs_or_new(session: AsyncSession, user: User) -> UserVisionPrefsRecord:
    prefs = await get_prefs(session, user)
    if prefs is None:
        prefs = UserVisionPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, enabled=False, provider=None,
                                      daily_cap_usd=float(config.BYOK_DEFAULT_DAILY_CAP_USD), monthly_cap_usd=float(config.BYOK_DEFAULT_MONTHLY_CAP_USD),
                                      audit_proposals=True, audit_scan_alerts=False, audit_daily_review=False, channel_telegram=False, channel_in_app=True)
        session.add(prefs)
    return prefs


async def save_key(session: AsyncSession, user: User, *, provider: str, api_key: str, model: Optional[str] = None) -> UserAiKeyRecord:
    provider = (provider or "").lower()
    key = validate_key_shape(provider, api_key)
    chosen = clean_model(provider, model)
    record = await get_key(session, user, provider)
    if record is None:
        record = UserAiKeyRecord(tenant_id=user.tenant_id, user_id=user.id, provider=provider, model="")
        session.add(record)
    record.encrypted_key = seal_key(key, tenant_id=user.tenant_id, user_id=user.id, provider=provider)
    record.key_hint = key_hint(key)
    if chosen is not None:
        record.model = chosen
    record.last_tested_at = None
    record.last_test_ok = None
    record.last_test_error = None
    await write_audit_log(session, user.tenant_id, user.id, "byok_key_saved", f"{provider} model={chosen or record.model or 'default'}")
    await session.commit()
    await session.refresh(record)
    return record


async def delete_key(session: AsyncSession, user: User, provider: str) -> bool:
    record = await get_key(session, user, (provider or "").lower())
    if record is None:
        return False
    await session.delete(record)
    prefs = await get_prefs(session, user)
    if prefs is not None and prefs.provider == record.provider:
        prefs.provider = None
    await write_audit_log(session, user.tenant_id, user.id, "byok_key_removed", record.provider)
    await session.commit()
    return True


async def test_key(session: AsyncSession, user: User, provider: str, *, client: Optional[Any] = None,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """One tiny call through the provider seam with the caller's own key; the outcome is stored on the key row and the
    call is metered as billed to the user's key. Raises LookupError (no key) or CapReached (hard stop)."""
    provider = (provider or "").lower()
    record = await get_key(session, user, provider)
    if record is None:
        raise LookupError("No key stored for this provider")
    blocked = await cap_block(session, user, await get_prefs(session, user), now=now)
    if blocked:
        raise CapReached(blocked)
    model = effective_model(record, provider)
    key = ""
    result: Optional[Completion] = None
    ok = False
    error: Optional[str] = None
    called = False
    try:
        key = open_key(record, user=user)
        llm = ai_providers.build_byok_provider(provider, key, model, client=client)
        called = True
        result = await llm.complete_full(TEST_SYSTEM, TEST_USER, max_tokens=TEST_MAX_TOKENS)
        ok = True
    except KeyBindingError as exc:
        error = sanitize_error(str(exc), [key])
    except ProviderError as exc:
        error = sanitize_error(str(exc), [key])
        result = exc.usage
    except Exception as exc:  # noqa: BLE001 - an unexpected failure is reported by class name only (never its text)
        logger.warning("BYOK key test for user %s (%s) failed: %s", user.id, provider, exc.__class__.__name__)
        error = sanitize_error(f"Test failed: {exc.__class__.__name__}", [key])
    finally:
        key = ""
    cost = 0.0
    if result is not None and (result.input_tokens or result.output_tokens or result.cache_read_tokens):
        cost = pricing.cost_usd(provider, result.model or model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens).amount
    if called:
        try:
            await log_call(session, tenant_id=user.tenant_id, user_id=user.id, feature=FEATURE_TEST, provider=provider,
                           model=(result.model if result is not None and result.model else model), prompt_version=TEST_PROMPT_VERSION,
                           system=TEST_SYSTEM, user=TEST_USER, response=(result.text if ok and result is not None else None),
                           status="ok" if ok else f"error: {error}", result=result, cost_usd=cost, billed_to_user_key=True)
        except Exception as exc:  # noqa: BLE001 - the outcome is still stored on the key row
            logger.warning("BYOK test call not logged for user %s: %s", user.id, exc.__class__.__name__)
    stamp = now or datetime.now(timezone.utc)
    if called:
        record.last_used_at = stamp
    record.last_tested_at = stamp
    record.last_test_ok = ok
    record.last_test_error = None if ok else error
    await write_audit_log(session, user.tenant_id, user.id, "byok_key_tested", f"{provider}: {'ok' if ok else 'failed'}")
    await session.commit()
    return {"provider": provider, "ok": ok, "error": None if ok else error, "model": model, "cost_usd": round(cost, 8)}


async def save_prefs(session: AsyncSession, user: User, *, enabled: Optional[bool] = None, provider: Optional[str] = None,
                     clear_provider: bool = False, daily_cap_usd: Optional[float] = None, monthly_cap_usd: Optional[float] = None,
                     audit: Optional[Dict[str, bool]] = None, channels: Optional[Dict[str, bool]] = None,
                     models: Optional[Dict[str, str]] = None) -> UserVisionPrefsRecord:
    keys = {k.provider: k for k in await list_keys(session, user)}
    prefs = await _prefs_or_new(session, user)
    daily = float(prefs.daily_cap_usd if daily_cap_usd is None else daily_cap_usd)
    monthly = float(prefs.monthly_cap_usd if monthly_cap_usd is None else monthly_cap_usd)
    ceiling = float(config.BYOK_MAX_CAP_USD)
    for label, value in (("daily", daily), ("monthly", monthly)):
        if not 0 <= value <= ceiling:
            raise ByokError(f"The {label} cap must be between 0 and {ceiling:g} USD")
    if daily > monthly:
        raise ByokError("The daily cap cannot be above the monthly cap")
    if provider is not None:
        provider = provider.lower()
        if provider not in BYOK_PROVIDERS:
            raise ByokError(f"Unknown provider; one of {', '.join(BYOK_PROVIDERS)}")
        if provider not in keys:
            raise ByokError(f"Add a key for {PROVIDER_LABELS[provider]} before choosing it")
    new_models: Dict[str, str] = {}
    for name, model in (models or {}).items():
        name = (name or "").lower()
        if name not in BYOK_PROVIDERS:
            raise ByokError(f"Unknown provider; one of {', '.join(BYOK_PROVIDERS)}")
        if name not in keys:
            raise ByokError(f"Add a key for {PROVIDER_LABELS[name]} before choosing its model")
        cleaned = clean_model(name, model)
        if cleaned is not None:
            new_models[name] = cleaned
    prefs.daily_cap_usd, prefs.monthly_cap_usd = daily, monthly
    if enabled is not None:
        prefs.enabled = bool(enabled)
    if clear_provider:
        prefs.provider = None
    elif provider is not None:
        prefs.provider = provider
    for field, attr in (("proposals", "audit_proposals"), ("scan_alerts", "audit_scan_alerts"), ("daily_review", "audit_daily_review")):
        if audit is not None and field in audit:
            setattr(prefs, attr, bool(audit[field]))
    for field, attr in (("telegram", "channel_telegram"), ("in_app", "channel_in_app")):
        if channels is not None and field in channels:
            setattr(prefs, attr, bool(channels[field]))
    for name, cleaned in new_models.items():
        keys[name].model = cleaned
    await write_audit_log(session, user.tenant_id, user.id, "byok_prefs_saved",
                          f"enabled={prefs.enabled} provider={prefs.provider or 'auto'} daily={daily:g} monthly={monthly:g}")
    await session.commit()
    await session.refresh(prefs)
    return prefs


# --- the status the Settings card shows -----------------------------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


async def status(session: AsyncSession, user: User, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the Settings card needs - the caller's rows only; never a key, only its hint."""
    from app.platform.controls import flag_enabled
    keys = {k.provider: k for k in await list_keys(session, user)}
    prefs = await get_prefs(session, user)
    since_month, since_day = month_start(now), day_start(now)
    providers: List[Dict[str, Any]] = []
    for name in BYOK_PROVIDERS:
        record = keys.get(name)
        providers.append({
            "provider": name, "label": PROVIDER_LABELS[name], "console_url": CONSOLE_URLS[name],
            "key_set": record is not None, "key_hint": record.key_hint if record is not None else None,
            "model": effective_model(record, name), "model_pinned": bool(record is not None and record.model),
            "default_model": default_model(name), "models": model_choices(name),
            "last_used_at": _iso(record.last_used_at) if record is not None else None,
            "last_tested_at": _iso(record.last_tested_at) if record is not None else None,
            "last_test_ok": record.last_test_ok if record is not None else None,
            "last_test_error": record.last_test_error if record is not None else None,
            "month_spend_usd": round(await byok_spend(session, user.id, name, since_month, tenant_id=user.tenant_id), 6) if record is not None else 0.0,
        })
    daily_cap, monthly_cap = _caps(prefs)
    today = await byok_spend(session, user.id, None, since_day, tenant_id=user.tenant_id)
    month = await byok_spend(session, user.id, None, since_month, tenant_id=user.tenant_id)
    allowed, reason = await byok_allowed(session, user.id, now=now)
    chosen = active_provider(prefs, list(keys.values()))
    if not keys:
        line = VISION_OFF_NO_KEY
    elif prefs is None or not prefs.enabled:
        line = "Vision off - turn it on below"
    elif chosen is None:
        line = VISION_OFF_NO_KEY
    elif reason in (REASON_DAILY_CAP, REASON_MONTHLY_CAP):
        line = f"Vision paused - {reason}; it resumes when the {'day' if reason == REASON_DAILY_CAP else 'month'} rolls over (UTC)"
    else:
        line = f"Vision on - {PROVIDER_LABELS[chosen]}, model {effective_model(keys.get(chosen), chosen)}"
    return {
        "enabled": bool(prefs.enabled) if prefs is not None else False,
        "active": allowed, "reason": reason, "vision_line": line,
        "provider": prefs.provider if prefs is not None else None, "active_provider": chosen,
        "providers": providers,
        "caps": {"daily_cap_usd": daily_cap, "monthly_cap_usd": monthly_cap, "max_cap_usd": float(config.BYOK_MAX_CAP_USD)},
        "spend": {"today_usd": round(today, 6), "month_usd": round(month, 6), "month": since_month.strftime("%Y-%m"),
                  "usd_inr_rate": pricing.usd_inr_rate(), "resets": "daily at 00:00 UTC, monthly on the 1st (UTC)"},
        "audit": {"proposals": prefs.audit_proposals if prefs is not None else True,
                  "scan_alerts": prefs.audit_scan_alerts if prefs is not None else False,
                  "daily_review": prefs.audit_daily_review if prefs is not None else False},
        "channels": {"telegram": prefs.channel_telegram if prefs is not None else False,
                     "in_app": prefs.channel_in_app if prefs is not None else True},
        "notes": {"subscription": SUBSCRIPTION_NOTE, "privacy": PRIVACY_NOTE},
        # BYOK-V3: what the scan-alert and daily-review scopes do, so the Settings card can say it.
        "scope": {"v3_enabled": await flag_enabled(session, "vision_byok_v3", user.tenant_id),
                  "scan_audits_per_hour": int(config.BYOK_SCAN_AUDITS_PER_HOUR), "daily_review_at": config.BYOK_DAILY_REVIEW_AT,
                  "daily_review_timezone": "Asia/Kolkata", "daily_review_max_symbols": int(config.BYOK_DAILY_REVIEW_MAX_SYMBOLS),
                  "daily_review_timeframe": config.BYOK_DAILY_REVIEW_TIMEFRAME},
    }
