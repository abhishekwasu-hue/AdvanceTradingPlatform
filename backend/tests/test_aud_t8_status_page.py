"""AUD-T8 (owner audit 28 Oct): the reliability promise.

- GET /api/status/sessions: public (no login), read-only, 404 while `status_page` is off; platform-wide only (no user,
  organisation, price or host in the body);
- uptime per market session from the worker's own samples, across a session close and the crypto day boundary;
- the in-app notice only while a market is open and the engine is late or market data is late;
- the worker's sampler: at most once a minute, pruned past the retention;
- the published SLO targets match docs/SLO.md word for word.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from app.core import rate_limit
from app.db.models import PlatformStatusSampleRecord
from app.market_data.calendar import IST
from app.observability import slo
from app.observability import status_history as sh
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from tests.test_auth_api import _register, _session_factory, client
from tests.test_z10e_rollout_status import _set_flags

URL = "/api/status/sessions"


def _run(coro):
    return asyncio.run(coro)


def _samples(start: datetime, end: datetime, skip=None, stale_at=()):
    out, t = [], start
    while t <= end:
        if not (skip and skip[0] <= t <= skip[1]):
            out.append((t.astimezone(timezone.utc), 3 if t in stale_at else 0))
        t += timedelta(minutes=1)
    return out


def _wipe():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(PlatformStatusSampleRecord))
            await session.commit()
    _run(go())


@pytest.fixture
def page_on():
    sh.reset()
    rate_limit.reset("public_status")
    _set_flags(status_page=True)
    yield
    _set_flags(status_page=False)
    sh.reset()
    rate_limit.reset("public_status")
    _wipe()


# --- the endpoint ---------------------------------------------------------------------------------------------------------

def test_flag_registered_off_by_default_and_404_while_off():
    assert "status_page" in controls.FEATURE_FLAGS and "status_page" in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO["status_page"].depends_on == ("public_status_page",)
    sh.reset()
    rate_limit.reset("public_status")
    r = client.get(URL)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


FORBIDDEN_KEYS = {"email", "user_id", "tenant_id", "user", "tenant", "organisation", "symbol", "price", "ltp", "host",
                  "hostname", "error", "last_error", "version", "commit", "worker_name", "cycle_count"}


def _keys(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def test_public_without_login_and_no_user_data(page_on):
    _register("t8-status-visitor@example.com")                       # a user exists; nothing about him may show
    now = datetime.now(timezone.utc)

    async def seed():
        async with _session_factory() as session:
            session.add(PlatformStatusSampleRecord(sampled_at=now - timedelta(seconds=30), stale_skips=0))
            await session.commit()
    _run(seed())
    r = client.get(URL)                                              # no Authorization header
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=15"
    body = r.json()
    assert set(body) == {"checked_at", "measured", "sessions", "slos", "notice", "public_page"}
    assert [s["key"] for s in body["sessions"]] == ["NSE", "MCX", "CDS", "CRYPTO"]
    for s in body["sessions"]:
        assert s["state"] in ("operational", "degraded", "outage", "unknown", "closed")
        assert s["recent"] and all(set(x) == {"date", "opens_at", "closes_at", "in_progress", "uptime_pct"} for x in s["recent"])
    crypto = body["sessions"][-1]
    assert crypto["open"] is True and crypto["recent"][0]["in_progress"] is True
    assert [x["id"] for x in body["slos"]] == [s.id for s in slo.SLOS]
    assert not (_keys(body) & FORBIDDEN_KEYS), _keys(body) & FORBIDDEN_KEYS
    assert "@" not in r.text and "example.com" not in r.text and "t8-status-visitor" not in r.text
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(URL).status_code == 405


def test_shares_the_status_page_rate_limit(page_on):
    for _ in range(60):
        assert client.get(URL).status_code == 200
    assert client.get(URL).status_code == 429


# --- uptime math ----------------------------------------------------------------------------------------------------------

def test_uptime_across_the_nse_close_counts_only_session_minutes():
    """Samples from 15:00:30 to 16:00:30 IST with a ten-minute gap; the NSE session ends at 15:30, so the minutes after
    the close never count, and the minutes before the first sample are not measured (not down)."""
    start = datetime(2026, 10, 29, 15, 0, 30, tzinfo=IST)
    samples = _samples(start, datetime(2026, 10, 29, 16, 0, 30, tzinfo=IST))
    ok = sh.data_ok_flags(samples)
    opens, closes = datetime(2026, 10, 29, 9, 15, tzinfo=IST), datetime(2026, 10, 29, 15, 30, tzinfo=IST)
    assert sh.uptime_pct(samples, ok, opens, closes, samples[0][0]) == 100.0
    gap = _samples(start, datetime(2026, 10, 29, 16, 0, 30, tzinfo=IST),
                   skip=(datetime(2026, 10, 29, 15, 10, tzinfo=IST), datetime(2026, 10, 29, 15, 20, tzinfo=IST)))
    # 30 minutes measured (15:00:30-15:30); the heartbeat is older than 180 s at 15:13:30 ... 15:19:30 -> 7 down.
    assert sh.uptime_pct(gap, sh.data_ok_flags(gap), opens, closes, gap[0][0]) == round(100 * 23 / 30, 2)
    assert sh.uptime_pct(gap, sh.data_ok_flags(gap), opens, closes, None) is None
    assert sh.uptime_pct(gap, sh.data_ok_flags(gap), opens, opens, gap[0][0]) is None


def test_uptime_across_the_crypto_day_boundary():
    """Crypto: one session per IST day. A gap that straddles midnight is charged to the minutes it covers on each side."""
    a = datetime(2026, 10, 29, 23, 50, 30, tzinfo=IST)
    b = datetime(2026, 10, 30, 0, 10, 30, tzinfo=IST)
    samples = _samples(a, b, skip=(datetime(2026, 10, 29, 23, 58, tzinfo=IST), datetime(2026, 10, 30, 0, 3, tzinfo=IST)))
    ok = sh.data_ok_flags(samples)
    windows = sh.session_windows("CRYPTO", b, count=2)
    assert windows[0] == (datetime(2026, 10, 30, 0, 0, tzinfo=IST), datetime(2026, 10, 31, 0, 0, tzinfo=IST))
    assert windows[1] == (datetime(2026, 10, 29, 0, 0, tzinfo=IST), datetime(2026, 10, 30, 0, 0, tzinfo=IST))
    first = samples[0][0]
    # Day 1: 23:50:30-24:00 -> 10 minutes, the newest sample (23:57:30) is at most 150 s old at midnight -> all up.
    assert sh.uptime_pct(samples, ok, *windows[1], first) == 100.0
    # Day 2: 00:00-00:10:30 -> 11 minutes; at 00:01, 00:02 and 00:03 the heartbeat is older than 180 s -> 3 down.
    assert sh.uptime_pct(samples, ok, windows[0][0], b, first) == round(100 * 8 / 11, 2)


def test_session_windows_skip_weekends_and_holidays():
    sunday = datetime(2026, 11, 1, 12, 0, tzinfo=IST)
    holiday = datetime(2026, 10, 29, tzinfo=IST).date()
    windows = sh.session_windows("NSE", sunday, holidays={holiday}, count=3)
    assert [w[0].date().isoformat() for w in windows] == ["2026-10-30", "2026-10-28", "2026-10-27"]
    assert windows[0][1] == datetime(2026, 10, 30, 15, 30, tzinfo=IST)
    before_open = datetime(2026, 10, 30, 8, 0, tzinfo=IST)           # today's session has not begun: not listed
    assert sh.session_windows("MCX", before_open, count=1)[0][0].date().isoformat() == "2026-10-29"


def test_stale_data_counts_as_down_and_rolls_off_after_ten_minutes():
    start = datetime(2026, 10, 29, 10, 0, 30, tzinfo=IST)
    stale = {datetime(2026, 10, 29, 10, 5, 30, tzinfo=IST), datetime(2026, 10, 29, 10, 6, 30, tzinfo=IST)}  # 3 + 3 refusals
    samples = _samples(start, datetime(2026, 10, 29, 10, 30, 30, tzinfo=IST), stale_at=stale)
    ok = sh.data_ok_flags(samples)
    bad = [s[0].astimezone(IST).strftime("%H:%M") for s, fine in zip(samples, ok) if not fine]
    # 6 >= 5 from 10:06:30 until 10:05:30 leaves the ten-minute window (at 10:15:30).
    assert bad == [f"10:{m:02d}" for m in range(6, 15)]


def test_notice_only_while_a_market_is_open():
    late = sh.Measured(heartbeat_age=sh.stale_seconds() + 1, data_fresh=True)
    assert sh.notice_for(False, late) is None
    assert sh.notice_for(True, late)["state"] == "outage"
    assert sh.notice_for(True, sh.Measured(30, False))["state"] == "degraded"
    assert sh.notice_for(True, sh.Measured(30, True)) is None
    assert sh.notice_for(True, sh.Measured(None, None)) is None       # never seen: a fresh install, nothing to say
    for notice in (sh.notice_for(True, late), sh.notice_for(True, sh.Measured(30, False))):
        assert "error" not in notice["text"].lower() and "fail" not in notice["text"].lower()
    assert sh.session_state(False, late) == "closed" and sh.session_state(True, late) == "outage"


# --- the sampler ----------------------------------------------------------------------------------------------------------

def test_sampler_writes_once_a_minute_and_prunes_past_retention():
    sh.reset()
    _wipe()
    now = datetime.now(timezone.utc)

    async def go():
        async with _session_factory() as session:
            session.add(PlatformStatusSampleRecord(sampled_at=now - timedelta(days=sh.RETENTION_DAYS + 1), stale_skips=0))
            await session.commit()
            assert await sh.record_sample(session, now, 2) is True
            assert await sh.record_sample(session, now + timedelta(seconds=5), 0) is False   # throttled
            rows = list(await session.scalars(select(PlatformStatusSampleRecord)))
            return [(r.stale_skips) for r in rows], await session.scalar(select(func.count()).select_from(PlatformStatusSampleRecord))
    skips, count = _run(go())
    assert count == 1 and skips == [2]
    sh.reset()
    _wipe()


# --- SLOs -----------------------------------------------------------------------------------------------------------------

def test_published_slos_match_docs_slo_md():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "SLO.md").read_text(encoding="utf-8")
    rows = {}
    for line in doc.splitlines():
        m = re.match(r"^\|\s*(SLO-\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3))
    for s in slo.SLOS:
        assert rows.get(s.id) == (s.objective, s.target), s.id
    for item in slo.public_slos():
        assert "`" not in item["target"] and "atp_" not in item["target"]   # no settings or metric names on a public page


def test_a_failing_read_is_a_plain_503_and_not_cached(page_on, monkeypatch):
    async def broken(session, now=None):
        raise RuntimeError("db-2.internal refused")
    monkeypatch.setattr(sh, "build", broken)
    r = client.get(URL)
    assert r.status_code == 503 and r.json() == {"detail": "Unavailable"} and "db-2" not in r.text
    monkeypatch.undo()
    assert client.get(URL).status_code == 200
