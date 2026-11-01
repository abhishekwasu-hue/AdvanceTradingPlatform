"""PAPER-NR (owner, 1 Nov; DECISIONS "PAPER-NR"): in PAPER no new entry is refused, reduced or held for margin, paper
capital or the user's own risk limits - each would-be refusal becomes an information note on the order trail / ticket.

The one chokepoint for that rule. Every producer of a user-restriction refusal asks `advisory(mode)` and turns its
refusal into `info(...)` here, so the rule lives in one place:

- LIVE is never advisory: every LIVE check runs exactly as before (the LIVE branch of each producer is unchanged).
- Platform safety is not a user restriction and stays in PAPER: kill switches, the Phase-0 lock, maintenance mode, admin
  controls (approval-only mode, user trading disabled, GLOBAL-scope risk limits, the risk ceilings), the F&O ban list,
  OI gates, his own Pause button, missing settings, data and auth checks.
- PAPER still sizes from his own sizing setting (risk per trade); a sizing refusal becomes his own lots or one lot with
  a note, and nothing cuts the size (no drawdown step, event cut, VIX cut, own cap or max-lots reduction).
- A PAPER risk-limit block never engages a STRATEGY / TENANT kill switch (`engages_kill_switch`).
- Exits never come here (ADR-0004): no exit path imports this module (tests/test_paper_nr.py checks the call graph).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional, Sequence, Tuple

from app.core.enums import ExecutionMode
from app.core.models import RiskConfig, RiskDecision, Signal
from app.instruments.models import ContractSpec

if TYPE_CHECKING:
    from app.risk_engine.guardian import GuardianVerdict
    from app.risk_engine.risk_manager import TradingDayState

PAPER = ExecutionMode.PAPER.value
PREFIX = "Practice only, not applied: "
BELOW_ZERO = "below zero - practice only"

# The Settings > Risk limits fields that only restrict an entry (refuse, pause or cut). The sizing fields (risk per
# trade %, rupees), the capital, the lot size and the margin safety share (read by LIVE margin checks only) are kept.
RESTRICTION_FIELDS: Tuple[str, ...] = (
    "max_daily_loss_pct", "daily_loss_rupees", "max_trades_per_day", "max_open_positions", "max_consecutive_losses",
    "loss_cooldown_minutes", "max_lots_per_order", "max_portfolio_risk_pct", "stop_cooldown_minutes", "dd_level_1_pct",
    "dd_level_2_pct", "event_size_cut_pct",
)


def advisory(mode: object) -> bool:
    """True only for a PAPER entry: its user-restriction and capital refusals are information, not refusals."""
    value = getattr(mode, "value", mode)
    return str(value or "").upper() == PAPER


def info(reason: str) -> str:
    """A would-be refusal as an information note (said once, never double-prefixed)."""
    text = str(reason).strip()
    return text if text.startswith(PREFIX) else PREFIX + text


def infos(reasons: Iterable[str]) -> list[str]:
    out: list[str] = []
    for r in reasons:
        if r and info(r) not in out:
            out.append(info(r))
    return out


def split(mode: object, reasons: Sequence[str]) -> Tuple[list[str], list[str]]:
    """(refusals, notes) for one producer's user-restriction reasons: PAPER -> ([], notes); LIVE -> (reasons, [])."""
    if advisory(mode):
        return [], infos(reasons)
    return list(reasons), []


def unrestricted(cfg: RiskConfig, *, keep_sizing: bool = True) -> RiskConfig:
    """The config a PAPER entry is checked with: every restriction reads "not set" (DEF-0 makes each check a no-op then).
    `keep_sizing` False also drops risk-based sizing - a ticket's own typed lots are then his size."""
    update: dict[str, object] = {key: None for key in RESTRICTION_FIELDS}
    update.update(min_risk_reward=0.0, event_rules=False)
    if not keep_sizing:
        update.update(risk_per_trade_pct=None, risk_per_trade_rupees=None)
    return cfg.model_copy(update=update)


def _lots(quantity: float, unit: float) -> str:
    from app.risk_engine.risk_limits import lots_text
    return lots_text(quantity / unit if unit else quantity)


def decide(cfg: RiskConfig, signal: Signal, state: TradingDayState, *, contract_spec: Optional[ContractSpec] = None,
           requested_quantity: Optional[float] = None, typed_wins: bool = False) -> RiskDecision:
    """The RiskManager decision of a PAPER entry: the real checks run once read-only for their notes; the size is his
    own sizing (risk per trade when set, else his lots / one lot) with no restriction applied. A data refusal (no
    trade, no entry / stop level, a non-finite level) still refuses. `typed_wins`: a ticket's own checked lots are his
    size (a manual order he confirmed), so risk-based sizing does not change them."""
    from app.risk_engine.risk_manager import RiskManager
    real = RiskManager(cfg).validate_and_size(signal, state, contract_spec=contract_spec, requested_quantity=requested_quantity)
    if not real.approved and not real.blocks:
        return real                                               # data: KEEP (not a user restriction)
    typed = float(requested_quantity) if requested_quantity is not None and requested_quantity > 0 else None
    free_cfg = unrestricted(cfg, keep_sizing=not (typed_wins and typed is not None))
    free = RiskManager(free_cfg).validate_and_size(signal, state, contract_spec=contract_spec, requested_quantity=requested_quantity)
    notes = infos(real.reasons) if not real.approved else []
    unit = float(contract_spec.lot_size if contract_spec is not None else (cfg.lot_size or 1))
    if not free.approved:
        if not free.blocks:
            return free
        # A sizing refusal (entry equal to stop, below one lot): his own lots, else one lot.
        quantity = typed if typed is not None else unit
        notes.extend(n for n in infos(free.reasons) if n not in notes)
        notes.append(f"Practice: {_lots(quantity, unit)} placed ({'your lots' if typed is not None else 'one lot'}).")
        return RiskDecision(approved=True, quantity=quantity, reasons=["Risk checks passed"], warnings=list(free.warnings) + notes)
    if real.approved and real.quantity + 1e-9 < free.quantity:
        notes.append(info(f"your limits would size this at {_lots(real.quantity, unit)}; {_lots(free.quantity, unit)} placed."))
    if typed_wins and typed is not None and real.approved and real.quantity + 1e-9 < typed and not notes:
        notes.append(info(f"your risk per trade sizes this at {_lots(real.quantity, unit)}; your {_lots(typed, unit)} placed."))
    return RiskDecision(approved=True, quantity=free.quantity, reasons=free.reasons, warnings=list(free.warnings) + notes)


def guardian(verdict: GuardianVerdict) -> GuardianVerdict:
    """A PAPER guardian verdict: its refusals (cool-down, drawdown pause, event block, VIX spike) and its size cuts
    (drawdown step, event cut, VIX level) become notes; the size multiplier is 1 (nothing cuts the size in PAPER)."""
    cut = verdict.size_multiplier < 1.0 - 1e-12
    notes = infos(verdict.reasons) + (infos(verdict.notes) if cut else list(verdict.notes))
    verdict.reasons = []
    verdict.notes = notes
    verdict.size_multiplier = 1.0
    return verdict


def hierarchy_blocks(mode: object, scope: str) -> bool:
    """Whether a breached organisation risk limit still refuses this entry: always in LIVE; in PAPER only a GLOBAL-scope
    limit (the platform's own, an admin control)."""
    return not advisory(mode) or str(scope).upper() == "GLOBAL"


def engages_kill_switch(mode: object) -> bool:
    """A PAPER risk-limit block never engages a STRATEGY / TENANT kill switch (owner, PAPER-NR (4))."""
    return not advisory(mode)


def entry_margin(*, kind: Optional[str], written: bool, direction: object, price: Optional[float], quantity: float) -> Optional[float]:
    """The money a PAPER entry ties up, where it is a plain fact: the premium of a bought option, the value of a cash buy.
    None for a future, a sold option or a short (their margin is the broker's SPAN / exposure - never made up here)."""
    side = str(getattr(direction, "value", direction) or "").upper()
    if price is None or quantity <= 0 or written or (kind or "").upper() == "FUTURE" or side != "LONG":
        return None
    return round(float(price) * float(quantity), 2)


def margin_info(needed: Optional[float], free: float) -> str:
    """The per-entry margin vs free balance information line (owner, PAPER-NR (2))."""
    if needed is None:
        return f"Practice margin: not estimated for this order; practice balance ₹{free:,.0f}" + (f" ({BELOW_ZERO})" if free < 0 else "")
    after = free - needed
    tail = f" ({BELOW_ZERO})" if after < 0 else ""
    return f"Practice margin: this entry uses ₹{needed:,.0f}; practice balance ₹{free:,.0f}, ₹{after:,.0f} after{tail}"
