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

Settings redesign part A ("Connect AI in one paste", docs/design/SETTINGS_REDESIGN_REVIEW.md R2-R6, behind the
`ai_one_key` flag): the key format comes from ONE table (`app.ai.key_formats`); "connected" means a `user_ai_keys` row
exists (`prefs.enabled` and the audit scopes only switch the background Vision reviews); the connect test also sends one
tiny image; a failure is stored and shown as one plain sentence per error kind (`PLAIN_ERRORS`); `connection()` is the
panel's summary; `copy_tenant_key()` lets the OWNER who entered the organisation's key use it for their own account too.

Vision only comments; it never places, blocks or changes an order (ADR-0006).
"""
import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import key_formats, pricing
from app.ai import providers as ai_providers
from app.ai.metering import log_call, month_start
from app.ai.providers import BYOK_PROVIDERS, Completion, ProviderError
from app.audit.log import write_audit_log
from app.core import config
from app.db.models import AiProviderConfigRecord, LlmCallRecord, User, UserAiKeyRecord, UserVisionPrefsRecord
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_AI_PROVIDER_KEY, PURPOSE_BYOK_AI_KEY

logger = logging.getLogger(__name__)

FLAG = "vision_byok"
FEATURE_TEST = "byok_test"
TEST_PROMPT_VERSION = "byok-test-v1"
TEST_SYSTEM = "You are a connectivity check for a trading dashboard. Answer with the single word OK."
TEST_USER = "Reply with OK."
TEST_MAX_TOKENS = 16
TEST_IMAGE_PROMPT_VERSION = "byok-test-v2-image"
TEST_IMAGE_USER = "This is a tiny test picture. Reply with OK."
# A constant 8 x 8 white PNG with one dark diagonal (109 bytes): the connect test checks the key can read a chart image.
TEST_IMAGE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAANElEQVR42m3OQQoAQAgCQPX/f3YPQkRul4KpkABso0q2SX4gF21Ka9NMx7S3tum8HlPniT2bBxgNciqs8AAAAABJRU5ErkJggg==")

# Settings redesign A (R6 / A4): the one plain sentence per error kind (app.ai.providers.ERROR_KINDS). Never the
# provider's own text.
PLAIN_ERRORS = {
    "auth": "This key did not work. Check that you copied all of it.",
    "no_credit": "This key has no credit left.",
    "rate": "Too many requests. Try again in a minute.",
    "no_vision": "This provider cannot read charts. Vision needs Gemini, Claude or OpenAI.",
    "model": "This provider could not answer right now. Try again later.",
    "unreachable": "The AI service could not be reached. Try again in a minute.",
    "other": "The AI service did not answer. Try again in a minute.",
    "binding": "Your saved key could not be opened. Paste it again.",
}


def plain_error(kind: Optional[str]) -> str:
    return PLAIN_ERRORS.get(kind or "other", PLAIN_ERRORS["other"])

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
# The prefixes of things people paste that are not API keys (sign-in sessions, OAuth tokens, admin keys) and of every
# provider's keys live in ONE table: app/ai/key_formats.py (review R5).
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
    """A cookie header, a JWT, an OAuth token or a sign-in session (the one table: app.ai.key_formats)."""
    return key_formats.looks_like_session(key)


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
    found = key_formats.detect_provider(key)
    if found.kind == "admin":
        raise ByokError("This is an organisation admin key. Use a standard API key from the provider's console")
    if found.kind == "unsupported":
        raise ByokError(unsupported_message(found.name))
    if provider == "anthropic" and found.provider != "anthropic":
        raise ByokError("An Anthropic API key starts with 'sk-ant-'")
    if provider == "openai" and found.provider != "openai":
        raise ByokError("An OpenAI API key starts with 'sk-'")
    if provider == "gemini" and found.provider in ("anthropic", "openai"):
        raise ByokError("This looks like an Anthropic or OpenAI key, not a Google Gemini key")
    return key


def unsupported_message(name: str) -> str:
    article = "an" if name[:1].upper() in "AEIOU" else "a"
    return f"This looks like {article} {name} key. AiAlgoDesk cannot use {name} keys yet. Use a Claude, OpenAI or Google Gemini key."


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
                   now: Optional[datetime] = None, with_image: bool = False, plain: bool = False) -> Dict[str, Any]:
    """One tiny call through the provider seam with the caller's own key; the outcome is stored on the key row and the
    call is metered as billed to the user's key. Raises LookupError (no key) or CapReached (hard stop).
    Settings redesign A: `with_image` sends the tiny constant PNG along when the provider reads images (A2); `plain`
    stores the plain sentence of the error kind on the row instead of the sanitised provider text (the connect panel)."""
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
    kind: Optional[str] = None
    image = with_image and provider in ai_providers.VISION_PROVIDERS
    prompt_version, test_user = (TEST_IMAGE_PROMPT_VERSION, TEST_IMAGE_USER) if image else (TEST_PROMPT_VERSION, TEST_USER)
    try:
        key = open_key(record, user=user)
        llm = ai_providers.build_byok_provider(provider, key, model, client=client)
        called = True
        if image:
            result = await llm.complete_full(TEST_SYSTEM, test_user, max_tokens=TEST_MAX_TOKENS, image_png=TEST_IMAGE_PNG)
        else:
            result = await llm.complete_full(TEST_SYSTEM, test_user, max_tokens=TEST_MAX_TOKENS)
        ok = True
    except KeyBindingError as exc:
        error, kind = sanitize_error(str(exc), [key]), "binding"
    except ProviderError as exc:
        error, kind = sanitize_error(str(exc), [key]), exc.kind
        result = exc.usage
    except Exception as exc:  # noqa: BLE001 - an unexpected failure is reported by class name only (never its text)
        logger.warning("BYOK key test for user %s (%s) failed: %s", user.id, provider, exc.__class__.__name__)
        error, kind = sanitize_error(f"Test failed: {exc.__class__.__name__}", [key]), "other"
    finally:
        key = ""
    if not ok and plain:
        error = plain_error(kind)
    cost = 0.0
    if result is not None and (result.input_tokens or result.output_tokens or result.cache_read_tokens):
        cost = pricing.cost_usd(provider, result.model or model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens).amount
    if called:
        try:
            await log_call(session, tenant_id=user.tenant_id, user_id=user.id, feature=FEATURE_TEST, provider=provider,
                           model=(result.model if result is not None and result.model else model), prompt_version=prompt_version,
                           system=TEST_SYSTEM, user=test_user, response=(result.text if ok and result is not None else None),
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
    return {"provider": provider, "ok": ok, "error": None if ok else error, "kind": None if ok else (kind or "other"),
            "model": model, "cost_usd": round(cost, 8), "image": image}


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


# --- Settings redesign part A: "Connect AI in one paste" (flag `ai_one_key`) ------------------------------------------------

CONNECT_LABELS = key_formats.PROVIDER_NAMES          # the plain names the panel shows: Claude / OpenAI / Google Gemini
FREE_TIER_WARNING = ("Free keys from {name} may be used to train its models. Do not paste anything private into the chat. "
                     "Chart analysis waits until you confirm the key is on a paid plan.")


def _hint_tail(hint: Optional[str]) -> str:
    """The last four characters of the stored hint (the panel shows "key ...a9F2")."""
    return (hint or "")[-4:]


async def connected_key(session: AsyncSession, user: User) -> Optional[UserAiKeyRecord]:
    """The key every AI feature of this user runs on: the provider chosen in the prefs, else the first one with a key.
    "Connected" is only this - a row in `user_ai_keys` (review R2); the Vision switch plays no part."""
    keys = await list_keys(session, user)
    provider = active_provider(await get_prefs(session, user), keys)
    return next((k for k in keys if k.provider == provider), None) if provider else None


async def consent_accepted(session: AsyncSession, user: User, provider: str) -> bool:
    from app.ai import compliance_terms as terms
    return await terms.latest(session, terms.KIND_BYOK, tenant_id=user.tenant_id, user_id=user.id,
                              version=terms.byok_consent_version(provider)) is not None


async def paid_plan_confirmed(session: AsyncSession, user: User, record: UserAiKeyRecord) -> bool:
    """True for a provider that does not train on its free tier, or once the user confirmed THIS key is on a paid plan."""
    from app.ai import compliance_terms as terms
    if not key_formats.MAY_TRAIN_ON_FREE_TIER.get(record.provider, True):
        return True
    return await terms.latest(session, terms.KIND_BYOK_PAID, tenant_id=user.tenant_id, user_id=user.id,
                              version=terms.byok_paid_plan_version(record.key_hint)) is not None


async def accept_consent(session: AsyncSession, user: User, provider: str, *, request: Optional[Any] = None) -> None:
    """Records the own-key consent for `provider` (once per text version and provider); does not commit."""
    from app.ai import compliance_terms as terms
    if not await consent_accepted(session, user, provider):
        await terms.accept(session, user, terms.KIND_BYOK, terms.byok_consent_version(provider), request=request)


async def confirm_paid_plan(session: AsyncSession, user: User, *, request: Optional[Any] = None) -> None:
    from app.ai import compliance_terms as terms
    record = await connected_key(session, user)
    if record is None:
        raise LookupError("No key is connected")
    if not await paid_plan_confirmed(session, user, record):
        await terms.accept(session, user, terms.KIND_BYOK_PAID, terms.byok_paid_plan_version(record.key_hint), request=request)
    await session.commit()


async def _tenant_key_offer(session: AsyncSession, user: User) -> Optional[AiProviderConfigRecord]:
    """The organisation's key that this user may copy to their own account: only the OWNER who entered it (review R1 /
    SR-8), only an Anthropic / OpenAI key that is set and enabled, and only while the user has no key of that provider."""
    from app.ai.settings import get_config
    from app.core.enums import UserRole
    if user.role != UserRole.OWNER.value:
        return None
    record = await get_config(session, user.tenant_id)
    if (record is None or not record.enabled or not record.encrypted_api_key or record.provider not in BYOK_PROVIDERS
            or record.updated_by is None or int(record.updated_by) != int(user.id)):
        return None
    if await get_key(session, user, record.provider) is not None:
        return None
    return record


async def connection(session: AsyncSession, user: User, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The Connect AI panel's summary - the caller's own rows only; never a key, only the last four characters."""
    from app.ai import compliance_terms as terms
    record = await connected_key(session, user)
    prefs = await get_prefs(session, user)
    daily_cap, monthly_cap = _caps(prefs)
    rate = pricing.usd_inr_rate()
    month = await byok_spend(session, user.id, None, month_start(now), tenant_id=user.tenant_id)
    today = await byok_spend(session, user.id, None, day_start(now), tenant_id=user.tenant_id)
    blocked = await cap_block(session, user, prefs, now=now) if record is not None else None
    limits = {"month_inr": round(month * rate, 2), "monthly_cap_inr": round(monthly_cap * rate, 2),
              "today_inr": round(today * rate, 2), "daily_cap_inr": round(daily_cap * rate, 2),
              "max_cap_inr": round(float(config.BYOK_MAX_CAP_USD) * rate, 2),
              "line": f"AI this month: ₹{month * rate:,.0f} of your ₹{monthly_cap * rate:,.0f} limit",
              "reached": None if blocked is None else ("day" if blocked == REASON_DAILY_CAP else "month")}
    offer = await _tenant_key_offer(session, user)
    out: Dict[str, Any] = {"connected": record is not None, "provider": None, "label": None, "key_end": None, "ok": None, "error": None,
                           "tested_at": None, "consent": None, "may_train": False, "paid_plan": True, "free_tier_warning": None,
                           "limits": limits,
                           "copy_tenant_key": ({"label": CONNECT_LABELS.get(offer.provider, offer.provider)} if offer is not None else None)}
    if record is None:
        return out
    label = CONNECT_LABELS.get(record.provider, record.provider)
    consent_ok = await consent_accepted(session, user, record.provider)
    may_train = bool(key_formats.MAY_TRAIN_ON_FREE_TIER.get(record.provider, True))
    error = None
    if record.last_test_ok is False:
        # A test from before the plain sentences (the Vision card) stored the provider's sanitised text: never shown here.
        error = record.last_test_error if record.last_test_error in PLAIN_ERRORS.values() else PLAIN_ERRORS["other"]
    out.update(provider=record.provider, label=label, key_end=_hint_tail(record.key_hint), ok=record.last_test_ok, error=error,
               tested_at=_iso(record.last_tested_at),
               consent={"accepted": consent_ok, "text": terms.byok_text(terms.KIND_BYOK, label)},
               may_train=may_train, paid_plan=await paid_plan_confirmed(session, user, record),
               paid_plan_text=terms.byok_text(terms.KIND_BYOK_PAID, label) if may_train else None,
               free_tier_warning=FREE_TIER_WARNING.format(name=label) if may_train else None)
    return out


def consent_line(provider: str) -> str:
    from app.ai import compliance_terms as terms
    return terms.byok_text(terms.KIND_BYOK, CONNECT_LABELS.get(provider, provider))


async def connect(session: AsyncSession, user: User, *, api_key: str, provider: Optional[str] = None, consent: bool = False,
                  client: Optional[Any] = None, request: Optional[Any] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Paste -> detect -> consent -> save (encrypted) -> test (text + one tiny image) -> the summary. The key becomes the
    one every AI feature of this user runs on. A failed test keeps the key (SR-7: the panel shows the plain error with
    Fix and Remove). Raises ByokError (a plain refusal) or ConsentRequired."""
    found = key_formats.detect_provider(api_key)
    if found.kind == "empty":
        raise ByokError("Paste an API key")
    if found.kind == "session":
        raise ByokError(SESSION_TOKEN_MESSAGE)
    if found.kind == "admin":
        raise ByokError("This is an organisation admin key. Use a standard API key from the provider's console")
    if found.kind == "unsupported":
        raise ByokError(unsupported_message(found.name))
    chosen = found.provider or (provider or "").lower()
    if not chosen:
        raise ProviderNeeded("Pick the service this key is from")
    if found.provider and provider and provider.lower() != found.provider:
        chosen = found.provider                          # the key's own format wins over a stale picker choice
    key = validate_key_shape(chosen, key_formats.clean(api_key))
    if not await consent_accepted(session, user, chosen):
        if not consent:
            raise ConsentRequired(consent_line(chosen))
        await accept_consent(session, user, chosen, request=request)
    await save_key(session, user, provider=chosen, api_key=key)
    key = ""
    prefs = await _prefs_or_new(session, user)
    prefs.provider = chosen                              # the newest key is the one in use; Vision's own switch is untouched
    await session.commit()
    try:
        await test_key(session, user, chosen, client=client, now=now, with_image=True, plain=True)
    except CapReached:
        pass                                             # nothing more may be spent today: the key stays, untested
    return await connection(session, user, now=now)


async def disconnect(session: AsyncSession, user: User) -> bool:
    record = await connected_key(session, user)
    if record is None:
        return False
    return await delete_key(session, user, record.provider)


async def save_limits_inr(session: AsyncSession, user: User, *, daily_inr: Optional[float], monthly_inr: Optional[float]) -> None:
    """The Limits row: the caps are typed in rupees and stored in USD (review A6)."""
    rate = pricing.usd_inr_rate()
    await save_prefs(session, user, daily_cap_usd=None if daily_inr is None else round(float(daily_inr) / rate, 6),
                     monthly_cap_usd=None if monthly_inr is None else round(float(monthly_inr) / rate, 6))


async def copy_tenant_key(session: AsyncSession, user: User, *, consent: bool = False, request: Optional[Any] = None) -> UserAiKeyRecord:
    """"Use this key for your account too" (one tap, never automatic): the organisation's key, entered by this OWNER, is
    re-sealed as this user's own key (bound to the user, tenant and provider) and audited. The organisation's row is not
    changed. Raises LookupError when there is nothing this user may copy."""
    offer = await _tenant_key_offer(session, user)
    if offer is None or not offer.encrypted_api_key:
        raise LookupError("There is no organisation key you can use here")
    if not consent and not await consent_accepted(session, user, offer.provider):
        raise ConsentRequired(consent_line(offer.provider))
    plain = decrypt_text(offer.encrypted_api_key, PURPOSE_AI_PROVIDER_KEY, offer.tenant_id)
    try:
        key = validate_key_shape(offer.provider, plain)
    finally:
        plain = ""
    await accept_consent(session, user, offer.provider, request=request)
    record = await save_key(session, user, provider=offer.provider, api_key=key)
    key = ""
    prefs = await _prefs_or_new(session, user)
    prefs.provider = offer.provider
    await write_audit_log(session, user.tenant_id, user.id, "byok_key_copied_from_tenant", offer.provider)
    await session.commit()
    return record


class ConsentRequired(ValueError):
    """The own-key consent line must be confirmed before the first save (A3). The message is the line to show."""


class ProviderNeeded(ValueError):
    """The key's format did not say which service it is from: the panel shows the small picker."""
