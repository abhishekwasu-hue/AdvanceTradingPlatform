"""One-off repair (security LOW follow-up of #234): shared company profiles a member wrote before shared writes were scoped.

Before #234 any trading member of any organisation could create or edit a company profile that every organisation read.
#234 made every contributed CHILD row (periods, shareholding, actions, ...) its author's organisation's own, but left the
company profiles shared - so a profile a member poisoned before the fix ("Poisoned Ltd", a fake website or description,
made-up holdings) is still what everybody reads.

EVERY shared profile with a member as its author is a suspect, whatever its source says: before #234 a member could
write any text into `source_json` - the provider's citation included - and could edit a profile the provider made (no
editor is recorded), so neither the citation nor the content proves anything. Only a platform administrator's own
entry is trusted. `--apply` QUARANTINES each suspect in place - every profile field a member could write, the
hand-entered ones (website, description, segments, ...) included, is reset (name = the symbol, sector / industry
"Unknown", the rest empty), its source says so and its author is cleared (the row holds nothing of theirs any more; the
audit entry keeps who it was) - so nothing a member entered stays in shared scope. The row stays shared (the symbol
keeps its shared children, and every organisation's own rows on it stay theirs); `--refetch` then REPLACES the profile
with the provider's (no hand-entered field survives - unlike a refresh's merge). Run `--apply --refetch`: a profile a
member's provider refresh created is a suspect too, and is restored by the refill.

A profile whose author is unknown (no `created_by`: a provider refresh with no user, or the row predates authorship)
is trusted when it carries the provider's citation AND none of the fields the provider never fills (website,
description, headquarters, segments, revenue split, cyclical) - such a field means a member edited it; it is then a
suspect like a member's. Any other unattributed profile is listed and left alone unless `--include-unattributed`.
Each change is written to the audit log.

The author's own copy is NOT re-created privately: the row may have been edited by a member of another organisation
after its author created it (no editor is recorded), so copying it to the author's organisation could carry that other
organisation's text into it. The report names the author for the operator to follow up.

    DATABASE_URL=postgresql+asyncpg://... python -m app.fundamentals.repair_shared            # dry run: the report only
    DATABASE_URL=... python -m app.fundamentals.repair_shared --apply [--refetch] [--include-unattributed]

Idempotent: a quarantined profile carries QUARANTINE_SOURCE (or, refilled, the provider's citation and no author) and
is skipped on the next run. Reference data only -
nothing here touches an order.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserRole
from app.db.models import CompanyRecord, User
from app.fundamentals import ingest

QUARANTINE_SOURCE = "ATP repair: shared profile reset - entered by an organisation's member before shared writes were scoped"
PROVIDER_CITATIONS = ("NSE India (nseindia.com",)            # the providers' own citations (app/fundamentals/providers)
# Profile fields no fundamentals provider fills: on a profile the provider made, only a member's edit sets them.
PROVIDER_NEVER_FIELDS = ("headquarters", "website", "business_description", "domestic_revenue_pct", "international_revenue_pct")
EVENT = "fundamentals.shared_profile_repaired"


@dataclass
class Finding:
    company_id: int
    symbol: str
    created_by: Optional[int]
    author_tenant: Optional[int]
    reason: str
    action: str = "none (dry run)"


def _source_name(raw: Optional[str]) -> str:
    try:
        return str((json.loads(raw) or {}).get("source") or "") if raw else ""
    except (ValueError, AttributeError):
        return ""


def member_only_fields(row: CompanyRecord) -> List[str]:
    """The fields set on a profile that no provider fills (so a member wrote them)."""
    found = [name for name in PROVIDER_NEVER_FIELDS if getattr(row, name) not in (None, "")]
    if (row.business_segments_json or "[]").strip() not in ("", "[]"):
        found.append("business_segments")
    if row.cyclical is not None:
        found.append("cyclical")
    return found


async def suspects(session: AsyncSession, *, include_unattributed: bool = False) -> List[Finding]:
    """The shared profiles neither a platform administrator nor (unattributed and untouched) the provider vouches for,
    oldest first. A member's authorship makes a profile a suspect whatever its citation says."""
    out: List[Finding] = []
    for row in await session.scalars(select(CompanyRecord).where(CompanyRecord.tenant_id.is_(None)).order_by(CompanyRecord.id)):
        source = _source_name(row.source_json)
        if source == QUARANTINE_SOURCE:
            continue
        cited = any(source.startswith(c) for c in PROVIDER_CITATIONS)
        if row.created_by is not None:
            author = await session.get(User, row.created_by)
            if author is not None and author.role == UserRole.SUPER_ADMIN.value:
                continue                                       # an operator's own entry
            if author is not None:
                reason = "entered by a member" + (" (carries the provider's citation, which a member could write)" if cited else "")
                out.append(Finding(row.id, row.symbol, row.created_by, author.tenant_id, reason))
                continue
        # no (surviving) author: the provider's own only when cited and free of the fields only a member writes
        edited = member_only_fields(row)
        if cited and not edited:
            continue
        if cited:
            out.append(Finding(row.id, row.symbol, row.created_by, None, f"provider profile with member-written fields: {', '.join(edited)}"))
        elif include_unattributed:
            out.append(Finding(row.id, row.symbol, row.created_by, None, "no author recorded"))
        else:
            out.append(Finding(row.id, row.symbol, row.created_by, None, "no author recorded", "left (pass --include-unattributed)"))
    return out


def quarantine(record: CompanyRecord) -> None:
    """Reset every profile field a member could write - the hand-entered ones always included; the row stays the
    symbol's shared profile, with no author (nothing of theirs is left on it)."""
    for name in ingest.HAND_FIELDS + PROVIDER_NEVER_FIELDS + ingest.PROFILE_REFRESH_FIELDS:
        if name not in ("name", "sector", "industry"):
            setattr(record, name, None)
    record.name, record.sector, record.industry = record.symbol, "Unknown", "Unknown"
    record.business_segments_json = "[]"
    record.cyclical = None
    record.created_by = None
    record.source_json = json.dumps({"source": QUARANTINE_SOURCE})


async def repair(session: AsyncSession, *, apply: bool = False, refetch: Any = None, include_unattributed: bool = False) -> List[Finding]:
    """The report; with `apply`, each suspect is quarantined (and refilled from `refetch`, a provider, when it answers).
    Does not commit."""
    from app.audit.log import write_audit_log
    from app.fundamentals.providers.exceptions import FundamentalDataProviderError
    found = await suspects(session, include_unattributed=include_unattributed)
    if not apply:
        return found
    for f in found:
        if f.action.startswith("left"):
            continue
        record = await session.get(CompanyRecord, f.company_id)
        if record is None or record.tenant_id is not None:
            f.action = "skipped (changed meanwhile)"
            continue
        quarantine(record)
        f.action = "quarantined"
        if refetch is not None:
            try:
                fetched = await refetch.get_company_profile(record.symbol)
            except FundamentalDataProviderError as exc:
                f.action = f"quarantined (provider: {exc})"
            else:
                ingest.replace_profile(record, fetched)          # the provider's profile only: never merged with what was there
                record.source_json = fetched.source.model_dump_json() if fetched.source is not None else json.dumps({"source": QUARANTINE_SOURCE})
                f.action = "quarantined and refilled from the provider"
        await write_audit_log(session, None, None, EVENT, json.dumps(asdict(f), sort_keys=True))
    await session.flush()
    return found


async def _main(argv: Optional[List[str]] = None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import DATABASE_URL
    parser = argparse.ArgumentParser(prog="python -m app.fundamentals.repair_shared", description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="quarantine the suspects (default: report only)")
    parser.add_argument("--refetch", action="store_true", help="refill each quarantined profile from the fundamentals provider")
    parser.add_argument("--include-unattributed", action="store_true", help="also quarantine shared profiles with no recorded author")
    args = parser.parse_args(argv)
    engine = create_async_engine(DATABASE_URL)
    try:
        async with async_sessionmaker(engine)() as session:
            provider = ingest.provider_for(None) if args.refetch else None
            found = await repair(session, apply=args.apply, refetch=provider, include_unattributed=args.include_unattributed)
            if args.apply:
                await session.commit()
    finally:
        await engine.dispose()
    print(json.dumps({"applied": args.apply, "findings": [asdict(f) for f in found]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
