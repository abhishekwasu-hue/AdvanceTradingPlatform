#!/usr/bin/env python
"""Z11: does the configured SECRETS_ENCRYPTION_KEY still open every organisation's data key?

    python scripts/check_secrets_key.py

Read-only. Unwraps each `tenant_keys` row with the current master and prints only counts - never a key, a
wrapped key or a tenant name. `deploy/atp.sh health` runs it inside the backend container after every deploy, so
a changed key is caught before a broker or AI credential fails to decrypt for a user.

Exit codes: 0 every stored data key opens (or none is stored yet); 1 at least one does not open - the key in .env
is not the one the database was written with: put the previous value back, never rotate it from the deploy;
2 no key is configured in production.
"""
import asyncio
import os
import sys
from typing import Dict, Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def count_unwrappable(wrapped_keys: Iterable[str], master) -> Dict[str, int]:
    """How many wrapped data keys the master opens. Pure: the caller loads the rows and builds the master."""
    from cryptography.fernet import InvalidToken

    ok = failed = 0
    for wrapped in wrapped_keys:
        try:
            master.decrypt(wrapped.encode())
            ok += 1
        except (InvalidToken, ValueError):
            failed += 1
    return {"tenant_keys": ok + failed, "open": ok, "do_not_open": failed}


async def _load_wrapped_keys() -> list:
    from sqlalchemy import select

    from app.db.models import TenantKeyRecord
    from app.db.session import _session_factory

    async with _session_factory() as session:
        return list(await session.scalars(select(TenantKeyRecord.wrapped_key)))


def main() -> int:
    from app.core import config
    from app.secrets_store import encryption

    if not config.SECRETS_ENCRYPTION_KEY and config.ENVIRONMENT in ("production", "staging"):
        print("secrets key: SECRETS_ENCRYPTION_KEY is not set")
        return 2
    result = count_unwrappable(asyncio.run(_load_wrapped_keys()), encryption.master_fernet())
    if result["do_not_open"]:
        print(f"secrets key: {result['do_not_open']} of {result['tenant_keys']} organisation data keys do NOT open with "
              "the configured SECRETS_ENCRYPTION_KEY - restore the previous value in .env (never rotate it here)")
        return 1
    print(f"secrets key: ok - {result['open']} organisation data keys open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
