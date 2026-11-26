"""Publish an informational breakout signal to the existing history and feed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import String, cast, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SignalDirection, SignalGrade, grade_from_score
from app.core.models import Signal
from app.db.models import RuntimeCounterRecord, SignalHistoryRecord, User
from app.signal_scoring.models import EnrichedSignal
from app.signals import language
from app.trading.persistence import persist_signal_history

STRATEGY_ID = "amw_breakout_signal"
DISPLAY_NAME = "AMW Breakout"
COUNTER_NAME = "amw_breakout_signal"
FEED_COUNTER_NAME = "amw_breakout_feed"
MAX_EVIDENCE_BYTES = 8192
SIDE_DIRECTIONS = {"bullish": SignalDirection.LONG, "bearish": SignalDirection.SHORT}
VARIANTS = ("BO-01", "BO-02", "BO-03", "BO-04")
GRADES = {"A1": SignalGrade.A1, "STRONG": SignalGrade.HIGH_QUALITY}


@dataclass(frozen=True, slots=True)
class BreakoutPublication:
    symbol: str
    instrument_key: str
    timeframe: str
    bar_end: datetime
    side: Literal["bullish", "bearish"]
    setup_id: str
    strategy_version: str
    config_hash: str
    score: int = 0
    reasons: tuple[str, ...] = ()
    variant: str | None = None
    grade: Literal["A1", "STRONG"] | None = None
    evidence: Mapping[str, Any] | None = None


def _required(value: str, field: str, limit: int) -> str:
    value = value.strip()
    if not value or len(value) > limit:
        raise ValueError(f"{field} must contain between one and {limit} characters")
    return value


def _identity(user: User, signal: BreakoutPublication) -> dict[str, str | int]:
    if signal.bar_end.utcoffset() is None:
        raise ValueError("bar_end must include its time zone")
    if signal.side not in SIDE_DIRECTIONS:
        raise ValueError("side must be bullish or bearish")
    if type(signal.score) is not int or not 0 <= signal.score <= 100:
        raise ValueError("score must be an integer from zero to one hundred")
    if signal.variant is not None and signal.variant not in VARIANTS:
        raise ValueError("Unknown breakout variant")
    if signal.grade is not None and signal.grade not in GRADES:
        raise ValueError("Unknown breakout grade")
    timeframe = _required(signal.timeframe, "timeframe", 16)
    if timeframe not in language.TIMEFRAMES:
        raise ValueError("timeframe is not a supported signal timeframe")
    identity: dict[str, str | int] = {
        "tenant_id": user.tenant_id,
        "user_id": user.id,
        "instrument": _required(signal.instrument_key, "instrument_key", 100).upper(),
        "timeframe": timeframe,
        "bar_end": signal.bar_end.astimezone(UTC).isoformat(),
        "side": signal.side,
        "setup_id": _required(signal.setup_id, "setup_id", 160),
        "strategy_version": _required(signal.strategy_version, "strategy_version", 40),
        "config_hash": _required(signal.config_hash, "config_hash", 80),
    }
    if signal.variant is not None:
        identity["variant"] = signal.variant
    return identity


def _history_signal(publication: BreakoutPublication) -> EnrichedSignal:
    symbol = _required(publication.symbol, "symbol", 50).upper()
    reasons = []
    for reason in publication.reasons:
        reason = " ".join(reason.split())
        if reason and len(reason) <= 200 and not language.check(reason, "signal_own", own_symbols=(symbol,)):
            if reason not in reasons:
                reasons.append(reason)
        if len(reasons) == 3:
            break

    grade = (
        GRADES[publication.grade] if publication.grade is not None
        else grade_from_score(publication.score) if publication.score else SignalGrade.VALID
    )
    signal = Signal(
        symbol=symbol,
        strategy_id=STRATEGY_ID,
        strategy_name=DISPLAY_NAME,
        direction=SIDE_DIRECTIONS[publication.side],
        timestamp=publication.bar_end.astimezone(UTC),
        score=publication.score,
        grade=grade,
        reasons=reasons,
        timeframe_combo=publication.timeframe.strip(),
    )
    return EnrichedSignal(signal=signal, composite_score=publication.score, grade=grade, breakdown={}, confirmations=[])


def _json_object(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _evidence(publication: BreakoutPublication) -> dict[str, Any]:
    if publication.evidence is None:
        return {}
    try:
        packed = _json_object(publication.evidence)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Evidence must contain only finite JSON data") from exc
    if len(packed.encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError("Evidence exceeds the publication size limit")
    return json.loads(packed)


async def feed_labels(
    session: AsyncSession,
    user: User,
    history_ids: Sequence[int],
    *,
    include_evidence: bool = False,
) -> dict[int, dict[str, Any]]:
    """Return the owner's labels and, when requested, their scoped evidence records."""
    if not history_ids:
        return {}
    rows = await session.execute(
        select(SignalHistoryRecord.id, RuntimeCounterRecord.value)
        .join(RuntimeCounterRecord, RuntimeCounterRecord.key == cast(SignalHistoryRecord.id, String))
        .where(
            RuntimeCounterRecord.name == FEED_COUNTER_NAME,
            SignalHistoryRecord.strategy_id == STRATEGY_ID,
            SignalHistoryRecord.tenant_id == user.tenant_id,
            SignalHistoryRecord.user_id == user.id,
            SignalHistoryRecord.id.in_(history_ids),
        )
    )
    result: dict[int, dict[str, Any]] = {}
    links = {}
    for history_id, raw in rows:
        try:
            labels = json.loads(raw)
        except ValueError:
            continue
        if isinstance(labels, dict):
            result[history_id] = {
                key: labels[key]
                for key, allowed in (("variant", VARIANTS), ("grade", GRADES))
                if isinstance(labels.get(key), str) and labels[key] in allowed
            }
            if include_evidence and type(labels.get("marker_id")) is int:
                links[labels["marker_id"]] = history_id
    if links:
        evidence_rows = await session.execute(
            select(RuntimeCounterRecord.id, RuntimeCounterRecord.value).where(
                RuntimeCounterRecord.name == COUNTER_NAME,
                RuntimeCounterRecord.id.in_(links),
            )
        )
        for marker_id, raw in evidence_rows:
            try:
                marker = json.loads(raw)
            except ValueError:
                continue
            history_id = links[marker_id]
            if (isinstance(marker, dict) and marker.get("history_id") == history_id and
                marker.get("tenant_id") == user.tenant_id and marker.get("user_id") == user.id):
                result[history_id]["evidence_record"] = marker
    return result


async def _claim(session: AsyncSession, key: str, value: str) -> int | None:
    builder = sqlite_insert if session.get_bind().dialect.name == "sqlite" else postgres_insert
    statement = builder(RuntimeCounterRecord).values(name=COUNTER_NAME, key=key, value=value)
    statement = statement.on_conflict_do_nothing(index_elements=["name", "key"]).returning(RuntimeCounterRecord.id)
    return await session.scalar(statement)


async def publish(session: AsyncSession, user: User, publication: BreakoutPublication) -> int | None:
    """Return the new history row ID, or None when this same event was already published.

    Use a dedicated session. The duplicate marker, history row and feed labels
    commit in one transaction.
    """
    if user.id is None or user.tenant_id is None or not user.is_active:
        raise ValueError("Breakout signals require an active user")
    identity = _identity(user, publication)
    enriched = _history_signal(publication)
    key = hashlib.sha256(_json_object(identity).encode()).hexdigest()
    labels = {key: value for key, value in (("variant", publication.variant), ("grade", publication.grade)) if value is not None}
    metadata = {**identity, **labels, "score": publication.score, "evidence": _evidence(publication)}

    try:
        marker_id = await _claim(session, key, _json_object(metadata))
        if marker_id is None:
            return None
        record = await persist_signal_history(session, user, enriched, commit=False)
        history_id = record.id
        marker = await session.get(RuntimeCounterRecord, marker_id)
        if marker is None:
            raise RuntimeError("Breakout publication marker could not be read")
        marker.value = _json_object({**metadata, "history_id": history_id})
        session.add(RuntimeCounterRecord(
            name=FEED_COUNTER_NAME,
            key=str(history_id),
            value=_json_object({**labels, "marker_id": marker_id}),
        ))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return history_id
