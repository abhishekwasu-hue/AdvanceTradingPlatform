"""M74: "Continue with your broker" - the broker sign-in providers (docs/design/M74_BROKER_SIGNIN.md).

One `BrokerSignInProvider` per broker whose documentation allows a redirect login through a platform app (BL-4: Upstox,
Zerodha Kite, Fyers). Each is a provider of kind `broker:<id>` for the M73 provider registry; this module adds no
second provider layer - it holds only what is broker-specific:

* `configured()` - ATP's own platform app for that broker is set AND approved (`config.BROKER_SIGNIN_APPS`); a broker
  that is not configured is simply absent from `configured_brokers()`, so the sign-in page never shows it (BL-1).
* `authorization_url()` - the broker's login page, carrying the `state` the M73 flow cookie holds.
* `identity_for_code()` - the one-time code exchanged with the EXISTING adapter (`authenticate()` + `get_profile()`),
  using the platform key + secret: the broker user id, email, name and that day's session token.
* `store_signin_session()` - that token stored as today's broker session through the existing store
  (`broker_credentials`, encrypted per tenant, VALID until the broker's daily expiry), so the broker is connected for
  data and trading at once (BL-2). The platform secret is never written to a tenant's row.

Nothing here logs or returns a code, a token or a secret; failures carry only a fixed short code (BL-5).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import ensure_account_for_credential
from app.audit.log import write_audit_log
from app.auth import social
from app.brokers.models import BrokerCredentials
from app.brokers.registry import get_broker_adapter
from app.brokers.token_lifecycle import (
    FYERS_AUTHORIZE_URL,
    KITE_AUTHORIZE_URL,
    build_upstox_authorization_url,
    cap_expiry_at_token_exp,
    get_credential_record,
    load_credentials,
    store_access_token,
)
from app.core import config
from app.db.models import BrokerCredentialRecord, User
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL, ensure_tenant_key

KIND_PREFIX = "broker:"
CALLBACK_PATH = "/api/auth/broker/{broker}/callback"
MAX_CODE_LENGTH = 4096
# The label for the row a sign-in stores: `primary` unless that row holds the user's OWN keys or another broker
# account's sign-in session (never overwritten); then this one (OPEN_QUESTIONS M74-1).
PRIMARY_LABEL = "primary"
SIGNIN_LABEL = "broker-login"


class BrokerSignInError(social.SignInError):
    """A refusal with one fixed short code the sign-in page words in plain English - never the broker's raw text. A
    SignInError, so the M73 callback turns it into its `?social_error=<code>` redirect."""


@dataclass(frozen=True)
class PlatformApp:
    api_key: str
    api_secret: str = field(repr=False)


@dataclass(frozen=True)
class BrokerIdentity:
    """What the broker said about the person who logged in. `subject` is the broker's own user id (one broker identity
    -> one ATP account, M73 `user_identities`); `email` is lower-cased, or None when the broker returned none."""

    kind: str
    broker: str
    subject: str
    email: str | None
    name: str | None
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class BrokerSignInProvider:
    broker: str
    display_name: str
    # The query parameter of the broker's redirect that carries the one-time code.
    code_param: str
    # A monogram badge: no broker logo is shipped (no logo licence was found that allows it; see the design doc).
    initials: str

    @property
    def kind(self) -> str:
        return KIND_PREFIX + self.broker

    @property
    def callback_path(self) -> str:
        return CALLBACK_PATH.format(broker=self.broker)

    def platform_app(self) -> PlatformApp | None:
        """ATP's platform app at this broker, only when its key, its secret and the owner's approval are all set."""
        app = (getattr(config, "BROKER_SIGNIN_APPS", {}) or {}).get(self.broker) or {}
        key, secret = (app.get("api_key") or "").strip(), (app.get("api_secret") or "").strip()
        if not (key and secret and app.get("approved")):
            return None
        return PlatformApp(api_key=key, api_secret=secret)

    def configured(self) -> bool:
        """The app is set and approved, and there is a public origin to build the redirect URL from (hardened
        environments need PUBLIC_BASE_URL, as Google sign-in does)."""
        if self.platform_app() is None:
            return False
        return bool(config.PUBLIC_BASE_URL) or config.ENVIRONMENT not in config.HARDENED_ENVIRONMENTS

    def redirect_uri(self, base_url: str) -> str:
        """PUBLIC_BASE_URL (or, on a dev box, the address the request reached) + this broker's sign-in callback path."""
        return (config.PUBLIC_BASE_URL or base_url).rstrip("/") + self.callback_path

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        app = self._require_app()
        if self.broker == "upstox":
            return build_upstox_authorization_url(app.api_key, redirect_uri, state)
        if self.broker == "zerodha":
            # Kite uses the redirect URL registered on the app and hands `redirect_params` back on the redirect.
            return f"{KITE_AUTHORIZE_URL}?{urlencode({'v': '3', 'api_key': app.api_key, 'redirect_params': urlencode({'state': state})})}"
        if self.broker == "fyers":
            return f"{FYERS_AUTHORIZE_URL}?{urlencode({'client_id': app.api_key, 'redirect_uri': redirect_uri, 'response_type': 'code', 'state': state})}"
        raise BrokerSignInError("not_configured")

    def refused(self, params: Mapping[str, str]) -> bool:
        """The broker's redirect says the person cancelled or the login did not complete."""
        if self.broker == "upstox":
            return bool(params.get("error"))
        if self.broker == "zerodha":
            status = params.get("status")
            return status is not None and status.lower() != "success"
        if self.broker == "fyers":
            status = params.get("s")
            return status is not None and status.lower() != "ok"
        return False

    def code_from(self, params: Mapping[str, str]) -> str:
        """The one-time code from the redirect's query, or a refusal code (`cancelled`, `failed`)."""
        if self.refused(params):
            raise BrokerSignInError("cancelled")
        code = params.get(self.code_param) or ""
        if not code or len(code) > MAX_CODE_LENGTH or any(c.isspace() for c in code):
            raise BrokerSignInError("failed")
        return code

    async def identity_for_code(self, code: str, redirect_uri: str,
                                client: httpx.AsyncClient | None = None) -> BrokerIdentity:
        """Exchanges the code with the existing adapter (the same exchange the daily connect uses) and reads the profile.
        Any broker failure, a profile without a user id or a session without a token is `failed`."""
        app = self._require_app()
        credentials = BrokerCredentials(api_key=app.api_key, api_secret=app.api_secret, request_token=code,
                                        redirect_uri=redirect_uri)
        try:
            adapter = get_broker_adapter(self.broker, credentials, client or http_client(self.broker))
            profile = await adapter.authenticate()
        except Exception as exc:  # noqa: BLE001 - one fixed code; the broker's text may echo the code or the secret
            raise BrokerSignInError("failed") from exc
        token = adapter.access_token
        subject = str(profile.user_id or "").strip()
        if not token or not subject:
            raise BrokerSignInError("failed")
        return BrokerIdentity(kind=self.kind, broker=self.broker, subject=subject, email=clean_email(profile.email),
                              name=(profile.name or "").strip() or None, access_token=token)

    def _require_app(self) -> PlatformApp:
        app = self.platform_app()
        if app is None:
            raise BrokerSignInError("not_configured")
        return app


def http_client(broker: str) -> httpx.AsyncClient | None:
    """The HTTP client for a sign-in exchange: None = the adapter's own (tests serve the broker from a mock transport)."""
    return None


PROVIDERS: dict[str, BrokerSignInProvider] = {
    p.broker: p for p in (
        BrokerSignInProvider("upstox", "Upstox", "code", "UP"),
        BrokerSignInProvider("zerodha", "Zerodha", "request_token", "Z"),
        BrokerSignInProvider("fyers", "Fyers", "auth_code", "FY"),
    )
}


def provider_for(kind_or_broker: str) -> BrokerSignInProvider | None:
    """The provider for `broker:<id>` or `<id>`; None for anything else (an unknown or own-keys-only broker)."""
    broker = kind_or_broker[len(KIND_PREFIX):] if kind_or_broker.startswith(KIND_PREFIX) else kind_or_broker
    return PROVIDERS.get(broker)


def configured_brokers() -> list[dict[str, str]]:
    """The sign-in page's broker list (BL-1): only brokers whose platform app is set and approved. No ids beyond the
    provider kind, no "coming soon" rows."""
    return [{"kind": p.kind, "broker": p.broker, "name": p.display_name, "initials": p.initials}
            for p in PROVIDERS.values() if p.configured()]


def clean_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email or "@" not in email or len(email) > 254 or any(c.isspace() for c in email):
        return None
    return email


def _is_platform_row(record: BrokerCredentialRecord) -> str | None:
    """The broker user id a sign-in row was stored for; None for a row with the user's own keys (or unreadable)."""
    try:
        creds = load_credentials(record)
    except Exception:  # noqa: BLE001 - an unreadable payload is treated as someone's own keys: never overwritten
        return None
    extra = creds.model_extra or {}
    return str(extra.get("broker_user_id") or "") if extra.get("platform_app") else None


async def _target_record(session: AsyncSession, tenant_id: int, broker: str, subject: str) -> tuple[str, BrokerCredentialRecord | None]:
    """The row this broker account's sign-in session goes to: its own earlier sign-in row first, else a free label.
    A row with the user's OWN keys and another broker account's sign-in row are never overwritten."""
    rows = {label: await get_credential_record(session, tenant_id, broker, label) for label in (PRIMARY_LABEL, SIGNIN_LABEL)}
    for label, record in rows.items():
        if record is not None and _is_platform_row(record) == subject:
            return label, record
    for label, record in rows.items():
        if record is None:
            return label, None
    raise BrokerSignInError("no_free_slot")


async def store_signin_session(session: AsyncSession, user: User, identity: BrokerIdentity,
                               now: datetime | None = None) -> BrokerCredentialRecord:
    """Stores the sign-in's session token as that day's broker session for the user's organisation (BL-2), through the
    existing encrypted store: VALID until the broker's daily expiry (capped at a JWT `exp`), held by `user`. The row
    carries the platform API key (every call needs it with the token) and `platform_app`, never the platform secret.
    The caller commits."""
    provider = provider_for(identity.broker)
    app = provider.platform_app() if provider else None
    if provider is None or app is None:
        raise BrokerSignInError("not_configured")
    await ensure_tenant_key(session, user.tenant_id)
    label, record = await _target_record(session, user.tenant_id, identity.broker, identity.subject)
    credentials = BrokerCredentials(api_key=app.api_key, platform_app=True, broker_user_id=identity.subject)
    payload = encrypt_text(credentials.model_dump_json(exclude_none=True), user.tenant_id, PURPOSE_BROKER_CREDENTIAL)
    if record is None:
        record = BrokerCredentialRecord(tenant_id=user.tenant_id, user_id=user.id, broker_name=identity.broker,
                                        account_label=label, encrypted_payload=payload)
        session.add(record)
    else:
        record.encrypted_payload = payload
        record.user_id = user.id
    store_access_token(record, identity.access_token, now=now)
    cap_expiry_at_token_exp(record, identity.access_token)
    await session.flush()
    await ensure_account_for_credential(session, record)
    await write_audit_log(session, user.tenant_id, user.id, "broker_signin_session_stored", f"{identity.broker}/{label}")
    return record


# --- M73 registry wiring: one `broker:<id>` provider per broker, served at /api/auth/broker/<id>/start|link|callback ---

def _social_provider(p: BrokerSignInProvider) -> social.Provider:
    async def identity_for_code(code: str, flow: object, redirect: str) -> social.ProviderIdentity:
        found = await p.identity_for_code(code, redirect)
        # A broker's e-mail is the one on the person's KYC'd trading account (OPEN_QUESTIONS M74-3); a match with an
        # account that has not linked this broker is still a pending link its password confirms (M73 SL-2).
        return social.ProviderIdentity(provider=p.kind, subject=found.subject, email=found.email,
                                       email_verified=found.email is not None, session_token=found)

    async def on_signed_in(session: AsyncSession, user: User, identity: social.ProviderIdentity, request: object) -> None:
        found = identity.session_token
        if not isinstance(found, BrokerIdentity) or found.broker != p.broker:
            return
        try:
            await store_signin_session(session, user, found)
            await session.commit()
        except BrokerSignInError:
            await session.rollback()          # no free row / the app withdrawn: signed in, the broker stays as it was
        except Exception:
            await session.rollback()
            raise

    return social.Provider(
        id=p.kind, slug=f"broker/{p.broker}", kind=social.KIND_BROKER, label=p.display_name, configured=p.configured,
        callback_path=p.callback_path, authorization_url=lambda flow, redirect: p.authorization_url(redirect, flow.state),
        identity_for_code=identity_for_code, on_signed_in=on_signed_in, read_code=p.code_from,
    )


def register_providers() -> None:
    """Adds each broker to the M73 registry and serves its routes (import time, before main.py includes the router)."""
    from app.auth import social_routes
    for p in PROVIDERS.values():
        if social.get(p.kind) is None:
            social_routes.add_provider(_social_provider(p))


register_providers()
