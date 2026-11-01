"""M73 SL-1: the provider-generic sign-in layer (GS-1's Google flow, made provider-generic; Facebook added).

A provider is one registry entry (`Provider`): its id ("google", "facebook"), its kind (`oidc` - an ID token with a
nonce; `oauth2` - an access token that reads the person's profile once; `broker` - reserved for M74 "Continue with
your broker": a callback that may carry no e-mail and that hands a broker session to `on_signed_in`), whether its
credentials are set, the authorization URL and the code -> `ProviderIdentity` exchange. The routes
(app/auth/social_routes.py) are the same for every provider: start (state + PKCE verifier, + nonce for OIDC, in a signed
short-lived HttpOnly cookie), callback (state check, exchange, then sign in / pending link / sign-up), the pending-step
endpoints and Settings › Account link / unlink.

Accounts: `user_identities` (provider + subject unique: one provider account = one ATP account; one provider per user).
A sign-in with a linked identity signs that account in. A provider-verified e-mail that matches an account without that
link never signs in silently: it becomes a pending link that the account's password (and two-factor code) confirms.

Nothing here stores a provider token; `ProviderIdentity.session_token` (broker only) is handed to `on_signed_in` and
dropped.
"""
import hmac
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth import facebook, google
from app.auth.google import Flow, GoogleSignInError
from app.core import config
from app.core.config import JWT_ALGORITHM
from app.db.models import User, UserIdentityRecord

SignInError = GoogleSignInError          # one refusal type with a fixed short code, for every provider

KIND_OIDC = "oidc"
KIND_OAUTH2 = "oauth2"
KIND_BROKER = "broker"                   # M74 (not built here): the registry accepts it; see docs/design/M73_SOCIAL_LOGIN.md

INTENTS = ("signin", "signup", "invite", "link")
FLOW_TTL_SECONDS = google.FLOW_TTL_SECONDS
PENDING_TTL_SECONDS = 600
PENDING_PURPOSE = "social_pending"


@dataclass(frozen=True)
class ProviderIdentity:
    """What a provider proved. `email` None = the provider gave none (Facebook may not; a broker usually does not)."""
    provider: str
    subject: str
    email: str | None = None
    email_verified: bool = False
    session_token: Any = field(default=None, repr=False, compare=False)   # M74 broker: for on_signed_in only, never stored


OnSignedIn = Callable[[AsyncSession, User, ProviderIdentity, Request], Awaitable[None]]


@dataclass(frozen=True)
class Provider:
    id: str                    # registry id, stored in user_identities.provider ("google"; M74 "broker:upstox")
    slug: str                  # the path segment: /api/auth/<slug>/start | /callback | /link
    kind: str                  # KIND_OIDC | KIND_OAUTH2 | KIND_BROKER
    label: str                 # the plain name the page shows ("Google")
    configured: Callable[[], bool]
    callback_path: str
    authorization_url: Callable[[Flow, str], str]
    identity_for_code: Callable[[str, Flow, str], Awaitable[ProviderIdentity]]
    on_signed_in: OnSignedIn | None = None     # generic hook after a sign-in / sign-up (M74 hands the broker session)

    @property
    def uses_nonce(self) -> bool:
        return self.kind == KIND_OIDC

    @property
    def flow_cookie(self) -> str:
        return f"atp_{self.slug}_flow"             # GS-1: "atp_google_flow"

    @property
    def flow_cookie_path(self) -> str:
        return f"/api/auth/{self.slug}"            # GS-1: "/api/auth/google"

    @property
    def flow_purpose(self) -> str:
        return f"{self.id}_signin_flow"            # GS-1: "google_signin_flow"

    def redirect_uri(self, request: Request) -> str:
        base = config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
        return base + self.callback_path


# --- the registry -----------------------------------------------------------------------------------------------

REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    if provider.kind not in (KIND_OIDC, KIND_OAUTH2, KIND_BROKER):
        raise ValueError(f"unknown provider kind {provider.kind!r}")
    REGISTRY[provider.id] = provider
    return provider


def get(provider_id: str) -> Provider | None:
    return REGISTRY.get(provider_id)


def by_slug(slug: str) -> Provider | None:
    return next((p for p in REGISTRY.values() if p.slug == slug), None)


def live() -> list[Provider]:
    """The providers whose credentials are set (the sign-in buttons, the admin health row)."""
    return [p for p in REGISTRY.values() if p.configured()]


async def _google_identity(code: str, flow: Flow, redirect: str) -> ProviderIdentity:
    found = await google.identity_for_code(code, flow, redirect)     # GS-1: verifies the ID token and email_verified
    return ProviderIdentity(provider="google", subject=found.subject, email=found.email, email_verified=True)


async def _facebook_identity(code: str, flow: Flow, redirect: str) -> ProviderIdentity:
    found = await facebook.identity_for_code(code, flow, redirect)
    # Facebook returns only a confirmed primary e-mail (or none).
    return ProviderIdentity(provider="facebook", subject=found["subject"], email=found["email"],
                            email_verified=found["email"] is not None)


register(Provider(id="google", slug="google", kind=KIND_OIDC, label="Google", configured=google.configured,
                  callback_path=google.CALLBACK_PATH, authorization_url=google.authorization_url,
                  identity_for_code=_google_identity))
register(Provider(id="facebook", slug="facebook", kind=KIND_OAUTH2, label="Facebook", configured=facebook.configured,
                  callback_path=facebook.CALLBACK_PATH, authorization_url=facebook.authorization_url,
                  identity_for_code=_facebook_identity))


# --- the flow cookie (state, nonce, PKCE verifier, and what the attempt is for) ----------------------------------

@dataclass(frozen=True)
class SocialFlow:
    flow: Flow
    intent: str = "signin"
    invite: str | None = None
    link_user_id: int | None = None


def encode_flow(provider: Provider, social_flow: SocialFlow) -> str:
    now = datetime.now(UTC)
    f = social_flow.flow
    payload: dict[str, Any] = {"purpose": provider.flow_purpose, "state": f.state, "nonce": f.nonce, "verifier": f.verifier,
                               "intent": social_flow.intent, "iat": now, "exp": now + timedelta(seconds=FLOW_TTL_SECONDS)}
    if social_flow.invite:
        payload["invite"] = social_flow.invite
    if social_flow.link_user_id is not None:
        payload["link_user_id"] = social_flow.link_user_id
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_flow(provider: Provider, value: str | None) -> SocialFlow | None:
    if not value:
        return None
    try:
        payload = jwt.decode(value, config.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], options={"require": ["exp"]})
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != provider.flow_purpose:
        return None                                   # another provider's (or another purpose's) cookie
    try:
        flow = Flow(state=str(payload["state"]), nonce=str(payload["nonce"]), verifier=str(payload["verifier"]))
    except KeyError:
        return None
    intent = payload.get("intent") if payload.get("intent") in INTENTS else "signin"
    link_user_id = payload.get("link_user_id")
    return SocialFlow(flow=flow, intent=intent, invite=payload.get("invite") if isinstance(payload.get("invite"), str) else None,
                      link_user_id=int(link_user_id) if isinstance(link_user_id, int) else None)


def state_matches(social_flow: SocialFlow | None, presented: str | None) -> bool:
    return bool(social_flow and presented and hmac.compare_digest(social_flow.flow.state.encode(), presented.encode()))


def new_flow(intent: str = "signin", invite: str | None = None, link_user_id: int | None = None) -> SocialFlow:
    return SocialFlow(flow=google.new_flow(), intent=intent if intent in INTENTS else "signin",
                      invite=invite, link_user_id=link_user_id)


# --- pending steps (a link to confirm, a sign-up to finish) ------------------------------------------------------

@dataclass(frozen=True)
class Pending:
    kind: str                     # "link" (confirm with the password) | "signup" (terms / beta code / an e-mail)
    provider: str
    subject: str
    email: str | None
    email_verified: bool
    intent: str = "signin"
    invite: str | None = None
    user_id: int | None = None


def encode_pending(p: Pending) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"purpose": PENDING_PURPOSE, "kind": p.kind, "provider": p.provider, "subject": p.subject,
                       "email": p.email, "email_verified": p.email_verified, "intent": p.intent, "invite": p.invite,
                       "user_id": p.user_id, "jti": secrets.token_urlsafe(8), "iat": now,
                       "exp": now + timedelta(seconds=PENDING_TTL_SECONDS)},
                      config.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_pending(value: str | None, kind: str | None = None) -> Pending | None:
    if not value or len(value) > 4096:
        return None
    try:
        payload = jwt.decode(value, config.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], options={"require": ["exp"]})
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != PENDING_PURPOSE or payload.get("kind") not in ("link", "signup"):
        return None
    if kind is not None and payload.get("kind") != kind:
        return None
    if get(str(payload.get("provider"))) is None or not payload.get("subject"):
        return None
    user_id = payload.get("user_id")
    return Pending(kind=payload["kind"], provider=str(payload["provider"]), subject=str(payload["subject"]),
                   email=payload.get("email") if isinstance(payload.get("email"), str) else None,
                   email_verified=payload.get("email_verified") is True,
                   intent=payload.get("intent") if payload.get("intent") in INTENTS else "signin",
                   invite=payload.get("invite") if isinstance(payload.get("invite"), str) else None,
                   user_id=int(user_id) if isinstance(user_id, int) else None)


def identity_of(p: Pending) -> ProviderIdentity:
    return ProviderIdentity(provider=p.provider, subject=p.subject, email=p.email, email_verified=p.email_verified)


# --- user_identities ---------------------------------------------------------------------------------------------

async def find_identity(session: AsyncSession, provider: str, subject: str) -> UserIdentityRecord | None:
    return await session.scalar(select(UserIdentityRecord).where(UserIdentityRecord.provider == provider,
                                                                 UserIdentityRecord.subject == subject))


async def identities_of(session: AsyncSession, user_id: int) -> list[UserIdentityRecord]:
    return list(await session.scalars(select(UserIdentityRecord).where(UserIdentityRecord.user_id == user_id)
                                      .order_by(UserIdentityRecord.id)))


async def link_identity(session: AsyncSession, user: User, identity: ProviderIdentity) -> UserIdentityRecord:
    """Adds the link and its audit row (`social_link`, detail = the provider). Does not commit."""
    record = UserIdentityRecord(user_id=user.id, provider=identity.provider, subject=identity.subject[:255],
                                email_at_link=(identity.email or None) and identity.email[:320])
    session.add(record)
    await session.flush()
    await write_audit_log(session, user.tenant_id, user.id, "social_link", identity.provider)
    return record


def public_providers() -> list[dict[str, str]]:
    """The live providers as the sign-in page needs them: id, label, kind and the start path - nothing secret."""
    return [{"id": p.id, "label": p.label, "kind": p.kind, "start": f"/auth/{p.slug}/start"} for p in live()]


__all__ = ["Provider", "ProviderIdentity", "SignInError", "SocialFlow", "Pending", "REGISTRY", "register", "get", "by_slug",
           "live", "encode_flow", "decode_flow", "state_matches", "new_flow", "encode_pending", "decode_pending",
           "identity_of", "find_identity", "identities_of", "link_identity", "public_providers",
           "KIND_OIDC", "KIND_OAUTH2", "KIND_BROKER"]
