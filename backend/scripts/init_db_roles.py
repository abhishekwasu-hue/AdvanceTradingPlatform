#!/usr/bin/env python
"""Phase O1: apply scripts/db_roles.sql with the passwords from the environment.

    SUPERUSER_DATABASE_URL=postgresql://postgres:...@host/advance_trading_platform \\
    ATP_APP_PASSWORD=... ATP_MIGRATOR_PASSWORD=... python scripts/init_db_roles.py

Wraps psql so the passwords never appear on a command line or in shell history. Prints what to
put in .env afterwards (never the passwords themselves).
"""
import os
import subprocess
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    url = os.environ.get("SUPERUSER_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    app_pw, mig_pw = os.environ.get("ATP_APP_PASSWORD"), os.environ.get("ATP_MIGRATOR_PASSWORD")
    if not url or not app_pw or not mig_pw:
        print("Set SUPERUSER_DATABASE_URL, ATP_APP_PASSWORD and ATP_MIGRATOR_PASSWORD", file=sys.stderr)
        return 2
    dbname = urlparse(url).path.lstrip("/")
    cmd = ["psql", url, "-v", f"app_password={app_pw}", "-v", f"migrator_password={mig_pw}", "-v", f"DBNAME={dbname}",
           "-f", os.path.join(HERE, "db_roles.sql")]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    host = urlparse(url).netloc.split("@")[-1]
    print("Roles ready. Put in the backend environment (passwords from your secret store):")
    print(f"  DATABASE_URL=postgresql+asyncpg://atp_app:<ATP_APP_PASSWORD>@{host}/{dbname}")
    print(f"  MIGRATION_DATABASE_URL=postgresql+asyncpg://atp_migrator:<ATP_MIGRATOR_PASSWORD>@{host}/{dbname}")
    print("Then: python scripts/migrate_guard.py --force   (once, to run migrations as the new owner)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
