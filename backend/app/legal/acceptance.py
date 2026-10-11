"""Z10-f: recording who accepted which version of the legal documents, and when.

An acceptance is a row in `ai_acknowledgements` - the platform's table of versioned acceptances (P0.8-D), kept for good
(`retention.NEVER_DELETED`) - with `kind` = `legal_<slug>`, the document's `version`, the SHA-256 of its source text,
the IP and browser it came from and `accepted_at`, plus a `legal_accepted` audit row. Reusing that table needs no
migration; the `kind` keeps legal acceptances apart from the AI Copilot ones.

A document counts as accepted only for its current version *and* text hash. Sign-up records all three in the same
transaction that creates the account (`source = "signup"`). Anyone without a current acceptance - every account that
existed before this feature, and everyone after a document changes - is asked on their next sign-in. The ask never
locks anyone out: no route depends on it, so positions, exits and every other screen keep working while it is open.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Mapping, Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import AiAcknowledgementRecord, User
from app.legal import documents

SOURCES = ("signup", "invite", "prompt")


class LegalVersionError(ValueError):
    """The client sent a version that is not the current one (the page was open while a document changed)."""

    def __init__(self, slug: str, sent: str, current: str):
        super().__init__(f"The {documents.get(slug).title} changed (current version {current}, you accepted {sent}); "
                         "reload and read the current text")
        self.slug, self.sent, self.current = slug, sent, current


def check_versions(versions: Mapping[str, str], *, require_all: bool) -> None:
    """Raises ValueError for an unknown document, LegalVersionError for a stale version; with `require_all`, every
    document must be in `versions` (sign-up accepts all three at once)."""
    docs = documents.documents()
    unknown = sorted(set(versions) - set(docs))
    if unknown:
        raise ValueError(f"Unknown legal document(s): {', '.join(unknown)}; one of {', '.join(documents.SLUGS)}")
    if require_all:
        missing = [s for s in documents.SLUGS if s not in versions]
        if missing:
            raise ValueError(f"Accept every legal document: missing {', '.join(missing)}")
    if not versions:
        raise ValueError("Name at least one legal document and the version you read")
    for slug, version in versions.items():
        if version != docs[slug].version:
            raise LegalVersionError(slug, version, docs[slug].version)


async def latest(session: AsyncSession, user: User, slug: str) -> Optional[AiAcknowledgementRecord]:
    """The user's acceptance of the current version and text of `slug`, if any."""
    doc = documents.get(slug)
    return await session.scalar(
        select(AiAcknowledgementRecord)
        .where(AiAcknowledgementRecord.tenant_id == user.tenant_id, AiAcknowledgementRecord.user_id == user.id,
               AiAcknowledgementRecord.kind == doc.kind, AiAcknowledgementRecord.version == doc.version,
               AiAcknowledgementRecord.text_sha256 == doc.sha256)
        .order_by(AiAcknowledgementRecord.accepted_at.desc(), AiAcknowledgementRecord.id.desc()).limit(1))


async def status(session: AsyncSession, user: User) -> dict:
    rows: List[dict] = []
    for slug in documents.SLUGS:
        doc = documents.get(slug)
        row = await latest(session, user, slug)
        rows.append({**doc.summary(), "accepted": row is not None,
                     "accepted_at": row.accepted_at.isoformat() if row is not None else None})
    pending = [r["slug"] for r in rows if not r["accepted"]]
    return {"documents": rows, "pending": pending, "all_accepted": not pending}


async def accept(session: AsyncSession, user: User, versions: Mapping[str, str], *, source: str,
                 request: Optional[Request] = None, require_all: bool = False) -> List[AiAcknowledgementRecord]:
    """Records the acceptance of each named document at its current version (idempotent: a document already accepted at
    this version and text is not recorded twice). Writes one audit row per new acceptance; does not commit."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    check_versions(versions, require_all=require_all)
    ip = request.client.host if request is not None and request.client else None
    agent = (request.headers.get("user-agent") or "")[:300] if request is not None else None
    now = datetime.now(timezone.utc)
    recorded: List[AiAcknowledgementRecord] = []
    for slug in documents.SLUGS:
        if slug not in versions or await latest(session, user, slug) is not None:
            continue
        doc = documents.get(slug)
        row = AiAcknowledgementRecord(tenant_id=user.tenant_id, user_id=user.id, kind=doc.kind, version=doc.version,
                                      text_sha256=doc.sha256, language="en", ip=ip, user_agent=agent or None, accepted_at=now)
        session.add(row)
        recorded.append(row)
        await write_audit_log(session, user.tenant_id, user.id, "legal_accepted",
                              f"{slug} version {doc.version} sha256 {doc.sha256[:16]} via {source}")
    return recorded


def current_versions() -> Dict[str, str]:
    return {slug: documents.get(slug).version for slug in documents.SLUGS}


__all__ = ["LegalVersionError", "accept", "check_versions", "current_versions", "latest", "status"]
