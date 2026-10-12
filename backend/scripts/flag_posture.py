#!/usr/bin/env python
"""Z11: the Phase 0 safety posture of the feature flags, as this release resolves them. Read-only; one JSON line.

    python scripts/flag_posture.py

    {"live_trading": {"on": true, "tenants": 0, "stored": false},
     "phase0_paper_only": {"known": true, "on": false, "stored": false},
     "stored_on": ["byob_data_tier", ...], "deployment_phase": "phase0"}

`deploy/atp.sh health` / `install` report these and ask the owner to act (exit 10) while LIVE can still be entered:
`live_trading` on (globally or for any organisation) or `phase0_paper_only` off where this release knows the flag.
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
    from app.platform import controls, deployment_phase

    async with _session_factory() as session:
        stored = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
        flags = await controls.feature_flags(session)
    live = flags.get("live_trading") or {"on": True, "tenants": []}
    paper_only = flags.get("phase0_paper_only")
    return {
        "live_trading": {"on": bool(live["on"]), "tenants": len(live.get("tenants") or []), "stored": "live_trading" in stored},
        "phase0_paper_only": {"known": paper_only is not None, "on": bool(paper_only and paper_only["on"]), "stored": "phase0_paper_only" in stored},
        "stored_on": sorted(name for name, entry in stored.items() if isinstance(entry, dict) and entry.get("on")),
        "deployment_phase": deployment_phase.current_phase() or "none",
    }


def main() -> int:
    print(json.dumps(asyncio.run(posture())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
