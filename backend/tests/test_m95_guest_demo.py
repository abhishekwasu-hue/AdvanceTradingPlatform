"""M95 PR-5: the read-only guest snapshot (app/guest_demo) and `GET /api/guest-demo`.

Made-up lake data only (two index series with invented levels, an invented option chain, one invented stock and one
invented third index that must never be named). Checks: the 90-day refusal, no price figure anywhere in the snapshot,
only the two index names, no later bar read, the route public + flag-gated + per-IP limited, and nothing read from a
tenant table.
"""
import asyncio
import io
import json
import math
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, event

from app.core import rate_limit
from app.db.models import MdCandleRecord, PlatformControlRecord
from app.guest_demo import FLAG, KEY, MIN_AGE_DAYS, SHOWN_NAMES, build, snapshot
from app.market_lake import ingest
from app.market_lake.bhavcopy_backfill import bar_end
from app.platform import controls
from tests.test_auth_api import _engine, _session_factory, client

TODAY = date(2026, 11, 10)
AS_OF = date(2026, 7, 31)                      # a Friday, 102 days before TODAY
NIFTY, BANK = "NSE_INDEX:NIFTY50", "NSE_INDEX:NIFTYBANK"
OTHER_INDEX, STOCK = "NSE_INDEX:NIFTYIT", "NSE:FAKECOGD"
KEYS = (NIFTY, BANK, OTHER_INDEX, STOCK)
OPTION_PREFIX = "NFO:NIFTY:"


def run(coro):
    return asyncio.run(coro)


def _weekdays(n: int, last: date) -> list[date]:
    out: list[date] = []
    d = last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _series(key: str, base: float, days: list[date], source: str = "nse_bhav:index") -> list[ingest.LakeBar]:
    bars = []
    for i, d in enumerate(days):
        mid = base * (1 + 0.0004 * i + 0.04 * math.sin(i / 9.0))
        o, c = mid * (1 - 0.003 * math.cos(i)), mid * (1 + 0.003 * math.sin(i * 1.7))
        if i % 23 == 0:                                         # an impulse bar now and then, so areas form
            c = o * (1.025 if (i // 23) % 2 else 0.975)
        bars.append(ingest.LakeBar(key, "day", bar_end(d), round(o, 2), round(max(o, c) * 1.004, 2), round(min(o, c) * 0.996, 2),
                                   round(c, 2), 0))
    return bars


def _chain(day: date, atm: float) -> list[ingest.LakeBar]:
    bars = []
    expiry = (day + timedelta(days=6)).isoformat()
    later = (day + timedelta(days=34)).isoformat()
    for k in range(-30, 31):
        strike = atm + 50 * k
        for side, oi in (("CE", 900_000 + 40_000 * max(k, 0) ** 2), ("PE", 700_000 + 30_000 * max(-k, 0) ** 2)):
            bars.append(ingest.LakeBar(f"{OPTION_PREFIX}{side}:{expiry}:{strike:g}", "day", bar_end(day), 10, 12, 9, 11, 100, oi))
            bars.append(ingest.LakeBar(f"{OPTION_PREFIX}{side}:{later}:{strike:g}", "day", bar_end(day), 10, 12, 9, 11, 100, oi * 3))
    for strike in (atm - 3000, atm + 3000):           # far strikes on a coarser grid
        bars.append(ingest.LakeBar(f"{OPTION_PREFIX}CE:{expiry}:{strike:g}", "day", bar_end(day), 1, 1, 1, 1, 1, 5000))
    return bars


DAYS = _weekdays(420, AS_OF)
LATER = [d for d in _weekdays(60, AS_OF + timedelta(days=70)) if d > AS_OF]


def _seed(with_later: bool = True) -> None:
    _clear_lake()

    async def go():
        async with _session_factory() as session:
            days = DAYS + (LATER if with_later else [])
            await ingest.write_candles_bulk(session, _series(NIFTY, 24137.0, days), "nse_bhav:index")
            await ingest.write_candles_bulk(session, _series(BANK, 51873.0, days), "nse_bhav:index")
            await ingest.write_candles_bulk(session, _series(OTHER_INDEX, 37211.0, days), "nse_bhav:index")
            await ingest.write_candles_bulk(session, _series(STOCK, 2711.0, days, "nse_bhav:cm"), "nse_bhav:cm")
            close = _series(NIFTY, 24137.0, DAYS)[-1].close
            await ingest.write_candles_bulk(session, _chain(AS_OF, round(close / 50) * 50), "nse_bhav:fo")
            await session.commit()
    run(go())


def _global(on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def _unstore() -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key == KEY))
            await session.commit()
    run(go())


def _build(as_of: date = AS_OF) -> dict:
    async def go():
        async with _session_factory() as session:
            return await build.build(session, as_of, today=TODAY)
    return run(go())


@pytest.fixture(autouse=True)
def _fresh_limit():
    for key in [k for k in rate_limit._WINDOWS if k[0] == "guest_demo"]:
        del rate_limit._WINDOWS[key]
    yield
    _global(False)
    _unstore()


def _clear_lake() -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_(KEYS)))
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.startswith(OPTION_PREFIX)))
            await session.commit()
    run(go())


@pytest.fixture(scope="module", autouse=True)
def _leave_nothing_behind():
    """The suite shares one database: the made-up index rows must not reach a later module (the market strip's EOD
    fallback reads NIFTY 50 / BANK NIFTY from the same lake)."""
    yield
    _clear_lake()
    _global(False)
    _unstore()


@pytest.fixture(scope="module")
def payload() -> dict:
    _seed()
    return _build()


# --- the 90-day rule ---------------------------------------------------------------------------------------------------------

def test_a_day_younger_than_ninety_days_is_refused_and_nothing_is_stored():
    _seed()
    with pytest.raises(build.Refused, match="too recent"):
        _build(TODAY - timedelta(days=MIN_AGE_DAYS - 1))
    build.check_age(TODAY - timedelta(days=MIN_AGE_DAYS), TODAY)       # exactly ninety days is allowed
    out = io.StringIO()
    code = run(build.main(["--as-of", date.today().isoformat()], out=out, session_factory=_session_factory))
    assert code == 2 and out.getvalue().startswith("REFUSED:")

    async def row():
        async with _session_factory() as session:
            return await session.get(PlatformControlRecord, KEY)
    assert run(row()) is None


def test_the_day_used_is_the_last_stored_close_on_or_before_the_date(payload):
    assert payload["as_of"] == AS_OF.isoformat()
    sunday = AS_OF + timedelta(days=2)
    assert _build(sunday)["as_of"] == AS_OF.isoformat()


# --- no price figure, only the two names ---------------------------------------------------------------------------------------

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def test_no_number_in_the_snapshot_looks_like_an_index_level(payload):
    text = _ISO_DATE.sub("", json.dumps(payload))          # the dates are the only four-digit figures allowed
    numbers = [float(m) for m in re.findall(r"\d+(?:\.\d+)?", text)]
    assert numbers and max(numbers) < 1000, sorted(numbers)[-5:]
    lows_highs = [(b.low, b.high) for key, base in ((NIFTY, 24137.0), (BANK, 51873.0)) for b in _series(key, base, DAYS)]
    assert snapshot.price_figures(payload, snapshot.price_ranges([(min(lo for lo, _ in lows_highs), max(hi for _, hi in lows_highs))])) == []
    for part in payload["indices"]:
        for bar in part["chart"]["bars"]:
            assert len(bar) == 4 and all(abs(x) < 100 for x in bar)
        assert part["chart"]["bars"][-1][3] == 0.0                 # the close is the zero line
        for area in part["chart"]["areas"]:
            assert set(area) == {"role", "kind", "state", "timeframe", "touches", "low_pct", "high_pct", "words"}


def test_only_the_two_index_names_appear(payload):
    assert [p["name"] for p in payload["indices"]] == list(SHOWN_NAMES)
    for screen in payload["screens"]:
        assert {r["name"] for r in screen["results"]} == set(SHOWN_NAMES)
    text = json.dumps(payload)
    for other in ("NIFTYIT", "NIFTY IT", "FAKECOGD", "NIFTY50", "NIFTYBANK", "NFO:"):
        assert other not in text
    assert snapshot.advice_words(payload) == []


def test_the_chain_is_open_interest_in_lakhs_by_steps_from_the_money(payload):
    nifty, bank = payload["indices"]
    chain = nifty["chain"]
    assert chain["expiry"] == (AS_OF + timedelta(days=6)).isoformat()          # the nearest expiry only
    steps = [r["steps"] for r in chain["rows"]]
    assert steps == list(range(-10, 11))                                         # the far coarse strikes are left out
    atm = next(r for r in chain["rows"] if r["steps"] == 0)
    assert atm == {"steps": 0, "call_oi_lakh": 9.0, "put_oi_lakh": 7.0}
    assert chain["most_call_oi_steps"] == 10 and chain["most_put_oi_steps"] == -10
    assert 0 < chain["pcr"] < 5
    assert bank["chain"] is None                                                # no option rows stored for it


def test_today_report_chart_and_screens_are_filled(payload):
    for part in payload["indices"]:
        assert part["today"]["lines"] and part["today"]["structure_daily"].startswith("Structure on the daily chart")
        assert part["report"][: len(part["today"]["lines"])] == part["today"]["lines"]
        assert len(part["chart"]["bars"]) == snapshot.CHART_BARS
        assert part["chart"]["last_date"] == AS_OF.isoformat()
        areas = part["chart"]["areas"]
        assert 0 < len(areas) <= 6 and all(a["kind"] == "band" and a["role"] in ("demand", "supply") for a in areas)
        assert all(a["words"].startswith(("Buyer area", "Seller area")) for a in areas)
    assert [s["name"] for s in payload["screens"]] == ["Momentum", "Oversold", "Trend on the daily"]


def test_a_bar_after_the_day_is_never_read(payload):
    _seed(with_later=False)
    try:
        alone = _build()
    finally:
        _seed()
    assert {k: v for k, v in alone.items() if k != "built_at"} == {k: v for k, v in payload.items() if k != "built_at"}


def test_the_build_reads_no_tenant_table():
    _seed()
    seen: list[str] = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    event.listen(_engine.sync_engine, "before_cursor_execute", spy)
    try:
        _build()
        _global(True)
        out = io.StringIO()
        assert run(build.main(["--as-of", AS_OF.isoformat()], out=out, session_factory=_session_factory)) == 0
        assert client.get("/api/guest-demo").status_code == 200
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", spy)
    tables = {t for s in seen for t in re.findall(r"(?:FROM|JOIN|INTO|UPDATE)\s+\"?([a-z_]+)", s)}
    assert tables and tables <= {"md_candles", "platform_controls"}, tables


# --- the stored snapshot and the public route --------------------------------------------------------------------------------

def test_the_route_is_public_flag_gated_and_404_without_a_snapshot():
    _seed()
    assert client.get("/api/guest-demo").status_code == 404                     # flag off
    _global(True)
    assert client.get("/api/guest-demo").status_code == 404                     # nothing stored
    out = io.StringIO()
    assert run(build.main(["--as-of", AS_OF.isoformat(), "--dry-run"], out=out, session_factory=_session_factory)) == 0
    assert "nothing stored" in out.getvalue()
    assert client.get("/api/guest-demo").status_code == 404
    assert run(build.main(["--as-of", AS_OF.isoformat()], out=io.StringIO(), session_factory=_session_factory)) == 0
    got = client.get("/api/guest-demo")                                          # no Authorization header
    assert got.status_code == 200
    body = got.json()
    assert set(body) == {"version", "as_of", "indices", "screens"} and body["as_of"] == AS_OF.isoformat()
    assert got.headers["cache-control"].startswith("public")
    _global(False)
    assert client.get("/api/guest-demo").status_code == 404


def test_the_route_is_rate_limited_per_ip():
    from app.guest_demo.routes import RATE_LIMIT
    codes = [client.get("/api/guest-demo").status_code for _ in range(RATE_LIMIT + 1)]
    assert codes[:RATE_LIMIT] == [404] * RATE_LIMIT and codes[-1] == 429


def test_a_stored_row_of_another_version_is_not_served():
    async def go():
        async with _session_factory() as session:
            await controls._set(session, KEY, {"version": 0, "indices": [{"name": "x"}]}, None)
            await session.commit()
    run(go())
    _global(True)
    assert client.get("/api/guest-demo").status_code == 404


# --- the pure parts ---------------------------------------------------------------------------------------------------------------

def test_strike_step_and_chain_view():
    assert snapshot.strike_step([100, 150, 200, 250, 400]) == 50
    view = snapshot.chain_view({100.0: {"call": 200_000, "put": None}, 150.0: {"call": 100_000, "put": 300_000},
                                200.0: {"call": None, "put": 50_000}}, spot=160)
    assert [r["steps"] for r in view["rows"]] == [-1, 0, 1]
    assert view["rows"][0] == {"steps": -1, "call_oi_lakh": 2.0, "put_oi_lakh": None}
    assert view["pcr"] == round(350_000 / 300_000, 2)
    assert snapshot.chain_view({}, spot=1) is None
    assert snapshot.steps_words(-2) == "2 steps below" and snapshot.steps_words(0) == "at the money"


def test_an_area_carries_no_price():
    band = {"role": "demand", "kind": "band", "state": "fresh", "timeframe": "1d", "touches": 2, "low": 23811.5, "high": 23900.0,
            "low_text": "23811.50", "high_text": "23900.00"}
    area = snapshot.area_of(band, close=24100.0)
    assert "23811" not in json.dumps(area) and "23900" not in json.dumps(area)
    assert area["words"] == "Buyer area from the daily chart: untouched since it formed (2 touches)."
    assert snapshot.price_figures({"a": "level 24,050.5"}, [(20000, 30000)]) == [24050.5]


def test_the_server_command_is_one_atp_sh_line():
    from pathlib import Path
    sh = (Path(__file__).resolve().parents[2] / "deploy" / "atp.sh").read_text(encoding="utf-8")
    assert re.search(r"guest-demo\)\s+cmd_guest_demo \"\$@\"", sh)
    assert "#   /opt/atp/deploy/atp.sh guest-demo --as-of" in sh
    assert "dc exec -T backend python -m app.guest_demo.build \"$@\"" in sh
