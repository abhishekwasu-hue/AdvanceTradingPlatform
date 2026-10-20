#!/usr/bin/env python
"""Real-host load test (`deploy/atp.sh loadtest`): the synthetic organisation it measures with - made, listed, removed.

Runs inside the release's backend image (`docker compose run --rm --no-deps -T backend python scripts/loadtest_org.py`),
against the host's own database, as the app connects to it (DATABASE_URL). The production-side twin of the CI stack's
`deploy/ci/seed_users.py`: the same direct writes (one password, hashed once; the rows a sign-up makes, its data key
included), with these differences that matter on the real host:

* **One dedicated organisation**, `loadtest-<stamp>` (stamp = `YYYYMMDD-HHMMSS`, IST, within 15 minutes of now), with N
  users `lt-<stamp>-0000@example.com` ... (the first is its OWNER, the rest traders). Email verified, PAPER only. Made in
  ONE transaction with its `loadtest_org_seeded` audit row: it exists whole, or not at all.
* **No broker credentials, no flag change.** Nothing broker-side exists for these users, so no request of theirs can
  reach a real broker (a chart or a screener run answers 409 "no broker session" before any broker call); no flag,
  secret, role or other organisation is written. `seed` refuses unless the deployment is PAPER only: phase0,
  `live_trading` off for everyone, `phase0_paper_only` on.
* **Identity by record, never by name.** A name and an email are both things any user can choose (an owner renames
  the organisation; a sign-up picks an address). What makes an organisation a load-test one is the hash-chained audit
  row `loadtest_org_seeded` that only `seed` writes, for that organisation id, at the stamp's time, with the
  organisation created in the same transaction; plus - for every member - the user ids `seed` reported (kept by the
  caller in `.deploy/loadtest/<stamp>/org.json` and handed to `cleanup` on stdin). Without that file, a member counts
  only when its address is `lt-<stamp>-NNNN@example.com` AND it was created in the seeding transaction. An organisation
  with no member, a renamed one, or one with any other member is refused, whole.
* **Removal limited to what a run writes.** `cleanup` deletes only from an allowlist of tables a seed and the load's
  scenarios write (measured: sessions, login events, notifications and their reads / deliveries, PAPER accounts,
  meters, the data key, verification / reset / MFA / AI-acknowledgement rows), by the organisation id - or by user id
  where a table has no organisation column. It never deletes through a "who acted" column. Before deleting anything it
  refuses if the organisation or its users own rows in any other table (marketplace listings, orders, ...), and says
  which. Ledgers (`audit_logs`, `audit_anchors`, `orders`, `order_events`, `trades`) are never deleted from, whatever
  role the app connects as. A user any remaining row still names is kept as an inert tombstone (deactivated, password
  replaced by the hash of a random secret nobody holds - a login is an ordinary 401); the others are deleted, which
  can cascade into nothing because nothing names them. The organisation is never deleted: it stays `suspended`, named
  by its audit rows. Then it counts again and says what is left.

    python scripts/loadtest_org.py seed --stamp 20261021-101500 --count 500
    python scripts/loadtest_org.py count --stamp 20261021-101500
    python scripts/loadtest_org.py cleanup --stamp 20261021-101500 [--tenant-id N] [--org-file -] [--verify-audit]
    python scripts/loadtest_org.py list

`seed` prints `account,<email>,<password>` lines and one `org,<json>` line (tenant id and user ids) on stdout, and one
JSON line on stderr; the other commands print one JSON line on stdout, last. Logging goes to stderr. Exit 0 ok,
1 failed, 2 refused.
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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAMP_RE = re.compile(r"^[0-9]{8}-[0-9]{6}$", re.ASCII)
TENANT_PREFIX = "loadtest-"
EMAIL_DOMAIN = "example.com"
MAX_USERS = 1000
PASSWORD_BYTES = 18
IST = ZoneInfo("Asia/Kolkata")
STAMP_SKEW = timedelta(minutes=15)       # seed: the stamp against now; cleanup: the seed record against the stamp
SAME_TRANSACTION = timedelta(minutes=5)  # the organisation / its users against their seed record (one transaction)
# Never deleted from, whatever role the app connects as: the audit log is a hash chain, and the application role may
# not delete from any of these (scripts/db_roles.sql).
LEDGERS = frozenset({"audit_logs", "audit_anchors", "orders", "order_events", "trades"})
# What a seed and the load's scenarios write for the organisation (measured on the test app: login -> user_sessions,
# login_events, notifications (new device) + audit_logs; GET /api/paper/account -> paper_accounts; seed -> tenant_keys).
# The rest are what those paths can add (deliveries, reads, meters, verification / reset / MFA / AI acknowledgement).
ALLOWLIST = frozenset({"user_sessions", "login_events", "notifications", "notification_reads", "alert_deliveries", "paper_accounts",
                       "usage_records", "tenant_keys", "email_verifications", "password_resets", "mfa_backup_codes",
                       "ai_acknowledgements"})
assert not ALLOWLIST & LEDGERS
SEED_EVENT = "loadtest_org_seeded"
CLEAN_EVENT = "loadtest_org_cleaned"


class Refused(Exception):
    """The command would act on something that is not provably a load-test organisation, or on a deployment that is
    not PAPER only, or on rows a load test does not write."""


def tenant_name(stamp: str) -> str:
    return TENANT_PREFIX + stamp


def email_for(stamp: str, index: int) -> str:
    return f"lt-{stamp}-{index:04d}@{EMAIL_DOMAIN}"


def is_loadtest_email(stamp: str, email: str) -> bool:
    return re.fullmatch(rf"lt-{re.escape(stamp)}-[0-9]{{4}}@{re.escape(EMAIL_DOMAIN)}", email or "", re.ASCII) is not None


def check_stamp(stamp: str) -> str:
    if not STAMP_RE.fullmatch(stamp or ""):
        raise Refused(f"stamp {stamp!r} is not YYYYMMDD-HHMMSS")
    return stamp


def stamp_time(stamp: str) -> datetime:
    """The stamp (IST) as an aware UTC time."""
    try:
        return datetime.strptime(check_stamp(stamp), "%Y%m%d-%H%M%S").replace(tzinfo=IST).astimezone(timezone.utc)
    except ValueError:
        raise Refused(f"stamp {stamp!r} is not a date and time") from None


def _utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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


async def _users(session: Any, tenant_id: int) -> List[Any]:
    from sqlalchemy import select

    from app.db.models import User
    return list(await session.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.id)))


async def _seed_records(session: Any, stamp: Optional[str] = None) -> List[Any]:
    from sqlalchemy import select

    from app.db.models import AuditLogRecord
    rows = list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.event == SEED_EVENT).order_by(AuditLogRecord.id)))
    if stamp is not None:
        rows = [r for r in rows if (r.detail or "").startswith(tenant_name(stamp) + ":")]
    return rows


def _stamp_of(record: Any) -> Optional[str]:
    head = (record.detail or "").split(":", 1)[0]
    stamp = head[len(TENANT_PREFIX):] if head.startswith(TENANT_PREFIX) else ""
    return stamp if STAMP_RE.fullmatch(stamp) else None


class Org:
    def __init__(self, stamp: str, record: Any, tenant: Any, users: List[Any]) -> None:
        self.stamp, self.record, self.tenant, self.users = stamp, record, tenant, users

    @property
    def user_ids(self) -> List[int]:
        return [u.id for u in self.users]


async def identify(session: Any, stamp: str, tenant_id: Optional[int] = None, user_ids: Optional[Iterable[int]] = None) -> Org:
    """The load-test organisation of `stamp`, proven by its seed record (see the module docstring), or Refused."""
    from app.audit.log import _compute_hash
    from app.db.models import Tenant

    when = stamp_time(stamp)
    records = [r for r in await _seed_records(session, stamp) if tenant_id is None or r.tenant_id == tenant_id]
    if not records:
        raise Refused(f"no {SEED_EVENT} audit record for {tenant_name(stamp)}"
                      + (f" and organisation #{tenant_id}" if tenant_id is not None else "") + " - not a load-test organisation")
    if len(records) > 1:
        raise Refused(f"{len(records)} {SEED_EVENT} records for {tenant_name(stamp)} - not touching any of them")
    record = records[0]
    if _compute_hash(record.prev_hash, record.tenant_id, record.user_id, record.event, record.detail, record.created_at) != record.hash:
        raise Refused(f"the {SEED_EVENT} record #{record.id} does not match its own hash - not touching it")
    if record.tenant_id is None or record.user_id is not None:
        raise Refused(f"the {SEED_EVENT} record #{record.id} names no organisation, or names a user - not one seed wrote")
    seeded_at = _utc(record.created_at)
    if abs(seeded_at - when) > STAMP_SKEW:
        raise Refused(f"the {SEED_EVENT} record #{record.id} was written at {seeded_at:%Y-%m-%d %H:%M} UTC, not at the stamp's time")
    tenant = await session.get(Tenant, record.tenant_id)
    if tenant is None:
        raise Refused(f"organisation #{record.tenant_id} of {tenant_name(stamp)} does not exist")
    if tenant.name != tenant_name(stamp):
        raise Refused(f"organisation #{tenant.id} is now named {tenant.name!r}, not {tenant_name(stamp)} - renamed, not touching it")
    if abs(_utc(tenant.created_at) - seeded_at) > SAME_TRANSACTION:
        raise Refused(f"organisation #{tenant.id} was not created with its seed record - not touching it")
    users = await _users(session, tenant.id)
    if not users:
        raise Refused(f"organisation #{tenant.id} has no member - a seed makes its users with it, so this is not one; not touching it")
    allowed = set(user_ids) if user_ids is not None else None
    foreign = [u.id for u in users if not is_loadtest_email(stamp, u.email)
               or (allowed is not None and u.id not in allowed)
               or (allowed is None and abs(_utc(u.created_at) - seeded_at) > SAME_TRANSACTION)]
    if foreign:
        raise Refused(f"organisation #{tenant.id} has {len(foreign)} member(s) its seed did not make (ids {foreign[:5]}) - "
                      "not touching it")
    return Org(stamp, record, tenant, users)


async def seed(stamp: str, count: int, factory: Any = None, now: Optional[datetime] = None) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    from app.audit.log import write_audit_log
    from app.auth.security import hash_password
    from app.core.enums import UserRole
    from app.db.models import Tenant, User
    from app.secrets_store.envelope import ensure_tenant_key
    from app.webhooks.routes import hash_webhook_token

    if abs(stamp_time(stamp) - (now or _now())) > STAMP_SKEW:
        raise Refused(f"stamp {stamp} is not the current IST time - nothing created")
    if not 1 <= count <= MAX_USERS:
        raise Refused(f"count must be 1..{MAX_USERS}")
    password = secrets.token_urlsafe(PASSWORD_BYTES)
    hashed = hash_password(password)
    async with _factory(factory)() as session:
        problems = await posture_problems(session)
        if problems:
            raise Refused("not a PAPER-only Phase 0 deployment: " + "; ".join(problems) + " - nothing created")
        if await _seed_records(session, stamp):
            raise Refused(f"{tenant_name(stamp)} was seeded already - nothing created")
        # One transaction: the organisation, its data key (as a sign-up), its seed record and every user.
        tenant = Tenant(name=tenant_name(stamp), webhook_token=None, webhook_token_hash=hash_webhook_token(secrets.token_urlsafe(24)))
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        await ensure_tenant_key(session, tenant_id)
        await write_audit_log(session, tenant_id, None, SEED_EVENT, f"{tenant_name(stamp)}: {count} synthetic PAPER users, no broker")
        created = _now()
        users = [User(tenant_id=tenant_id, email=email_for(stamp, i), hashed_password=hashed, email_verified_at=created,
                      role=(UserRole.OWNER if i == 0 else UserRole.USER).value) for i in range(count)]
        session.add_all(users)
        await session.flush()
        user_ids = [u.id for u in users]
        await session.commit()
    accounts = [(email_for(stamp, i), password) for i in range(count)]
    return accounts, {"stamp": stamp, "tenant_id": tenant_id, "tenant": tenant_name(stamp), "users": count, "user_ids": user_ids}


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


def _owner_condition(table: Any, tenant_id: int, user_ids: Sequence[int]) -> Any:
    """The organisation's rows of `table`: by its `tenant_id` column, or by `user_id` where it has no organisation column.
    Never through another column (who created / changed / approved something)."""
    if "tenant_id" in table.c:
        return table.c.tenant_id == tenant_id
    if "user_id" in table.c and user_ids:
        return table.c.user_id.in_(list(user_ids))
    return None


async def _owned(session: Any, md: Any, tenant_id: int, user_ids: Sequence[int]) -> Dict[str, int]:
    from sqlalchemy import func, select

    out: Dict[str, int] = {}
    for table in _sorted(md):
        if table.name in ("tenants", "users"):
            continue
        cond = _owner_condition(table, tenant_id, user_ids)
        if cond is None:
            continue
        n = int(await session.scalar(select(func.count()).select_from(table).where(cond)) or 0)
        if n:
            out[table.name] = n
    return out


async def _named_users(session: Any, md: Any, user_ids: Sequence[int]) -> set:
    """Users any row anywhere still names, through any column that references users.id."""
    from sqlalchemy import select

    named: set = set()
    for table in _sorted(md):
        if table.name == "users":
            continue
        for col in table.columns:
            if any(fk.column.table.name == "users" for fk in col.foreign_keys) or (col.name == "user_id" and not col.foreign_keys):
                named.update(v for v in await session.scalars(select(col).where(col.in_(list(user_ids))).distinct()) if v is not None)
    return named


def _split(rows: Dict[str, int]) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    """(allowlisted, ledgers, anything else) of an ownership count."""
    allow = {k: v for k, v in rows.items() if k in ALLOWLIST}
    ledger = {k: v for k, v in rows.items() if k in LEDGERS}
    other = {k: v for k, v in rows.items() if k not in ALLOWLIST and k not in LEDGERS}
    return allow, ledger, other


async def counts(stamp: str, factory: Any = None, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    async with _factory(factory)() as session:
        org = await identify(session, stamp, tenant_id)
        rows = await _owned(session, await _reflect(session), org.tenant.id, org.user_ids)
        return {"tenant": tenant_name(stamp), "tenant_id": org.tenant.id, "status": org.tenant.status, "users": len(org.users),
                "active_users": sum(1 for u in org.users if u.is_active), "rows": rows}


async def cleanup(stamp: str, factory: Any = None, verify_audit: bool = False, tenant_id: Optional[int] = None,
                  user_ids: Optional[Sequence[int]] = None, absent_ok: bool = False) -> Dict[str, Any]:
    """Removes what the run made (see the module docstring). Idempotent: a second run finds nothing more to delete.
    `absent_ok` (the run's own cleanup when its seed did not report back): no seed record at all for this stamp means the
    seeding transaction never committed - nothing to remove, clean. Any other doubt still refuses."""
    from sqlalchemy import delete, update
    from sqlalchemy.exc import DBAPIError

    from app.audit.log import write_audit_log
    from app.auth.security import hash_password
    from app.db.models import Tenant, User

    tombstone_hash = hash_password(secrets.token_urlsafe(32))   # the secret is dropped here: no password matches it
    report: Dict[str, Any] = {"tenant": tenant_name(stamp), "deleted": {}, "ledger_rows_kept": {}, "remaining": {}, "failed": {}}
    async with _factory(factory)() as session:
        if absent_ok and tenant_id is None and not await _seed_records(session, stamp):
            report.update(exists=False, clean=True, tombstone=None, note="the seed never committed: nothing was created")
            return report
        org = await identify(session, stamp, tenant_id, user_ids)
        tid, uids = org.tenant.id, org.user_ids
        report["tenant_id"] = tid
        md = await _reflect(session)
        allow, ledger, other = _split(await _owned(session, md, tid, uids))
        if other:
            raise Refused(f"organisation #{tid} owns rows a load test does not write: "
                          + ", ".join(f"{k} {v}" for k, v in sorted(other.items()))
                          + " - nothing deleted; look at them first (docs/deploy/LOADTEST_REAL_HOST.md, manual cleanup)")
        report["ledger_rows_kept"] = ledger
        pending = [t for t in reversed(_sorted(md)) if t.name in allow and t.name not in LEDGERS]
        for _ in range(3):
            left = []
            for table in pending:
                try:
                    async with session.begin_nested():
                        result = await session.execute(delete(table).where(_owner_condition(table, tid, uids)))
                    report["deleted"][table.name] = report["deleted"].get(table.name, 0) + int(result.rowcount or 0)
                    report["failed"].pop(table.name, None)
                except DBAPIError as exc:
                    report["failed"][table.name] = str(getattr(exc, "orig", exc))[:200]
                    left.append(table)
            pending = left
            if not pending:
                break
        named = await _named_users(session, md, uids)
        drop = [u for u in uids if u not in named]
        keep = [u for u in uids if u in named]
        if drop:
            try:
                async with session.begin_nested():
                    await session.execute(delete(User).where(User.id.in_(drop), User.tenant_id == tid))
                report["deleted"]["users"] = len(drop)
            except DBAPIError as exc:
                report["failed"]["users"] = str(getattr(exc, "orig", exc))[:200]
                keep = uids
        if keep:
            await session.execute(update(User).where(User.id.in_(keep), User.tenant_id == tid).values(
                is_active=False, hashed_password=tombstone_hash, mfa_enabled=False, mfa_secret_encrypted=None, scope_overrides=None))
        changed = bool(report["deleted"]) or org.tenant.status != "suspended" or any(u.is_active for u in org.users)
        await session.execute(update(Tenant).where(Tenant.id == tid).values(
            status="suspended", status_reason=f"loadtest {stamp}: synthetic organisation, cleaned up"))
        if changed:                                           # a repeated cleanup that finds nothing adds no audit row
            await write_audit_log(session, tid, None, CLEAN_EVENT,
                                  f"{tenant_name(stamp)}: {sum(report['deleted'].values())} rows deleted, {len(keep)} users kept as "
                                  "inactive tombstones (named by the audit trail), ledgers untouched")
        await session.commit()

    # Count again, in a new transaction: what is left besides the ledgers and the tombstones.
    async with _factory(factory)() as session:
        tenant = await session.get(Tenant, tid)
        users = await _users(session, tid)
        allow, ledger, other = _split(await _owned(session, await _reflect(session), tid, [u.id for u in users]))
        report["remaining"] = {**allow, **other}
        report["ledger_rows_kept"] = ledger
        report["tombstone"] = {"tenant_id": tid, "status": tenant.status if tenant else None, "users_kept": len(users),
                               "active_users": sum(1 for u in users if u.is_active),
                               "usable_passwords": sum(1 for u in users if u.hashed_password != tombstone_hash)}
        report["exists"] = tenant is not None
        tomb = report["tombstone"]
        report["clean"] = (not report["remaining"] and not report["failed"] and tomb["status"] == "suspended"
                           and not tomb["active_users"] and not tomb["usable_passwords"])
        if verify_audit:
            from app.audit.log import verify_audit_chain
            intact, broken = await verify_audit_chain(session, since_anchor=True)
            report["audit_chain"] = "intact" if intact else f"broken at row {broken}"
    return report


async def leftovers(factory: Any = None) -> List[Dict[str, Any]]:
    """Every organisation a seed record names (never found by name), and whether it is still provably a load-test one."""
    from app.db.models import Tenant

    out = []
    async with _factory(factory)() as session:
        for record in await _seed_records(session):
            stamp = _stamp_of(record)
            if stamp is None or record.tenant_id is None:
                continue
            tenant = await session.get(Tenant, record.tenant_id)
            users = await _users(session, record.tenant_id) if tenant is not None else []
            row: Dict[str, Any] = {"stamp": stamp, "tenant_id": record.tenant_id, "name": tenant.name if tenant else None,
                                   "status": tenant.status if tenant else None, "users": len(users),
                                   "active_users": sum(1 for u in users if u.is_active)}
            try:
                await identify(session, stamp, record.tenant_id)
                row.update(ours=True, reason="")
            except Refused as exc:
                done = tenant is not None and not users and tenant.status == "suspended" and (tenant.status_reason or "").startswith(f"loadtest {stamp}:")
                row.update(ours=False, reason="already cleaned up (suspended, no user left)" if done else str(exc))
            out.append(row)
    return out


def read_org_file(text: str, stamp: str) -> Tuple[int, List[int]]:
    """seed's `org,<json>` (or the bare JSON) for this stamp -> (tenant id, user ids)."""
    line = (text or "").strip().splitlines()[-1] if (text or "").strip() else ""
    try:
        data = json.loads(line[4:] if line.startswith("org,") else line)
    except ValueError:
        raise Refused("the org file is not JSON") from None
    if (not isinstance(data, dict) or data.get("stamp") != stamp or not isinstance(data.get("tenant_id"), int)
            or not isinstance(data.get("user_ids"), list) or not data["user_ids"]):
        raise Refused("the org file is not the one seed wrote for this stamp")
    return data["tenant_id"], [int(u) for u in data["user_ids"]]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The real-host load test's synthetic organisation (see the module docstring).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--stamp", required=True)
    p_seed.add_argument("--count", type=int, default=500)
    p_count = sub.add_parser("count")
    p_count.add_argument("--stamp", required=True)
    p_count.add_argument("--tenant-id", type=int, default=None)
    p_clean = sub.add_parser("cleanup")
    p_clean.add_argument("--stamp", required=True)
    p_clean.add_argument("--tenant-id", type=int, default=None, help="the organisation id seed reported (or the seed record names)")
    p_clean.add_argument("--org-file", default=None, help="seed's org line: tenant id and user ids; '-' = stdin")
    p_clean.add_argument("--verify-audit", action="store_true", help="also verify the audit chain from its latest anchor")
    p_clean.add_argument("--absent-ok", action="store_true", help="no seed record for the stamp = nothing was created (clean)")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)     # stdout carries the results only
    try:
        if args.cmd == "seed":
            accounts, info = asyncio.run(seed(args.stamp, args.count))
            sys.stdout.write("".join(f"account,{e},{p}\n" for e, p in accounts))
            sys.stdout.write("org," + json.dumps({k: info[k] for k in ("stamp", "tenant_id", "user_ids")}) + "\n")
            sys.stdout.flush()
            print(json.dumps({k: v for k, v in info.items() if k != "user_ids"}), file=sys.stderr)
        elif args.cmd == "count":
            print(json.dumps(asyncio.run(counts(args.stamp, tenant_id=args.tenant_id))))
        elif args.cmd == "cleanup":
            check_stamp(args.stamp)
            tenant_id, user_ids = args.tenant_id, None
            if args.org_file:
                text = sys.stdin.read() if args.org_file == "-" else open(args.org_file, encoding="utf-8").read()
                file_tid, user_ids = read_org_file(text, args.stamp)
                if tenant_id is not None and tenant_id != file_tid:
                    raise Refused("--tenant-id and the org file disagree")
                tenant_id = file_tid
            report = asyncio.run(cleanup(args.stamp, verify_audit=args.verify_audit, tenant_id=tenant_id, user_ids=user_ids,
                                         absent_ok=args.absent_ok))
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
