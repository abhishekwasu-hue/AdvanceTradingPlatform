"""Phase 0 deployment defaults: a Phase 0 install starts in the safe state with nothing for the owner to set.

Deployment mode `ATP_DEPLOYMENT_PHASE=phase0`. The Z11 bundle sets it for the Hostinger host (docker-compose.hostinger.yml
and deploy/atp.sh, default `phase0`); nothing else does. Any other value, or none, is the normal mode and changes nothing.

In Phase 0 mode:
1. **Seed.** At start-up the API and the worker each store two flags where the stored feature-flag row has no entry for
   them yet: `live_trading` OFF and `phase0_paper_only` ON (existing flag storage, one audit row per flag written,
   event `phase0_flag_seeded`). An entry already stored is never touched: an owner's later change in Admin -> Controls
   survives every restart and redeploy (the rule is "seed absent entries only"). Until the seed is written (the moment
   between start and the first write, or a database error during it), the same two values are the resolved defaults
   (`default_for`), so the platform is never less safe than the seed in this mode.
2. **No LIVE entry path.** While `phase0_paper_only` is on, LIVE entries are refused by layer 1
   (`controls.entry_blocks`) and layer 2 (the broker-order chokepoint, `phase0_lock`), and the trading worker does not
   evaluate LIVE deployments at all (no LIVE entry is ever built). Exits of existing LIVE positions, protective stops,
   square-off and reconciliation always run (ADR-0004). `GET /api/system/health/deep` (`checks.live_paths`), the
   worker log and `scripts/flag_posture.py` (read by `deploy/atp.sh health`) say why.
3. **Admin refusal.** Admin -> Controls refuses to turn `live_trading` on (globally or for any organisation) while
   `phase0_paper_only` is on, with a clear message and an audit row (`feature_flag_refused`).

Why a flag may default on here, against the standing rule "code never turns a flag on": it is the owner's instruction
(2026-10-12: "the owner cannot set Admin -> Controls before the app is deployed, so a Phase 0 deployment must start in the
safe state"), it happens only through this explicit deployment mode, and both writes go in the restrictive direction -
`phase0_paper_only` ON only refuses new LIVE entries, `live_trading` OFF only refuses new LIVE deployments. Neither can
enable anything, place an order or block an exit. `controls.DEFAULT_OFF_FLAGS` and the defaults of every other mode are
unchanged (docs/OPEN_QUESTIONS.md Z10-3, Z11-7).
"""
import json
import logging
import os
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import PlatformControlRecord

logger = logging.getLogger(__name__)

ENV_VAR = "ATP_DEPLOYMENT_PHASE"
PHASE0 = "phase0"
LIVE_TRADING = "live_trading"
PAPER_ONLY = "phase0_paper_only"

# The only values this module ever writes, and only where the flag has no stored entry. Both are restrictive.
SEED: Dict[str, bool] = {LIVE_TRADING: False, PAPER_ONLY: True}

SEED_AUDIT_EVENT = "phase0_flag_seeded"
SEED_ATTEMPTS = 3
REFUSAL_AUDIT_EVENT = "feature_flag_refused"
LIVE_TRADING_REFUSAL = ("Phase 0 deployment (ATP_DEPLOYMENT_PHASE=phase0): live_trading cannot be turned on while "
                        "phase0_paper_only is on - Phase 0 is PAPER only. Nothing was changed.")
LIVE_PATHS_NOT_STARTED = ("phase0_paper_only is on: LIVE entry paths not started (LIVE deployments are not evaluated, "
                          "new LIVE entries are refused); exits of existing LIVE positions still run")


def current_phase() -> str:
    """The deployment mode from the environment, normalised ("" when unset). Read on each call."""
    return (os.environ.get(ENV_VAR) or "").strip().lower()


def is_phase0() -> bool:
    return current_phase() == PHASE0


def default_for(name: str) -> Optional[bool]:
    """In Phase 0 mode, the default of a seeded flag that has no stored entry yet (the seed value); else None."""
    if is_phase0() and name in SEED:
        return SEED[name]
    return None


def _entry_on(entry: Optional[dict]) -> bool:
    """On for anyone: globally, or for at least one organisation through the allow-list."""
    if not entry:
        return False
    return bool(entry.get("on")) or bool(entry.get("tenants"))


async def _seed_once(session: AsyncSession, process: str) -> List[str]:
    from app.platform.controls import KEY_FEATURE_FLAGS
    # FOR UPDATE on Postgres: the API and the worker may seed at the same moment (SQLite ignores it and serialises).
    row = await session.scalar(select(PlatformControlRecord).where(PlatformControlRecord.key == KEY_FEATURE_FLAGS).with_for_update())
    try:
        current = json.loads(row.value_json or "{}") if row is not None else {}
    except ValueError:
        current = None
    if not isinstance(current, dict) or not isinstance(current.get("flags") or {}, dict):
        # An unreadable row is never overwritten (it may hold the owner's flags): no seed, the Phase 0 defaults
        # (`default_for`) apply to the absent entries, and the operator repairs the row in Admin -> Controls.
        await session.rollback()
        logger.error("Phase 0 flag seed skipped in the %s: the stored feature-flag row is not a JSON object with a "
                     "'flags' object - left untouched; the Phase 0 defaults apply until it is repaired", process)
        return []
    flags = dict(current.get("flags") or {})
    missing = [name for name in SEED if name not in flags]
    if not missing:
        await session.rollback()            # releases the row lock; nothing to write
        return []
    for name in missing:
        flags[name] = {"on": SEED[name], "tenants": []}
    current["flags"] = flags
    if row is None:
        row = PlatformControlRecord(key=KEY_FEATURE_FLAGS, value_json="{}")
        session.add(row)
    row.value_json = json.dumps(current)
    row.updated_by = None
    for name in missing:
        await write_audit_log(session, None, None, SEED_AUDIT_EVENT,
                              f"{name}: on={SEED[name]} (ATP_DEPLOYMENT_PHASE=phase0, first boot of the {process}; "
                              "owner decision: Phase 0 starts PAPER-only, restrictive direction)")
    await session.commit()
    return missing


async def seed_flags(session: AsyncSession, process: str = "api") -> List[str]:
    """Phase 0 mode only: stores `live_trading` OFF and `phase0_paper_only` ON where no entry is stored. Returns the
    names written (empty when nothing was missing or the mode is not Phase 0). Idempotent; never overwrites."""
    if not is_phase0():
        return []
    for _ in range(SEED_ATTEMPTS):
        try:
            return await _seed_once(session, process)
        except IntegrityError:
            # The other process inserted the flags row first: read it again and seed only what is still missing.
            await session.rollback()
    logger.warning("Phase 0 flag seed in the %s gave up after %d attempts (the flags row kept conflicting) - the Phase 0 "
                   "defaults apply to absent entries; the next start seeds them", process, SEED_ATTEMPTS)
    return []


async def live_paths(session: AsyncSession) -> Dict[str, object]:
    """What health and the logs say about LIVE entry paths. Read-only (it refreshes the chokepoint's cached lock state,
    which only ever adds refusals)."""
    from app.platform import phase0_lock
    from app.platform.controls import feature_flags
    flags = await feature_flags(session)
    paper_only = _entry_on(flags.get(PAPER_ONLY))
    await phase0_lock.is_on(session, None)            # the broker chokepoint in this process reads the same state
    return {
        "deployment_phase": current_phase() or "none",
        "phase0_paper_only": paper_only,
        "live_trading": _entry_on(flags.get(LIVE_TRADING)),
        "live_entries": "refused" if paper_only else "flag-controlled",
        "exits": "always allowed",
        "detail": LIVE_PATHS_NOT_STARTED if paper_only else "phase0_paper_only is off: LIVE entries follow the flags and plans",
    }


async def _safe_rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001 - a broken connection must not turn a logged start-up problem into a crash
        logger.exception("Rollback after a Phase 0 start-up failure failed")


async def on_startup(session: AsyncSession, process: str) -> Dict[str, object]:
    """Start-up check of the API and the worker: seed (Phase 0 mode), then log why LIVE entry paths are (not) started.
    Never raises: a failure is logged and the process comes up (in Phase 0 mode `default_for` keeps the safe values)."""
    seeded: List[str] = []
    try:
        seeded = await seed_flags(session, process)
    except Exception:  # noqa: BLE001 - start-up must not fail on it; the defaults stay safe in Phase 0 mode
        logger.exception("Phase 0 flag seed failed in the %s - the Phase 0 defaults still apply until it is written", process)
        await _safe_rollback(session)
    if seeded:
        logger.warning("Phase 0 deployment: seeded %s (live_trading OFF, phase0_paper_only ON) - owner decision, audited",
                       ", ".join(seeded))
    try:
        state = await live_paths(session)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the LIVE path state in the %s", process)
        await _safe_rollback(session)
        return {"deployment_phase": current_phase() or "none", "seeded": seeded, "error": "unreadable"}
    state["seeded"] = seeded
    if state["phase0_paper_only"]:
        logger.warning("%s start-up (deployment phase %s): %s", process, state["deployment_phase"], LIVE_PATHS_NOT_STARTED)
    else:
        logger.info("%s start-up (deployment phase %s): phase0_paper_only is off", process, state["deployment_phase"])
    return state


async def refuse_live_trading_on(session: AsyncSession, user_id: Optional[int], *, on: bool, tenants: Optional[List[int]]) -> None:
    """Admin -> Controls in Phase 0 mode: refuses turning `live_trading` on for anyone while `phase0_paper_only` is on.
    The refusal is audited (and committed) before the ValueError the route turns into a 400."""
    if not is_phase0() or not (on or tenants):
        return
    from app.platform.controls import feature_flags
    if not _entry_on((await feature_flags(session)).get(PAPER_ONLY)):
        return
    await write_audit_log(session, None, user_id, REFUSAL_AUDIT_EVENT,
                          f"{LIVE_TRADING}: on={on} tenants={sorted(int(t) for t in tenants or []) or '-'} refused - "
                          "phase0_paper_only is on (ATP_DEPLOYMENT_PHASE=phase0)")
    await session.commit()
    raise ValueError(LIVE_TRADING_REFUSAL)
