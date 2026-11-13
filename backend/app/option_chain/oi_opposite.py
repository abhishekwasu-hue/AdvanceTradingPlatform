"""Owner order 13 Nov: an alert when call OI and put OI move in OPPOSITE directions within one window.

Owner's words: a sudden rise of call OI with a sudden fall of put OI (or the reverse) inside a time frame notifies; both
falling or both rising does not.

Trade source (read-only audit): Trade has the definition but no alert on it - `oi_analysis.is_genuine_rotation` (calls
and puts moving in opposite directions, each by at least `min_change_pct` = 2.0 % of the earlier reading, else None),
used only inside the OI Diff hysteresis. ATP's port of it is `oi_regime.is_genuine_rotation`, called here AS IS, on
the closes of Trade's History buckets (`oi_regime.aggregate_history` = Trade `aggregate_oi_history`: the 5 / 10 / 15
minute View interval, each bucket's last reading). What this module adds, each a user setting (OIAlertSettings
`opposite_*`), never a rule nobody stated:
* the window: 5 / 10 / 15 minutes (Trade's View interval; reference 5);
* the scope (owner answer 13 Nov): the total call and total put OI over ATM +- `atm_range` strikes (the banner's and the
  OI-difference table's window, reference 6) - never one strike, never the whole chain;
* "sudden" (owner answer 13 Nov): this window's |call change| and |put change| each at least k x that side's average
  |change| over the last M compared windows (references k 2, M 6; fewer earlier windows at the open: the ones there are,
  at least one). No absolute lakh level; Trade's 2 % per side is the only floor.

No look-ahead: a window counts only once it is closed - its last collector slot is stored - and every comparison
reads that window's close and earlier ones. Two closes more than one window apart (a missing bucket) are not compared:
the change would not be "in N minutes". Pure functions; app/option_chain/oi_alerts.py raises the alert through the
existing notify path, app/option_chain/routes.py marks the History rows.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core import config
from app.option_chain import oi_regime
from app.option_chain.oi_regime import Direction, OIAlertSettings
from app.option_chain.snapshots import IST

ALERT_TYPE = "OPPOSITE_OI"


def bell_on(own_row) -> bool:
    """Owner answer 13 Nov: only the symbols he turns on with the bell - OPPOSITE_OI in THIS underlying's own alert
    types (its own settings row); an organisation-wide default ("*") never turns it on for every symbol."""
    import json
    if own_row is None or not own_row.overrides:
        return False
    try:
        alerts = (json.loads(own_row.overrides) or {}).get("alerts") or {}
    except (ValueError, AttributeError):
        return False
    return isinstance(alerts, dict) and ALERT_TYPE in (alerts.get("types") or [])


def collector_slot_minutes() -> int:
    """The collector's cadence (one stored slot per this many minutes) - when a window's last slot is in."""
    return int(config.OI_BANNER_SLOT_MINUTES)


@dataclass(frozen=True)
class Point:
    """One stored slot: its start and the call / put OI totals of the chosen scope."""
    slot_start: datetime
    call_oi: float
    put_oi: float


@dataclass(frozen=True)
class OppositeMove:
    bucket: datetime          # the window's start (IST), as the History table shows it
    slot_start: datetime      # the window's closing slot (the reading the alert is about)
    call_change: float
    put_change: float
    call_pct: float
    put_pct: float
    window: int
    rotation: Direction       # Trade's internal value (payload only; the user reads neutral words)

    def state(self) -> str:
        """The alert log's new_state (also the dedupe part): "CE +1240000 PE -810000 5m"."""
        return f"CE {self.call_change:+.0f} PE {self.put_change:+.0f} {self.window}m"


def points_of(timeline) -> list[Point]:
    """The day's points: the replay's ATM +- atm_range totals (the History table's Total Call OI / Total Put OI)."""
    return [Point(e.slot_start, float(e.totals.total_call_oi), float(e.totals.total_put_oi)) for e in timeline]


def closed_windows(points: Sequence[Point], window: int, slot_minutes: int) -> list[tuple[datetime, Point]]:
    """(window start IST, its close) oldest first - Trade's buckets; the newest only when its last slot is stored."""
    buckets = sorted(oi_regime.aggregate_history(points, window, lambda p: p.slot_start.astimezone(IST)), key=lambda kv: kv[0])
    if buckets:
        floor, last = buckets[-1]
        if last.slot_start.astimezone(IST) + timedelta(minutes=slot_minutes) < floor + timedelta(minutes=window):
            buckets.pop()                                   # the window is still open
    return buckets


def moves(points: Sequence[Point], settings: OIAlertSettings, slot_minutes: int, until: datetime | None = None) -> list[OppositeMove]:
    """Every closed window (up to `until`, inclusive) whose call and put OI moved opposite enough. Pure."""
    window = int(settings.opposite_window_minutes)
    pts = [p for p in points if until is None or p.slot_start <= until]
    closes = closed_windows(pts, window, slot_minutes)
    changes: list[tuple[float, float]] = []                 # |call|, |put| of every earlier compared window
    out: list[OppositeMove] = []
    for (f0, prev), (f1, cur) in zip(closes, closes[1:]):
        if f1 - f0 != timedelta(minutes=window):
            continue                                        # a missing window: not "in N minutes"
        dce, dpe = cur.call_oi - prev.call_oi, cur.put_oi - prev.put_oi
        recent = changes[-settings.opposite_relative_windows:]
        changes.append((abs(dce), abs(dpe)))
        rotation = oi_regime.is_genuine_rotation(cur.call_oi, prev.call_oi, cur.put_oi, prev.put_oi, settings.opposite_min_change_pct)
        if rotation is None:
            continue                                        # same direction, a flat side, or below the %
        if not recent:
            continue                                        # nothing earlier to call it sudden against
        k = settings.opposite_relative_k
        avg_c = sum(c for c, _ in recent) / len(recent)
        avg_p = sum(p for _, p in recent) / len(recent)
        if abs(dce) < k * avg_c or abs(dpe) < k * avg_p:
            continue
        out.append(OppositeMove(f1, cur.slot_start, dce, dpe, dce / prev.call_oi * 100, dpe / prev.put_oi * 100, window, rotation))
    return out


def latest_move(points: Sequence[Point], settings: OIAlertSettings, slot_minutes: int, latest: datetime) -> OppositeMove | None:
    """The move the window closed by the `latest` slot shows, if any (what the alert evaluates on that slot)."""
    found = [m for m in moves(points, settings, slot_minutes, until=latest) if m.slot_start == latest]
    return found[-1] if found else None


def _lakh(value: float) -> str:
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{abs(value) / 1e5:,.2f} L"


def _pct(value: float) -> str:
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{abs(value):.1f}%"


def _expiry_words(expiry: str | None) -> str:
    try:
        return datetime.fromisoformat(str(expiry)[:10]).strftime("%d %b") if expiry else ""
    except ValueError:
        return ""


def render(underlying: str, move: OppositeMove, settings: OIAlertSettings, atm_range: int, expiry: str | None, slot: datetime,
           data_as_of: datetime, schema: str) -> dict:
    """Title / message / payload in OI words only: the two changes, the window and the scope - never an order word,
    never a direction word (the rotation stays in the payload)."""
    stamp = slot.astimezone(IST).strftime("%d %b %H:%M IST")
    exp = _expiry_words(expiry)
    head = f"{underlying} {exp}".strip()
    scope = f"ATM ± {atm_range}"
    title = f"{underlying} OI: call and put OI moved in opposite directions in {move.window} min"
    message = (f"{head}: Call OI {_lakh(move.call_change)} ({_pct(move.call_pct)}) and Put OI {_lakh(move.put_change)} "
               f"({_pct(move.put_pct)}) in {move.window} min ({scope}) - opposite OI activity. Data as of {stamp}. "
               "Information only, not advice.")
    detail = {"call_change": move.call_change, "put_change": move.put_change, "call_pct": round(move.call_pct, 3),
              "put_pct": round(move.put_pct, 3), "window": move.window, "atm_range": atm_range, "relative_k": settings.opposite_relative_k,
              "relative_windows": settings.opposite_relative_windows,
              "bucket": move.bucket.isoformat(), "expiry": expiry, "rotation": move.rotation.value}
    payload = {"schema": schema, "underlying": underlying, "alert_type": ALERT_TYPE, "old_state": None, "new_state": move.state(),
               "detail": detail, "data_timestamps": {"slot": slot.isoformat(), "data_as_of": data_as_of.isoformat()}}
    return {"title": title[:200], "message": message, "payload": payload}


def history_marks(points: Sequence[Point], settings: OIAlertSettings, slot_minutes: int, interval: int) -> dict[datetime, dict]:
    """The History rows' marks for the table's `interval`: closing slot -> the move's numbers (the user's own levels)."""
    s = settings.model_copy(update={"opposite_window_minutes": interval})
    return {m.slot_start: {"call_change": m.call_change, "put_change": m.put_change, "window": m.window} for m in moves(points, s, slot_minutes)}


__all__ = ["ALERT_TYPE", "Point", "OppositeMove", "points_of", "closed_windows", "moves", "latest_move", "bell_on", "render", "history_marks"]
