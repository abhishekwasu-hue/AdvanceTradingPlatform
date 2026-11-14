"""LIVE-1 (one live switch): a test locks or unlocks LIVE the way the owner does - through `live_trading` (the PAPER-only
lock is derived from it; a stored `phase0_paper_only` entry is ignored)."""
import asyncio

from app.platform import controls, phase0_lock


def set_live(on: bool | None) -> None:
    """Stores `live_trading` ON / OFF; None drops the stored entry (the defaults, or a test's LIVE posture, apply)."""
    from tests.test_auth_api import _session_factory

    async def go():
        async with _session_factory() as session:
            flags = dict((await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {})
            if on is None:
                flags.pop("live_trading", None)
            else:
                flags["live_trading"] = {"on": bool(on), "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())
    phase0_lock.reset_cache()


def make_owner_org(monkeypatch, tenant_id: int) -> int:
    """B1 (owner 14 Nov): LIVE through ATP's server is for the owner's organisation only. A test that sends LIVE makes its
    organisation the owner's the real way: an active platform administrator in it, named by `LIVE_OWNER_USER_ID`.
    Returns that owner account's id."""
    import secrets

    from app.core import config
    from app.core.enums import UserRole
    from app.db.models import User
    from tests.test_auth_api import _session_factory

    async def go() -> int:
        async with _session_factory() as session:
            owner = User(tenant_id=int(tenant_id), email=f"owner-{secrets.token_hex(6)}@example.com", hashed_password="x",
                         role=UserRole.SUPER_ADMIN.value, is_active=True)
            session.add(owner)
            await session.commit()
            return int(owner.id)
    owner_id = asyncio.run(go())
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", owner_id)
    phase0_lock.reset_cache()
    return owner_id
