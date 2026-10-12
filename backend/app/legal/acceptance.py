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
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
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


class LegalAck(BaseModel):
    """What the client read: the version and (review L1) the SHA-256 of the text it showed. A text edited without a
    version bump then fails the check instead of being accepted under the old reading."""
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=24)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


# slug -> "version" (older clients) or {"version", "sha256"}
LegalAcceptance = Dict[str, Union[str, LegalAck]]


def _split(entries: Mapping[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    versions: Dict[str, str] = {}
    hashes: Dict[str, str] = {}
    for slug, value in entries.items():
        if isinstance(value, LegalAck):
            value = value.model_dump()
        if isinstance(value, dict):
            versions[slug] = str(value.get("version") or "")
            if value.get("sha256"):
                hashes[slug] = str(value["sha256"])
        else:
            versions[slug] = str(value)
    return versions, hashes


def check_versions(entries: Mapping[str, Any], *, require_all: bool) -> Dict[str, str]:
    """{slug: version} once every named document is known and current. Raises ValueError for an unknown document,
    LegalVersionError for a stale version or a text hash that is not the current text; with `require_all`, every
    document must be named (sign-up accepts all three at once)."""
    versions, hashes = _split(entries)
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
        if version != docs[slug].version or (slug in hashes and hashes[slug] != docs[slug].sha256):
            raise LegalVersionError(slug, version, docs[slug].version)
    return versions


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


async def accept(session: AsyncSession, user: User, entries: Mapping[str, Any], *, source: str,
                 request: Optional[Request] = None, require_all: bool = False) -> List[AiAcknowledgementRecord]:
    """Records the acceptance of each named document at its current version (idempotent: a document already accepted at
    this version and text is not recorded twice). Writes one audit row per new acceptance; does not commit."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    versions = check_versions(entries, require_all=require_all)
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


__all__ = ["LegalAck", "LegalAcceptance", "LegalVersionError", "accept", "check_versions", "current_versions", "latest", "status"]
