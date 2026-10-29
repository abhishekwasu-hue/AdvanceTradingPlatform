"""AUD-G2.4 (owner audit 28 Oct): activation / time to first value.

Five steps, in the order a new person usually takes them: `signup` -> `broker_connect` -> `first_chart` ->
`first_paper_trade` -> `first_journal_review`. `record_step()` is called where each step happens:

* signup - a new account (the register form, Google sign-in, an accepted team invite);
* broker_connect - broker credentials saved (`app/brokers/routes.py` `save_credentials`);
* first_chart - a single-symbol candle read that returned bars (the chart; a multi-symbol read is a screener);
* first_paper_trade - a PAPER trade written (`app/trading/persistence.py` `persist_trade`, every paper path);
* first_journal_review - the weekly review opened with at least one journaled trade, or one trade's journal opened.

Each (person, step) is written once to `activation_events` (unique pair). The Prometheus counter
`atp_activation_total{step}` goes up only when that row is newly written, so it counts each person once per step across
every API process and the worker. Its only label is the step: nothing that identifies a person.

Measurement only: `record_step()` never raises and never changes what the caller does. It commits its own row, so it is
called after the caller's own commit (nothing else pending on the session). A small process-local memory of pairs
already seen keeps the chart path from writing on every candle read.

`value_funnel()` is the admin funnel's math (pure): how many people reached each step, the median hours from signup to
each step, and the share whose first paper trade came within their first session (`ACTIVATION_FIRST_SESSION_HOURS`
after signing up, app/core/config.py).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActivationEventRecord, User
from app.observability.metrics import ACTIVATION

logger = logging.getLogger(__name__)

STEPS: Tuple[Tuple[str, str], ...] = (
    ("signup", "Signed up"),
    ("broker_connect", "Connected a broker"),
    ("first_chart", "Opened a first chart"),
    ("first_paper_trade", "First paper trade"),
    ("first_journal_review", "First journal review"),
)
STEP_KEYS = tuple(key for key, _ in STEPS)

# (database, person, step) pairs this process already knows are recorded. Bounded: cleared when full (the database's
# unique pair stays the real guard; the memory only saves a write).
_KNOWN: set = set()
_KNOWN_MAX = 200_000


def reset_memory() -> None:
    """Tests: forget the pairs this process has seen."""
    _KNOWN.clear()


def _insert(session: AsyncSession) -> Any:
    name = session.get_bind().dialect.name
    table: Any = ActivationEventRecord.__table__
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        return pg_insert(table).on_conflict_do_nothing(index_elements=["user_id", "step"])
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        return sqlite_insert(table).on_conflict_do_nothing(index_elements=["user_id", "step"])
    return insert(table)


async def record_step(session: AsyncSession, user: Optional[User], step: str, *, at: Optional[datetime] = None) -> bool:
    """Records that `user` took `step` now (or `at`), once. True when this call recorded it (and counted it). Call it
    after the caller's own commit: it commits its own row. Never raises. The insert runs inside a savepoint, so a failed
    write rolls back that savepoint only and expires none of the caller's objects."""
    if user is None or step not in STEP_KEYS:
        return False
    try:
        key = (id(session.get_bind()), user.id, step)
        if key in _KNOWN:
            return False
        values = {"tenant_id": user.tenant_id, "user_id": user.id, "step": step, "reached_at": at or datetime.now(timezone.utc)}
        try:
            async with session.begin_nested():
                result = await session.execute(_insert(session).values(**values))
            written = (getattr(result, "rowcount", 0) or 0) == 1
        except IntegrityError:          # a database without ON CONFLICT: the pair already exists
            written = False
        await session.commit()
    except Exception:  # noqa: BLE001 - measurement must never break the step itself
        logger.warning("Activation step %s not recorded", step, exc_info=True)
        return False
    if len(_KNOWN) >= _KNOWN_MAX:
        _KNOWN.clear()
    _KNOWN.add(key)
    if written:
        ACTIVATION.labels(step=step).inc()
    return written


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def value_funnel(people: Iterable[Tuple[Optional[datetime], Mapping[str, Optional[datetime]]]], *, now: datetime,
                 first_session_hours: int) -> Dict[str, Any]:
    """`people`: (signed up at, {step: first reached at}) per person. Each step counts the people who reached it (a step
    taken out of the usual order still counts) with the median hours from signing up to it. `first_session`: of the
    people whose first session is decided - over (`first_session_hours` after signing up) or already holding a paper
    trade inside it - how many made their first paper trade inside it; the rest are `open`."""
    window = timedelta(hours=first_session_hours)
    at_now = _aware(now) or now
    reached = {key: 0 for key in STEP_KEYS}
    hours: Dict[str, List[float]] = {key: [] for key in STEP_KEYS}
    decided = within = still_open = 0
    for signed_up, steps in people:
        signed_up = _aware(signed_up)
        reached["signup"] += 1
        hours["signup"].append(0.0)
        for key in STEP_KEYS[1:]:
            at = _aware(steps.get(key))
            if at is None:
                continue
            reached[key] += 1
            if signed_up is not None:
                hours[key].append(max(0.0, (at - signed_up).total_seconds() / 3600))
        if signed_up is None:
            continue
        trade_at = _aware(steps.get("first_paper_trade"))
        inside = trade_at is not None and trade_at - signed_up <= window
        if inside:
            decided += 1
            within += 1
        elif at_now - signed_up >= window:
            decided += 1
        else:
            still_open += 1
    out_steps = []
    for key, label in STEPS:
        m = median(hours[key])
        out_steps.append({"key": key, "label": label, "count": reached[key],
                          "median_hours": round(m, 1) if m is not None else None})
    return {
        "steps": out_steps,
        "first_session": {"hours": first_session_hours, "decided": decided, "within": within, "open": still_open,
                          "share_pct": round(100.0 * within / decided, 1) if decided else None},
    }
