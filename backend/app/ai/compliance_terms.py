"""P0.8-D: the two acceptances the AI Copilot needs before it says anything, and the gate that enforces the first.

* **Copilot terms** (per user, versioned): the AI Copilot is not a SEBI-registered Investment Adviser or Research
  Analyst; it explains the platform's rules and data; the decision is the trader's. Every AI content route depends on
  `require_ai_acknowledged`, which answers 428 until the current version is accepted; the acceptance is a row in
  `ai_acknowledgements` and an audit event.
* **Data-sharing consent** (per organisation, by the owner, versioned): what leaves the platform when an external
  provider is configured (questions, interview answers including capital and experience, trade facts, market data,
  headlines - never broker credentials or keys), that the provider may process it outside India, and how to opt out
  (the rule-based provider). Saving an external provider requires it.
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
ACK_VERSION = "2026-10-07"
DATA_CONSENT_VERSION = "2026-10-07"

ACK_TEXT = {
    "en": ("The AI Copilot is not a SEBI-registered Investment Adviser or Research Analyst and does not give investment advice. "
           "It explains the platform's rules, templates and data (backtests, market reads, risk settings) in plain words. "
           "Which strategy to run, with how much capital and whether to trade at all is your decision alone. Nothing it writes is a "
           "recommendation to buy, sell or hold any security, and past or simulated results do not predict future results. "
           "Paper trading first, then your own judgement."),
    "mr": ("AI Copilot हा SEBI-नोंदणीकृत Investment Adviser किंवा Research Analyst नाही आणि गुंतवणुकीचा सल्ला देत नाही. "
           "तो platform चे नियम, templates आणि data (backtest, market चे वाचन, risk settings) सोप्या शब्दांत स्पष्ट करतो. "
           "कोणती strategy चालवायची, किती भांडवलाने, आणि trade करायचा की नाही - हा निर्णय फक्त तुमचा. तो जे लिहितो ती कोणताही "
           "share घेण्याची, विकण्याची किंवा ठेवण्याची शिफारस नाही; भूतकाळातले किंवा simulated निकाल भविष्याची खात्री देत नाहीत. "
           "आधी PAPER trading, मग तुमचा स्वतःचा निर्णय."),
}
DATA_CONSENT_TEXT = {
    "en": ("When an external AI provider (Anthropic or OpenAI) is configured, the platform sends it the text needed to answer: your "
           "questions and messages, your interview answers (including capital, experience, risk appetite and goals), facts about your "
           "deployments and trades (symbols, P&L, risk settings), market data and news headlines. It never sends broker credentials, "
           "API keys, passwords or bank details. The provider processes this data on its own servers, which may be outside India, under "
           "its terms. Purpose: explaining rules and data to you. You can opt out at any time by switching to the rule-based provider "
           "or removing the provider; the platform then shares nothing externally."),
    "mr": ("बाहेरचा AI provider (Anthropic किंवा OpenAI) लावला असेल तर उत्तरासाठी लागणारा मजकूर platform त्याला पाठवतो: तुमचे प्रश्न आणि "
           "संदेश, interview ची उत्तरे (भांडवल, अनुभव, risk ची तयारी, उद्दिष्टे यासह), तुमच्या deployments आणि trades ची माहिती (symbol, "
           "P&L, risk settings), market data आणि बातम्यांची शीर्षके. Broker credentials, API keys, passwords किंवा bank तपशील कधीच "
           "पाठवले जात नाहीत. Provider हा data त्याच्या servers वर (भारताबाहेर असू शकतात) त्याच्या अटींनुसार process करतो. उद्देश: "
           "नियम आणि data तुम्हाला स्पष्ट करणे. कधीही rule-based provider निवडून किंवा provider काढून तुम्ही opt out करू शकता; मग platform "
           "बाहेर काहीही पाठवत नाही."),
}


def text_hash(kind: str, version: str) -> str:
    texts = ACK_TEXT if kind == KIND_COPILOT else DATA_CONSENT_TEXT
    return hashlib.sha256(f"{kind}|{version}|{texts['en']}|{texts['mr']}".encode()).hexdigest()


def current_version(kind: str) -> str:
    return ACK_VERSION if kind == KIND_COPILOT else DATA_CONSENT_VERSION


async def latest(session: AsyncSession, kind: str, *, tenant_id: int, user_id: Optional[int] = None) -> Optional[AiAcknowledgementRecord]:
    query = select(AiAcknowledgementRecord).where(AiAcknowledgementRecord.tenant_id == tenant_id, AiAcknowledgementRecord.kind == kind,
                                                  AiAcknowledgementRecord.version == current_version(kind))
    if user_id is not None:
        query = query.where(AiAcknowledgementRecord.user_id == user_id)
    return await session.scalar(query.order_by(AiAcknowledgementRecord.accepted_at.desc(), AiAcknowledgementRecord.id.desc()).limit(1))


async def accept(session: AsyncSession, user: User, kind: str, version: str, *, language: str = "en", request: Optional[Request] = None) -> AiAcknowledgementRecord:
    """Records the acceptance of `version` (must be the current one) with an audit row; does not commit."""
    if version != current_version(kind):
        raise ValueError(f"The current {kind} version is {current_version(kind)}; reload and read the current text")
    ip = request.client.host if request is not None and request.client else None
    agent = (request.headers.get("user-agent") or "")[:300] if request is not None else None
    row = AiAcknowledgementRecord(tenant_id=user.tenant_id, user_id=user.id, kind=kind, version=version, text_sha256=text_hash(kind, version),
                                  language=language if language in ("en", "mr") else "en", ip=ip, user_agent=agent or None,
                                  accepted_at=datetime.now(timezone.utc))
    session.add(row)
    await write_audit_log(session, user.tenant_id, user.id, f"ai_{kind}_accepted", f"version {version} sha256 {row.text_sha256[:16]} lang {row.language}")
    return row


def status_dict(kind: str, row: Optional[AiAcknowledgementRecord]) -> dict:
    texts = ACK_TEXT if kind == KIND_COPILOT else DATA_CONSENT_TEXT
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
