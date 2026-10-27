"""M45 MA-2 (docs/design/M45_MULTI_ACCOUNT.md): which of the user's own accounts an order goes to, and how many lots each
gets - pure functions, no I/O, nothing sent. The fan-out executor (MA-3) calls these and then places one child order per
account through the existing single-account path.

Resolver (MB-3 / MB-4):
* order classes: ENTRY, ADJUSTMENT (adjustment / roll), EXIT (manual exit), COMBINED_EXIT (CX / SX), STOP_TARGET;
* ENTRY and ADJUSTMENT follow the routing matrix (row = order class, columns = PRIMARY and each Secondary group); the
  defaults are the owner's: ENTRY -> Primary only, ADJUSTMENT -> follow positions; a per-order override ("PRIMARY",
  "PRIMARY+<group>", "ALL") replaces the matrix row for that one order;
* every exit-type class goes to EVERY account that holds the position, whatever the matrix says (MB-4), including a
  switched-off account - exits are never blocked (ADR-0004);
* a new entry skips a switched-off account with a visible reason; only accounts with a role take part.

Sizing (MB-2): per account FIXED lots, MULTIPLIER x the Primary's lots (default 1) or PCT_CAPITAL (% of the account's
capital / margin per lot); always rounded DOWN to whole lots; under one lot -> that account is skipped with a reason; each
account's quantity is cut into freeze-limit slices (the existing `smart_execution.slice_units`). Every number comes from
the user's settings; nothing here adds a rule of its own.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PRIMARY = "PRIMARY"
SECONDARY = "SECONDARY"

ENTRY = "ENTRY"
ADJUSTMENT = "ADJUSTMENT"
EXIT = "EXIT"
COMBINED_EXIT = "COMBINED_EXIT"
STOP_TARGET = "STOP_TARGET"
ORDER_CLASSES = (ENTRY, ADJUSTMENT, EXIT, COMBINED_EXIT, STOP_TARGET)
EXIT_CLASSES = frozenset({EXIT, COMBINED_EXIT, STOP_TARGET})

FOLLOW_POSITIONS = "FOLLOW_POSITIONS"     # a matrix row value: the accounts that hold the position
# The owner's defaults (MB-3): a new entry on the Primary only; everything else follows the positions.
DEFAULT_MATRIX: Mapping[str, object] = {ENTRY: (PRIMARY,), ADJUSTMENT: FOLLOW_POSITIONS}

FIXED = "FIXED"
MULTIPLIER = "MULTIPLIER"
PCT_CAPITAL = "PCT_CAPITAL"
SIZING_MODES = (FIXED, MULTIPLIER, PCT_CAPITAL)


@dataclass(frozen=True)
class Account:
    """What the planner needs to know about one of the user's accounts (from the MA-1 registry)."""
    id: int
    role: Optional[str]
    group: Optional[str] = None
    enabled: bool = True
    capital: Optional[float] = None


@dataclass(frozen=True)
class Sizing:
    mode: str = MULTIPLIER
    value: float = 1.0


@dataclass(frozen=True)
class Skip:
    account_id: int
    reason: str


@dataclass
class Route:
    """The accounts an order goes to, and the ones left out with their reasons (always shown, never silent)."""
    accounts: List[Account] = field(default_factory=list)
    skipped: List[Skip] = field(default_factory=list)
    why: str = ""


@dataclass(frozen=True)
class Leg:
    account_id: int
    lots: int
    slices: Tuple[int, ...]       # quantities (units), each within the freeze limit; they add up to lots x lot size


@dataclass
class SizedPlan:
    legs: List[Leg] = field(default_factory=list)
    skipped: List[Skip] = field(default_factory=list)


def _columns(order_class: str, matrix: Mapping[str, object], override: Optional[str]) -> object:
    """A matrix row: FOLLOW_POSITIONS or a tuple of columns (PRIMARY, group names, or ALL)."""
    if override:
        text = override.strip()
        if text.upper() == "ALL":
            return ("ALL",)
        if text.upper() == PRIMARY:
            return (PRIMARY,)
        if text.upper().startswith(PRIMARY + "+"):
            return (PRIMARY, text[len(PRIMARY) + 1:].strip())
        raise ValueError("The override is PRIMARY, PRIMARY+<group> or ALL.")
    row = matrix.get(order_class, DEFAULT_MATRIX.get(order_class, (PRIMARY,)))
    return row


def resolve(order_class: str, accounts: Sequence[Account], *, matrix: Optional[Mapping[str, object]] = None,
            override: Optional[str] = None, holding: Iterable[int] = ()) -> Route:
    """The accounts this order goes to. `holding`: the ids of the accounts that hold the position (exits, adjustments)."""
    if order_class not in ORDER_CLASSES:
        raise ValueError(f"Unknown order class {order_class!r}.")
    held = set(holding)
    in_play = [a for a in accounts if a.role in (PRIMARY, SECONDARY)]
    if order_class in EXIT_CLASSES:
        # MB-4: exits follow positions on every account that holds them; nothing (matrix, switch, override) narrows it.
        return Route(accounts=[a for a in accounts if a.id in held], why="exits go to every account that holds the position")
    columns = _columns(order_class, {**DEFAULT_MATRIX, **(matrix or {})}, override)
    if columns == FOLLOW_POSITIONS:
        chosen = [a for a in in_play if a.id in held]
        why = "follows the accounts that hold the position"
    else:
        wanted = {str(c) for c in columns} if isinstance(columns, (tuple, list, set, frozenset)) else {str(columns)}
        chosen = [a for a in in_play if "ALL" in wanted or (a.role == PRIMARY and PRIMARY in wanted)
                  or (a.role == SECONDARY and a.group is not None and a.group in wanted)]
        why = "override " + override if override else "routing matrix"
    route = Route(why=why)
    for a in chosen:
        if not a.enabled:
            route.skipped.append(Skip(a.id, "switched off: no new entries or adjustments"))
        else:
            route.accounts.append(a)
    return route


def lots_for(account: Account, sizing: Sizing, *, primary_lots: int, margin_per_lot: Optional[float]) -> Tuple[int, Optional[str]]:
    """Whole lots for one account (rounded down) and, when it gets none, the reason."""
    if sizing.mode not in SIZING_MODES:
        return 0, f"unknown sizing mode {sizing.mode}"
    if sizing.value <= 0:
        return 0, "the sizing value is not above zero"
    if sizing.mode == FIXED:
        lots = math.floor(sizing.value)
    elif sizing.mode == MULTIPLIER:
        lots = math.floor(primary_lots * sizing.value + 1e-9)
    else:
        if not account.capital or account.capital <= 0:
            return 0, "no capital known for this account"
        if not margin_per_lot or margin_per_lot <= 0:
            return 0, "no margin per lot known for this order"
        lots = math.floor(account.capital * sizing.value / 100.0 / margin_per_lot + 1e-9)
    if lots < 1:
        return 0, "under one lot after rounding down"
    return lots, None


def size(route: Route, sizing: Mapping[int, Sizing], *, primary_lots: int, lot_size: int,
         freeze_qty: Optional[int] = None, margin_per_lot: Optional[float] = None) -> SizedPlan:
    """Lots and freeze-limit slices for every routed account; accounts without a setting use MULTIPLIER x 1 (MB-2)."""
    from app.execution.smart_execution import slice_units
    plan = SizedPlan(skipped=list(route.skipped))
    for a in route.accounts:
        lots, reason = lots_for(a, sizing.get(a.id, Sizing()), primary_lots=primary_lots, margin_per_lot=margin_per_lot)
        if reason is not None:
            plan.skipped.append(Skip(a.id, reason))
            continue
        plan.legs.append(Leg(a.id, lots, tuple(slice_units(lots * lot_size, lot_size, freeze_qty))))
    return plan


def exit_quantities(held_units: Mapping[int, int]) -> Dict[int, int]:
    """MB-4: an exit closes, on each account, exactly what that account holds (never sized from the Primary)."""
    return {account_id: units for account_id, units in held_units.items() if units}


__all__ = ["ADJUSTMENT", "COMBINED_EXIT", "DEFAULT_MATRIX", "ENTRY", "EXIT", "EXIT_CLASSES", "FIXED", "FOLLOW_POSITIONS",
           "Account", "Leg", "MULTIPLIER", "ORDER_CLASSES", "PCT_CAPITAL", "PRIMARY", "Route", "SECONDARY", "SIZING_MODES",
           "STOP_TARGET", "SizedPlan", "Sizing", "Skip", "exit_quantities", "lots_for", "resolve", "size"]
