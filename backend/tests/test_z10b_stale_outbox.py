"""Z10-b (G5): stale data -> zero alerts end to end, and a reliable alert outbox.

* the alert badge on the exchange clock (night, weekend and holiday hours add nothing; unknown time fails closed);
* the bar-close engine records nothing on stale data; today's daily bar is never built from part of a day; a cached
  candle list never outlives the bar that was forming when it was fetched;
* the rule flush suppresses an event that is no longer current when it would go out (quiet hours, cap), never a digest;
* the dispatcher suppresses a stale screen alert (retry, replay) and never any other notification (exits, system);
* outbox: a lease per claimed row (two workers never send one row), the attempt counted at the claim, DEAD after the
  last attempt, one idempotency key per delivery on every retry, a crashed flush completed instead of repeated;
* chaos: channel down, worker killed mid-send, duplicate cycle; operator replay of dead letters (audited).
Flags off: everything above is unchanged (the existing S3/S4 suites run with both flags off).
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
import pandas as pd
import pytest
from sqlalchemy import delete, select, update

from app.alerts import barclose, dispatcher, engine, staleness
from app.alerts.barclose import with_today
from app.alerts.staleness import alert_badge, closed_bars_after
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AlertDeliveryRecord, AlertEventRecord, AlertRuleRecord, AuditLogRecord, NotificationPolicyRecord, NotificationRecord, User
from app.market_data import service as md_service
from app.market_data.calendar import IST
from app.notifications.service import notify
from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.utils import enable_mfa

TUE = date(2026, 3, 10)
SECRET = "z10b-webhook-secret-012345"


def _run(coro):
    return asyncio.run(coro)


def ist(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


BAR = utc(ist(TUE, 9, 30))
FLAGS = (staleness.FLAG, dispatcher.OUTBOX_FLAG, "screener_v2")


def _flags(tenant_id: int, *names: str) -> None:
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name in names:
                entry = dict(flags.get(name) or {"on": False, "tenants": []})
                entry["tenants"] = sorted(set(entry.get("tenants") or []) | {tenant_id})
                flags[name] = entry
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _reset_flags():
    yield

    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = {k: v for k, v in (current.get("flags") or {}).items() if k not in (*FLAGS[:2], "byob_data_tier")}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


# --- the alert badge --------------------------------------------------------------------------------------------------

def test_bars_are_counted_on_the_exchange_clock():
    assert closed_bars_after(BAR, "5m", ist(TUE, 9, 36)) == 0                    # the 09:35 bar is still forming
    assert closed_bars_after(BAR, "5m", ist(TUE, 9, 40)) == 1
    assert closed_bars_after(BAR, "5m", ist(TUE, 9, 51)) == 3
    last = utc(ist(TUE, 15, 25))
    assert closed_bars_after(last, "5m", ist(TUE, 23, 0)) == 0                    # the night adds nothing
    assert closed_bars_after(last, "5m", ist(date(2026, 3, 11), 9, 20)) == 1
    daily = utc(ist(TUE, 9, 15))
    assert closed_bars_after(daily, "1d", ist(date(2026, 3, 11), 7, 0)) == 0      # quiet hours until 07:00: still current
    assert closed_bars_after(daily, "1d", ist(date(2026, 3, 13), 15, 31)) == 3
    assert closed_bars_after(daily, "1d", ist(date(2026, 3, 15), 12, 0)) == 3     # the weekend adds nothing
    assert closed_bars_after(daily, "1d", ist(date(2026, 3, 13), 15, 31), holidays={date(2026, 3, 12)}) == 2
    assert closed_bars_after(utc(ist(TUE, 0, 0)), "1d", ist(TUE, 15, 31)) == 0    # a broker's midnight label is the same day
    assert closed_bars_after(BAR, "1w", ist(TUE, 12, 0)) is None


def test_the_badge_has_the_bt1_shape_and_fails_closed():
    fresh = alert_badge(BAR, "5m", ist(TUE, 9, 51))
    assert fresh["stale"] is False and fresh["alerts_suppressed"] is False and fresh["bars_behind"] == 3 and fresh["stale_reason"] is None
    stale = alert_badge(BAR, "5m", ist(TUE, 9, 56))
    assert stale["alerts_suppressed"] and stale["stale"] and "more than 3 newer 5m bar" in stale["stale_reason"]
    assert alert_badge(None, "5m", ist(TUE, 9, 36))["alerts_suppressed"]          # unknown time = stale
    assert alert_badge(BAR, None, ist(TUE, 9, 36))["alerts_suppressed"]
    assert alert_badge(BAR, "1w", ist(TUE, 9, 36))["alerts_suppressed"]
    assert not alert_badge(BAR, "5m", ist(date(2026, 3, 20), 9, 36), max_bars=0)["alerts_suppressed"]   # G1 limit off = off here too
    assert alert_badge(BAR, "5m", ist(TUE, 9, 41), max_bars=0)["stale"] is False


def test_only_market_data_alerts_can_ever_be_suppressed():
    """ADR-0004: trade, exit, risk, broker and system notifications are never held back by the stale-data guard."""
    assert staleness.SUPPRESSIBLE == frozenset({NotificationType.SCREEN_ALERT.value})
    for kind in (NotificationType.EXIT, NotificationType.EMERGENCY_EXIT, NotificationType.ENTRY, NotificationType.SYSTEM_FAILURE,
                 NotificationType.TOKEN_EXPIRED, NotificationType.DAILY_LOSS_LIMIT, NotificationType.AI_PROPOSAL):
        assert kind.value not in staleness.SUPPRESSIBLE


# --- bar-close engine ---------------------------------------------------------------------------------------------------

class _Fetch:
    def __init__(self, frames):
        self.frames, self.calls = frames, 0

    async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback, **kw):
        self.calls += 1
        return [SymbolData(s, {base_tf: self.frames[s]}) for s in symbols if s in self.frames], {}, "test"


def _bars(start: datetime, closes, minutes=5) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(utc(start + timedelta(minutes=minutes * i))) for i in range(len(closes))])
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(closes)}, index=idx)


def _instrument_rule(tenant_id: int, symbol: str) -> int:
    async def go():
        async with _session_factory() as session:
            row = AlertRuleRecord(tenant_id=tenant_id, name=f"r-{symbol}", kind="instrument", symbol=symbol, condition_text="close > 100",
                                  base_tf="5m", priority="normal", cooldown_minutes=0, mode="instant", digest_every="hourly", status="active",
                                  created_at=ist(TUE, 9, 0), updated_at=ist(TUE, 9, 0))
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _evaluate(rule_id: int, now: datetime, fetch) -> Any:
    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            result = await barclose.evaluate_rule(session, rule, now, fetch)
            await session.commit()
            return result, rule.last_problem
    return _run(go())


def _events(rule_id: int) -> List[AlertEventRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule_id).order_by(AlertEventRecord.id)))
    return _run(go())


def test_the_bar_close_engine_records_nothing_on_stale_data(monkeypatch):
    """`closed_frame` already refuses data that has not reached the expected bar; the badge is the second guard, for any
    path that lets an old bar through. Bypassing the first shows the second."""
    _, me = _owner("z10b-barclose@example.com")
    monkeypatch.setattr(barclose, "closed_frame", lambda df, tf, expected: df)
    now = ist(TUE, 11, 1)
    stale = _instrument_rule(me["tenant_id"], "TCS")
    fresh = _instrument_rule(me["tenant_id"], "INFY")
    fetch = _Fetch({"TCS": _bars(ist(TUE, 9, 15), [101, 102, 103]), "INFY": _bars(ist(TUE, 10, 40), [101, 102, 103, 104])})

    _flags(me["tenant_id"], staleness.FLAG)
    (state, fired), problem = _evaluate(stale, now, fetch)
    assert (state, fired) == ("suppressed", 0) and problem.startswith("alerts suppressed: TCS: market data stale") and _events(stale) == []
    assert _evaluate(fresh, now, fetch)[0] == ("done", 1) and len(_events(fresh)) == 1

    other = _owner("z10b-barclose-off@example.com")[1]                          # flag off: the old behaviour
    off = _instrument_rule(other["tenant_id"], "TCS")
    assert _evaluate(off, now, fetch)[0] == ("done", 1)


def test_todays_daily_bar_is_never_built_from_part_of_a_day():
    daily = pd.DataFrame({"open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0], "volume": [5.0]},
                         index=pd.DatetimeIndex([pd.Timestamp(utc(ist(date(2026, 3, 9), 9, 15)))]))
    noon = _bars(ist(TUE, 9, 15), [20.0] * 12, minutes=15)                         # the feed stopped at 12:00
    assert with_today(daily, noon, TUE, complete_only=True) is daily
    assert len(with_today(daily, noon, TUE)) == 2                                  # the intrabar path (forming) still builds it
    forming = pd.concat([daily, pd.DataFrame({"open": [20.0], "high": [20.0], "low": [20.0], "close": [20.0], "volume": [1.0]},
                                             index=pd.DatetimeIndex([pd.Timestamp(utc(ist(TUE, 0, 0)))]))])
    kept = with_today(forming, noon, TUE, complete_only=True)                    # the broker's own (forming) candle for today
    assert len(kept) == 1 and kept.index[-1] == daily.index[-1]
    assert with_today(forming, noon, TUE) is forming                              # intrabar path: unchanged
    whole = _bars(ist(TUE, 9, 15), [20.0] * 25, minutes=15)                         # 09:15 .. 15:15
    out = with_today(daily, whole, TUE, complete_only=True)
    assert len(out) == 2 and out.iloc[-1]["volume"] == 25000


def test_a_cached_candle_list_never_outlives_its_forming_bar():
    ttl = md_service.forming_capped_ttl
    at = utc(ist(TUE, 9, 29)) + timedelta(seconds=30)
    assert ttl(utc(ist(TUE, 9, 25)), "5min", "NSE", at, 60) == 30                  # expires at 09:30, when the bar closes
    assert ttl(utc(ist(TUE, 9, 28)), "1min", "NSE", at, 60) == 60                  # closed already: the usual TTL
    assert ttl(utc(ist(TUE, 15, 15)), "60min", "NSE", utc(ist(TUE, 15, 29)), 60) == 60
    assert ttl(utc(ist(TUE, 15, 15)), "60min", "NSE", utc(ist(TUE, 15, 29)) + timedelta(seconds=50), 60) == 10   # short last bucket
    assert ttl(utc(ist(TUE, 0, 0)), "day", "NSE", utc(ist(TUE, 15, 29)) + timedelta(seconds=45), 60) == 15
    assert ttl(utc(ist(TUE, 9, 25)), "5min", "MCX", at, 60) == 30
    assert ttl(utc(ist(TUE, 9, 25)), "week", "NSE", at, 60) == 60


def test_get_candles_caches_with_the_capped_ttl(monkeypatch):
    from app.core.models import OHLCVBar
    written: List[int] = []

    async def fake_set(key, value, ttl_seconds):
        written.append(ttl_seconds)

    async def fake_get(key):
        return None
    monkeypatch.setattr(md_service, "cache_set", fake_set)
    monkeypatch.setattr(md_service, "cache_get", fake_get)

    class Broker:
        name = "fake"

    service = md_service.MarketDataService(Broker())  # type: ignore[arg-type]
    bar = OHLCVBar(timestamp=utc(ist(TUE, 9, 25)), open=1, high=1, low=1, close=1, volume=1)

    async def fetch(symbol, exchange, interval, now):
        return [bar]
    monkeypatch.setattr(service, "_fetch", fetch)
    _run(service.get_candles("TCS", "NSE", "5min", now=utc(ist(TUE, 9, 29)) + timedelta(seconds=20)))
    assert written == [40]


# --- the alert path: setup ---------------------------------------------------------------------------------------------

def _capture(status=200, fail_with: Exception = None):
    seen: List[Dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"body": request.content, "headers": dict(request.headers)})
        if fail_with is not None:
            raise fail_with
        return httpx.Response(status, json={})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _setup(email: str, *flags: str, tf: str = "5m", bar: datetime = BAR, quiet=None, mode="instant") -> Dict[str, Any]:
    """An organisation with a webhook channel and one rule that matched TCS and INFY on `bar` (events recorded at the bar's
    close + 30 s, not flushed yet)."""
    headers, me = _owner(email)
    tid = me["tenant_id"]
    if flags:
        _flags(tid, *flags)
    cfg = {"url": "https://hooks.example.com/z10b", "secret": SECRET}
    assert client.put("/api/alert-channels/webhook", headers=headers, json={"config": cfg, "min_severity": "INFO"}).status_code in (200, 201)
    recorded = bar + timedelta(minutes=5 if tf == "5m" else 375, seconds=30)

    async def go():
        async with _session_factory() as session:
            session.add(NotificationPolicyRecord(tenant_id=tid, timezone="Asia/Kolkata", quiet_start=quiet[0] if quiet else None,
                                                 quiet_end=quiet[1] if quiet else None, max_per_hour=30, group_window_seconds=0,
                                                 eod_digest_time="15:45", updated_at=bar))
            rule = AlertRuleRecord(tenant_id=tid, name="Volume shocker", kind="instrument", symbol="X", condition_text="volume > 1",
                                   base_tf=tf, priority="normal", cooldown_minutes=0, mode=mode, digest_every="hourly", status="active",
                                   created_at=bar, updated_at=bar)
            session.add(rule)
            await session.commit()
            for sym, close in (("TCS", 3810.5), ("INFY", 1502.0)):
                await engine.record_event(session, rule, sym, bar, {"close": close, "as_of": bar.isoformat()}, now=recorded)
            await session.commit()
            return rule.id
    rule_id = _run(go())
    return {"headers": headers, "me": me, "tid": tid, "rule_id": rule_id, "recorded": recorded}


def _flush(tid: int, now: datetime) -> Dict[str, int]:
    async def go():
        async with _session_factory() as session:
            return await engine.flush(session, now, tenant_id=tid)
    return _run(go())


REAL_CLOCK = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _dispatch(tid: int, http, now: datetime):
    """The tests run on a March 2026 clock; rows queued by `notify()` or a replay are due at the real time, so they are
    made due at the start of the year first (backoff times the tests set stay as they are)."""
    async def go():
        async with _session_factory() as session:
            await session.execute(update(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tid,
                                                                    AlertDeliveryRecord.next_attempt_at > REAL_CLOCK)
                                  .values(next_attempt_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
            await session.commit()
            return await dispatcher.dispatch_pending(session, client=http, now=now, tenant_id=tid)
    return _run(go())


def _rows(tid: int) -> List[AlertDeliveryRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tid).order_by(AlertDeliveryRecord.id)))
    return _run(go())


def _notes(tid: int) -> List[NotificationRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tid,
                                                                               NotificationRecord.event_type == "SCREEN_ALERT")))
    return _run(go())


def _set_row(row_id: int, **values) -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(update(AlertDeliveryRecord).where(AlertDeliveryRecord.id == row_id).values(**values))
            await session.commit()
    _run(go())


# --- rule flush ---------------------------------------------------------------------------------------------------------

def test_an_event_held_past_its_bar_is_suppressed_not_sent():
    """Quiet hours 09:00-11:00 IST hold a 5-minute alert from 09:35; at 11:00 its bar is 17 bars old - nothing goes out."""
    ctx = _setup("z10b-held@example.com", staleness.FLAG, quiet=("09:00", "11:00"))
    assert _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1)) == {"sent": 0, "held": 2}
    out = _flush(ctx["tid"], utc(ist(TUE, 11, 0)) + timedelta(seconds=5))
    assert out == {"sent": 0, "held": 0, "suppressed": 2}
    assert {(e.status, e.reason_code) for e in _events(ctx["rule_id"])} == {("suppressed", "stale_data")}
    assert _notes(ctx["tid"]) == [] and _rows(ctx["tid"]) == []

    off = _setup("z10b-held-off@example.com", quiet=("09:00", "11:00"))            # flag off: sent late, as before
    _flush(off["tid"], off["recorded"] + timedelta(seconds=1))
    assert _flush(off["tid"], utc(ist(TUE, 11, 0)) + timedelta(seconds=5))["sent"] == 2 and len(_notes(off["tid"])) == 1


def test_a_daily_alert_held_overnight_is_still_current_and_digests_are_not_judged():
    daily_bar = utc(ist(TUE, 9, 15))
    ctx = _setup("z10b-daily@example.com", staleness.FLAG, tf="1d", bar=daily_bar, quiet=("15:00", "07:00"))
    assert _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))["held"] == 2
    assert _flush(ctx["tid"], utc(ist(date(2026, 3, 11), 7, 0)) + timedelta(seconds=5))["sent"] == 2

    digest = _setup("z10b-digest@example.com", staleness.FLAG, mode="digest")
    assert _flush(digest["tid"], digest["recorded"] + timedelta(seconds=1)) == {"sent": 0, "held": 0}
    assert _flush(digest["tid"], utc(ist(TUE, 10, 0)) + timedelta(seconds=5))["sent"] == 2     # the hourly digest: past matches by design


# --- dispatcher: stale screen alerts --------------------------------------------------------------------------------------

def test_a_screen_alert_retried_after_its_bar_is_suppressed_and_other_notifications_still_go():
    ctx = _setup("z10b-retry@example.com", staleness.FLAG)
    _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))
    down, seen = _capture(status=503)
    t0 = ctx["recorded"] + timedelta(seconds=2)
    assert _dispatch(ctx["tid"], down, t0) == (0, 1) and len(seen) == 1               # channel down: retry scheduled
    up, seen_up = _capture()
    assert _dispatch(ctx["tid"], up, utc(ist(TUE, 10, 30))) == (0, 0) and seen_up == []   # 12 bars later: not sent
    row = _rows(ctx["tid"])[0]
    assert (row.status, row.reason_code) == ("SUPPRESSED", "stale_data") and "market data stale" in row.last_error

    # Not market data: an exit, a stop-out, a system failure always go out, flag on or not, however late.
    async def raise_others():
        async with _session_factory() as session:
            for kind in (NotificationType.EXIT, NotificationType.EMERGENCY_EXIT, NotificationType.SYSTEM_FAILURE):
                await notify(session, ctx["tid"], kind, title=f"{kind.value} happened", severity=NotificationSeverity.CRITICAL)
    _run(raise_others())
    assert _dispatch(ctx["tid"], up, utc(ist(date(2026, 3, 20), 12, 0))) == (3, 0) and len(seen_up) == 3


def test_a_screen_alert_without_its_events_is_not_sent():
    _, me = _owner("z10b-orphan-send@example.com")
    _flags(me["tenant_id"], staleness.FLAG)
    assert client.put("/api/alert-channels/webhook", headers=_, json={"config": {"url": "https://hooks.example.com/o", "secret": SECRET},
                                                                      "min_severity": "INFO"}).status_code in (200, 201)

    async def go():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.SCREEN_ALERT, title="r: 1 symbol(s) matched", severity=NotificationSeverity.WARNING)
    _run(go())
    http, seen = _capture()
    assert _dispatch(me["tenant_id"], http, BAR) == (0, 0) and seen == []
    assert _rows(me["tenant_id"])[0].status == "SUPPRESSED"                        # data time unknown: fails closed


def test_send_time_staleness_uses_the_timeframe_recorded_with_the_event():
    ctx = _setup("z10b-tf@example.com", staleness.FLAG)
    _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))
    assert {e.base_tf for e in _events(ctx["rule_id"])} == {"5m"}

    async def edit_rule():                                                          # the rule is changed to daily afterwards
        async with _session_factory() as session:
            await session.execute(update(AlertRuleRecord).where(AlertRuleRecord.id == ctx["rule_id"]).values(base_tf="1d"))
            await session.commit()
    _run(edit_rule())
    http, seen = _capture()
    assert _dispatch(ctx["tid"], http, utc(ist(TUE, 10, 30))) == (0, 0) and seen == []   # 12 five-minute bars: stale
    assert _rows(ctx["tid"])[0].status == "SUPPRESSED"


def test_a_fresh_screen_alert_is_sent_with_both_flags_on():
    ctx = _setup("z10b-fresh@example.com", staleness.FLAG, dispatcher.OUTBOX_FLAG)
    _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))
    http, seen = _capture()
    assert _dispatch(ctx["tid"], http, ctx["recorded"] + timedelta(seconds=2)) == (1, 0)
    row = _rows(ctx["tid"])[0]
    assert row.status == "SENT" and row.attempts == 1 and row.lease_until is None and row.idem_key
    assert seen[0]["headers"]["idempotency-key"] == row.idem_key
    assert json.loads(seen[0]["body"])["alert"]["symbols"] == ["INFY", "TCS"]


# --- outbox reliability ----------------------------------------------------------------------------------------------------

def _plain(email: str, *flags: str) -> Dict[str, Any]:
    """An organisation with a webhook channel and one SYSTEM_FAILURE notification queued (not market data)."""
    headers, me = _owner(email)
    if flags:
        _flags(me["tenant_id"], *flags)
    cfg = {"url": "https://hooks.example.com/plain", "secret": SECRET}
    assert client.put("/api/alert-channels/webhook", headers=headers, json={"config": cfg, "min_severity": "INFO"}).status_code in (200, 201)

    async def go():
        async with _session_factory() as session:
            return (await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="Worker stopped",
                                 severity=NotificationSeverity.CRITICAL)).id
    note_id = _run(go())
    return {"headers": headers, "me": me, "tid": me["tenant_id"], "note_id": note_id}


def test_channel_down_ends_dead_with_one_key_on_every_attempt():
    ctx = _plain("z10b-down@example.com", dispatcher.OUTBOX_FLAG)
    down, seen = _capture(fail_with=httpx.ConnectError("connection refused"))
    t0 = BAR
    for i in range(dispatcher.MAX_ATTEMPTS):
        assert _dispatch(ctx["tid"], down, t0 + timedelta(hours=i)) == (0, 1)
    row = _rows(ctx["tid"])[0]
    assert (row.status, row.reason_code, row.attempts, row.lease_until) == ("DEAD", "dead_letter", dispatcher.MAX_ATTEMPTS, None)
    assert len(seen) == dispatcher.MAX_ATTEMPTS and {s["headers"]["idempotency-key"] for s in seen} == {row.idem_key}
    assert _dispatch(ctx["tid"], down, t0 + timedelta(days=1)) == (0, 0)             # DEAD stays dead until replayed
    dead = client.get("/api/alerts/dead-letters", headers=ctx["headers"]).json()
    assert [(d["id"], d["status"], d["reason"]) for d in dead] == [(row.id, "DEAD", "dead_letter")]
    assert client.post(f"/api/alerts/deliveries/{row.id}/retry", headers=ctx["headers"]).json()["status"] == "PENDING"
    up, seen_up = _capture()
    assert _dispatch(ctx["tid"], up, t0 + timedelta(days=2)) == (1, 0)
    assert seen_up[0]["headers"]["idempotency-key"] == row.idem_key                 # the receiver can drop the repeat


class _Killed(BaseException):
    """What a SIGKILL looks like from inside the coroutine: nothing after the send runs."""


class _Clock:
    """The dispatcher's real clock (`_utcnow`, what leases run on), set by the test."""
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def clock(monkeypatch):
    c = _Clock(BAR)
    monkeypatch.setattr(dispatcher, "_utcnow", c)
    return c


def test_a_worker_killed_mid_send_leaves_a_lease_and_the_row_is_sent_again_once_it_expires(monkeypatch, clock):
    ctx = _plain("z10b-killed@example.com", dispatcher.OUTBOX_FLAG)
    real = dispatcher.send_via_channel
    keys: List[str] = []

    async def dies(*args, **kw):
        keys.append(kw["idempotency_key"])
        raise _Killed()
    monkeypatch.setattr(dispatcher, "send_via_channel", dies)
    with pytest.raises(_Killed):
        _dispatch(ctx["tid"], None, BAR)
    row = _rows(ctx["tid"])[0]
    assert row.status == "PENDING" and row.attempts == 1 and row.claimed_by.startswith(dispatcher.WORKER_ID + ":")
    lease = row.lease_until.replace(tzinfo=timezone.utc) if row.lease_until.tzinfo is None else row.lease_until
    assert lease == BAR + timedelta(seconds=dispatcher.LEASE_SECONDS)

    monkeypatch.setattr(dispatcher, "send_via_channel", real)
    http, seen = _capture()
    clock.at = BAR + timedelta(seconds=30)
    assert _dispatch(ctx["tid"], http, clock.at) == (0, 0) and seen == []                        # another worker: the lease holds
    clock.at = lease + timedelta(seconds=1)
    assert _dispatch(ctx["tid"], http, clock.at) == (1, 0)
    row = _rows(ctx["tid"])[0]
    assert row.status == "SENT" and row.attempts == 2 and seen[0]["headers"]["idempotency-key"] == keys[0] == row.idem_key


def test_a_worker_lost_on_the_last_attempt_dead_letters_the_row(monkeypatch):
    ctx = _plain("z10b-lost@example.com", dispatcher.OUTBOX_FLAG)
    row = _rows(ctx["tid"])[0]
    _set_row(row.id, attempts=dispatcher.MAX_ATTEMPTS, lease_until=BAR - timedelta(seconds=1), claimed_by="gone:1")
    http, seen = _capture()
    assert _dispatch(ctx["tid"], http, BAR) == (0, 1) and seen == []
    row = _rows(ctx["tid"])[0]
    assert (row.status, row.reason_code) == ("DEAD", "worker_lost")


def test_two_workers_never_send_the_same_row(monkeypatch):
    """Worker B drains while worker A is in the middle of sending: B skips A's claimed row. And of two workers that
    both read the row before either claimed it, exactly one wins the claim."""
    ctx = _plain("z10b-two@example.com", dispatcher.OUTBOX_FLAG)
    real = dispatcher.send_via_channel
    sends: List[str] = []
    other: List[Any] = []

    async def slow(channel, notification, *args, **kw):
        sends.append("A")
        async with _session_factory() as session_b:                                 # worker B, its own session
            other.append(await dispatcher.dispatch_pending(session_b, now=BAR, tenant_id=ctx["tid"]))
    monkeypatch.setattr(dispatcher, "send_via_channel", slow)
    assert _dispatch(ctx["tid"], None, BAR) == (1, 0)
    assert sends == ["A"] and other == [(0, 0)]
    monkeypatch.setattr(dispatcher, "send_via_channel", real)

    second = _plain("z10b-two-race@example.com", dispatcher.OUTBOX_FLAG)

    row_id = _rows(second["tid"])[0].id

    async def race():
        async with _session_factory() as a, _session_factory() as b:
            row_a = await a.get(AlertDeliveryRecord, row_id)
            row_b = await b.get(AlertDeliveryRecord, row_a.id)
            return (await dispatcher._claim(a, row_a)).state, (await dispatcher._claim(b, row_b)).state
    assert _run(race()) == ("ok", "taken")


def test_a_slow_drain_never_hands_out_a_lease_that_is_already_over(monkeypatch, clock):
    """Review of #189 (regression): worker A spends 125 s on row 1, then claims row 2. The lease runs from the clock at
    the claim, not from A's drain start, so worker B draining at that moment cannot take row 2 as well."""
    ctx = _plain("z10b-slow-drain@example.com", dispatcher.OUTBOX_FLAG)

    async def second():
        async with _session_factory() as session:
            await notify(session, ctx["tid"], NotificationType.SYSTEM_FAILURE, title="Second", severity=NotificationSeverity.CRITICAL)
    _run(second())
    sends: List[Any] = []
    b_result: List[Any] = []

    async def send(channel, notification, *args, **kw):
        sends.append(("A", notification.title))
        if notification.title == "Worker stopped":
            clock.at = BAR + timedelta(seconds=125)                              # a slow SMTP / SMS fan-out
            return
        async def b_send(channel, notification, *a, **k):
            sends.append(("B", notification.title))
        monkeypatch.setattr(dispatcher, "send_via_channel", b_send)
        async with _session_factory() as session_b:                               # worker B, on the real clock
            b_result.append(await dispatcher.dispatch_pending(session_b, now=clock.at, tenant_id=ctx["tid"]))
        monkeypatch.setattr(dispatcher, "send_via_channel", send)
    monkeypatch.setattr(dispatcher, "send_via_channel", send)
    assert _dispatch(ctx["tid"], None, BAR) == (2, 0)
    assert sends == [("A", "Worker stopped"), ("A", "Second")] and b_result == [(0, 0)]


def test_a_send_is_cut_off_before_its_lease_ends(monkeypatch):
    ctx = _plain("z10b-cutoff@example.com", dispatcher.OUTBOX_FLAG)
    monkeypatch.setattr(dispatcher, "LEASE_SECONDS", dispatcher.LEASE_MARGIN_SECONDS + 1)      # a 1 s send budget

    async def hangs(*args, **kw):
        await asyncio.sleep(5)
    monkeypatch.setattr(dispatcher, "send_via_channel", hangs)
    assert _dispatch(ctx["tid"], None, BAR) == (0, 1)
    row = _rows(ctx["tid"])[0]
    assert (row.status, row.attempts, row.last_error, row.lease_until) == ("PENDING", 1, "TimeoutError", None)


def test_the_send_budget_is_what_is_left_of_the_lease(monkeypatch, clock):
    """Slow steps between the claim and the send (loading the channel, the stale-data check) use up the lease: the send
    gets only what is left, less the margin, and none at all once that is gone."""
    ctx = _plain("z10b-budget@example.com", dispatcher.OUTBOX_FLAG)
    budgets: List[float] = []
    real_wait_for = asyncio.wait_for

    async def wait_for(aw, *args, **kwargs):                                     # stands in for asyncio.wait_for(aw, timeout)
        budgets.append(kwargs["timeout"] if "timeout" in kwargs else args[0])
        return await real_wait_for(aw, *args, **kwargs)
    monkeypatch.setattr(dispatcher.asyncio, "wait_for", wait_for)
    real_get = dispatcher.ensure_tenant_key

    async def slow_db(session, tenant_id):                                       # 90 s of DB work after the claim
        clock.at = clock.at + timedelta(seconds=90)
        return await real_get(session, tenant_id)
    monkeypatch.setattr(dispatcher, "ensure_tenant_key", slow_db)
    sent: List[str] = []

    async def send(*args, **kw):
        sent.append("x")
    monkeypatch.setattr(dispatcher, "send_via_channel", send)
    assert _dispatch(ctx["tid"], None, BAR) == (1, 0)
    assert budgets == [dispatcher.LEASE_SECONDS - 90 - dispatcher.LEASE_MARGIN_SECONDS] and sent == ["x"]

    late = _plain("z10b-budget-late@example.com", dispatcher.OUTBOX_FLAG)

    async def slower_db(session, tenant_id):                                     # past the lease less its margin
        clock.at = clock.at + timedelta(seconds=dispatcher.LEASE_SECONDS - dispatcher.LEASE_MARGIN_SECONDS + 1)
        return await real_get(session, tenant_id)
    monkeypatch.setattr(dispatcher, "ensure_tenant_key", slower_db)
    sent.clear()
    assert _dispatch(late["tid"], None, clock.at) == (0, 1) and sent == []
    row = _rows(late["tid"])[0]
    assert (row.status, row.attempts, row.lease_until) == ("PENDING", 1, None) and "lease nearly over" in row.last_error


def test_only_the_claim_holder_writes_the_outcome(monkeypatch):
    """A worker whose lease ran out and was taken over (here: the row's claim token changed under it) writes nothing."""
    ctx = _plain("z10b-holder@example.com", dispatcher.OUTBOX_FLAG)
    row_id = _rows(ctx["tid"])[0].id

    async def taken_over(*args, **kw):
        async with _session_factory() as other:
            await other.execute(update(AlertDeliveryRecord).where(AlertDeliveryRecord.id == row_id).values(claimed_by="other-worker:1"))
            await other.commit()
    monkeypatch.setattr(dispatcher, "send_via_channel", taken_over)
    _dispatch(ctx["tid"], None, BAR)
    row = _rows(ctx["tid"])[0]
    assert (row.status, row.claimed_by, row.sent_at) == ("PENDING", "other-worker:1", None)


def test_a_duplicate_enqueue_queues_nothing_more():
    ctx = _plain("z10b-dup-enqueue@example.com", dispatcher.OUTBOX_FLAG)

    async def again():
        async with _session_factory() as session:
            note = await session.get(NotificationRecord, ctx["note_id"])
            added = await dispatcher.enqueue_for_notification(session, note)
            await session.commit()
            return added
    assert _run(again()) == [] and len(_rows(ctx["tid"])) == 1
    assert _rows(ctx["tid"])[0].idem_key == dispatcher.delivery_key(ctx["note_id"], _rows(ctx["tid"])[0].channel_id)


def test_rows_queued_before_the_flag_are_keyed_on_claim_and_a_duplicate_is_not_sent():
    ctx = _plain("z10b-dup-rows@example.com")                                       # queued with the flag off: no key
    first = _rows(ctx["tid"])[0]
    assert first.idem_key is None

    async def copy():
        async with _session_factory() as session:
            session.add(AlertDeliveryRecord(tenant_id=ctx["tid"], notification_id=first.notification_id, channel_id=first.channel_id,
                                            status="PENDING", attempts=0, next_attempt_at=BAR))
            await session.commit()
    _run(copy())

    async def later():                                                              # a row after the duplicate is still drained
        async with _session_factory() as session:
            await notify(session, ctx["tid"], NotificationType.SYSTEM_FAILURE, title="Again", severity=NotificationSeverity.CRITICAL)
    _run(later())
    _flags(ctx["tid"], dispatcher.OUTBOX_FLAG)
    http, seen = _capture()
    assert _dispatch(ctx["tid"], http, BAR) == (2, 0) and len(seen) == 2
    rows = _rows(ctx["tid"])
    assert [(r.status, r.reason_code) for r in rows] == [("SENT", None), ("SUPPRESSED", "duplicate"), ("SENT", None)]


def test_a_crashed_flush_is_completed_not_repeated(monkeypatch):
    """notify() commits the notification and its outbox rows; a crash before the flush marks the events sent used to
    raise the same alert again on the next cycle."""
    from app.alerts import links

    async def crash(*args, **kw):
        raise RuntimeError("worker killed")

    def run(email, *flags):
        ctx = _setup(email, *flags)
        monkeypatch.setattr(links, "add_linked_delivery", crash)
        with pytest.raises(RuntimeError):
            _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))
        monkeypatch.undo()
        assert {e.status for e in _events(ctx["rule_id"])} == {"pending"} and len(_notes(ctx["tid"])) == 1
        assert _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=40))["sent"] == 2
        return ctx

    on = run("z10b-crash@example.com", dispatcher.OUTBOX_FLAG)
    notes = _notes(on["tid"])
    assert len(notes) == 1 and {e.notification_id for e in _events(on["rule_id"])} == {notes[0].id}
    assert json.loads(notes[0].metadata_json)["group_id"] == _events(on["rule_id"])[0].group_id      # matched by its group
    rows = _rows(on["tid"])
    assert len(rows) == 1 and rows[0].group_id and rows[0].priority == "normal"

    off = run("z10b-crash-off@example.com")
    assert len(_notes(off["tid"])) == 2                                             # flag off: the alert is raised twice


def test_record_event_twice_and_flush_twice_raise_one_alert():
    ctx = _setup("z10b-dup-cycle@example.com", staleness.FLAG, dispatcher.OUTBOX_FLAG)

    async def again():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, ctx["rule_id"])
            return await engine.record_event(session, rule, "TCS", BAR, {"close": 1.0}, now=ctx["recorded"])
    assert _run(again()) is None
    _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))
    assert _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=2)) == {"sent": 0, "held": 0}
    http, seen = _capture()
    now = ctx["recorded"] + timedelta(seconds=3)
    assert _dispatch(ctx["tid"], http, now) == (1, 0) and _dispatch(ctx["tid"], http, now) == (0, 0) and len(seen) == 1


# --- operator replay ------------------------------------------------------------------------------------------------------

def _admin(email: str):
    headers, me = _owner(email)

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(headers)
    return headers, me


def test_the_operator_replays_dead_letters_and_it_is_audited():
    ctx = _plain("z10b-replay@example.com", dispatcher.OUTBOX_FLAG)
    row = _rows(ctx["tid"])[0]
    _set_row(row.id, status="DEAD", reason_code="dead_letter", attempts=dispatcher.MAX_ATTEMPTS, idem_key=row.idem_key,
             last_error="SMS gateway: +919876543210: HTTP 503")
    for path in ("/api/admin/alerts/outbox", "/api/admin/alerts/dead-letters"):
        assert client.get(path, headers=ctx["headers"]).status_code == 403
    assert client.post("/api/admin/alerts/dead-letters/replay", headers=ctx["headers"], json={"ids": [row.id], "reason": "x" * 5}).status_code == 403

    admin, _ = _admin("z10b-ops@example.com")
    listed = client.get(f"/api/admin/alerts/dead-letters?tenant_id={ctx['tid']}", headers=admin).json()
    assert [(d["id"], d["status"], d["event_type"], d["channel"]) for d in listed] == [(row.id, "DEAD", "SYSTEM_FAILURE", "WEBHOOK")]
    assert "title" not in listed[0] and "message" not in listed[0] and "last_error" not in listed[0]
    assert listed[0]["error_class"] == "http_5xx" and "9876543210" not in json.dumps(listed)
    assert client.get("/api/admin/alerts/outbox", headers=admin).json()["by_status"]["DEAD"] >= 1

    sent = _plain("z10b-replay-sent@example.com")
    sent_row = _rows(sent["tid"])[0]
    _set_row(sent_row.id, status="SENT")
    out = client.post("/api/admin/alerts/dead-letters/replay", headers=admin,
                      json={"ids": [row.id, sent_row.id, 99999999], "reason": "provider outage over"}).json()
    assert out["replayed"] == [row.id] and set(out["skipped"]) == {str(sent_row.id), "99999999"}
    again = _rows(ctx["tid"])[0]
    assert (again.status, again.attempts, again.reason_code, again.idem_key) == ("PENDING", 0, "replayed", row.idem_key)

    async def audit():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == ctx["tid"],
                                                                             AuditLogRecord.event == "alert_dead_letters_replayed")))
    entries = _run(audit())
    assert len(entries) == 1 and "provider outage over" in entries[0].detail
    http, seen = _capture()
    assert _dispatch(ctx["tid"], http, BAR) == (1, 0) and seen[0]["headers"]["idempotency-key"] == row.idem_key


def test_a_replayed_screen_alert_whose_data_went_stale_is_suppressed():
    ctx = _setup("z10b-replay-stale@example.com", staleness.FLAG, dispatcher.OUTBOX_FLAG)
    _flush(ctx["tid"], ctx["recorded"] + timedelta(seconds=1))
    row = _rows(ctx["tid"])[0]
    _set_row(row.id, status="DEAD", reason_code="dead_letter")
    admin, _ = _admin("z10b-ops-stale@example.com")
    assert client.post("/api/admin/alerts/dead-letters/replay", headers=admin, json={"ids": [row.id], "reason": "retry all"}).json()["replayed"] == [row.id]
    http, seen = _capture()
    assert _dispatch(ctx["tid"], http, utc(ist(date(2026, 3, 11), 10, 0))) == (0, 0) and seen == []
    assert (_rows(ctx["tid"])[0].status, _rows(ctx["tid"])[0].reason_code) == ("SUPPRESSED", "stale_data")


def test_dead_letters_follow_bt1b_visibility():
    """With `byob_data_tier` on, a member's screen-alert delivery is theirs only - also in the dead-letter list and its
    retry; an exit notification stays organisation-wide (BT1b's feed rule)."""
    from app.db.models import AlertChannelRecord
    from tests.test_bt1b_guard_all_reads import _member
    owner_headers, owner = _owner("z10b-bt1b-owner@example.com")
    member_headers, member = _member(owner_headers)
    t = owner["tenant_id"]

    async def seed():
        async with _session_factory() as session:
            session.add(AlertChannelRecord(tenant_id=t, channel_type="webhook", enabled=True, min_severity="INFO", encrypted_config="x"))
            await session.commit()
            alert = await notify(session, t, NotificationType.SCREEN_ALERT, "Breakout: TCS", user_id=member["id"])
            exit_ = await notify(session, t, NotificationType.EXIT, "TCS closed at stop", user_id=member["id"])
            await session.execute(update(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == t).values(status="DEAD"))
            await session.commit()
            return alert.id, exit_.id
    alert, exit_ = _run(seed())
    _flags(t, "byob_data_tier")
    rows = {r.notification_id: r.id for r in _rows(t)}
    owner_dead = {d["notification_id"] for d in client.get("/api/alerts/dead-letters", headers=owner_headers).json()}
    member_dead = {d["notification_id"] for d in client.get("/api/alerts/dead-letters", headers=member_headers).json()}
    assert owner_dead == {exit_} and member_dead == {alert, exit_}
    assert client.post(f"/api/alerts/deliveries/{rows[alert]}/retry", headers=owner_headers).status_code == 404
    assert client.post(f"/api/alerts/deliveries/{rows[alert]}/retry", headers=member_headers).json()["status"] == "PENDING"


def test_flags_are_off_by_default():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    for name in (staleness.FLAG, dispatcher.OUTBOX_FLAG):
        assert name in FEATURE_FLAGS and name in DEFAULT_OFF_FLAGS


@pytest.fixture(autouse=True)
def _clean_rules():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(NotificationPolicyRecord))
            await session.commit()
    _run(go())
    yield
