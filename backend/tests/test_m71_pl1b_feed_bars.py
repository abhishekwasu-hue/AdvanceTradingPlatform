"""M71 PL-1b / PL-4: the universe feed - ticks -> closed 1-minute bars -> 5m / 15m / 60m, the session and DH3 rules,
the connection split, reconnect + gap detection, the universe and the feed health. Fake feeds only (no network)."""
from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import config
from app.db.models import MdBarFlagRecord, MdTenantCandleRecord, Tenant
from app.market_data import feed_health
from app.market_data import stream as st
from app.market_data import universe_feed as uf
from app.market_data.bar_builder import BarPipeline, FeedBar, MinuteBarBuilder, Resampler, gap_range, is_post_close
from app.market_data.calendar import IST
from app.market_data.stream import Tick, TickStream, UpstoxTickStream, decode_upstox_v3, encode_upstox_v3
from app.market_lake.ingest import LakeBar
from app.market_lake.recorder import LiveBarRecorder, write_live_bars
from app.market_lake.tenant_store import resample
from tests.test_auth_api import _session_factory

DAY = datetime(2026, 10, 28).date()          # a Wednesday
KEY = "NSE:RELIANCE"
IDX = "NSE_INDEX:NIFTY50"


def _run(coro):
    return asyncio.run(coro)


def ist(h: int, m: int, s: float = 0.0) -> datetime:
    return (datetime.combine(DAY, datetime.min.time(), tzinfo=IST) + timedelta(hours=h, minutes=m, seconds=s)).astimezone(UTC)


@pytest.fixture(autouse=True)
def _fresh_health():
    feed_health.reset()
    feed_health.clear_gap_fillers()
    yield
    feed_health.reset()
    feed_health.clear_gap_fillers()


# --- 1-minute bars -----------------------------------------------------------------------------------------------------
def test_minute_bar_boundaries_end_label_and_close_only_after_grace():
    b = MinuteBarBuilder(grace_seconds=3)
    assert b.feed(KEY, ist(10, 0, 5), 100.0, ist(10, 0, 5)) == []
    assert b.feed(KEY, ist(10, 0, 30), 103.0, ist(10, 0, 30)) == []
    assert b.feed(KEY, ist(10, 0, 59.9), 99.0, ist(10, 0, 59.9)) == []
    # the next minute's first tick, inside the grace window: the bar is still open
    assert b.feed(KEY, ist(10, 1, 0), 101.0, ist(10, 1, 0)) == []
    assert b.close_due(ist(10, 1, 2.9)) == []
    bars = b.close_due(ist(10, 1, 3))
    assert len(bars) == 1
    bar = bars[0]
    assert bar.ts == ist(10, 1) and bar.timeframe == "1m"                     # END label, UTC
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 103.0, 99.0, 99.0)
    assert bar.volume_known is False and bar.volume == 0                      # no volume figure: marked, not invented


def test_late_tick_within_grace_updates_the_open_bar_only():
    b = MinuteBarBuilder(grace_seconds=3)
    b.feed(KEY, ist(10, 0, 10), 100.0, ist(10, 0, 10))
    b.feed(KEY, ist(10, 1, 0.5), 101.0, ist(10, 1, 0.5))
    b.feed(KEY, ist(10, 0, 58), 105.0, ist(10, 1, 1))         # arrives late, inside the grace window
    first = b.close_due(ist(10, 1, 3))[0]
    assert first.high == 105.0 and first.close == 105.0
    # a tick for the bar already handed out is dropped and counted - the written bar never changes
    assert b.feed(KEY, ist(10, 0, 59), 200.0, ist(10, 1, 4)) == []
    assert b.stats.late == 1
    second = b.close_due(ist(10, 2, 3))[0]
    assert second.ts == ist(10, 2) and second.high == 101.0


def test_tick_beyond_grace_for_a_new_minute_is_late():
    b = MinuteBarBuilder(grace_seconds=3)
    b.feed(KEY, ist(10, 0, 10), 100.0, ist(10, 1, 10))        # its minute ended 10 s ago
    assert b.open_bars() == 0 and b.stats.late == 1


def test_no_look_ahead_property():
    rng = random.Random(7)
    b = MinuteBarBuilder(grace_seconds=2)
    t = ist(10, 0)
    for _ in range(3000):
        t += timedelta(seconds=rng.random() * 0.5)
        arrival = t + timedelta(seconds=rng.random() * 3)
        for bar in b.feed(KEY, t, 100 + rng.random(), arrival):
            assert bar.ts + timedelta(seconds=2) <= arrival
        if rng.random() < 0.05:
            for bar in b.close_due(arrival):
                assert bar.ts + timedelta(seconds=2) <= arrival


def test_session_rules_pre_open_dropped_post_close_flaggable():
    b = MinuteBarBuilder(grace_seconds=0)
    b.feed(KEY, ist(9, 8, 0), 100.0, ist(9, 8, 0))             # pre-open equilibrium tick
    assert b.stats.outside_session == 1 and b.open_bars() == 0
    b.feed(KEY, ist(15, 30, 10), 100.0, ist(15, 30, 10))       # after continuous trading
    bar = b.close_due(ist(15, 31))[0]
    assert is_post_close(KEY, bar)
    assert not is_post_close(KEY, LakeBar(KEY, "1m", ist(15, 30), 1, 1, 1, 1))  # the 15:29 minute is continuous


def test_volume_from_the_cumulative_day_volume():
    b = MinuteBarBuilder(grace_seconds=0)
    bars = b.feed(KEY, ist(10, 0, 1), 100.0, ist(10, 0, 1), 1000)
    bars += b.feed(KEY, ist(10, 0, 40), 100.5, ist(10, 0, 40), 1300)
    bars += b.feed(KEY, ist(10, 1, 5), 101.0, ist(10, 1, 5), 1450)      # closes 10:00 (grace 0)
    bars += b.feed(KEY, ist(10, 1, 50), 101.0, ist(10, 1, 50), 1500)
    bars += b.close_due(ist(10, 2))
    assert [x.volume for x in bars] == [300, 200] and all(x.volume_known for x in bars)
    b.feed(KEY, ist(10, 2, 5), 101.0, ist(10, 2, 5), 20)       # the cumulative figure went back: starts from zero
    assert b.close_due(ist(10, 3))[0].volume == 20


# --- resampling --------------------------------------------------------------------------------------------------------
def _minutes(key: str, start: datetime, n: int) -> list[LakeBar]:
    return [FeedBar(key, "1m", start + timedelta(minutes=i + 1), 100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i in range(n)]


def test_resample_matches_the_store_rule_and_waits_for_complete_buckets():
    bars = _minutes(KEY, ist(10, 0), 15)
    r = Resampler(5, grace_seconds=0)
    out = []
    for bar in bars[:-1]:
        out += r.add(bar)
    assert [x.ts for x in out] == [ist(10, 5), ist(10, 10)]               # 10:10-10:15 still incomplete
    out += r.add(bars[-1])
    assert out[-1:] == [] or out[-1].ts != ist(10, 15)
    out += r.close_due(ist(10, 15))
    expected = resample(bars, 5)
    assert [(x.ts, x.open, x.high, x.low, x.close, x.volume) for x in out] == \
           [(x.ts, x.open, x.high, x.low, x.close, x.volume) for x in expected]


def test_last_hour_bucket_capped_at_the_close_and_post_close_excluded():
    bars = _minutes(KEY, ist(15, 15), 16)                       # 15:15 .. 15:30 starts; the last is post-close
    r = Resampler(60, grace_seconds=0)
    out = []
    for bar in bars:
        out += r.add(bar)
    out += r.close_due(ist(15, 31))
    assert len(out) == 1 and out[0].ts == ist(15, 30) and out[0].close == bars[14].close


def test_gap_suppresses_minutes_and_taints_buckets():
    p = BarPipeline(grace_seconds=0, minutes=(5,))
    out = p.feed(KEY, ist(10, 0, 5), 100.0, ist(10, 0, 5))
    out += p.feed(KEY, ist(10, 1, 5), 100.0, ist(10, 1, 5))     # closes the 10:00 minute (grace 0)
    p.gap([KEY], ist(10, 1), ist(10, 3))                        # the forming 10:01 bar is discarded
    out += p.feed(KEY, ist(10, 2, 5), 100.0, ist(10, 2, 5))     # inside the gap: not built
    out += p.feed(KEY, ist(10, 3, 5), 100.0, ist(10, 3, 5))
    out += p.flush(ist(10, 6))
    ones = [b.ts for b in out if b.timeframe == "1m"]
    assert ones == [ist(10, 1), ist(10, 4)]
    assert [b for b in out if b.timeframe == "5m"] == []           # 10:00-10:05 held a gap minute
    assert gap_range(KEY, ist(10, 1), ist(10, 3), 1) == (ist(10, 1), ist(10, 3))
    assert gap_range(KEY, ist(10, 1), ist(10, 3), 15) == (ist(10, 0), ist(10, 15))


# --- the split, the universe -------------------------------------------------------------------------------------------
def _items(n: int) -> list[uf.UniverseItem]:
    return [uf.UniverseItem(f"NSE:S{i}", f"S{i}", "NSE", "watchlist") for i in range(n)]


def test_subscription_split_and_broker_caps(monkeypatch):
    chunks, overflow = uf.split(_items(4500), 2000, 2)
    assert [len(c) for c in chunks] == [2000, 2000] and len(overflow) == 500
    chunks, overflow = uf.split(_items(10), 2000, 2)
    assert [len(c) for c in chunks] == [10] and overflow == []
    assert uf.per_connection_cap("upstox", "full") == 2000 and uf.per_connection_cap("upstox", "ltpc") == 5000
    assert uf.per_connection_cap("zerodha", "any") == 3000
    monkeypatch.setattr(config, "MARKET_FEED_SYMBOLS_PER_CONNECTION", 300)
    assert uf.per_connection_cap("upstox", "full") == 300
    monkeypatch.setattr(config, "MARKET_FEED_MAX_CONNECTIONS", 9)
    assert uf.max_connections("upstox") == 2 and uf.max_connections("zerodha") == 3


def test_universe_items_and_dedupe():
    nifty = uf.item_for("NIFTY 50", "NSE_INDEX", "watchlist")
    assert nifty is not None and nifty.key == IDX and nifty.group == "index"
    assert uf.item_for("GOLD", "MCX", "watchlist") is None and uf.item_for("", "NSE", "watchlist") is None
    rel_w = uf.item_for("reliance", "NSE", "watchlist")
    rel_f = uf.item_for("RELIANCE", "NSE", "fno")
    assert rel_w is not None and rel_w.key == KEY
    out = uf.dedupe([rel_w, rel_f, nifty, *uf.index_items()])
    keys = [i.key for i in out]
    assert len(keys) == len(set(keys))
    assert next(i for i in out if i.key == KEY).group == "fno"        # the earlier group wins
    assert out[0].group == "index"


def test_shared_index_keys_held_back_by_default(monkeypatch):
    assert uf.held_back(IDX) is True and uf.held_back(KEY) is False
    monkeypatch.setattr(config, "MARKET_FEED_SHARE_WITH_OTHERS", True)
    assert uf.held_back(IDX) is False


# --- Upstox full mode ----------------------------------------------------------------------------------------------------
def test_upstox_full_mode_carries_the_day_volume():
    frame = encode_upstox_v3({"NSE_EQ|INE002A01018": {"ltp": 2500.5, "ltt": 1, "vtt": 123456}}, full=True)
    assert decode_upstox_v3(frame)["NSE_EQ|INE002A01018"]["vtt"] == 123456

    class _A:
        name = "upstox"
    s = UpstoxTickStream(_A(), mode="full")
    assert json.loads(s.subscribe_frames(["k"])[0])["data"]["mode"] == "full"
    s.decode(frame)
    assert s._volumes["NSE_EQ|INE002A01018"] == 123456
    assert json.loads(UpstoxTickStream(_A()).subscribe_frames(["k"])[0])["data"]["mode"] == "ltpc"   # others unchanged


# --- the recorder and the store ----------------------------------------------------------------------------------------
def test_recorder_keeps_index_bars_without_volume_but_not_stock_bars():
    rec = LiveBarRecorder(grace_seconds=0)
    for key in (KEY, IDX):
        rec.on_tick(key, Tick("upstox", "NSE", "X", 100.0, ist(10, 0, 5), ist(10, 0, 5)))
    kept = rec.take(ist(10, 1))
    assert {b.instrument_key for b in kept if b.timeframe == "1m"} == {IDX} and rec.no_volume == 1


def test_write_live_bars_private_store_holder_source_and_post_close_flag():
    async def go():
        async with _session_factory() as session:
            tenant = Tenant(name="m71-feed-owner")
            session.add(tenant)
            await session.commit()
            bars = [LakeBar(KEY, "1m", ist(10, 1), 1, 2, 0.5, 1.5, 7), LakeBar(KEY, "1m", ist(15, 31), 1, 1, 1, 1, 0),
                    LakeBar(KEY, "5m", ist(10, 5), 1, 2, 0.5, 1.5, 7)]
            with pytest.raises(ValueError):
                await write_live_bars(session, tenant.id, bars, "stream:upstox")
            res = await write_live_bars(session, tenant.id, bars, "broker:upstox:u42")
            again = await write_live_bars(session, tenant.id, bars, "broker:upstox:u42")
            rows = (await session.scalars(select(MdTenantCandleRecord).where(MdTenantCandleRecord.tenant_id == tenant.id))).all()
            flags = (await session.scalars(select(MdBarFlagRecord).where(MdBarFlagRecord.tenant_id == tenant.id))).all()
            return res, again, rows, flags
    res, again, rows, flags = _run(go())
    assert res.inserted == 3 and again.skipped == 3
    assert {r.source for r in rows} == {"broker:upstox:u42"} and {r.timeframe for r in rows} == {"1m", "5m"}
    assert [(f.kind, f.ts.replace(tzinfo=UTC)) for f in flags] == [("post_close", ist(15, 31))]


# --- reconnect, gap, health --------------------------------------------------------------------------------------------
class _Conn:
    def __init__(self, frames, *, fail=None, hold=None):
        self.frames, self.fail, self.hold, self.sent = frames, fail, hold, []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            await asyncio.sleep(0.01)
            yield f
        if self.hold is not None:
            await self.hold.wait()
        if self.fail is not None:
            raise self.fail

    async def close(self):
        pass


class _FakeStream(TickStream):
    name = "upstox"

    def __init__(self, conns, **kw):
        it = iter(conns)

        async def connector(url):
            return next(it)
        super().__init__(connector=connector, **kw)

    async def connect_url(self):
        return "wss://fake"

    async def wire_key(self, symbol, exchange):
        return f"{exchange}:{symbol}"

    def subscribe_frames(self, keys):
        return [json.dumps({"sub": list(keys)})]

    def decode(self, frame):
        return [(k, v, None) for k, v in json.loads(frame).items()]


class _Rec:
    id, broker_name = 7, "upstox"


class _User:
    id = 42


def test_reconnect_detects_the_gap_and_asks_only_the_gap(monkeypatch):
    monkeypatch.setattr(config, "MARKET_FEED_ENABLED", True)
    monkeypatch.setattr(config, "MARKET_FEED_CREDENTIAL", "owner")
    monkeypatch.setattr(st, "BACKOFF_SECONDS", (0.01,))
    hold = asyncio.Event()
    conns = [_Conn([json.dumps({"NSE:RELIANCE": 100.0})], fail=ConnectionError("dropped")),
             _Conn([json.dumps({"NSE:RELIANCE": 101.0})], hold=hold)]
    clock_times = iter([ist(10, 0, 30), ist(10, 0, 40), ist(10, 3, 20)] + [ist(10, 3, 30)] * 50)
    asked: list = []

    async def gap_requester(symbol, interval, start, end):
        asked.append((symbol, interval, start, end))
        return "pending"

    async def owner(session, now):
        return 1, _User(), _Rec()

    async def universe(session):
        return [uf.UniverseItem(KEY, "RELIANCE", "NSE", "fno")]

    async def writer_session():
        return None

    feed = uf.UniverseFeed(stream_factory=lambda adapter, mode, cache: _FakeStream(conns, cache=cache), adapter_factory=lambda r: object(),
                           universe_loader=universe, owner_loader=owner, gap_requester=gap_requester,
                           clock=lambda: next(clock_times))

    class _NoWrite:
        async def drain(self, session, tenant_id, source, now):
            from app.market_lake.ingest import WriteResult
            return WriteResult()
    feed.recorder.drain = _NoWrite().drain        # the store is tested above; here only the connection and the gap

    async def go():
        await feed.cycle(_session_factory, ist(10, 0, 30), market_open=True)
        for _ in range(300):
            if feed.streams.get("c1") is not None and feed.streams.get("c1").reconnects >= 1 and feed.streams.get("c1").state == "live" and asked:
                break
            await asyncio.sleep(0.01)
        await feed.cycle(_session_factory, ist(10, 3, 30), market_open=True)
        snap = await feed_health.snapshot()
        hold.set()
        await feed.stop()
        return snap
    snap = _run(go())
    one = [a for a in asked if a[1] == "1m"]
    assert one == [(KEY, "1m", ist(10, 0), ist(10, 4))]                 # the drop minute .. the reconnect minute
    assert {a[1] for a in asked} == {"1m", "5m", "15m", "60m"}
    assert snap["reconnects_today"] == 1 and snap["symbols_subscribed"] == 1 and snap["connections"][0]["id"] == "c1"


def test_feed_off_and_no_session_states(monkeypatch):
    monkeypatch.setattr(config, "MARKET_FEED_ENABLED", False)
    feed = uf.UniverseFeed()
    assert _run(feed.cycle(_session_factory, ist(10, 0), market_open=True)) == 0
    assert feed_health.health.state == "off"
    monkeypatch.setattr(config, "MARKET_FEED_ENABLED", True)

    async def none(session, now):
        return None
    feed = uf.UniverseFeed(owner_loader=none)
    _run(feed.cycle(_session_factory, ist(10, 0), market_open=True))
    assert feed_health.health.state == "no_session"


SNAPSHOT_KEYS = {"enabled", "credential", "state", "broker", "note", "day", "connections", "symbols_subscribed",
                 "symbols_wanted", "symbols_over_limit", "symbols_held_back", "per_connection_cap", "max_connections",
                 "universe_at", "last_tick_age_seconds", "reconnects_today", "gaps_detected_today", "gaps_filled_today",
                 "gaps_pending", "pending_gaps", "bars_written_today", "late_ticks_today", "stock_bars_without_volume",
                 "updated_at"}


def test_health_snapshot_gap_hook_and_day_roll():
    accepted: list = []

    async def filler(symbol, interval, start, end):
        accepted.append(symbol)
        return symbol == KEY
    assert _run(feed_health.request_gap_fill("NSE:TCS", "1m", ist(10, 0), ist(10, 2))) == "pending"   # no filler yet
    feed_health.register_gap_filler(filler)
    assert _run(feed_health.request_gap_fill(KEY, "1m", ist(10, 0), ist(10, 2))) == "requested"
    assert _run(feed_health.request_gap_fill(KEY, "1m", ist(10, 0), ist(10, 2))) == "requested"     # the same gap once
    assert feed_health.mark_gap_filled(KEY, "1m", ist(10, 0), ist(10, 2)) is True
    feed_health.health.connection("c1", "upstox").last_tick_at = datetime.now(UTC) - timedelta(seconds=5)
    snap = feed_health.health.as_dict()
    assert set(snap) == SNAPSHOT_KEYS
    assert set(snap["connections"][0]) == {"id", "broker", "state", "symbols", "last_tick_at", "last_tick_age_seconds", "reconnects_today"}
    assert snap["gaps_detected_today"] == 2 and snap["gaps_filled_today"] == 1 and snap["gaps_pending"] == 1
    assert snap["pending_gaps"][0]["symbol"] == "NSE:TCS" and 4 <= snap["last_tick_age_seconds"] <= 60
    _run(feed_health.publish())
    assert set(_run(feed_health.snapshot())) == SNAPSHOT_KEYS               # Redis (or this process's own without it)
    feed_health.health.roll(datetime.now(UTC) + timedelta(days=1))
    rolled = feed_health.health.as_dict()
    assert rolled["gaps_detected_today"] == 0 and rolled["gaps_pending"] == 1     # an open gap stays open
