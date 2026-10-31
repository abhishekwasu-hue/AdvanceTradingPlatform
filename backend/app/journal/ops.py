"""M62 JN-1: the journal's real state, and the backfill from closed trades - the logic behind `deploy/atp.sh
journal-status` and `deploy/atp.sh journal-backfill [--tenant ID]`, run inside the backend container:

    python -m app.journal.ops status
    python -m app.journal.ops backfill [--tenant ID]

`status` is READ-ONLY: whether `discipline_engine` is on (for everyone, or for which organisations), and per
organisation the journal rows, the manual (outside-ATP) entries, the closed trades and the closed trades that have no
journal row yet. Only counts, organisation ids and names - never a secret, a price or a person's data.

`backfill` creates the missing journal rows of CLOSED trades with the same rebuild the journal uses on every read
(`capture.ensure_entries`: from the trade row only, INSERT ... ON CONFLICT DO NOTHING), in batches, one commit per
batch. Idempotent: a second run finds nothing missing and creates nothing. It runs only for organisations the flag is
on for (the journal is off - and empty - elsewhere); it never changes a flag, a trade, an order or a setting.

Exit codes: 0 done; 2 refused (the flag is off for the organisation asked for, or it does not exist) - the line printed
says why.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntryRecord, JournalManualTradeRecord, Tenant, TradeRecord
from app.journal import FLAG, capture

BATCH = 500


class Refused(RuntimeError):
    """A backfill that cannot run as asked (exit 2)."""


async def flag_state(session: AsyncSession) -> dict[str, Any]:
    """{"on": bool, "tenants": [ids]} - the flag as the platform resolves it (absent = its default, OFF)."""
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    return {"on": bool(entry.get("on")), "tenants": sorted(int(t) for t in entry.get("tenants") or [])}


def _closed() -> Any:
    return TradeRecord.exit_time.is_not(None)


def _missing_query() -> Any:
    """Closed trades with no journal row."""
    return (select(TradeRecord).outerjoin(JournalEntryRecord, JournalEntryRecord.trade_id == TradeRecord.id)
            .where(_closed(), JournalEntryRecord.id.is_(None)))


async def _counts(session: AsyncSession, model: Any, column: Any, *where: Any) -> dict[int, int]:
    rows = await session.execute(select(column, func.count()).select_from(model).where(*where).group_by(column))
    return {int(t): int(n) for t, n in rows.all()}


async def status(session: AsyncSession) -> dict[str, Any]:
    """Read-only: the flag and the journal counts per organisation (only organisations with trades or journal rows)."""
    flag = await flag_state(session)
    journal = await _counts(session, JournalEntryRecord, JournalEntryRecord.tenant_id)
    manual = await _counts(session, JournalManualTradeRecord, JournalManualTradeRecord.tenant_id)
    closed = await _counts(session, TradeRecord, TradeRecord.tenant_id, _closed())
    missing_rows = await session.execute(select(TradeRecord.tenant_id, func.count()).select_from(TradeRecord)
                                         .outerjoin(JournalEntryRecord, JournalEntryRecord.trade_id == TradeRecord.id)
                                         .where(_closed(), JournalEntryRecord.id.is_(None)).group_by(TradeRecord.tenant_id))
    missing = {int(t): int(n) for t, n in missing_rows.all()}
    ids = sorted(set(journal) | set(manual) | set(closed) | set(flag["tenants"]))
    names = {int(i): str(n or "") for i, n in (await session.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids or [-1])))).all()}
    tenants = [{"tenant_id": t, "name": names.get(t, ""), "flag_on": flag["on"] or t in flag["tenants"], "journal_rows": journal.get(t, 0),
                "manual_entries": manual.get(t, 0), "closed_trades": closed.get(t, 0), "closed_without_journal": missing.get(t, 0)}
               for t in ids]
    return {"flag": flag, "tenants": tenants}


def format_status(report: dict[str, Any]) -> list[str]:
    flag = report["flag"]
    if flag["on"]:
        head = f"{FLAG}: ON for everyone"
    elif flag["tenants"]:
        head = f"{FLAG}: OFF for everyone; ON for organisation(s) {', '.join(str(t) for t in flag['tenants'])}"
    else:
        head = f"{FLAG}: OFF for everyone (no organisation has it on)"
    lines = [head]
    if not report["tenants"]:
        lines.append("  no organisation has trades or journal rows yet")
    for t in report["tenants"]:
        name = f' "{t["name"]}"' if t["name"] else ""
        lines.append(f"  organisation {t['tenant_id']}{name}: flag {'ON' if t['flag_on'] else 'OFF'}; journal rows {t['journal_rows']}; "
                     f"manual entries {t['manual_entries']}; closed trades {t['closed_trades']}; "
                     f"closed trades without a journal row {t['closed_without_journal']}")
    return lines


async def _targets(session: AsyncSession, tenant_id: int | None) -> list[int]:
    flag = await flag_state(session)
    if tenant_id is not None:
        if await session.get(Tenant, tenant_id) is None:
            raise Refused(f"organisation {tenant_id} does not exist")
        if not (flag["on"] or tenant_id in flag["tenants"]):
            raise Refused(f"{FLAG} is OFF for organisation {tenant_id} - turn it on for that organisation first "
                          "(Admin > Controls > Feature flags); nothing was written")
        return [tenant_id]
    if flag["on"]:
        return sorted(int(t) for t in await session.scalars(select(TradeRecord.tenant_id).where(_closed()).distinct()))
    return list(flag["tenants"])


async def backfill(session: AsyncSession, tenant_id: int | None = None, *, batch: int = BATCH) -> dict[str, Any]:
    """Creates the journal rows missing for closed trades (the organisations the flag is on for, or the one asked)."""
    out: list[dict[str, Any]] = []
    for tenant in await _targets(session, tenant_id):
        before = int(await session.scalar(select(func.count()).select_from(_missing_query().where(TradeRecord.tenant_id == tenant).subquery())) or 0)
        created = 0
        while True:
            trades = list(await session.scalars(_missing_query().where(TradeRecord.tenant_id == tenant).order_by(TradeRecord.id).limit(batch)))
            if not trades:
                break
            made = await capture.ensure_entries(session, trades)
            await session.commit()
            created += len(made)
            if len(made) < len(trades):     # a trade deleted mid-run: stop rather than loop on it
                break
        after = int(await session.scalar(select(func.count()).select_from(_missing_query().where(TradeRecord.tenant_id == tenant).subquery())) or 0)
        out.append({"tenant_id": tenant, "missing_before": before, "created": created, "missing_after": after})
    return {"tenants": out}


def format_backfill(report: dict[str, Any]) -> list[str]:
    if not report["tenants"]:
        return [f"journal-backfill: {FLAG} is OFF for every organisation - nothing to backfill (turn it on first)"]
    lines = [f"  organisation {t['tenant_id']}: closed trades without a journal row {t['missing_before']}; created {t['created']}; "
             f"still missing {t['missing_after']}" for t in report["tenants"]]
    total = sum(t["created"] for t in report["tenants"])
    return [*lines, f"journal-backfill: done - {total} journal row(s) created; a re-run creates none"]


async def _main(args: argparse.Namespace) -> int:
    from app.db.session import _session_factory
    async with _session_factory() as session:
        if args.what == "status":
            print("\n".join(format_status(await status(session))))
            return 0
        try:
            report = await backfill(session, args.tenant)
        except Refused as exc:
            print(f"journal-backfill: refused - {exc}")
            return 2
    print("\n".join(format_backfill(report)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="journal-ops", description="M62 JN-1: the journal's state and the backfill from closed trades")
    sub = parser.add_subparsers(dest="what", required=True)
    sub.add_parser("status", help="read-only: the flag and the journal counts per organisation")
    b = sub.add_parser("backfill", help="create the missing journal rows of closed trades (idempotent)")
    b.add_argument("--tenant", type=int, default=None, help="only this organisation id")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
