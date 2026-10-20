#!/usr/bin/env python
"""Real-host load test (`deploy/atp.sh loadtest`): the synthetic organisation it measures with - made, listed, removed.

Runs inside the release's backend image (`docker compose run --rm --no-deps -T backend python scripts/loadtest_org.py`),
against the host's own database, as the app connects to it (DATABASE_URL). The production-side twin of the CI stack's
`deploy/ci/seed_users.py`: the same direct writes (one password, hashed once; the rows a sign-up makes), with three
differences that matter on the real host:

* **One dedicated organisation**, `loadtest-<stamp>` (stamp = `YYYYMMDD-HHMMSS`, IST), with N users
  `lt-<stamp>-0000@example.com` ... (the first is its OWNER, the rest traders). Email verified, PAPER only.
* **No broker credentials, no flag change.** The CI seed gives every user a fake Dhan session and sets the CI flag
  posture; here nothing broker-side exists for these users, so no request of theirs can reach a real broker (a chart or
  a screener run answers 409 "no broker session" before any broker call), and no flag, secret, role or other
  organisation is read for writing. `seed` refuses unless the deployment is PAPER only: phase0, `live_trading` off for
  everyone, `phase0_paper_only` on.
* **Removal that keeps the audit trail intact.** The audit log is a tamper-evident hash chain and append-only for the
  application role (scripts/db_roles.sql, like orders, order_events and trades); each login appends a row that names
  the user and the organisation (foreign keys RESTRICT). `cleanup` deletes every other row that names the organisation
  or one of its users (sessions, login events, notifications, meters, PAPER accounts, ...: found from the live schema,
  so a table added later is covered), deletes the users no ledger row names, and keeps the others - and the
  organisation - as inert tombstones: users deactivated, their password replaced by the hash of a random secret nobody
  holds (a real bcrypt hash, so a login attempt is an ordinary 401), the organisation `suspended` with the reason. The
  ledgers themselves are never touched. Then it counts again and says what is left.

    python scripts/loadtest_org.py seed --stamp 20261021-101500 --count 500     # accounts CSV on stdout, JSON on stderr
    python scripts/loadtest_org.py count --stamp 20261021-101500                # rows that name the organisation, per table
    python scripts/loadtest_org.py cleanup --stamp 20261021-101500 [--verify-audit]
    python scripts/loadtest_org.py list                                         # every load-test organisation and its state

`seed` prints `account,<email>,<password>` lines on stdout (the caller keeps `email,password` in a mode-600 file and
deletes it at cleanup; the prefix keeps any stray line out of it) and one JSON line on stderr; the other commands print
one JSON line on stdout, last. Logging goes to stderr. Exit 0 ok, 1 failed, 2 refused.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import sys
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAMP_RE = re.compile(r"^\d{8}-\d{6}$")
TENANT_PREFIX = "loadtest-"
EMAIL_DOMAIN = "example.com"
MAX_USERS = 1000
PASSWORD_BYTES = 18
# Never deleted from: the application role may not (scripts/db_roles.sql), and the audit log is a hash chain.
LEDGERS = frozenset({"audit_logs", "audit_anchors", "orders", "order_events", "trades"})
SEED_EVENT = "loadtest_org_seeded"
CLEAN_EVENT = "loadtest_org_cleaned"


class Refused(Exception):
    """The command would act on something that is not a load-test organisation, or on a deployment that is not PAPER only."""


def tenant_name(stamp: str) -> str:
    return TENANT_PREFIX + stamp


def email_for(stamp: str, index: int) -> str:
    return f"lt-{stamp}-{index:04d}@{EMAIL_DOMAIN}"


def is_loadtest_email(stamp: str, email: str) -> bool:
    return re.fullmatch(rf"lt-{re.escape(stamp)}-\d{{4}}@{re.escape(EMAIL_DOMAIN)}", email or "") is not None


def check_stamp(stamp: str) -> str:
    if not STAMP_RE.fullmatch(stamp or ""):
        raise Refused(f"stamp {stamp!r} is not YYYYMMDD-HHMMSS")
    return stamp


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _factory(factory: Any = None) -> Any:
    if factory is not None:
        return factory
    from app.db.session import _session_factory
    return _session_factory


async def posture_problems(session: Any) -> List[str]:
    """Why this deployment is not PAPER only (empty = it is). Read-only: the flags as the release resolves them."""
    from app.platform import controls, deployment_phase

    problems: List[str] = []
    phase = deployment_phase.current_phase()
    if phase != deployment_phase.PHASE0:
        problems.append(f"deployment phase is {phase or 'unset'}, not phase0")
    flags = await controls.feature_flags(session)
    live = flags.get("live_trading") or {"on": True, "tenants": []}
    if live.get("on"):
        problems.append("live_trading is on for everyone")
    elif live.get("tenants"):
        problems.append(f"live_trading is on for {len(live['tenants'])} organisation(s)")
    paper_only = flags.get("phase0_paper_only")
    if not paper_only or not paper_only.get("on"):
        problems.append("phase0_paper_only is not on")
    return problems


async def _tenant(session: Any, stamp: str) -> Any:
    from sqlalchemy import select

    from app.db.models import Tenant
    rows = list(await session.scalars(select(Tenant).where(Tenant.name == tenant_name(stamp))))
    if len(rows) > 1:
        raise Refused(f"{len(rows)} organisations are named {tenant_name(stamp)} - not touching any of them")
    return rows[0] if rows else None


async def _users(session: Any, tenant_id: int) -> List[Any]:
    from sqlalchemy import select

    from app.db.models import User
    return list(await session.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.id)))


def _check_ours(stamp: str, users: Sequence[Any]) -> None:
    """Every member must be one this tool made for this stamp, or the organisation is not ours to remove."""
    foreign = [u.id for u in users if not is_loadtest_email(stamp, u.email)]
    if foreign:
        raise Refused(f"{tenant_name(stamp)} has {len(foreign)} member(s) that are not load-test users (ids {foreign[:5]}) - "
                      "not a load-test organisation; nothing changed")


async def seed(stamp: str, count: int, factory: Any = None) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    from app.audit.log import write_audit_log
    from app.auth.security import hash_password
    from app.core.enums import UserRole
    from app.db.models import Tenant, User
    from app.webhooks.routes import hash_webhook_token

    check_stamp(stamp)
    if not 1 <= count <= MAX_USERS:
        raise Refused(f"count must be 1..{MAX_USERS}")
    password = secrets.token_urlsafe(PASSWORD_BYTES)
    hashed = hash_password(password)
    async with _factory(factory)() as session:
        problems = await posture_problems(session)
        if problems:
            raise Refused("not a PAPER-only Phase 0 deployment: " + "; ".join(problems) + " - nothing created")
        if await _tenant(session, stamp) is not None:
            raise Refused(f"{tenant_name(stamp)} exists already - nothing created")
        tenant = Tenant(name=tenant_name(stamp), webhook_token=None, webhook_token_hash=hash_webhook_token(secrets.token_urlsafe(24)))
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        # Recorded before the users exist: if the run stops half-way, the chain already says the organisation is synthetic.
        await write_audit_log(session, tenant_id, None, SEED_EVENT, f"{tenant_name(stamp)}: {count} synthetic PAPER users, no broker")
        await session.commit()
        now = _now()
        emails: List[str] = []
        for i in range(count):
            email = email_for(stamp, i)
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hashed, email_verified_at=now,
                             role=(UserRole.OWNER if i == 0 else UserRole.USER).value))
            emails.append(email)
            if len(emails) % 100 == 0:
                await session.commit()
        await session.commit()
    return [(e, password) for e in emails], {"tenant_id": tenant_id, "tenant": tenant_name(stamp), "users": len(emails)}


async def _reflect(session: Any) -> Any:
    from sqlalchemy import MetaData

    md = MetaData()
    conn = await session.connection()
    await conn.run_sync(lambda sync_conn: md.reflect(bind=sync_conn))
    return md


def _sorted(md: Any) -> List[Any]:
    """Tables in dependency order. A foreign-key cycle (custom_strategies <-> strategy_versions) is left out of the sort
    with a warning; the delete passes retry what a foreign key refused, so the order only needs to be mostly right."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return list(md.sorted_tables)


def _conditions(table: Any, tenant_ids: Sequence[int], user_ids: Sequence[int]) -> List[Any]:
    """Columns of `table` that name one of the organisation's rows: `tenant_id` / any FK to tenants.id with the
    organisation's id; `user_id` / any FK to users.id with one of its users' ids."""
    out: List[Any] = []
    for col in table.columns:
        targets = {fk.column.table.name for fk in col.foreign_keys}
        if (col.name == "tenant_id" or "tenants" in targets) and tenant_ids:
            out.append(col.in_(list(tenant_ids)))
        elif (col.name == "user_id" or "users" in targets) and user_ids:
            out.append(col.in_(list(user_ids)))
    return out


def _is_ledger(table: Any) -> bool:
    names = {c.name for c in table.columns}
    return table.name in LEDGERS or {"prev_hash", "hash"} <= names


async def count_rows(session: Any, tenant_id: int, user_ids: Sequence[int], md: Any = None) -> Dict[str, int]:
    from sqlalchemy import func, or_, select

    md = md if md is not None else await _reflect(session)
    out: Dict[str, int] = {}
    for table in _sorted(md):
        if table.name in ("tenants", "users"):
            continue
        conds = _conditions(table, [tenant_id], user_ids)
        if not conds:
            continue
        n = int(await session.scalar(select(func.count()).select_from(table).where(or_(*conds))) or 0)
        if n:
            out[table.name] = n
    return out


async def counts(stamp: str, factory: Any = None) -> Dict[str, Any]:
    check_stamp(stamp)
    async with _factory(factory)() as session:
        tenant = await _tenant(session, stamp)
        if tenant is None:
            return {"tenant": tenant_name(stamp), "exists": False, "rows": {}}
        users = await _users(session, tenant.id)
        return {"tenant": tenant_name(stamp), "exists": True, "tenant_id": tenant.id, "status": tenant.status,
                "users": len(users), "active_users": sum(1 for u in users if u.is_active),
                "rows": await count_rows(session, tenant.id, [u.id for u in users])}


async def cleanup(stamp: str, factory: Any = None, verify_audit: bool = False) -> Dict[str, Any]:
    """Removes what the run made (see the module docstring). Idempotent: a second run finds nothing more to delete."""
    from sqlalchemy import delete, func, or_, select, update
    from sqlalchemy.exc import DBAPIError

    from app.audit.log import write_audit_log
    from app.auth.security import hash_password
    from app.db.models import Tenant, User

    check_stamp(stamp)
    tombstone_hash = hash_password(secrets.token_urlsafe(32))   # the secret is dropped here: no password matches it
    report: Dict[str, Any] = {"tenant": tenant_name(stamp), "deleted": {}, "ledger_rows_kept": {}, "remaining": {}, "failed": {}}
    async with _factory(factory)() as session:
        tenant = await _tenant(session, stamp)
        if tenant is None:
            report.update(exists=False, clean=True, note="no such organisation (never created, or removed entirely)")
            return report
        tenant_id = tenant.id
        users = await _users(session, tenant_id)
        _check_ours(stamp, users)
        user_ids = [u.id for u in users]
        md = await _reflect(session)
        tables = [t for t in reversed(_sorted(md)) if t.name not in ("tenants", "users")]
        # Children before parents (reversed dependency order); a delete refused by a foreign key is retried on the next
        # pass, after the rows that pointed at it are gone. Each delete in its own savepoint.
        pending = []
        for table in tables:
            conds = _conditions(table, [tenant_id], user_ids)
            if not conds:
                continue
            n = int(await session.scalar(select(func.count()).select_from(table).where(or_(*conds))) or 0)
            if not n:
                continue
            if _is_ledger(table):
                report["ledger_rows_kept"][table.name] = n
                continue
            pending.append((table, conds))
        for _ in range(3):
            left = []
            for table, conds in pending:
                try:
                    async with session.begin_nested():
                        result = await session.execute(delete(table).where(or_(*conds)))
                    report["deleted"][table.name] = report["deleted"].get(table.name, 0) + int(result.rowcount or 0)
                    report["failed"].pop(table.name, None)
                except DBAPIError as exc:
                    report["failed"][table.name] = str(getattr(exc, "orig", exc))[:200]
                    left.append((table, conds))
            pending = left
            if not pending:
                break
        # Users a ledger row names stay as tombstones; the rest go.
        named: set = set()
        for table in _sorted(md):
            if not _is_ledger(table) or table.name not in report["ledger_rows_kept"]:
                continue
            for col in table.columns:
                targets = {fk.column.table.name for fk in col.foreign_keys}
                if col.name == "user_id" or "users" in targets:
                    named.update(v for v in await session.scalars(select(col).where(col.in_(user_ids)).distinct()) if v is not None)
        drop = [uid for uid in user_ids if uid not in named]
        keep = [uid for uid in user_ids if uid in named]
        if drop:
            try:
                async with session.begin_nested():
                    await session.execute(delete(User).where(User.id.in_(drop), User.tenant_id == tenant_id))
                report["deleted"]["users"] = len(drop)
            except DBAPIError as exc:
                report["failed"]["users"] = str(getattr(exc, "orig", exc))[:200]
                keep = user_ids
        if keep:
            await session.execute(update(User).where(User.id.in_(keep), User.tenant_id == tenant_id).values(
                is_active=False, hashed_password=tombstone_hash, mfa_enabled=False, mfa_secret_encrypted=None, scope_overrides=None))
        tenant_kept = bool(report["ledger_rows_kept"]) or bool(keep) or "users" in report["failed"]
        changed = bool(report["deleted"]) or tenant.status != "suspended" or any(u.is_active for u in users)
        detail = (f"{tenant_name(stamp)}: {sum(report['deleted'].values())} rows deleted, {len(keep)} users kept as inactive "
                  f"tombstones (named by the audit trail), ledgers untouched")
        if tenant_kept:
            await session.execute(update(Tenant).where(Tenant.id == tenant_id).values(
                status="suspended", status_reason=f"loadtest {stamp}: synthetic organisation, cleaned up"))
            if changed:                                   # a repeated cleanup that finds nothing adds no audit row
                await write_audit_log(session, tenant_id, None, CLEAN_EVENT, detail)
        else:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await write_audit_log(session, None, None, CLEAN_EVENT, detail + "; organisation removed")
        await session.commit()

    # Count again, in a new transaction: what is left besides the ledgers and the tombstones.
    async with _factory(factory)() as session:
        tenant = await _tenant(session, stamp)
        if tenant is not None:
            users = await _users(session, tenant.id)
            md = await _reflect(session)
            ledgers = {t.name for t in _sorted(md) if _is_ledger(t)}
            rows = await count_rows(session, tenant.id, [u.id for u in users], md)
            report["remaining"] = {k: v for k, v in rows.items() if k not in ledgers}
            report["ledger_rows_kept"] = {k: v for k, v in rows.items() if k in ledgers}
            report["tombstone"] = {"tenant_id": tenant.id, "status": tenant.status, "users_kept": len(users),
                                   "active_users": sum(1 for u in users if u.is_active),
                                   "usable_passwords": sum(1 for u in users if u.hashed_password != tombstone_hash)}
        else:
            report["tombstone"] = None
        report["exists"] = tenant is not None
        tomb = report["tombstone"] or {}
        report["clean"] = (not report["remaining"] and not report["failed"]
                           and not tomb.get("active_users") and not tomb.get("usable_passwords"))
        if verify_audit:
            from app.audit.log import verify_audit_chain
            intact, broken = await verify_audit_chain(session, since_anchor=True)
            report["audit_chain"] = "intact" if intact else f"broken at row {broken}"
    return report


async def leftovers(factory: Any = None) -> List[Dict[str, Any]]:
    """Every organisation named loadtest-<stamp>, with its state - what a manual cleanup would look at."""
    from sqlalchemy import select

    from app.db.models import Tenant
    out = []
    async with _factory(factory)() as session:
        for tenant in await session.scalars(select(Tenant).where(Tenant.name.like(TENANT_PREFIX + "%")).order_by(Tenant.id)):
            stamp = tenant.name[len(TENANT_PREFIX):]
            if not STAMP_RE.fullmatch(stamp):
                continue
            users = await _users(session, tenant.id)
            out.append({"stamp": stamp, "tenant_id": tenant.id, "status": tenant.status, "users": len(users),
                        "active_users": sum(1 for u in users if u.is_active),
                        "ours": all(is_loadtest_email(stamp, u.email) for u in users)})
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The real-host load test's synthetic organisation (see the module docstring).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--stamp", required=True)
    p_seed.add_argument("--count", type=int, default=500)
    p_count = sub.add_parser("count")
    p_count.add_argument("--stamp", required=True)
    p_clean = sub.add_parser("cleanup")
    p_clean.add_argument("--stamp", required=True)
    p_clean.add_argument("--verify-audit", action="store_true", help="also verify the audit chain from its latest anchor")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)     # stdout carries the results only
    try:
        if args.cmd == "seed":
            accounts, info = asyncio.run(seed(args.stamp, args.count))
            sys.stdout.write("".join(f"account,{e},{p}\n" for e, p in accounts))
            sys.stdout.flush()
            print(json.dumps(info), file=sys.stderr)
        elif args.cmd == "count":
            print(json.dumps(asyncio.run(counts(args.stamp))))
        elif args.cmd == "cleanup":
            report = asyncio.run(cleanup(args.stamp, verify_audit=args.verify_audit))
            print(json.dumps(report))
            return 0 if report["clean"] else 1
        else:
            print(json.dumps(asyncio.run(leftovers())))
    except Refused as exc:
        print(json.dumps({"refused": str(exc)}))
        print(f"loadtest_org: refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
