"""U5 D3b: relative rotation (POST /api/screener/board/rrg) from the shared lake's daily closes, and the template
sheet's last runs (POST /api/screener/board/template-runs) from the organisation's own stored runs - behind
`screener_board_views` (off -> 404). Synthetic rows only; nothing here places an order."""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import MdCandleRecord, MdCorporateActionRecord, ScreenEvidenceRecord, ScreenRunRecord
from app.market_lake.ingest import trade_date
from app.screener import board as B
from app.screener import compile_screen, nodes
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z8_tx1_taxonomy import _set_flags

NOW = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _run(coro):
    return asyncio.run(coro)


def _put(rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def test_rrg_math_and_quadrants():
    weeks = [(2026, w) for w in range(1, 31)]
    bench = {w: 100.0 for w in weeks}
    import math
    rising = {w: 100.0 * math.exp(0.001 * i * i) for i, w in enumerate(weeks)}           # outperforms, faster and faster
    pts = B.rrg_points(rising, bench)
    assert pts and pts[0]["week"] == "2026-W19"                                         # 10 weeks for RS-Ratio, then 10 more
    assert pts[-1]["rs_ratio"] > 100 and pts[-1]["rs_momentum"] > 100
    flat = B.rrg_points({w: 100.0 for w in weeks}, bench)
    assert all(p["rs_ratio"] == 100 and p["rs_momentum"] == 100 for p in flat)
    steady = B.rrg_points({w: 100.0 * (1.01 ** i) for i, w in enumerate(weeks)}, bench)[-1]
    assert steady["rs_ratio"] > 100 and steady["rs_momentum"] == pytest.approx(100.0, abs=0.01)   # a steady lead has no momentum
    fading = {w: 100.0 * (1.02 ** min(i, 26)) * (0.995 ** max(0, i - 26)) for i, w in enumerate(weeks)}
    last = B.rrg_points(fading, bench)[-1]
    assert last["rs_ratio"] > 100 > last["rs_momentum"]                                  # still ahead, losing momentum: Weakening
    assert B.QUADRANTS[(True, False)] == "Weakening"
    marked = B.rrg_points(rising, bench, forming=weeks[-1])
    assert marked[-1]["forming"] is True and not any(p["forming"] for p in marked[:-1])


def test_forming_week_is_the_ist_week_until_fridays_close():
    from app.market_data.calendar import IST
    wed = datetime(2026, 10, 7, 12, 0, tzinfo=IST)
    assert B.forming_week(wed) == (2026, 41)
    assert B.forming_week(datetime(2026, 10, 9, 15, 29, tzinfo=IST)) == (2026, 41)        # Friday before the close
    assert B.forming_week(datetime(2026, 10, 9, 15, 30, tzinfo=IST)) is None              # Friday's close: the week is complete
    assert B.forming_week(datetime(2026, 10, 10, 10, 0, tzinfo=IST)) is None              # Saturday


def test_rrg_adjusts_the_symbol_for_a_split():
    """A symbol that tracks the benchmark exactly but split 1:2 mid-window: unadjusted, its RS would halve (a fake
    collapse into Lagging); adjusted by the split known now, RS-Ratio and RS-Momentum stay at 100."""
    tag = uuid.uuid4().hex[:6].upper()
    sym, bench_key = f"RSP{tag}", "NSE_INDEX:NIFTY50"
    split_day = NOW - timedelta(days=40)
    rows, have_bench = [], set()
    for d in range(B.RRG_LOOKBACK_DAYS - 5, 0, -1):
        ts = NOW - timedelta(days=d)
        if ts.weekday() > 4:
            continue
        level = 100.0 * (1.0 + 0.002 * ((B.RRG_LOOKBACK_DAYS - d) % 17))                 # wiggles, the symbol tracks it
        raw = level * 2 if trade_date(ts) < trade_date(split_day) else level
        rows.append(MdCandleRecord(instrument_key=f"NSE:{sym}", timeframe="day", ts=ts, source="nse_bhav:cm", version=1, open=raw, high=raw, low=raw,
                                   close=raw, volume=1, ingested_at=ts + timedelta(hours=1)))
        have_bench.add(ts)
        rows.append(MdCandleRecord(instrument_key=bench_key, timeframe="day", ts=ts, source="nse_bhav:index", version=1, open=level * 100,
                                   high=level * 100, low=level * 100, close=level * 100, volume=1, ingested_at=ts + timedelta(hours=1)))
    rows.append(MdCorporateActionRecord(symbol=sym, exchange="NSE", ex_date=trade_date(split_day), action="SPLIT", ratio_new=2, ratio_old=1,
                                        version=1, source="nse:ca", ingested_at=split_day - timedelta(days=3)))
    _put(rows)
    headers, _ = _owner(f"u5d3b-s-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True, screener_board_views=True)
    try:
        body = client.post("/api/screener/board/rrg", json={"symbols": [sym]}, headers=headers).json()
        tail = body["symbols"][0]["tail"]
        assert len(tail) == B.RRG_TAIL
        assert all(p["rs_ratio"] == pytest.approx(100.0, abs=0.01) and p["rs_momentum"] == pytest.approx(100.0, abs=0.01) for p in tail), tail
        assert "adjusted for splits" in body["note"]
    finally:
        _set_flags(screener_v2=False, screener_board_views=False)

        async def clean():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([bench_key, f"NSE:{sym}"])))
                await session.execute(delete(MdCorporateActionRecord).where(MdCorporateActionRecord.symbol == sym))
                await session.commit()
        _run(clean())


def test_rrg_endpoint_reads_the_shared_lake_only():
    tag = uuid.uuid4().hex[:6].upper()
    sym = f"RRG{tag}"
    bench_key = "NSE_INDEX:NIFTY50"
    rows = []
    for d in range(B.RRG_LOOKBACK_DAYS - 5, 0, -1):
        ts = NOW - timedelta(days=d)
        if ts.weekday() > 4:
            continue
        i = B.RRG_LOOKBACK_DAYS - d
        for key, close, src in ((bench_key, 20000.0, "nse_bhav:index"), (f"NSE:{sym}", 100.0 * (2.718281828 ** (0.00003 * i * i)), "nse_bhav:cm"),
                                (f"NSE:{sym}", 1.0, "stream:upstox")):
            rows.append(MdCandleRecord(instrument_key=key, timeframe="day", ts=ts, source=src, version=1 if src.startswith("nse") else 5,
                                       open=close, high=close, low=close, close=close, volume=1, ingested_at=ts + timedelta(hours=1)))
    _put(rows)
    headers, _ = _owner(f"u5d3b-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True, screener_board_views=True)
    try:
        body = client.post("/api/screener/board/rrg", json={"symbols": [sym, "NOPE"]}, headers=headers).json()
        got = {s["symbol"]: s for s in body["symbols"]}
        assert body["benchmark_key"] == bench_key and body["reason"] is None
        assert got[sym]["quadrant"] == "Leading" and len(got[sym]["tail"]) == B.RRG_TAIL
        assert got["NOPE"] == {"symbol": "NOPE", "tail": [], "quadrant": None}
        assert [p["forming"] for p in got[sym]["tail"][:-1]] == [False] * (B.RRG_TAIL - 1)
        assert got[sym]["tail"][-1]["forming"] == (body["forming_week"] == got[sym]["tail"][-1]["week"])
        assert "approximation" in body["note"]
    finally:
        _set_flags(screener_v2=False, screener_board_views=False)

        async def clean():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([bench_key, f"NSE:{sym}"])))
                await session.commit()
        _run(clean())


def _stored_hash(source):
    ast, _ = compile_screen(source, base_tf="1d")
    return hashlib.sha256(json.dumps(nodes.to_json(ast), sort_keys=True).encode()).hexdigest()


def test_template_runs_are_the_organisations_own_latest_with_a_readable_thumbnail():
    src = "RSI(14) > 60 AND close > EMA(close, 20)"
    assert B.ast_hash(src) == _stored_hash(src) and B.ast_hash("close >") is None       # the run route's own hash
    headers, me = _owner(f"u5d3b-t-{uuid.uuid4().hex[:8]}@example.com")
    _other, them = _owner(f"u5d3b-o-{uuid.uuid4().hex[:8]}@example.com")
    h = _stored_hash(src)

    def run(tenant, user, minutes, matched):
        return ScreenRunRecord(tenant_id=tenant, user_id=user, ast_sha256=h, ast_version=nodes.VERSION, base_tf="1d", universe_json="[]",
                               data_source="broker:test", scanned=10, matched=matched, result_json="[]",
                               created_at=NOW - timedelta(minutes=minutes))
    old, new, theirs = run(me["tenant_id"], me["id"], 60, 2), run(me["tenant_id"], me["id"], 5, 3), run(them["tenant_id"], them["id"], 1, 9)
    src2 = "close > EMA(close, 50)"
    other = run(me["tenant_id"], me["id"], 30, 4)
    other.ast_sha256 = _stored_hash(src2)
    _put([old, new, theirs, other])

    async def add_evidence():
        async with _session_factory() as session:
            row = ScreenEvidenceRecord(tenant_id=me["tenant_id"], user_id=me["id"], run_id=new.id, symbol="AAA", base_tf="1d", bar_time=NOW,
                                       schema="evidence/1", pack_json="{}", pack_sha256="x", per_user=False, created_at=NOW)
            session.add(row)
            await session.commit()
            return row.id
    ev = _run(add_evidence())
    _set_flags(screener_v2=True, screener_board_views=True)
    try:
        body = client.post("/api/screener/board/template-runs", json={"templates": [{"id": "starter.momentum", "source": src},
                                                                                    {"id": "x", "source": "close > 1"}]}, headers=headers).json()
        assert body["runs"]["starter.momentum"] == {"run_id": new.id, "at": body["runs"]["starter.momentum"]["at"], "base_tf": "1d", "scanned": 10,
                                                    "matched": 3, "evidence_id": ev}
        assert body["runs"]["x"] is None
        both = client.post("/api/screener/board/template-runs", json={"templates": [{"id": "a", "source": src}, {"id": "b", "source": src2},
                                                                                    {"id": "c", "source": "close >"}]}, headers=headers).json()["runs"]
        assert both["a"]["run_id"] == new.id and both["b"]["run_id"] == other.id and both["b"]["evidence_id"] is None and both["c"] is None
        _set_flags(screener_board_views=False)
        assert client.post("/api/screener/board/template-runs", json={"templates": [{"id": "a", "source": src}]}, headers=headers).status_code == 404
    finally:
        _set_flags(screener_v2=False, screener_board_views=False)
