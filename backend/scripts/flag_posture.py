#!/usr/bin/env python
"""Z11: the Phase 0 safety posture of the feature flags, as this release resolves them. Read-only; one JSON line.

    python scripts/flag_posture.py

    {"live_trading": {"on": true, "tenants": 0, "stored": false},
     "phase0_paper_only": {"known": true, "on": false, "stored": false},
     "stored_on": ["byob_data_tier", ...], "deployment_phase": "phase0", "live_owner": {"set": true, "resolved": true}}

`deploy/atp.sh health` / `install` report these. Since LIVE-1 `live_trading` is the ONE live switch (`phase0_paper_only`
follows it and cannot be set on its own) and since B1 LIVE through ATP's server opens only for the owner's organisation;
the switch is the owner's decision, so health reports it as a warning, never as an owner action (owner 15 Nov).
`live_owner` (B1): whether LIVE_OWNER_USER_ID is set to a valid id in the environment (`set`; unset = the default account
1) and whether it resolves to an active platform administrator with an organisation (`resolved`) - when it does not, LIVE
through ATP stays closed for every organisation even with the switch ON.
With ATP_DEPLOYMENT_PHASE=phase0 (the Hostinger default) the API seeds both at first boot (app/platform/deployment_phase.py),
so a fresh install passes with nothing for the owner to set. Defaults follow app/platform/controls.py (kill flags are on
unless stored off; in Phase 0 mode the two seeded flags default to their seed). It never writes a flag.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def posture() -> dict:
    from app.db.session import _session_factory
    from app.platform import controls, deployment_phase, phase0_lock

    async with _session_factory() as session:
        stored = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
        flags = await controls.feature_flags(session)
        owner_tenant = await phase0_lock.owner_tenant_id(session)
    live = flags.get("live_trading") or {"on": True, "tenants": []}
    paper_only = flags.get("phase0_paper_only")
    return {
        "live_trading": {"on": bool(live["on"]), "tenants": len(live.get("tenants") or []), "stored": "live_trading" in stored},
        "phase0_paper_only": {"known": paper_only is not None, "on": bool(paper_only and paper_only["on"]), "stored": "phase0_paper_only" in stored},
        "stored_on": sorted(name for name, entry in stored.items() if isinstance(entry, dict) and entry.get("on")),
        "deployment_phase": deployment_phase.current_phase() or "none",
        "live_owner": {"set": owner_id_set(os.environ.get("LIVE_OWNER_USER_ID")), "resolved": owner_tenant is not None},
    }


def owner_id_set(raw: str | None) -> bool:
    """LIVE_OWNER_USER_ID holds a valid account id (app/core/config.py keeps it in 1 .. 2**31 - 1, else the default 1)."""
    try:
        return raw is not None and 1 <= int(raw.strip()) <= 2**31 - 1
    except ValueError:
        return False


def main() -> int:
    print(json.dumps(asyncio.run(posture())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
