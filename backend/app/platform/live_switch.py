"""LIVE-1 (owner order, binding): ONE live switch.

The `live_trading` feature flag is the single button for LIVE entries on every order path - the F&O ticket and baskets,
trading bots / deployments, strategy templates and custom strategies:
* ON  - PAPER and LIVE are both available; each user picks his own mode per order / bot (PAPER stays the default);
* OFF - PAPER only: no new LIVE entry reaches a broker from any path.

Only the global `on` decides. The flag's organisation list (the former M55 S1b "LIVE for organisations" grant) and the
former `phase0_paper_only` lock are folded into this switch: the lock is now derived from it (`controls.feature_flags`
answers `phase0_paper_only` = NOT `live_trading`), so every existing layer - `controls.entry_blocks`, the broker-order
chokepoint (`brokers.rate_budget.RateLimitedBroker`), the trading worker and `phase0_lock.assert_paper` - follows the one
switch. Who may turn it on is unchanged: the owner only, two-factor checked, typed confirmation (app/platform/live_owner.py);
production default OFF.

B1 (owner decision 14 Nov, LIVE-1 amended for other users): LIVE orders through ATP's server are for the owner's
organisation only - the organisation of the owner account (`LIVE_OWNER_USER_ID`, as `live_owner.is_owner` resolves it).
`live_trading` ON opens LIVE for that organisation; every other organisation stays PAPER (`NOT_OWNER_ORG`) and trades
live in its own broker app ("Trade in your broker app"). The switch stays ONE switch; the decision is `live_allowed`
(through the same `phase0_lock`, so the entry checks, the broker chokepoint and the worker agree).

Unchanged safety: exits are never blocked (ADR-0004; no exit path calls this module), the AI never places or changes an
order (ADR-0006), the user presses Place / Start himself, and the kill switch, risk limits, idempotency and the broker
chokepoint apply to LIVE as to PAPER. A LIVE order also needs the user's own signed-in broker - a fact, not a setting.
"""
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

FLAG = "live_trading"
# The plain sentence every path gives when LIVE is refused because the switch is off.
LIVE_OFF = "Live trading is switched off by the admin, so only paper orders can be placed now. Exits always run."
# B1: the plain sentence when the switch is ON but the organisation is not the owner's (same words as phase0_lock).
NOT_OWNER_ORG = "Live orders from ATP are for the platform owner only. Use Trade in your broker app."


def live_on(flags: Mapping[str, Any]) -> bool:
    """The switch from resolved flags (`controls.feature_flags`): the global `on` of `live_trading`, nothing else."""
    entry = flags.get(FLAG) or {}
    return bool(entry.get("on"))


async def live_allowed(session: AsyncSession, tenant_id: int | None = None) -> bool:
    """True when LIVE entries through ATP's server are available for this organisation: the switch is ON and (B1) the
    organisation is the owner's. Read fresh; it also refreshes the process state the broker chokepoint reads
    (`phase0_lock`). No organisation given = False."""
    from app.platform import phase0_lock
    return not await phase0_lock.is_on(session, tenant_id)


async def live_refusal(session: AsyncSession, tenant_id: int | None = None) -> str | None:
    """The plain reason LIVE is refused now for this organisation (`LIVE_OFF` while the switch is off, else
    `NOT_OWNER_ORG`), or None when LIVE is open for it."""
    if await live_allowed(session, tenant_id):
        return None
    from app.platform import phase0_lock
    return LIVE_OFF if phase0_lock.switch_locked() else NOT_OWNER_ORG


__all__ = ["FLAG", "LIVE_OFF", "NOT_OWNER_ORG", "live_on", "live_allowed", "live_refusal"]
