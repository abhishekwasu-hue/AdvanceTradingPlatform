import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import AiActionRecord, ExecutionIntentRecord, OrderRecord, RuntimeCounterRecord, SignalHistoryRecord, User
from app.signals_feed import service as feed
from app.workers import amw_breakout_publish as publisher
from tests.test_auth_api import _register, _session_factory


def _email() -> str:
    email = f"amw-breakout-{uuid.uuid4().hex}@example.com"
    _register(email)
    return email


def _event() -> publisher.BreakoutPublication:
    return publisher.BreakoutPublication(
        symbol="NIFTY",
        instrument_key="NSE_INDEX:NIFTY50",
        timeframe="15m",
        bar_end=datetime(2026, 10, 1, 5, tzinfo=UTC),
        side="bullish",
        setup_id="range-edge",
        strategy_version="v1",
        config_hash="tests",
        score=87,
        reasons=("Price closed above the range.", "BUY at 123"),
        variant="BO-02",
        grade="A1",
        evidence={
            "level": 123.45,
            "trigger_ohlc": {"open": 123, "high": 125, "low": 122, "close": 124},
            "sources": ["SESSION", "HORIZONTAL", "UNKNOWN"],
            "gates": [{"name": "Opposing zone", "status": "PASS"}, {"name": "runtime-only", "status": "PASS"}],
            "zone": {"low": 130, "high": 140, "tf": "1h", "key": "internal-zone-key"},
            "breakdown": {"level": {"maximum": 15, "awarded": 15}, "internal_only": {"maximum": 4, "awarded": 2}},
            "candle": {"body": 0.8, "close": 0.15},
            "clearance_atr": 2.5,
            "acceptance": "RETEST_BREAKOUT",
        },
    )


async def _user(session, email: str) -> User:
    return (await session.scalars(select(User).where(User.email == email))).one()


def test_publish_is_idempotent_scoped_and_informational():
    email, other_email = _email(), _email()
    event = _event()

    async def go():
        async with _session_factory() as session:
            user = await _user(session, email)
            first = await publisher.publish(session, user, event)
            assert first is not None
            first_user_id = user.id

        async with _session_factory() as session:
            user = await _user(session, email)
            ist = timezone(timedelta(hours=5, minutes=30))
            equivalent = replace(event, bar_end=event.bar_end.astimezone(ist), instrument_key=event.instrument_key.lower())
            assert await publisher.publish(session, user, equivalent) is None
            second = await publisher.publish(session, user, replace(event, side="bearish"))
            assert second not in (None, first)
            assert await publisher.publish(session, user, replace(event, setup_id="other-edge")) not in (None, first, second)

        async with _session_factory() as session:
            other = await _user(session, other_email)
            other_id = await publisher.publish(session, other, event)
            assert other_id not in (None, first)
            assert await publisher.feed_labels(session, other, (first,), include_evidence=True) == {}
            user = await session.get(User, first_user_id)
            assert user is not None
            result = await feed.feed(session, user, source="library", symbol="NIFTY")
            assert len(result["items"]) == 3
            assert {item["setup"] for item in result["items"]} == {publisher.DISPLAY_NAME}
            for item in result["items"]:
                assert item["action_id"] is None
                assert item["status"] == "info"
                assert all(item[field] is None for field in ("entry", "stop", "target", "rr"))
                assert item["variant"] == "BO-02"
                assert item["grade"] == "A1"
                assert item["signal_type"] == "amw_breakout"
                assert "evidence" not in item
                assert item["breakout"]["opposing_area"] == {"low": 130, "high": 140, "timeframe": "1h"}
                assert item["breakout"]["factors"] == [{"component": "level", "maximum": 15, "awarded": 15}]
                assert item["breakout"]["confirmed_at"] == "2026-10-01T05:00:00+00:00"
                assert item["breakout"]["score"] == 87
                assert "level" not in item["breakout"]
                assert item["breakout"]["breakout_level"] == 123.45
                assert item["breakout"]["trigger_ohlc"]["close"] == 124
                assert item["breakout"]["sources"] == ["SESSION", "HORIZONTAL"]
                assert item["breakout"]["gates"] == [{"name": "Opposing zone", "status": "PASS"}]

            row = await session.get(SignalHistoryRecord, first)
            assert row is not None
            assert row.direction == "LONG"
            assert row.score == 87
            assert row.grade == "A1"
            assert json.loads(row.reasons_json) == ["Price closed above the range."]
            counters = await session.scalars(
                select(RuntimeCounterRecord.value).where(RuntimeCounterRecord.name == publisher.COUNTER_NAME)
            )
            saved = next(item for raw in counters if (item := json.loads(raw)).get("history_id") == first)
            assert saved["evidence"] == event.evidence
            for model in (OrderRecord, AiActionRecord, ExecutionIntentRecord):
                assert await session.scalar(select(model.id).where(model.tenant_id == user.tenant_id)) is None

    asyncio.run(go())


def test_history_failure_releases_dedupe_marker_for_retry(monkeypatch):
    email = _email()
    event = _event()
    real_writer = publisher.persist_signal_history

    async def abort_writer(*args, **kwargs):
        raise RuntimeError("history write failed")

    async def go():
        async with _session_factory() as session:
            user = await _user(session, email)
            monkeypatch.setattr(publisher, "persist_signal_history", abort_writer)
            with pytest.raises(RuntimeError, match="history write failed"):
                await publisher.publish(session, user, event)

        async with _session_factory() as session:
            user = await _user(session, email)
            dedupe_rows = list(await session.scalars(
                select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == publisher.COUNTER_NAME)
            ))
            assert all(json.loads(row.value)["user_id"] != user.id for row in dedupe_rows)
            monkeypatch.setattr(publisher, "persist_signal_history", real_writer)
            assert await publisher.publish(session, user, event) is not None

    asyncio.run(go())


@pytest.mark.parametrize("evidence", [{"strength": float("nan")}, {"zone": object()}, {"large": "X" * 8192}])
def test_evidence_rejects_non_json_and_oversize(evidence):
    with pytest.raises(ValueError, match="Evidence"):
        publisher._evidence(replace(_event(), evidence=evidence))
