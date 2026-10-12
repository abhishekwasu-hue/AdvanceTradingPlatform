"""N2-9 (P1 spec 2.1 / 2.5, docs/design/N2_P1FGH.md): the Trade workspace page behind `trade_workspace` (off by default).
The page uses existing routes only (worker status, the P1-d ticket, the positions board, Exit now), so this checks the
flag reaches the page per organisation and that the workspace adds no route."""
import asyncio
import uuid

from app.main import app
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant


def _flag(tenant_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get("trade_workspace") or {"on": False, "tenants": []}
            flags["trade_workspace"] = {"on": False, "tenants": sorted(set(entry.get("tenants") or []) | {tenant_id})}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())


def test_flag_registered_off_per_organisation_and_no_new_route():
    assert "trade_workspace" in controls.FEATURE_FLAGS and "trade_workspace" in controls.DEFAULT_OFF_FLAGS
    t = _tenant(f"n2tw-{uuid.uuid4().hex[:6]}@example.com")
    other = _tenant(f"n2tw-o-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get("/api/system/features", headers=t["headers"]).json()["trade_workspace"] is False
    _flag(t["tenant_id"])
    assert client.get("/api/system/features", headers=t["headers"]).json()["trade_workspace"] is True
    assert client.get("/api/system/features", headers=other["headers"]).json()["trade_workspace"] is False
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any("workspace" in p for p in paths)
