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
