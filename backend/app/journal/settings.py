"""Z6: the discipline engine's settings - every threshold and weight in one place, editable per trader.

The defaults are starting points, not fitted values: each one and the reasoning behind it is in
docs/DISCIPLINE_ENGINE.md ("Defaults and research note"). Nothing here was tuned on anyone's results. A trader can
change any of them (PUT /api/journal/settings); a stored value outside its documented range is ignored and the
default used instead, so a bad row can never make a detector misbehave.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalSettingsRecord, User

# The score components (each one a share of the week's trades free of the tags listed) and the tags each one counts.
COMPONENT_TAGS: Dict[str, Tuple[str, ...]] = {
    "stop_discipline": ("no_stop", "moved_stop_against"),
    "plan_adherence": ("fomo_entry", "early_exit"),
    "emotional_control": ("revenge_trade", "averaging_loser"),
    "frequency_control": ("overtrading",),
    "event_awareness": ("held_through_event",),
}
TAGS: Tuple[str, ...] = ("revenge_trade", "fomo_entry", "averaging_loser", "moved_stop_against", "no_stop", "overtrading",
                         "early_exit", "held_through_event")
EVENT_KINDS: Tuple[str, ...] = ("BUDGET", "RBI_POLICY", "EXPIRY", "RESULTS", "FED", "ELECTION", "OTHER")


def _default_weights() -> Dict[str, float]:
    return {"stop_discipline": 25.0, "plan_adherence": 20.0, "emotional_control": 25.0, "frequency_control": 15.0, "event_awareness": 15.0}


def _default_event_kinds() -> List[str]:
    return ["BUDGET", "RBI_POLICY", "RESULTS", "FED", "ELECTION", "OTHER"]


@dataclass
class DisciplineSettings:
    """Thresholds (see docs/DISCIPLINE_ENGINE.md for each default's reasoning)."""

    # Revenge trade: an entry within this many minutes of a losing exit, sized larger than the losing trade.
    revenge_window_minutes: float = 15.0
    # "Larger": the new trade's risk (or notional when either has no stop) above the losing trade's times this.
    revenge_size_multiple: float = 1.0
    # FOMO entry: filled beyond the plan's entry, in the chasing direction, by more than this share of the planned risk.
    fomo_chase_r: float = 0.25
    # Overtrading: a fixed trades-per-day norm when > 0; 0 = the trader's own median over the look-back, times the multiple.
    overtrading_daily_norm: int = 0
    overtrading_lookback_days: int = 20
    overtrading_multiple: float = 1.5
    overtrading_min_norm: int = 3
    # Held through event: which market-event kinds count (events from the events calendar, global or the tenant's own).
    event_kinds: List[str] = field(default_factory=_default_event_kinds)
    # Whether SUGGESTED (not yet confirmed) tags count against the score. DISMISSED tags never count.
    count_unconfirmed: bool = True
    # Score weights per component; normalised by their sum, so only the proportions matter.
    weights: Dict[str, float] = field(default_factory=_default_weights)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# (min, max) for every number; a value outside is rejected by the API and ignored when read back.
RANGES: Dict[str, Tuple[float, float]] = {
    "revenge_window_minutes": (1, 24 * 60),
    "revenge_size_multiple": (0.5, 10),
    "fomo_chase_r": (0.05, 5),
    "overtrading_daily_norm": (0, 500),
    "overtrading_lookback_days": (5, 365),
    "overtrading_multiple": (1, 10),
    "overtrading_min_norm": (1, 500),
}
WEIGHT_RANGE = (0.0, 100.0)


class SettingsError(ValueError):
    pass


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{name} must be a number")
    low, high = RANGES[name]
    if not low <= float(value) <= high:
        raise SettingsError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def validate(values: Dict[str, Any], base: Optional[DisciplineSettings] = None) -> DisciplineSettings:
    """`values` merged over `base` (default: the defaults). Raises SettingsError naming the first bad key."""
    out = DisciplineSettings(**(base or DisciplineSettings()).to_dict())
    known = {f.name for f in fields(DisciplineSettings)}
    for key, value in values.items():
        if key not in known:
            raise SettingsError(f"Unknown setting '{key}' - known: {sorted(known)}")
        if key in RANGES:
            number = _number(key, value)
            setattr(out, key, int(number) if key in ("overtrading_daily_norm", "overtrading_lookback_days", "overtrading_min_norm") else number)
        elif key == "event_kinds":
            if not isinstance(value, list) or any(not isinstance(v, str) or v.upper() not in EVENT_KINDS for v in value):
                raise SettingsError(f"event_kinds must be a list of {list(EVENT_KINDS)}")
            out.event_kinds = sorted({v.upper() for v in value})
        elif key == "count_unconfirmed":
            if not isinstance(value, bool):
                raise SettingsError("count_unconfirmed must be true or false")
            out.count_unconfirmed = value
        elif key == "weights":
            if not isinstance(value, dict):
                raise SettingsError("weights must be an object of component -> weight")
            weights = dict(out.weights)
            for comp, w in value.items():
                if comp not in COMPONENT_TAGS:
                    raise SettingsError(f"Unknown score component '{comp}' - known: {sorted(COMPONENT_TAGS)}")
                if isinstance(w, bool) or not isinstance(w, (int, float)) or not WEIGHT_RANGE[0] <= float(w) <= WEIGHT_RANGE[1]:
                    raise SettingsError(f"weight of {comp} must be between {WEIGHT_RANGE[0]:g} and {WEIGHT_RANGE[1]:g}")
                weights[comp] = float(w)
            if sum(weights.values()) <= 0:
                raise SettingsError("at least one weight must be above 0")
            out.weights = weights
    return out


def from_stored(raw: Optional[str]) -> DisciplineSettings:
    """The stored JSON merged over the defaults, key by key: a key that is unreadable or out of range keeps its default."""
    try:
        stored = json.loads(raw or "{}")
    except ValueError:
        stored = {}
    out = DisciplineSettings()
    if not isinstance(stored, dict):
        return out
    for key, value in stored.items():
        try:
            out = validate({key: value}, out)
        except SettingsError:
            continue
    return out


async def load(session: AsyncSession, user: User) -> DisciplineSettings:
    row = await session.scalar(select(JournalSettingsRecord).where(JournalSettingsRecord.user_id == user.id))
    return from_stored(row.settings_json if row is not None else None)


async def save(session: AsyncSession, user: User, values: Dict[str, Any]) -> DisciplineSettings:
    """Validates `values` over the trader's current settings and stores only what differs from the defaults."""
    current = await load(session, user)
    merged = validate(values, current)
    defaults = DisciplineSettings().to_dict()
    diff = {k: v for k, v in merged.to_dict().items() if v != defaults[k]}
    row = await session.scalar(select(JournalSettingsRecord).where(JournalSettingsRecord.user_id == user.id))
    if row is None:
        row = JournalSettingsRecord(tenant_id=user.tenant_id, user_id=user.id)
        session.add(row)
    row.settings_json = json.dumps(diff, sort_keys=True)
    await session.commit()
    return merged
