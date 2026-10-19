#!/usr/bin/env python3
"""CI staging stack: the load test's synthetic users, written straight into the stack's own throwaway database
(run inside the `ci-runner` container: deploy/ci/stack.sh seed N).

Each user gets what a self sign-up gives (an organisation of its own with its own data key, the user as its OWNER) and
a Dhan connection to the CI fake broker (a client id and an access token, marked valid), so the chart and screener
scenarios read bars through each user's own broker session, as Tier A does in Phase 0. One password for all of them,
hashed once. The flag posture of deploy/ci/ci_posture.py is set unless it already is (the acceptance flow sets it
through the admin API when it runs first).

Refuses to run unless DATABASE_URL points at the compose service `postgres` of a CI stack (ATP_CI_BASE_URL set): this
never writes to any other database. Writes `email,password` lines to --out.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent))

PASSWORD_BYTES = 18
DOMAIN = "example.com"


def _guard() -> None:
    url = urlparse(os.environ.get("DATABASE_URL", "").replace("+asyncpg", ""))
    if url.hostname != "postgres" or not os.environ.get("ATP_CI_BASE_URL"):
        raise SystemExit("seed_users.py runs only inside the CI stack's ci-runner container (DATABASE_URL host 'postgres')")


async def seed(count: int, prefix: str) -> list:
    from sqlalchemy import select

    from app.accounts.service import ensure_account_for_credential
    from app.auth.security import hash_password
    from app.brokers.models import BrokerCredentials
    from app.brokers.token_lifecycle import default_token_expiry
    from app.core.enums import BrokerTokenStatus, UserRole
    from app.db.models import BrokerCredentialRecord, Tenant, User
    from app.db.session import _session_factory
    from app.platform import controls
    from app.secrets_store.encryption import encrypt_text
    from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL, ensure_tenant_key
    from app.webhooks.routes import hash_webhook_token
    from ci_posture import POSTURE

    password = secrets.token_urlsafe(PASSWORD_BYTES)
    hashed = hash_password(password)
    now = datetime.now(timezone.utc)
    rows = []
    async with _session_factory() as session:
        flags = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
        if any(bool((flags.get(name) or {}).get("on")) != on for name, on in POSTURE.items()):
            for name, on in POSTURE.items():
                flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
        existing = set(await session.scalars(select(User.email).where(User.email.like(f"{prefix}-%@{DOMAIN}"))))
        for i in range(count):
            email = f"{prefix}-{i:04d}@{DOMAIN}"
            if email in existing:
                continue
            tenant = Tenant(name=email, webhook_token=None, webhook_token_hash=hash_webhook_token(secrets.token_urlsafe(24)))
            session.add(tenant)
            await session.flush()
            await ensure_tenant_key(session, tenant.id)
            user = User(tenant_id=tenant.id, email=email, hashed_password=hashed, role=UserRole.OWNER.value)
            session.add(user)
            await session.flush()
            creds = BrokerCredentials(client_id=f"CILOAD{i:04d}", access_token=f"ci-dhan-{secrets.token_hex(8)}")
            record = BrokerCredentialRecord(tenant_id=tenant.id, user_id=user.id, broker_name="dhan", account_label="primary",
                                            encrypted_payload=encrypt_text(creds.model_dump_json(), tenant.id, PURPOSE_BROKER_CREDENTIAL),
                                            token_status=BrokerTokenStatus.VALID.value, token_expires_at=default_token_expiry("dhan", now),
                                            last_verified_at=now)
            session.add(record)
            await session.flush()
            await ensure_account_for_credential(session, record)
            rows.append(email)
            if len(rows) % 100 == 0:
                await session.commit()
        await session.commit()
    return [(email, password) for email in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the CI stack's load-test users")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--prefix", default="load")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    _guard()
    accounts = asyncio.run(seed(args.count, args.prefix))
    if not accounts:
        raise SystemExit(f"no new user: {args.prefix}-* users exist already (their password is in the file written when they were made)")
    Path(args.out).write_text("# email,password - CI stack load-test users (throwaway)\n" + "".join(f"{e},{p}\n" for e, p in accounts))
    os.chmod(args.out, 0o600)
    print(f"seeded {len(accounts)} users -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
