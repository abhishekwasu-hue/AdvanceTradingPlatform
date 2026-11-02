"""N2-5 (P1 spec 1.5, docs/design/N2_P1FGH.md): the market context panel behind the `context_panel` flag (off by
default).

Covers:
- the pure reads: Dow trend, weekly bars, breadth, VIX rank and percentile, staleness;
- every tile from seeded lake bars and events (the caller's organisation and global only);
- a failing tile is NA on its own, and FII / DII and global cues are NA with their reason;
- no advice wording.
"""
import asyncio
import math
import uuid
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from app.context_panel import service
from app.db.models import MarketEventRecord, MdCandleRecord
from app.journal.review import advice_in
from app.market_data.calendar import IST
from app.market_lake.bhavcopy_backfill import bar_end
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n2c-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(service.FLAG) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[service.FLAG] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _today():
    return datetime.now(UTC).astimezone(IST).date()


def _sessions(n: int, end: date):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _seed(key: str, closes, days, spread=0.01):
    async def go():
        async with _session_factory() as session:
            for d, c in zip(days, closes):
                await session.merge(MdCandleRecord(instrument_key=key, timeframe="day", ts=bar_end(d), source="test-n2c", version=98,
                                                   open=c, high=c * (1 + spread), low=c * (1 - spread), close=c, volume=1))
            await session.commit()
    _run(go())


def _frame(highs, lows, closes):
    idx = pd.date_range("2026-01-05", periods=len(closes), freq="B")
    return pd.DataFrame({"high": highs, "low": lows, "close": closes}, index=idx)


# --- pure ------------------------------------------------------------------------------------------------------------

def test_dow_trend_weekly_breadth_rank_and_staleness():
    up = [100 + 2 * i + 6 * math.sin(i / 2.0) for i in range(80)]
    down = [300 - 2 * i - 6 * math.sin(i / 2.0) for i in range(80)]
    assert service.dow_trend(_frame([c + 1 for c in up], [c - 1 for c in up], up))["trend"] == "UPTREND"
    assert service.dow_trend(_frame([c + 1 for c in down], [c - 1 for c in down], down))["trend"] == "DOWNTREND"
    flat = [100 + 5 * math.sin(i / 2.0) for i in range(80)]
    assert service.dow_trend(_frame([c + 1 for c in flat], [c - 1 for c in flat], flat))["trend"] == "RANGE"
    w = service.weekly(_frame([1, 5, 2, 3, 4, 9], [0, 1, 1, 2, 3, 2], [1, 2, 3, 4, 5, 6]))
    assert list(w["high"]) == [5, 9] and list(w["low"]) == [0, 2] and list(w["close"]) == [5, 6]
    assert service.breadth({"A": 11, "B": 9, "C": 10, "D": 5}, {"A": 10, "B": 10, "C": 10}) == {"advances": 1, "declines": 1, "unchanged": 1}
    rp = service.rank_and_percentile([10, 20, 15, 12])
    assert rp == {"rank": 20.0, "percentile": round(100 * 1 / 3, 1)}
    assert service.rank_and_percentile([5]) == {"rank": None, "percentile": None}
    monday, friday = date(2026, 10, 12), date(2026, 10, 9)
    assert service.sessions_missing(friday, monday) == 0 and service.freshness(friday, monday) == "ok"
    assert service.freshness(date(2026, 10, 7), monday) == "stale"                     # Thu and Fri missing
    assert service.freshness(date(2026, 10, 7), monday, holidays=[date(2026, 10, 8)]) == "ok"


# --- API -------------------------------------------------------------------------------------------------------------

def test_flag_off_is_404():
    assert service.FLAG in controls.FEATURE_FLAGS and service.FLAG in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    r = client.get("/api/context/panel", headers=t["headers"])
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "context_panel"


@pytest.fixture
def own_index_bars():
    """Review: other tests seed the same index keys (NSE_INDEX:NIFTY50); this test's values come from its own seed, so
    the keys' daily rows are cleared before it seeds (and after it, for the next test)."""
    from sqlalchemy import delete

    def clear():
        async def go():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_(["NSE_INDEX:NIFTY50", "NSE_INDEX:INDIAVIX"]),
                                                                   MdCandleRecord.timeframe == "day"))
                await session.commit()
        _run(go())
    clear()
    yield
    clear()


def test_panel_tiles_from_the_lake_events_and_seams(own_index_bars):
    end = _today() - timedelta(days=1)
    days = _sessions(300, end)                       # weekdays only: days[-1] is the newest bar (a Friday after a weekend)
    nifty = [20000 + 10 * i + 150 * math.sin(i / 3.0) for i in range(300)]
    _seed("NSE_INDEX:NIFTY50", nifty, days)
    vix = [12 + 3 * math.sin(i / 7.0) for i in range(300)]
    _seed("NSE_INDEX:INDIAVIX", vix, days)
    tag = uuid.uuid4().hex[:5].upper()
    _seed(f"NSE:UP{tag}", [100, 105], days[-2:])
    _seed(f"NSE:DN{tag}", [100, 95], days[-2:])
    _seed(f"NSE:FL{tag}", [100, 100], days[-2:])

    t = _tenant(_email("panel"))
    other = _tenant(_email("other"))
    _flag(t["tenant_id"])
    today = _today()

    async def events():
        async with _session_factory() as session:
            session.add(MarketEventRecord(tenant_id=None, event_date=today, kind="RBI_POLICY", action="SIZE_CUT", description=f"Policy {tag}"))
            session.add(MarketEventRecord(tenant_id=t["tenant_id"], event_date=today + timedelta(days=(6 - today.weekday())), kind="RESULTS",
                                          underlying="RELIANCE", action="BLOCK", description=f"Own {tag}"))
            session.add(MarketEventRecord(tenant_id=other["tenant_id"], event_date=today, kind="OTHER", action="SIZE_CUT", description=f"Theirs {tag}"))
            session.add(MarketEventRecord(tenant_id=None, event_date=today + timedelta(days=8), kind="OTHER", action="SIZE_CUT", description=f"Next week {tag}"))
            await session.commit()
    _run(events())

    body = client.get("/api/context/panel", headers=t["headers"]).json()
    tiles = body["tiles"]
    assert body["order"] == ["regime", "breadth", "vix", "fii_dii", "global", "events"]

    regime = tiles["regime"]
    assert regime["status"] in ("ok", "stale") and regime["as_of"] == days[-1].isoformat() and regime["daily"]["trend"] in ("UPTREND", "DOWNTREND", "RANGE")
    assert regime["weekly"]["trend"] in ("UPTREND", "DOWNTREND", "RANGE") and regime["close"] == round(nifty[-1], 2)

    breadth = tiles["breadth"]
    assert breadth["as_of"] == days[-1].isoformat() and breadth["advances"] >= 1 and breadth["declines"] >= 1 and breadth["unchanged"] >= 1

    v = tiles["vix"]
    window = vix[-service.VIX_RANK_SESSIONS:]
    expected = service.rank_and_percentile([round(x, 4) for x in window])
    assert v["close"] == round(vix[-1], 2) and v["sessions"] == service.VIX_RANK_SESSIONS
    assert v["rank"] == pytest.approx(expected["rank"], abs=0.2) and v["percentile"] == pytest.approx(expected["percentile"], abs=0.5)

    assert tiles["fii_dii"]["status"] == "na" and "No FII / DII source" in tiles["fii_dii"]["reason"]
    assert tiles["global"]["status"] == "na" and "switched off" in tiles["global"]["reason"]     # GLOBAL_CUES_ENABLED=false in tests

    ev = tiles["events"]
    today_desc = [e["description"] for e in ev["today"]]
    week_desc = [e["description"] for e in ev["this_week"]]
    assert f"Policy {tag}" in today_desc and f"Theirs {tag}" not in today_desc and f"Next week {tag}" not in today_desc + week_desc
    if today.weekday() < 6:
        assert f"Own {tag}" in week_desc
    for text in _strings(body):
        assert advice_in(text) == [], text


def test_a_failing_tile_is_na_on_its_own(monkeypatch):
    t = _tenant(_email("fail"))
    _flag(t["tenant_id"])

    async def boom(*args, **kwargs):
        raise RuntimeError("lake down")
    monkeypatch.setitem(service.BUILDERS, "breadth", boom)
    body = client.get("/api/context/panel", headers=t["headers"]).json()
    assert body["tiles"]["breadth"] == {"status": "na", "reason": "Could not be read (RuntimeError)", "source": "breadth", "as_of": None}
    assert body["tiles"]["events"]["status"] == "ok"


def test_a_failed_global_fetch_is_not_retried_on_every_panel_load(monkeypatch):
    from app.ai import global_cues
    calls = []

    async def failing(*args, **kwargs):
        calls.append(1)
        return [], ["SP500: yahoo: timeout"]
    monkeypatch.setattr(global_cues, "enabled", lambda: True)
    monkeypatch.setattr(global_cues, "fetch_all", failing)
    service.reset_global_backoff()
    viewer = service.Viewer(id=1, tenant_id=1)
    first = _run(service.global_tile(None, viewer, _today(), []))
    second = _run(service.global_tile(None, viewer, _today(), []))
    assert first["status"] == "na" and "(1 failed)" in first["reason"] and second["status"] == "na" and "a moment ago" in second["reason"]
    assert len(calls) == 1
    monkeypatch.setattr(service, "GLOBAL_FAIL_BACKOFF_SECONDS", 0)
    _run(service.global_tile(None, viewer, _today(), []))
    assert len(calls) == 2
    service.reset_global_backoff()


def test_the_first_global_fetch_after_a_fresh_boot_is_not_backed_off(monkeypatch):
    """N2 review LOW: time.monotonic() may be smaller than the backoff right after the host boots; no failure yet must
    never read as "failed a moment ago"."""
    import time as time_module

    from app.ai import global_cues
    calls = []

    async def failing(*args, **kwargs):
        calls.append(1)
        return [], ["x"]
    monkeypatch.setattr(global_cues, "enabled", lambda: True)
    monkeypatch.setattr(global_cues, "fetch_all", failing)
    monkeypatch.setattr(time_module, "monotonic", lambda: 5.0)            # five seconds after boot
    service.reset_global_backoff()
    _run(service.global_tile(None, service.Viewer(id=1, tenant_id=1), _today(), []))
    assert len(calls) == 1
    service.reset_global_backoff()
