"""P0.8-D: the two acceptances the AI Copilot needs before it says anything, and the gate that enforces the first.

* **Copilot terms** (per user, versioned): the AI Copilot is not a SEBI-registered Investment Adviser or Research
  Analyst; it explains the platform's rules and data; the decision is the trader's. Every AI content route depends on
  `require_ai_acknowledged`, which answers 428 until the current version is accepted; the acceptance is a row in
  `ai_acknowledgements` and an audit event.
* **Data-sharing consent** (per organisation, by the owner, versioned): what leaves the platform when an external
  provider is configured (questions, interview answers including capital and experience, trade facts, market data,
  headlines - never broker credentials or keys), that the provider may process it outside India, and how to opt out
  (the rule-based provider). Saving an external provider requires it.
* **Your own key's consent** (per user and provider, versioned; Settings redesign A3, `byok_consent`): one plain line
  shown before the first save of a user's own AI key - what is sent to that provider, and that the broker login and
  passwords never are. The version carries the provider, so a key of another provider asks again.
* **"My key is on a paid plan"** (per user and key, `byok_paid_plan`; review R4 / SR-2): a provider whose free tier may
  train on what it is sent (Gemini) gets no ATP private method prompt (chart analysis) until the user confirms this for
  that key. The version carries the key hint, so a new key asks again.
"""
import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user
from app.db.models import AiAcknowledgementRecord, User
from app.db.session import get_session

KIND_COPILOT = "copilot_terms"
KIND_DATA = "data_consent"
KIND_BYOK = "byok_consent"
KIND_BYOK_PAID = "byok_paid_plan"
ACK_VERSION = "2026-10-07"
DATA_CONSENT_VERSION = "2026-10-07"
BYOK_CONSENT_VERSION = "2026-10-22"
BYOK_PAID_PLAN_VERSION = "p1"

ACK_TEXT = {
    "en": ("The AI Copilot is not a SEBI-registered Investment Adviser or Research Analyst and does not give investment advice. "
           "It explains the platform's rules, templates and data (backtests, market reads, risk settings) in plain words. "
           "Which strategy to run, with how much capital and whether to trade at all is your decision alone. Nothing it writes is a "
           "recommendation to buy, sell or hold any security, and past or simulated results do not predict future results. "
           "Paper trading first, then your own judgement."),
}
DATA_CONSENT_TEXT = {
    "en": ("When an external AI provider (Anthropic or OpenAI) is configured, the platform sends it the text needed to answer: your "
           "questions and messages, your interview answers (including capital, experience, risk appetite and goals), facts about your "
           "deployments and trades (symbols, P&L, risk settings), market data and news headlines. It never sends broker credentials, "
           "API keys, passwords or bank details. The provider processes this data on its own servers, which may be outside India, under "
           "its terms. Purpose: explaining rules and data to you. You can opt out at any time by switching to the rule-based provider "
           "or removing the provider; the platform then shares nothing externally."),
}


# Settings redesign A3: "<provider>" is replaced with the provider's plain name when shown; the hash is of this template
# and the version (which names the provider).
BYOK_CONSENT_TEXT = {
    "en": "Your questions and chart images are sent to <provider> to answer you. Never your broker login or passwords.",
}
BYOK_PAID_PLAN_TEXT = {
    "en": "My key is on a paid plan, so <provider> does not use what I send to train its models.",
}
def _texts(kind: str) -> dict:
    """The text of one kind, read at call time (a test may swap a text to prove it is asked again)."""
    if kind == KIND_COPILOT:
        return ACK_TEXT
    if kind == KIND_BYOK:
        return BYOK_CONSENT_TEXT
    if kind == KIND_BYOK_PAID:
        return BYOK_PAID_PLAN_TEXT
    return DATA_CONSENT_TEXT


def byok_consent_version(provider: str) -> str:
    """`2026-10-22:gemini` - the consent text's version for one provider (String(24))."""
    return f"{BYOK_CONSENT_VERSION}:{provider}"[:24]


def byok_paid_plan_version(key_hint: str) -> str:
    """`p1:AIza...4444` - the paid-plan confirmation of one key (its hint), String(24)."""
    return f"{BYOK_PAID_PLAN_VERSION}:{key_hint}"[:24]


def byok_text(kind: str, provider_name: str) -> str:
    return _texts(kind)["en"].replace("<provider>", provider_name)


# ATP is English only (the owner's ruling, Oct 2026): the Marathi translation of both texts was removed; the English
# text - the legal text - was not changed. Acceptances recorded before hashed the English and the Marathi text together.
# They still count for the version they accepted, but only while the English text is byte-for-byte the one they accepted
# (its own sha256 is the first value): any edit of the English text asks everyone again, as before.
_BILINGUAL_HASHES = {
    (KIND_COPILOT, "2026-10-07"): ("99dd9edc651e64848d5a90566af6c3c4feacf5122b36b6be32e93a5a65f744e6",
                                   "58025da752bb69b94cf5b412337a62c7458c423b31520c648a94ade7c76469bb"),
    (KIND_DATA, "2026-10-07"): ("61a8f663e6fc0a0645cd802846fb2485db9f8eecb333dcdc2103b207789e685d",
                                "410e16ed70e60fab430cad532c43e1f87085d4f0f7d9939092484fb27548afb8"),
}


def text_hash(kind: str, version: str) -> str:
    """The sha256 of the text shown now (English only), stored with every new acceptance."""
    texts = _texts(kind)
    return hashlib.sha256(f"{kind}|{version}|{texts['en']}".encode()).hexdigest()


def accepted_hashes(kind: str, version: str) -> list:
    """The stored hashes that count as accepting `version`: the current text's, plus the bilingual one recorded before the
    Marathi translation was removed - only while the English text is unchanged."""
    texts = _texts(kind)
    out = [text_hash(kind, version)]
    legacy = _BILINGUAL_HASHES.get((kind, version))
    if legacy and hashlib.sha256(texts["en"].encode()).hexdigest() == legacy[0]:
        out.append(legacy[1])
    return out


def current_version(kind: str) -> str:
    return ACK_VERSION if kind == KIND_COPILOT else DATA_CONSENT_VERSION


async def latest(session: AsyncSession, kind: str, *, tenant_id: int, user_id: Optional[int] = None,
                 version: Optional[str] = None) -> Optional[AiAcknowledgementRecord]:
    """The newest acceptance of the current text. `version` names a per-provider / per-key version (the two own-key
    kinds); the others use their one current version."""
    wanted = version or current_version(kind)
    query = select(AiAcknowledgementRecord).where(AiAcknowledgementRecord.tenant_id == tenant_id, AiAcknowledgementRecord.kind == kind,
                                                  AiAcknowledgementRecord.version == wanted,
                                                  # the text itself, not only its version label: editing the text without
                                                  # bumping the version still asks everyone again.
                                                  AiAcknowledgementRecord.text_sha256.in_(accepted_hashes(kind, wanted)))
    if user_id is not None:
        query = query.where(AiAcknowledgementRecord.user_id == user_id)
    return await session.scalar(query.order_by(AiAcknowledgementRecord.accepted_at.desc(), AiAcknowledgementRecord.id.desc()).limit(1))


async def accept(session: AsyncSession, user: User, kind: str, version: str, *, language: str = "en", request: Optional[Request] = None) -> AiAcknowledgementRecord:
    """Records the acceptance of `version` (must be the current one) with an audit row; does not commit. The text is
    English only, so the recorded language is "en" (`language` is accepted for older callers)."""
    if kind not in (KIND_BYOK, KIND_BYOK_PAID) and version != current_version(kind):
        raise ValueError(f"The current {kind} version is {current_version(kind)}; reload and read the current text")
    ip = request.client.host if request is not None and request.client else None
    agent = (request.headers.get("user-agent") or "")[:300] if request is not None else None
    row = AiAcknowledgementRecord(tenant_id=user.tenant_id, user_id=user.id, kind=kind, version=version, text_sha256=text_hash(kind, version),
                                  language="en", ip=ip, user_agent=agent or None,
                                  accepted_at=datetime.now(timezone.utc))
    session.add(row)
    await write_audit_log(session, user.tenant_id, user.id, f"ai_{kind}_accepted", f"version {version} sha256 {row.text_sha256[:16]} lang {row.language}")
    return row


def status_dict(kind: str, row: Optional[AiAcknowledgementRecord]) -> dict:
    texts = _texts(kind)
    return {"kind": kind, "version": current_version(kind), "text": texts, "accepted": row is not None,
            "accepted_at": row.accepted_at.isoformat() if row is not None else None, "accepted_by": row.user_id if row is not None else None}


async def require_ai_acknowledged(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    """The AI content routes' user dependency: 428 Precondition Required until this user accepted the current Copilot
    terms. Safety routes (approve/reject a proposal, provider settings) never depend on it."""
    if await latest(session, KIND_COPILOT, tenant_id=user.tenant_id, user_id=user.id) is None:
        raise HTTPException(status_code=428, detail={"code": "ai_acknowledgement_required", "version": ACK_VERSION,
                                                     "message": "Read and accept the AI Copilot acknowledgement first (POST /api/ai/acknowledgement)"})
    return user


async def ai_acknowledged(user: User = Depends(require_ai_acknowledged)) -> None:
    """Add-on form for routes that already carry a role dependency (`require_trader`)."""
    return None
