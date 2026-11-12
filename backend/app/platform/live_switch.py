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

Unchanged safety: exits are never blocked (ADR-0004; no exit path calls this module), the AI never places or changes an
order (ADR-0006), the user presses Place / Start himself, and the kill switch, risk limits, idempotency and the broker
chokepoint apply to LIVE as to PAPER. A LIVE order also needs the user's own signed-in broker - a fact, not a setting.
"""
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

FLAG = "live_trading"
# The plain sentence every path gives when LIVE is refused because the switch is off.
LIVE_OFF = "Live trading is switched off by the admin, so only paper orders can be placed now. Exits always run."


def live_on(flags: Mapping[str, Any]) -> bool:
    """The switch from resolved flags (`controls.feature_flags`): the global `on` of `live_trading`, nothing else."""
    entry = flags.get(FLAG) or {}
    return bool(entry.get("on"))


async def live_allowed(session: AsyncSession, tenant_id: int | None = None) -> bool:
    """True when LIVE entries are available (the switch is ON). Read fresh; it also refreshes the process state the broker
    chokepoint reads (`phase0_lock`). `tenant_id` is accepted for the callers' symmetry: the switch is the same for
    everyone (the order: ON = every user chooses his own mode)."""
    from app.platform import phase0_lock
    return not await phase0_lock.is_on(session, tenant_id)


async def live_refusal(session: AsyncSession, tenant_id: int | None = None) -> str | None:
    """The plain reason LIVE is refused now (`LIVE_OFF`), or None when the switch is ON."""
    return None if await live_allowed(session, tenant_id) else LIVE_OFF


__all__ = ["FLAG", "LIVE_OFF", "live_on", "live_allowed", "live_refusal"]
