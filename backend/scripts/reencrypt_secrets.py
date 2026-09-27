#!/usr/bin/env python
"""Phase N1: move secrets to per-tenant data keys and rotate the master key.

    python scripts/reencrypt_secrets.py status          # how much still sits under the master key
    python scripts/reencrypt_secrets.py reencrypt       # upgrade every legacy row to its tenant key
    python scripts/reencrypt_secrets.py rotate-master   # after changing SECRETS_ENCRYPTION_KEY:
                                                        #   OLD_SECRETS_ENCRYPTION_KEY=<previous> ...

Order for a master rotation: (1) `reencrypt` under the OLD key so no row depends on it directly,
(2) set the new SECRETS_ENCRYPTION_KEY and OLD_SECRETS_ENCRYPTION_KEY, (3) `rotate-master`,
(4) restart API and worker (their key rings reload). Nothing here prints a key.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(command: str) -> int:
    from app.db.session import _session_factory
    from app.secrets_store import envelope

    async with _session_factory() as session:
        if command == "status":
            print(await envelope.status(session))
        elif command == "reencrypt":
            print(await envelope.reencrypt_all(session))
        elif command == "rotate-master":
            old = os.environ.get("OLD_SECRETS_ENCRYPTION_KEY")
            if old is None:
                print("OLD_SECRETS_ENCRYPTION_KEY must hold the previous master key (empty string = the dev default)", file=sys.stderr)
                return 2
            print({"rewrapped_tenant_keys": await envelope.rotate_master(session, old or None)})
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "")))
