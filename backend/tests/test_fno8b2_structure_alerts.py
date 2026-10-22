"""F&O workspace 8b, slice 2 (owner order ATP_SESSION_FNO_WORKSPACE.md 8b-f): "Tell me when" on the positions page saves
the user's OWN level of his OWN position (a breakeven or a short strike on the underlying, his exit level or a share of
the premium on the contract) as ONE instrument rule of the existing alert rules. These tests pin that the exact body the
page sends is an ordinary instrument rule, that it has no "Then" action, and that when it fires it only records the
event - no order, no trade, no execution intent, no exit. Made-up symbols, levels and bars only."""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import func, select

from app.alerts import barclose
from app.db.models import AlertEventRecord, AlertRuleRecord, ExecutionIntentRecord, OrderRecord, ScanThenTemplateRecord, TradeRecord
from app.market_data.calendar import IST
from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner
from tests.test_s3a_notification_service import _flag

TUE = date(2026, 3, 10)


def _run(coro):
    return asyncio.run(coro)


def ist(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


# The bodies `positionInsights.alertBody` builds (frontend/src/fno/positionInsights.test.tsx pins the same shape).
BREAKEVEN = {"name": "NIFTY 50 at or below 24,440 (your breakeven)", "kind": "instrument", "symbol": "NIFTY 50", "exchange": "NSE",
             "condition": "close <= 24440", "base_tf": "1m", "priority": "normal", "cooldown_minutes": 60, "mode": "instant",
             "digest_every": "hourly"}
EXIT_LEVEL = {**BREAKEVEN, "name": "NIFTY 27 OCT 24500 PE at or above 90 (your exit level)", "symbol": "NIFTY 24500 PE 27 OCT 26",
              "exchange": "NFO", "condition": "close >= 90"}


class FakeFetch:
    def __init__(self, frames):
        self.frames = frames

    async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback):
        return [SymbolData(s, {base_tf: self.frames[s]}) for s in symbols if s in self.frames], {}, "test"


def _bars(start: datetime, closes) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp((start + timedelta(minutes=i)).astimezone(timezone.utc)) for i in range(len(closes))])
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(closes)}, index=idx)


def _count(model, tenant_id):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id))
    return _run(go())


def test_the_pages_alert_bodies_are_plain_instrument_rules_with_no_action():
    headers, me = _owner("fno8b2-alerts@example.com")
    _flag(True)
    try:
        for body in (BREAKEVEN, EXIT_LEVEL):
            made = client.post("/api/alerts/rules", headers=headers, json=body)
            assert made.status_code == 201, made.text
            rule = made.json()
            assert (rule["kind"], rule["symbol"], rule["exchange"], rule["base_tf"], rule["status"]) == (
                "instrument", body["symbol"], body["exchange"], "1m", "active")

        async def thens():
            async with _session_factory() as session:
                ids = list(await session.scalars(select(AlertRuleRecord.id).where(AlertRuleRecord.tenant_id == me["tenant_id"])))
                n = await session.scalar(select(func.count()).select_from(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id.in_(ids)))
                return ids, n
        ids, n = _run(thens())
        assert len(ids) == 2 and n == 0                          # no "Then" template: the rule can only notify
    finally:
        _flag(False)


def test_a_structure_alert_firing_records_the_event_and_places_nothing():
    _, me = _owner("fno8b2-fire@example.com")
    tenant = me["tenant_id"]

    async def make():
        async with _session_factory() as session:
            row = AlertRuleRecord(tenant_id=tenant, created_by=me["id"], name=BREAKEVEN["name"], kind="instrument", symbol="NIFTY 50",
                                  exchange="NSE", condition_text="close <= 24440", base_tf="1m", priority="normal", cooldown_minutes=60,
                                  mode="instant", digest_every="hourly", status="active", created_at=ist(TUE, 9, 0), updated_at=ist(TUE, 9, 0))
            session.add(row)
            await session.commit()
            return row.id
    rid = _run(make())
    before = (_count(OrderRecord, tenant), _count(TradeRecord, tenant), _count(ExecutionIntentRecord, tenant))

    async def evaluate():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rid)
            # NIFTY 50 falls through the user's breakeven on the 09:17 bar (closed by 09:19).
            fetch = FakeFetch({"NIFTY 50": _bars(ist(TUE, 9, 15), [24500.0, 24470.0, 24430.0, 24420.0])})
            result = await barclose.evaluate_rule(session, rule, ist(TUE, 9, 19), fetch)
            await session.commit()
            events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rid)))
            return result, events
    result, events = _run(evaluate())
    assert result[0] == "done" and result[1] >= 1 and len(events) >= 1
    # An alert only tells him: nothing was ordered, traded or queued for execution.
    assert (_count(OrderRecord, tenant), _count(TradeRecord, tenant), _count(ExecutionIntentRecord, tenant)) == before == (0, 0, 0)
