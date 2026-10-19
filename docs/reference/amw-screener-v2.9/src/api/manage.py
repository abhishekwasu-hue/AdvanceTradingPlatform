"""CLI: python -m src.api.manage create-user <name> [admin|analyst|viewer]  |  python -m src.api.manage schema"""
from __future__ import annotations

import sys

from ..config import ROOT
from ..storage.db import Store, default_url


def main(argv: list[str]) -> int:
    store = Store(default_url(ROOT))
    if argv[:1] == ["create-user"] and len(argv) >= 2:
        uid, tok = store.create_user(argv[1], argv[2] if len(argv) > 2 else "analyst")
        print(f"user id {uid}. API token (shown once, store it securely):\n{tok}")
        return 0
    if argv[:1] == ["schema"]:
        print(f"backend {store.backend}, schema version {store.schema_version()}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
