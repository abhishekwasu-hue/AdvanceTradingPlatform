#!/usr/bin/env python
"""Phase N4 / section 51: refuse schema migrations during the NSE trading session.

    python scripts/migrate_guard.py [--force] [--dry-run]

* No pending migration -> exits 0 immediately (a plain restart during market hours is fine).
* Pending migration, market closed (or --force / MIGRATION_FORCE=1) -> runs `alembic upgrade head`.
* Pending migration, market open -> exits 3 with the next allowed window, and nothing runs.

The Dockerfile CMD calls this before uvicorn, so a new image rolled out at 11:00 IST with a schema
change refuses to start rather than altering tables under a live worker. Deploy after 15:30 IST or
force it knowingly. Holidays come from the exchange_holidays table when reachable; otherwise
weekday/hours only (the safe direction: a holiday is then treated as a possible trading day).
"""
import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.market_data.calendar import IST, session_status  # noqa: E402


def pending_migrations() -> bool:
    current = subprocess.run([sys.executable, "-m", "alembic", "current"], capture_output=True, text=True)
    heads = subprocess.run([sys.executable, "-m", "alembic", "heads"], capture_output=True, text=True)
    if current.returncode != 0 or heads.returncode != 0:
        print(current.stderr or heads.stderr, file=sys.stderr)
        return True  # cannot tell -> treat as pending so the guard applies
    current_revs = {line.split()[0] for line in current.stdout.splitlines() if line.strip() and not line.startswith("INFO")}
    head_revs = {line.split()[0] for line in heads.stdout.splitlines() if line.strip() and not line.startswith("INFO")}
    return not head_revs or current_revs != head_revs


async def load_holidays():
    try:
        from app.db.session import _session_factory
        from app.market_data.calendar import load_holidays
        async with _session_factory() as session:
            return await load_holidays(session, "NSE")
    except Exception as exc:  # noqa: BLE001 - DB may not be reachable before first migration
        print(f"guard: holidays unavailable ({type(exc).__name__}); using weekday/hours only")
        return ()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="migrate even during market hours")
    parser.add_argument("--dry-run", action="store_true", help="decide, print, do not run alembic")
    args = parser.parse_args()
    force = args.force or os.environ.get("MIGRATION_FORCE", "").lower() in {"1", "true", "yes"}

    if not pending_migrations():
        print("guard: schema already at head - nothing to migrate")
        return 0
    now = datetime.now(timezone.utc)
    status = session_status(now, asyncio.run(load_holidays()))
    if status.is_open and not force:
        print(f"guard: REFUSING to migrate - NSE session is open ({now.astimezone(IST):%H:%M} IST). "
              "Deploy after 15:30 IST, or re-run with --force / MIGRATION_FORCE=1 knowing positions may be open.", file=sys.stderr)
        return 3
    print(f"guard: market {'open (FORCED)' if status.is_open else 'closed'} - running alembic upgrade head")
    if args.dry_run:
        return 0
    return subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"])


if __name__ == "__main__":
    sys.exit(main())
