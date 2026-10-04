"""Phase AR: the Copilot's market memory - snapshots, cues, history, plan background, worker job."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.ai import market_memory as mm
from app.core.models import OHLCVBar
from app.db.models import MarketSnapshotRecord, StrategyDeploymentRecord, TraderProfileRecord
from app.retention.policy import load_policy
from app.retention.service import run_retention
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_ap_interview import _sessions

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)   # a Friday, 14:30 IST


def _bars(df):
    return [OHLCVBar(timestamp=ts.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume) for ts, r in df.iterrows()]


def _day_bars(closes):
    start = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    return [OHLCVBar(timestamp=start + timedelta(days=i), open=c, high=c + 1, low=c - 1, close=c, volume=0) for i, c in enumerate(closes)]


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


def _run(coro):
    return asyncio.run(coro)


async def _add(*rows):
    async with _session_factory() as session:
        session.add_all(rows)
        await session.commit()


def test_symbol_snapshot_reads_the_market_from_one_minute_bars():
    row = mm.symbol_snapshot(1, "NIFTY 50", "NSE", _bars(_sessions(days=3, minutes=1)), "upstox", NOW)
    assert row is not None and row.kind == "SYMBOL" and row.timeframe == "5min" and row.source == "upstox"
    assert row.bias == "BULLISH" and row.regime == "TRENDING_UP"
    assert json.loads(row.payload_json)["structure"]["trend"] == row.structure
    assert mm.symbol_snapshot(1, "X", "NSE", _bars(_sessions(days=1, minutes=1))[:100], "upstox", NOW) is None   # too little to read


def test_cue_snapshot_day_and_five_day_change():
    row = mm.cue_snapshot(1, "INDIA VIX", "NSE", _day_bars([10, 11, 12, 13, 14, 15, 18]), "upstox", NOW)
    payload = json.loads(row.payload_json)
    assert row.kind == "CUE" and row.last_price == 18 and row.change_pct == 20.0 and payload["change_5d_pct"] == round((18 / 11 - 1) * 100, 2)
    assert mm.cue_snapshot(1, "INDIA VIX", "NSE", _day_bars([10]), "upstox", NOW) is None


class _FakeMD:
    calls = []

    def __init__(self, broker, lookback_days=5):
        self.lookback_days = lookback_days

    async def get_candles(self, symbol, exchange, interval, now=None):
        _FakeMD.calls.append((symbol, exchange, interval, self.lookback_days))
        if symbol == "BROKEN":
            raise RuntimeError("instrument not found")
        if interval == "day":
            return _day_bars([22.0, 21.0] if symbol == "INDIA VIX" else [100.0, 101.0])
        return _bars(_sessions(days=3, minutes=1))


class _Broker:
    name = "upstox"


def test_capture_stores_symbols_and_cues_and_survives_a_bad_symbol():
    _, tenant_id, _ = _tenant("memory-capture@example.com")
    _FakeMD.calls = []

    async def go():
        async with _session_factory() as session:
            return await mm.capture(session, tenant_id, _FakeMD, _Broker(), now=NOW, symbols=["NIFTY 50", "BROKEN"])
    report = _run(go())
    assert report["symbols"] == 1 and report["cues"] == len(mm.CUES) and "BROKEN" in report["errors"][0]
    assert ("SENSEX", "BSE", "day", mm.DAILY_LOOKBACK_DAYS) in _FakeMD.calls
    assert ("NIFTY 50", "NSE", "1min", mm.INTRADAY_LOOKBACK_DAYS) in _FakeMD.calls

    async def read():
        async with _session_factory() as session:
            return await mm.latest(session, tenant_id, now=NOW + timedelta(minutes=5))
    memory = _run(read())
    assert [s["symbol"] for s in memory["symbols"]] == ["NIFTY 50"] and {c["symbol"] for c in memory["cues"]} == {s for s, _ in mm.CUES}
    assert memory["history"]["NIFTY 50"][0]["bias"] == "BULLISH"


def test_latest_keeps_one_row_per_day_and_describe_tells_the_story():
    _, tenant_id, _ = _tenant("memory-history@example.com")
    rows = []
    for days_ago, bias in ((3, "BULLISH"), (2, "BULLISH"), (1, "BULLISH")):
        for minutes in (0, 120):   # two reads a day; the later one is the day's
            rows.append(MarketSnapshotRecord(tenant_id=tenant_id, kind="SYMBOL", symbol="NIFTY BANK", exchange="NSE", timeframe="5min", source="upstox",
                                             bias=bias if minutes else "NEUTRAL", regime="TRENDING_UP", payload_json="{}",
                                             captured_at=NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)))
    rows.append(MarketSnapshotRecord(tenant_id=tenant_id, kind="CUE", symbol="INDIA VIX", exchange="NSE", timeframe="day", source="upstox",
                                     last_price=21.5, change_pct=6.0, payload_json="{}", captured_at=NOW - timedelta(minutes=10)))
    _run(_add(*rows))

    async def read():
        async with _session_factory() as session:
            return await mm.latest(session, tenant_id, now=NOW)
    memory = _run(read())
    assert [d["bias"] for d in memory["history"]["NIFTY BANK"]] == ["BULLISH", "BULLISH", "BULLISH"]
    lines = mm.describe("mr", memory, "NIFTY BANK", now=NOW)
    text = " ".join(lines)
    assert "India VIX 21.5" in text and "जास्त भीती" in text and "सलग तीन सत्रे" in text and "मिनिटांपूर्वी" in text
    assert "high fear" in " ".join(mm.describe("en", memory, "NIFTY BANK", now=NOW))
    assert mm.describe("en", {"symbols": [], "cues": [], "history": {}}, "NIFTY 50") == []


def test_watchlist_defaults_then_profiles_then_deployments():
    _, tenant_id, user_id = _tenant("memory-watch@example.com")
    _run(_add(TraderProfileRecord(tenant_id=tenant_id, user_id=user_id, answers_json=json.dumps({"symbol": "reliance"}), preferences_json="{}"),
              StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id="macd_ema_trend_5m", symbol="TCS", exchange="NSE", timeframe="1min",
                                       mode="PAPER", status="ACTIVE")))

    async def go():
        async with _session_factory() as session:
            return await mm.watchlist(session, tenant_id)
    assert _run(go()) == ["NIFTY 50", "NIFTY BANK", "RELIANCE", "TCS"]


def test_plan_carries_the_background_and_the_high_fear_warning():
    headers, tenant_id, _ = _tenant("memory-plan@example.com")
    _run(_add(MarketSnapshotRecord(tenant_id=tenant_id, kind="CUE", symbol="INDIA VIX", exchange="NSE", timeframe="day", source="upstox",
                                   last_price=24.0, change_pct=9.0, payload_json="{}", captured_at=datetime.now(UTC))))
    df = _sessions(days=5)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    r = client.post("/api/ai/interview/plan", headers=headers, json={"answers": {"language": "en", "experience": "new"}, "candles": candles})
    assert r.status_code == 200, r.text
    plan = r.json()
    background = next(s for s in plan["sections"] if s["id"] == "background")
    assert any("India VIX 24.0" in line for line in background["lines"])
    assert any("VIX is 20 or higher" in w for w in plan["warnings"])
    memory = client.get("/api/ai/market-memory", headers=headers).json()
    assert memory["watchlist"][:2] == ["NIFTY 50", "NIFTY BANK"] and memory["cues"][0]["symbol"] == "INDIA VIX"
    assert client.post("/api/ai/market-memory/refresh", headers=headers, json={}).status_code == 409   # no broker session yet


def test_retention_prunes_old_snapshots():
    _, tenant_id, _ = _tenant("memory-retention@example.com")
    old = datetime.now(UTC) - timedelta(days=load_policy().market_snapshots_days + 5)
    _run(_add(MarketSnapshotRecord(tenant_id=tenant_id, kind="CUE", symbol="OLD", exchange="NSE", timeframe="day", source="x", payload_json="{}", captured_at=old),
              MarketSnapshotRecord(tenant_id=tenant_id, kind="CUE", symbol="NEW", exchange="NSE", timeframe="day", source="x", payload_json="{}",
                                   captured_at=datetime.now(UTC))))

    async def go():
        async with _session_factory() as session:
            report = await run_retention(session)
            from sqlalchemy import select
            left = set(await session.scalars(select(MarketSnapshotRecord.symbol).where(MarketSnapshotRecord.tenant_id == tenant_id)))
            return report, left
    report, left = _run(go())
    assert report.deleted["market_snapshots"] >= 1 and left == {"NEW"}


def test_worker_captures_for_copilot_tenants_every_fifteen_minutes(monkeypatch):
    from app.workers import trading_worker as tw
    _, tenant_id, user_id = _tenant("memory-worker@example.com")
    _run(_add(TraderProfileRecord(tenant_id=tenant_id, user_id=user_id, answers_json="{}", preferences_json="{}")))
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60, market_data_factory=_FakeMD)

    class _Account:
        status, broker_name, account_label = "ACTIVE", "upstox", "primary"

    async def accounts(session, tid):
        return [_Account()] if tid == tenant_id else []

    async def adapter(self, session, tid, broker_name, uid, now, account_label="primary"):
        return _Broker()
    monkeypatch.setattr(tw, "list_accounts", accounts)
    monkeypatch.setattr(tw.TradingWorker, "_usable_adapter", adapter)

    async def go(now):
        async with _session_factory() as session:
            return await worker._market_memory(session, now)
    first = _run(go(NOW))
    assert first >= 2 + len(mm.CUES)            # NIFTY 50 + NIFTY BANK + cues for this tenant
    assert _run(go(NOW + timedelta(minutes=5))) == 0          # inside the 15-minute interval
    assert _run(go(NOW + timedelta(minutes=16))) >= 2 + len(mm.CUES)
    assert tw._just_closed(datetime(2026, 9, 25, 10, 15, tzinfo=UTC)) and not tw._just_closed(datetime(2026, 9, 26, 10, 15, tzinfo=UTC))
