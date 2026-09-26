"""CLI: verify the audit-log hash chain of the configured database (Phase E3 backup rehearsal).

    DATABASE_URL=postgresql+asyncpg://... python -m app.audit.verify_chain

Prints `intact` (exit 0) or `broken at row <id>` (exit 1). Used by scripts/backup/verify_backup.sh
against the restored scratch copy; also handy against production for the monthly check.
"""
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audit.log import verify_audit_chain
from app.core.config import DATABASE_URL


async def _main() -> int:
    engine = create_async_engine(DATABASE_URL)
    try:
        async with async_sessionmaker(engine)() as session:
            intact, first_broken = await verify_audit_chain(session)
    finally:
        await engine.dispose()
    if intact:
        print("intact")
        return 0
    print(f"broken at row {first_broken}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
