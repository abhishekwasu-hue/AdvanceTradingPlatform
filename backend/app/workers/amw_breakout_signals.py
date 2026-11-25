"""Opt-in host entry point for AMW breakout analysis and informational delivery."""

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping

import pandas as pd

from app.alerts.barclose import expected_bar
from app.screener.breakouts.amw_analysis import iter_snapshots
from app.screener.breakouts.amw_breakout import VERSION, Decision, Settings, Snapshot, replay
from app.workers.amw_breakout_publish import BreakoutPublication, publish

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    decisions: tuple[Decision, ...] = ()
    published: tuple[int, ...] = ()
    duplicates: int = 0
    reason: str = ""


def analyze(
    frames: Mapping[str, pd.DataFrame],
    settings: Settings,
    *,
    as_of: datetime,
    instrument_is_index: bool = False,
) -> tuple[Decision, ...]:
    """Common evaluator for closed-prefix replay and an authorized live read."""
    return tuple(replay(iter_snapshots(frames, settings, as_of=as_of,
                                      instrument_is_index=instrument_is_index), settings))


class ScanFacts:
    """A bounded memo for a single entitled instrument and immutable frame batch."""

    def __init__(self, frames: Mapping[str, pd.DataFrame]) -> None:
        self._frames = frames
        self._facts: dict[tuple, tuple[Snapshot, ...]] = {}
        self.hits = 0
        self.misses = 0

    def decisions(self, frames, settings: Settings, *, as_of, instrument_is_index):
        if frames is not self._frames:
            raise ValueError("Scan facts are scoped to one frame batch")
        key = (
            as_of, instrument_is_index, settings.breakout_tf, settings.rsi_tfs,
            settings.demand_tfs, settings.supply_tfs, settings.structure_tf, settings.htf_tfs,
            settings.sources, settings.buffer, settings.swing_degree, settings.rsi_period,
            settings.range_mode, settings.range_n, settings.divergence_age,
            settings.divergence_mode == "OFF", settings.horizontal_lookback,
            settings.horizontal_tolerance_pct, settings.horizontal_min_touches,
            (settings.max_age, settings.retest_tolerance) if settings.acceptance == "RETEST_BREAKOUT" else None,
        )
        facts = self._facts.get(key)
        if facts is None:
            self.misses += 1
            facts = tuple(iter_snapshots(frames, settings, as_of=as_of, instrument_is_index=instrument_is_index))
            if len(self._facts) < 4:
                self._facts[key] = facts
        else:
            self.hits += 1
        return tuple(replay(facts, settings))


async def run_closed(
    session,
    user,
    frames: Mapping[str, pd.DataFrame],
    settings: Settings,
    *,
    symbol: str,
    instrument_key: str,
    now: datetime,
    holidays: Iterable[date] = (),
    enabled: bool = False,
    instrument_is_index: bool = False,
    facts: ScanFacts | None = None,
    scheduled_end: datetime | None = None,
) -> RunResult:
    """Publish the current scheduled close only. The caller supplies entitled frames."""
    if not enabled:
        return RunResult(reason="DISABLED")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Expected timezone-aware clock")
    now = now.astimezone(timezone.utc)
    due = expected_bar(now, settings.breakout_tf, holidays) if scheduled_end is None else None
    source = frames.get(settings.breakout_tf)
    if source is None or source.empty or "bar_end" not in source or (due is None and scheduled_end is None):
        return RunResult(reason="DUE_BAR_UNAVAILABLE")
    if scheduled_end is not None:
        matches = source.loc[source["bar_end"] == pd.Timestamp(scheduled_end), "bar_end"]
        if len(matches) != 1:
            return RunResult(reason="DUE_BAR_UNAVAILABLE")
        raw_end = pd.Timestamp(matches.iloc[0])
    else:
        expected = pd.Timestamp(due)
        if expected not in source.index or not source.index.is_unique:
            return RunResult(reason="DUE_BAR_UNAVAILABLE")
        raw_end = pd.Timestamp(source.loc[expected, "bar_end"])
    if raw_end.tzinfo is None:
        raise ValueError("Expected timezone-aware source bar end")
    end = raw_end.to_pydatetime()
    if not timedelta(0) <= now - end <= timedelta(minutes=2):
        return RunResult(reason="DUE_BAR_STALE")
    decisions = await asyncio.to_thread(facts.decisions if facts is not None else analyze, frames, settings, as_of=end,
                                        instrument_is_index=instrument_is_index)
    current = tuple(item for item in decisions if item.at == end)
    ids = []
    duplicates = 0
    for item in current:
        if item.state != "SIGNAL_GENERATED":
            continue
        result = await publish(session, user, BreakoutPublication(
            symbol=symbol, instrument_key=instrument_key, timeframe=settings.breakout_tf,
            bar_end=item.at, side="bullish" if item.side == 1 else "bearish",
            setup_id=f"{settings.profile}/{item.detected_at.isoformat()}/{item.key}",
            strategy_version=VERSION, config_hash=settings.version,
            score=item.score, variant=settings.profile, grade=item.grade, evidence={
                **asdict(item)["evidence"], "detected_at": item.detected_at.isoformat(),
                "confirmed_at": item.at.isoformat(), "available_at": now.isoformat(),
                "published_at": now.isoformat(), "rsi_tfs": settings.rsi_tfs,
                "supply_tfs": settings.supply_tfs, "demand_tfs": settings.demand_tfs,
            }, reasons=(
                "Price closed beyond the breakout level.",
                "The scan cleared its opposing-zone check.",
            ),
        ))
        if result is None:
            duplicates += 1
        else:
            ids.append(result)
    log.info("amw_breakout_scan_completed", extra={
        "instrument": instrument_key, "profile": settings.profile,
        "decision_at": end.isoformat(), "config_version": settings.version,
        "gate_reasons": sorted({item.reason for item in current}),
        "published_count": len(ids), "duplicate_count": duplicates,
    })
    return RunResult(current, tuple(ids), duplicates)
