"""One-off repair (security LOW follow-up of #234): shared company profiles a member wrote before shared writes were scoped.

Before #234 any trading member of any organisation could create or edit a company profile that every organisation read.
#234 made every contributed CHILD row (periods, shareholding, actions, ...) its author's organisation's own, but left the
company profiles shared - so a profile a member poisoned before the fix ("Poisoned Ltd", a fake website or description,
made-up holdings) is still what everybody reads.

EVERY shared profile with a member as its author is a suspect, whatever its source says: before #234 a member could
write any text into `source_json` - the provider's citation included - and could edit a profile the provider made (no
editor is recorded), so neither the citation nor the content proves anything. Only a platform administrator's own
entry is trusted. (Since this fix a provider refresh records no author on the shared profile it creates or promotes -
the content is the provider's; who asked for it is in the audit log, `ingest.SHARED_PROFILE_EVENT` - so the suspect set
no longer grows.)

`--apply` must say what replaces a suspect's content:

* `--refetch` (the default to use): the provider's profile REPLACES the suspect's (`ingest.replace_profile` - no
  hand-entered field survives, unlike a refresh's merge). When the provider does not answer for a symbol (NSE often
  blocks cloud IPs) that row is LEFT UNTOUCHED and reported (`action` "left untouched (provider: ...)", exit status 1);
  a failed refetch never blanks a profile. Run it again from where the provider answers.
* `--no-refetch`: QUARANTINE each suspect in place without the provider - every profile field a member could write is
  reset (name = the symbol, sector / industry "Unknown", the rest empty), its source says so. Only for when blanking is
  intended.

Either way the author is cleared (the row holds nothing of theirs any more), the row stays shared (the symbol keeps its
shared children, and every organisation's own rows on it stay theirs), and the audit entry (`EVENT`) keeps the finding,
who the author was, and every field's value BEFORE and AFTER the change - so a change is reversible:

    python -m app.fundamentals.repair_shared --restore <audit_id>

puts the `before` values back (the report names each change's `audit_id`) when the row still holds exactly what the
repair left (refused, naming the fields, when anything changed it since - a provider refresh, an administrator). The
author is NOT put back (the restored row has no author; both audit entries name it), so a restored profile is judged
like any unattributed one on a later run: trusted when it carries the provider's citation and no member-only field,
otherwise listed and changed only with `--include-unattributed`. A restore is audit-logged (`RESTORE_EVENT`).

A profile whose author is unknown (no `created_by`: a provider refresh, or the row predates authorship) is trusted when
it carries the provider's citation AND none of the fields the provider never fills (website, description, headquarters,
segments, revenue split, cyclical) - such a field means a member edited it; it is then a suspect like a member's. Any
other unattributed profile is listed and left alone unless `--include-unattributed`.

The author's own copy is NOT re-created privately: the row may have been edited by a member of another organisation
after its author created it (no editor is recorded), so copying it to the author's organisation could carry that other
organisation's text into it. The report names the author for the operator to follow up.

    DATABASE_URL=postgresql+asyncpg://... python -m app.fundamentals.repair_shared            # dry run: the report only
    DATABASE_URL=... python -m app.fundamentals.repair_shared --apply --refetch [--include-unattributed]
    DATABASE_URL=... python -m app.fundamentals.repair_shared --apply --no-refetch [--include-unattributed]
    DATABASE_URL=... python -m app.fundamentals.repair_shared --restore <audit_id>

Idempotent: a repaired profile carries QUARANTINE_SOURCE (or, refilled, the provider's citation and no author) and is
skipped on the next run. Reference data only - nothing here touches an order.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserRole
from app.db.models import AuditLogRecord, CompanyRecord, User
from app.fundamentals import ingest

QUARANTINE_SOURCE = "ATP repair: shared profile reset - entered by an organisation's member before shared writes were scoped"
PROVIDER_CITATIONS = ("NSE India (nseindia.com",)            # the providers' own citations (app/fundamentals/providers)
# Profile fields no fundamentals provider fills: on a profile the provider made, only a member's edit sets them.
PROVIDER_NEVER_FIELDS = ("headquarters", "website", "business_description", "domestic_revenue_pct", "international_revenue_pct")
EVENT = "fundamentals.shared_profile_repaired"
RESTORE_EVENT = "fundamentals.shared_profile_restored"
# Every column a repair may change: their values before and after go into the audit entry (what `restore` puts back).
SNAPSHOT_FIELDS = tuple(dict.fromkeys(("name", "sector", "industry") + ingest.PROFILE_REFRESH_FIELDS + ingest.HAND_FIELDS + PROVIDER_NEVER_FIELDS
                                      + ("business_segments_json", "cyclical", "source_json", "created_by")))
_DATE_FIELDS = ("listing_date",)


@dataclass
class Finding:
    company_id: int
    symbol: str
    created_by: Optional[int]
    author_tenant: Optional[int]
    reason: str
    action: str = "none (dry run)"
    audit_id: Optional[int] = None


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


def snapshot(row: CompanyRecord) -> Dict[str, Any]:
    """The repairable columns as JSON values (dates as ISO strings)."""
    out: Dict[str, Any] = {}
    for name in SNAPSHOT_FIELDS:
        value = getattr(row, name)
        out[name] = value.isoformat() if isinstance(value, date) else value
    return out


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


async def repair(session: AsyncSession, *, apply: bool = False, refetch: Any = None, no_refetch: bool = False,
                 include_unattributed: bool = False) -> List[Finding]:
    """The report; with `apply`, each suspect's profile is replaced by the provider's (`refetch`, a provider) - a row the
    provider does not answer for is left untouched and reported - or, only with an explicit `no_refetch`, quarantined
    (blanked). Every change is audit-logged with the values before and after. Does not commit."""
    from app.audit.log import write_audit_log
    if apply and refetch is None and not no_refetch:
        raise ValueError("apply needs a provider to refetch from (--refetch), or an explicit no_refetch (--no-refetch) to blank the suspects")
    if refetch is not None and no_refetch:
        raise ValueError("refetch and no_refetch are exclusive")
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
        fetched = None
        if refetch is not None:
            try:
                fetched = await refetch.get_company_profile(record.symbol)
            except Exception as exc:  # any provider failure (blocked, timeout, bad payload): never blank on a failed refetch
                f.action = f"left untouched (provider: {exc})"
                continue
        before = snapshot(record)
        quarantine(record)
        if fetched is not None:
            ingest.replace_profile(record, fetched)          # the provider's profile only: never merged with what was there
            record.source_json = fetched.source.model_dump_json() if fetched.source is not None else json.dumps({"source": QUARANTINE_SOURCE})
            f.action = "replaced by the provider's profile"
        else:
            f.action = "quarantined (no refetch)"
        entry = await write_audit_log(session, None, None, EVENT, json.dumps({**asdict(f), "before": before, "after": snapshot(record)}, sort_keys=True))
        await session.flush()
        f.audit_id = entry.id
    await session.flush()
    return found


def _from_json(name: str, value: Any) -> Any:
    return date.fromisoformat(value) if name in _DATE_FIELDS and isinstance(value, str) else value


async def restore(session: AsyncSession, audit_id: int) -> Dict[str, Any]:
    """Put back what one repair (its audit entry) changed, when the row still holds exactly what that repair left. The
    author stays empty (see the module doc). Raises ValueError with the reason otherwise. Does not commit."""
    from app.audit.log import write_audit_log
    entry = await session.get(AuditLogRecord, audit_id)
    if entry is None or entry.event != EVENT:
        raise ValueError(f"audit entry {audit_id} is not a shared-profile repair")
    detail = json.loads(entry.detail)
    if "before" not in detail or "after" not in detail:
        raise ValueError(f"audit entry {audit_id} predates the stored values - nothing to restore from")
    record = await session.get(CompanyRecord, detail["company_id"])
    if record is None or record.tenant_id is not None:
        raise ValueError(f"company {detail['company_id']} is no longer a shared profile")
    now = snapshot(record)
    changed = sorted(name for name, value in detail["after"].items() if now.get(name) != value)
    if changed:
        raise ValueError(f"{record.symbol} changed since the repair ({', '.join(changed)}) - not restored")
    for name, value in detail["before"].items():
        if name != "created_by":
            setattr(record, name, _from_json(name, value))
    record.created_by = None
    out = {"audit_id": audit_id, "company_id": record.id, "symbol": record.symbol, "restored": detail["before"], "replaced": detail["after"]}
    await write_audit_log(session, None, None, RESTORE_EVENT, json.dumps(out, sort_keys=True))
    await session.flush()
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.fundamentals.repair_shared", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="change the suspects (default: report only); needs --refetch or --no-refetch")
    how = parser.add_mutually_exclusive_group()
    how.add_argument("--refetch", action="store_true", help="replace each suspect with the provider's profile; a row the provider does not answer for is left untouched")
    how.add_argument("--no-refetch", action="store_true", help="blank (quarantine) each suspect without the provider")
    parser.add_argument("--include-unattributed", action="store_true", help="also change shared profiles with no recorded author")
    parser.add_argument("--restore", type=int, metavar="AUDIT_ID", help="undo one repair (its audit entry id, from the report)")
    return parser


async def _main(argv: Optional[List[str]] = None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import DATABASE_URL
    parser = _parser()
    args = parser.parse_args(argv)
    if args.restore is not None and (args.apply or args.refetch or args.no_refetch or args.include_unattributed):
        parser.error("--restore takes no other option")
    if args.apply and not (args.refetch or args.no_refetch):
        parser.error("--apply needs --refetch (replace from the provider; rows it does not answer for are left untouched) "
                     "or an explicit --no-refetch (blank the suspects)")
    engine = create_async_engine(DATABASE_URL)
    try:
        async with async_sessionmaker(engine)() as session:
            if args.restore is not None:
                try:
                    restored = await restore(session, args.restore)
                except ValueError as exc:
                    print(json.dumps({"restored": False, "reason": str(exc)}, indent=2))
                    return 1
                await session.commit()
                print(json.dumps({"restored": True, **restored}, indent=2))
                return 0
            provider = ingest.provider_for(None) if args.refetch else None
            found = await repair(session, apply=args.apply, refetch=provider, no_refetch=args.no_refetch,
                                 include_unattributed=args.include_unattributed)
            if args.apply:
                await session.commit()
    finally:
        await engine.dispose()
    left = [f.symbol for f in found if f.action.startswith("left untouched")]
    print(json.dumps({"applied": args.apply, "left_untouched": left, "findings": [asdict(f) for f in found]}, indent=2))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
