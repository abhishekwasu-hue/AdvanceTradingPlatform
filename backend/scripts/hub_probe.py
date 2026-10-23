#!/usr/bin/env python
"""TM Part 1: the data hub's measurement harness (docs/design/DATA_HUB.md section 7). No network, no database.

    python backend/scripts/hub_probe.py                  # 30 virtual minutes, the table + JSON
    python backend/scripts/hub_probe.py --minutes 10 --json

One trader's working screen on ONE broker connection (Upstox), the scenario of the review (TERMINAL_MASTER_REVIEW
section 5): an active chart, the option chain page, a 50-symbol watchlist, a paper strategy with two open legs, and a
Copilot question every five minutes. Measured twice on a fake broker in virtual time:

* **before** = main with the hub off, as production runs today (`STREAMING_QUOTES_ENABLED` off): every page asks the
  broker through the per-credential API budget and its short caches; the worker's paper engine asks REST each cycle;
* **after**  = the hub on: one stream for the connection carries every symbol inside the session's cap (positions
  first), the pages and the engine read its ticks, the leftovers of the watchlist stay on one batch call, and an option
  chain is shared within the connection for a few seconds.

The paths are modelled with the code's own constants (poll intervals of the pages, `QUOTE_CACHE_SECONDS`,
`BATCH_CACHE_SECONDS`, `api_limits`, `session_cap`, `plan_symbols`, `CHAIN_SNAPSHOT_SECONDS`), so a change there shows
up here. Reported per run: broker calls per minute (all / API process / worker), rate-limit errors (the API budget's
refusals - what the trader sees as "budget used" / a 429 - and the broker's own 429s at its published limits), and one
price everywhere (at each sample instant the last price `/ltp`, `/quotes`, the paper engine and the Copilot would read).

Then two real asyncio runs of the stream code itself (`TickStream`, `DataHub`) against fake sockets: a dropped socket
(states, time back to live, every symbol subscribed again) and a token the broker refuses (attempts, prompts, resume
after a renewed login).

Places nothing; reads nothing from a broker. Prices are synthetic numbers, never shown to a user.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers.rate_budget import limits_for  # noqa: E402
from app.market_data import api_budget  # noqa: E402
from app.market_data import hub as data_hub  # noqa: E402
from app.market_data.fanin import Priority, plan_symbols, session_cap  # noqa: E402
from app.market_data.quotes_routes import BATCH_CACHE_SECONDS  # noqa: E402

# The pages' poll intervals (frontend): charting/ltpHub.ts ACTIVE_LTP_MS, chart/watchlistsV2.ts QUOTE_POLL_MS,
# optionChain/model.ts POLL_OPEN_MS; the worker's cycle (60 s).
CHART_POLL = 5.0
WATCHLIST_POLL = 10.0
CHAIN_POLL = 15.0
WORKER_CYCLE = 60.0
COPILOT_EVERY = 300.0
STEP = 0.05
STREAM_LATENCY = 0.2          # exchange -> broker -> stream -> hub
TICK_EVERY = 0.25             # each synthetic price moves this often
SAMPLE_EVERY = 7.0            # one-price samples
BROKER = "upstox"
# Upstox's published standard-API limits per user (per second, per minute, per 30 minutes) - the fake broker 429s above.
BROKER_LIMITS = ((1, 50), (60, 500), (1800, 2000))

WATCHLIST = ["NIFTY"] + [f"W{i:02d}" for i in range(49)]
POSITIONS = ["LEG1", "LEG2"]
DEPLOYMENT = "NIFTY"
FUTURE = "NIFTYFUT"


class Market:
    """Synthetic prices: a seeded random walk per symbol, one move every TICK_EVERY seconds."""

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.price: Dict[str, float] = {}
        self.history: Dict[str, Deque[Tuple[float, float]]] = {}
        self.t = 0.0

    def symbols(self) -> List[str]:
        return sorted(set(WATCHLIST + POSITIONS + [FUTURE, DEPLOYMENT]))

    def start(self) -> None:
        for s in self.symbols():
            base = 24000.0 if s.startswith("NIFTY") else 100.0 + 10 * (hash(s) % 50)
            self.price[s] = base
            self.history[s] = deque([(0.0, base)], maxlen=4000)

    def advance(self, t: float) -> None:
        while self.t + TICK_EVERY <= t + 1e-9:
            self.t += TICK_EVERY
            for s in self.price:
                p = self.price[s] * (1 + self.rng.gauss(0, 0.00004))
                self.price[s] = round(p, 2)
                self.history[s].append((self.t, self.price[s]))

    def at(self, symbol: str, t: float) -> float:
        """The price as of time t (the newest move at or before it)."""
        value = self.history[symbol][0][1]
        for ts, p in reversed(self.history[symbol]):
            if ts <= t + 1e-9:
                return p
        return value


@dataclass
class Windows:
    """Sliding windows plus an optional token bucket, in virtual time (the api_budget / broker semantics)."""
    windows: Tuple[Tuple[float, int], ...]
    rate: Optional[float] = None
    capacity: Optional[float] = None
    calls: Dict[float, Deque[float]] = field(default_factory=dict)
    tokens: Optional[float] = None
    at: float = 0.0

    def take(self, t: float) -> bool:
        for seconds, limit in self.windows:
            q = self.calls.setdefault(seconds, deque())
            while q and q[0] <= t - seconds:
                q.popleft()
            if len(q) + 1 > limit:
                return False
        if self.rate is not None and self.capacity is not None:
            tokens = self.capacity if self.tokens is None else min(self.capacity, self.tokens + (t - self.at) * self.rate)
            self.at = t
            if tokens < 1:
                self.tokens = tokens
                return False
            self.tokens = tokens - 1
        for seconds, _ in self.windows:
            self.calls[seconds].append(t)
        return True


@dataclass
class Counters:
    api: int = 0
    worker: int = 0
    refused_api: int = 0
    refused_worker: int = 0
    broker_429: int = 0
    by_consumer: Dict[str, int] = field(default_factory=dict)


class Sim:
    def __init__(self, mode: str, seconds: float, seed: int = 7) -> None:
        assert mode in ("before", "after")
        self.mode = mode
        self.seconds = seconds
        self.market = Market(seed)
        self.market.start()
        self.c = Counters()
        limits = api_budget.api_limits(BROKER)
        api_windows: List[Tuple[float, int]] = [(60, limits.per_minute)]
        if limits.per_30min:
            api_windows.append((1800, limits.per_30min))
        self.api_budget = Windows(tuple(api_windows), rate=float(limits.per_second), capacity=float(limits.capacity))
        worker = limits_for(BROKER)
        self.worker_budget = Windows(((60, int(worker.per_minute)),), rate=float(worker.per_second), capacity=float(worker.burst))
        self.broker = Windows(BROKER_LIMITS)
        self.quote_cache: Dict[str, Tuple[float, float, float]] = {}      # symbol -> (value, stored at, ttl)
        self.chain_copy: Optional[Tuple[float, float]] = None              # (spot value, fetched at) - hub only
        self.streamed: set = set()
        self.ticks: Dict[str, Tuple[float, float]] = {}                    # symbol -> (value, delivered at)
        self.samples: List[float] = []
        self.agree = 0
        if mode == "after":
            self._plan()

    # -- the hub's symbol set (the real planner and cap) --
    def _plan(self) -> None:
        entries: Dict[str, List[float]] = {}
        t0 = 0.0
        for s in [DEPLOYMENT] + POSITIONS:
            entries[f"NSE:{s}"] = [int(Priority.POSITION), t0, t0]
        for i, s in enumerate(["NIFTY", FUTURE]):
            entries.setdefault(f"NSE:{s}", [int(Priority.CHART), t0, t0 + i * 1e-3])
        for i, s in enumerate(WATCHLIST):
            entries.setdefault(f"NSE:{s}", [int(Priority.WATCHLIST), t0, t0 + 0.01 + i * 1e-3])
        plan = plan_symbols(entries, session_cap(BROKER))
        self.streamed = {sym for sym, _ in plan.kept}
        self.dropped = {sym for sym, _ in plan.dropped}
        self._call("stream", "worker", 0.0)     # the stream's authorise call, once per session

    def _call(self, consumer: str, side: str, t: float) -> bool:
        budget = self.api_budget if side == "api" else self.worker_budget
        if not budget.take(t):
            if side == "api":
                self.c.refused_api += 1
            else:
                self.c.refused_worker += 1
            return False
        if not self.broker.take(t):
            self.c.broker_429 += 1
            return False
        if side == "api":
            self.c.api += 1
        else:
            self.c.worker += 1
        self.c.by_consumer[consumer] = self.c.by_consumer.get(consumer, 0) + 1
        return True

    # -- the read paths --
    def _tick(self, symbol: str, t: float) -> Optional[float]:
        if self.mode != "after" or symbol not in self.streamed:
            return None
        return self.market.at(symbol, t - STREAM_LATENCY)

    def _cached(self, symbol: str, t: float) -> Optional[float]:
        hit = self.quote_cache.get(symbol)
        if hit is not None and t - hit[1] <= hit[2]:
            return hit[0]
        return None

    def ltp(self, symbol: str, t: float, consumer: str, *, count: bool = True) -> Optional[float]:
        """GET /api/market-data/ltp: a fresh tick, else the credential's quote cache, else one quote call."""
        tick = self._tick(symbol, t)
        if tick is not None:
            return tick
        cached = self._cached(symbol, t)
        if cached is not None:
            return cached
        if not count:
            return self.market.at(symbol, t)
        if self._call(consumer, "api", t):
            value = self.market.at(symbol, t)
            self.quote_cache[symbol] = (value, t, float(api_budget.QUOTE_CACHE_SECONDS))
            return value
        return None

    def quotes(self, symbols: List[str], t: float, *, count: bool = True) -> Dict[str, Optional[float]]:
        """POST /api/market-data/quotes: ticks, then the cache, then ONE multi-quote call for the rest."""
        out: Dict[str, Optional[float]] = {}
        rest = []
        for s in symbols:
            tick = self._tick(s, t)
            cached = self._cached(s, t) if tick is None else None
            out[s] = tick if tick is not None else cached
            if out[s] is None:
                rest.append(s)
        if rest:
            if not count:
                for s in rest:
                    out[s] = self.market.at(s, t)
            elif self._call("watchlist", "api", t):
                for s in rest:
                    out[s] = self.market.at(s, t)
                    self.quote_cache[s] = (out[s], t, float(BATCH_CACHE_SECONDS))
        return out

    def chain(self, t: float, consumer: str) -> None:
        """The option chain: one chain call (shared for CHAIN_SNAPSHOT_SECONDS within the connection with the hub)."""
        if self.mode == "after" and self.chain_copy is not None and t - self.chain_copy[1] < data_hub.CHAIN_SNAPSHOT_SECONDS:
            return
        if self._call(consumer, "api", t):
            self.chain_copy = (self.market.at("NIFTY", t), t)

    def engine_ltp(self, symbol: str, t: float, *, count: bool = True) -> Optional[float]:
        """The worker's get_ltp: a fresh hub tick, else one REST quote through the worker's budget."""
        tick = self._tick(symbol, t)
        if tick is not None:
            return tick
        if not count:
            return self.market.at(symbol, t)
        return self.market.at(symbol, t) if self._call("paper engine", "worker", t) else None

    def copilot_quote(self, t: float, *, count: bool = True) -> Optional[float]:
        """The Copilot's quote tool: one full quote (OHLC); with the hub its last price is the connection's tick."""
        tick = self._tick("NIFTY", t)
        if count:
            self._call("copilot", "api", t)
        return tick if tick is not None else self.market.at("NIFTY", t)

    # -- the run --
    def run(self) -> dict:
        steps = int(self.seconds / STEP)
        next_at = {"chart": 0.0, "watch": 0.0, "chain": 0.0, "worker": 0.0, "copilot": 30.0, "sample": 3.5}
        for i in range(steps + 1):
            t = round(i * STEP, 6)
            self.market.advance(t)
            if t >= next_at["chart"]:
                self.ltp("NIFTY", t, "chart")
                next_at["chart"] += CHART_POLL
            if t >= next_at["watch"]:
                self.quotes(WATCHLIST, t)
                next_at["watch"] += WATCHLIST_POLL
            if t >= next_at["chain"]:
                self.chain(t, "option chain")
                self.ltp(FUTURE, t, "option chain")          # the page's future price (the spot comes with the chain)
                next_at["chain"] += CHAIN_POLL
            if t >= next_at["worker"]:
                for s in [DEPLOYMENT] + POSITIONS:
                    self.engine_ltp(s, t)
                next_at["worker"] += WORKER_CYCLE
            if t >= next_at["copilot"]:
                self.copilot_quote(t)
                self.chain(t, "copilot")
                next_at["copilot"] += COPILOT_EVERY
            if t >= next_at["sample"]:
                self._sample(t)
                next_at["sample"] += SAMPLE_EVERY
        minutes = self.seconds / 60.0
        total = self.c.api + self.c.worker
        return {
            "mode": self.mode, "minutes": round(minutes, 1),
            "broker_calls": total, "broker_calls_per_minute": round(total / minutes, 2),
            # What the pages and the engine asked for, refused calls included (a refusal is a price the trader did not get).
            "asked_per_minute": round((total + self.c.refused_api + self.c.refused_worker + self.c.broker_429) / minutes, 2),
            "api_calls_per_minute": round(self.c.api / minutes, 2), "worker_calls_per_minute": round(self.c.worker / minutes, 2),
            "by_consumer_per_minute": {k: round(v / minutes, 2) for k, v in sorted(self.c.by_consumer.items())},
            "rate_limit_errors": {"api_budget_refusals": self.c.refused_api, "worker_budget_refusals": self.c.refused_worker,
                                  "broker_429": self.c.broker_429},
            "streamed_symbols": len(self.streamed), "symbols_on_rest": (len(set(WATCHLIST + POSITIONS + [FUTURE])) - len(self.streamed)),
            "one_price": {"samples": len(self.samples), "all_equal_pct": round(100.0 * self.agree / max(1, len(self.samples)), 1),
                          "mean_spread_points": round(sum(self.samples) / max(1, len(self.samples)), 2),
                          "max_spread_points": round(max(self.samples) if self.samples else 0.0, 2)},
        }

    def _sample(self, t: float) -> None:
        """What `/ltp`, `/quotes`, the paper engine and the Copilot would answer for NIFTY at this instant."""
        values = [self.ltp("NIFTY", t, "sample", count=False), self.quotes(["NIFTY"], t, count=False)["NIFTY"],
                  self.engine_ltp("NIFTY", t, count=False), self.copilot_quote(t, count=False)]
        got = [v for v in values if v is not None]
        spread = (max(got) - min(got)) if got else 0.0
        self.samples.append(spread)
        if spread < 1e-9:
            self.agree += 1


# --- the stream itself: reconnect and token expiry (real code, fake sockets) --------------------------------------------

class _Conn:
    def __init__(self, frames: List[Any], *, fail: Optional[Exception] = None, hold: Optional[asyncio.Event] = None) -> None:
        self.frames = list(frames)
        self.fail = fail
        self.hold = hold
        self.sent: List[Any] = []

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            yield f
        if self.hold is not None:
            await self.hold.wait()
        if self.fail is not None:
            raise self.fail

    async def close(self) -> None:
        return None


def _stream_class():
    from app.market_data.stream import TickStream

    class JsonStream(TickStream):
        name = BROKER

        def __init__(self, connector, refuse: bool = False) -> None:
            super().__init__(connector=connector)
            self.refuse = refuse
            self.attempts = 0

        async def connect_url(self) -> str:
            self.attempts += 1
            if self.refuse:
                from app.brokers.exceptions import BrokerAPIError
                raise BrokerAPIError("401 Unauthorized", status_code=401)
            return "wss://fake"

        async def wire_key(self, symbol: str, exchange: str) -> Optional[str]:
            return f"{exchange}:{symbol}"

        def subscribe_frames(self, keys):
            return [json.dumps({"sub": list(keys)})]

        def decode(self, frame):
            return [(k, v, None) for k, v in json.loads(frame).items()]
    return JsonStream


async def reconnect_probe(symbols: int = 50) -> dict:
    """A socket that drops after its first frame: the states, the time back to live and what the new socket gets."""
    JsonStream = _stream_class()
    hold = asyncio.Event()
    pairs = [(f"S{i:02d}", "NSE") for i in range(symbols)]
    first = _Conn([json.dumps({"NSE:S00": 101.0})], fail=ConnectionError("dropped"))
    second = _Conn([json.dumps({"NSE:S00": 102.0})], hold=hold)
    conns = iter([first, second])
    states: List[Tuple[str, float]] = []
    started = time.monotonic()

    async def connector(url: str):
        return next(conns)
    stream = JsonStream(connector)
    hub = data_hub.DataHub()
    hub.attach(stream, "probe.reconnect", BROKER)
    publish = stream.on_state

    async def on_state(s) -> None:
        states.append((s.state, round(time.monotonic() - started, 2)))
        if publish is not None:
            await publish(s)
    stream.on_state = on_state
    await stream.set_symbols(pairs)
    task = asyncio.create_task(stream.run())
    for _ in range(600):
        if stream.reconnects >= 1 and stream.state == "live" and stream.ticks >= 2:
            break
        await asyncio.sleep(0.01)
    stream.stop()
    hold.set()
    await asyncio.wait_for(task, timeout=5)
    drop_at = next((ts for st, ts in states if st == "reconnecting"), None)
    back_at = next((ts for st, ts in states[1:] if st == "live"), None)
    resent = json.loads(second.sent[0])["sub"] if second.sent else []
    status = await data_hub.read_status("probe.reconnect")
    return {"states": [st for st, _ in states], "seconds_back_to_live": round((back_at or 0) - (drop_at or 0), 2),
            "symbols_subscribed_again": len(resent), "of": symbols, "ticks_after_reconnect": stream.ticks,
            "published_state": status.state if status else None, "backoff_schedule_seconds": list(__import__("app.market_data.stream", fromlist=["BACKOFF_SECONDS"]).BACKOFF_SECONDS)}


async def expiry_probe(symbols: int = 50, cycles: int = 5, watch_seconds: float = 4.0) -> dict:
    """The broker refuses the token: attempts and prompts over `cycles` worker cycles, with and without the hub, then a
    renewed login resumes every symbol."""
    JsonStream = _stream_class()
    pairs = [(f"S{i:02d}", "NSE") for i in range(symbols)]

    # Without the hub: the old loop retries on its backoff.
    async def no_conn(url: str):
        raise AssertionError("never reached")
    old = JsonStream(no_conn, refuse=True)
    old_task = asyncio.create_task(old.run())
    await asyncio.sleep(watch_seconds)
    old.stop()
    await asyncio.wait_for(old_task, timeout=40)

    class Adapter:
        name = BROKER

        def __init__(self, token: str) -> None:
            self.access_token = token

    made: List[Any] = []
    hold = asyncio.Event()

    def factory(adapter):
        refuse = adapter.access_token == "expired"

        async def connector(url: str):
            return _Conn([json.dumps({"NSE:S00": 100.0})], hold=hold)
        s = JsonStream(connector, refuse=refuse)
        made.append(s)
        return s
    hub = data_hub.DataHub(stream_factory=factory)
    now = datetime.now(timezone.utc)
    dead = Adapter("expired")
    await hub.ensure_org(990001, dead, pairs, now)
    for _ in range(300):
        if made and made[0].state == "renew_needed":
            break
        await asyncio.sleep(0.01)
    for _ in range(cycles - 1):
        await hub.ensure_org(990001, dead, pairs, now)
    prompts = len(hub.prompts)
    status_dead = await data_hub.read_status(data_hub.org_namespace(990001))
    renewed = Adapter("renewed")
    await hub.ensure_org(990001, renewed, pairs, now)
    for _ in range(300):
        if len(made) > 1 and made[-1].state == "live":
            break
        await asyncio.sleep(0.01)
    resumed = made[-1]
    status_live = await data_hub.read_status(data_hub.org_namespace(990001))
    hold.set()
    await hub.stop_all()
    return {"without_hub_attempts_in_seconds": {"seconds": watch_seconds, "attempts": old.attempts},
            "with_hub_attempts_over_cycles": {"cycles": cycles, "attempts": made[0].attempts, "renew_prompts": prompts,
                                              "state": status_dead.state if status_dead else None},
            "after_renew": {"new_stream": len(made) == 2, "state": status_live.state if status_live else None,
                            "symbols_subscribed": resumed.subscribed}}


async def load_probe(connections: int = 500, symbols: int = 50, tabs: int = 3, rounds: int = 3) -> dict:
    """B10-style load of the hub's fan-out in one process: `connections` broker connections (one per user), each with
    `symbols` streamed symbols ticking `rounds` times, and `tabs` open browser tabs per connection polling the `ticks`
    topic after each round (the real HubTickCache, snapshot and BrowserFeed code; the cache in memory)."""
    from app.market_data.stream import Tick
    caches = [data_hub.HubTickCache(f"load.c{i}") for i in range(connections)]
    pairs = [(f"S{j:02d}", "NSE") for j in range(symbols)]
    feeds = []
    for i in range(connections):
        for k in range(tabs):
            feed = data_hub.BrowserFeed(lambda: None, i, f"tab{k:02d}xxxxxx", clock=lambda: 0.0)
            feed.conn = data_hub.UserConnection(namespace=f"load.c{i}", broker=BROKER, account_label="primary", byob=True, renew=False)
            feed._resolved_at = 0.0
            feed.resolve_seconds = float("inf")
            await data_hub.set_browser_symbols(i, f"tab{k:02d}xxxxxx", pairs)
            feeds.append(feed)
    put_seconds = 0.0
    poll_latencies: List[float] = []
    events = 0
    for r in range(rounds):
        now = datetime.now(timezone.utc)
        started = time.perf_counter()
        for cache in caches:
            for symbol, exchange in pairs:
                await cache.put(Tick(broker=BROKER, exchange=exchange, symbol=symbol, ltp=100.0 + r, exchange_ts=now, received_at=now))
            await cache.flush()
        put_seconds += time.perf_counter() - started
        data_hub._snapshot_memo.clear()
        for feed in feeds:
            t0 = time.perf_counter()
            out = await feed.poll()
            poll_latencies.append(time.perf_counter() - t0)
            events += sum(len(d.get("items", [])) for name, d in out if name == "ticks")
    poll_latencies.sort()
    ticks = connections * symbols * rounds
    p95 = poll_latencies[int(0.95 * (len(poll_latencies) - 1))] if poll_latencies else 0.0
    return {"connections": connections, "symbols_per_connection": symbols, "tabs_per_connection": tabs, "rounds": rounds,
            "ticks_in": ticks, "ticks_in_per_second_of_cpu": round(ticks / put_seconds) if put_seconds else None,
            "tab_updates_out": events, "expected_tab_updates": connections * tabs * symbols * rounds,
            "poll_p95_ms": round(p95 * 1000, 3), "poll_total_seconds_per_round": round(sum(poll_latencies) / rounds, 3)}


async def _in_memory(coro):
    """Run with the cache in memory (the load run measures the hub's own code, not a Redis round-trip)."""
    from app.cache import client as cache_client

    class Memory:
        def __init__(self) -> None:
            self.data: Dict[str, str] = {}

        async def get(self, key):
            return self.data.get(key)

        async def set(self, key, value, ex=None):
            self.data[key] = value
    original = cache_client._get_client
    memory = Memory()
    cache_client._get_client = lambda: memory  # type: ignore[assignment]
    try:
        return await coro
    finally:
        cache_client._get_client = original  # type: ignore[assignment]


def run_all(minutes: float = 30.0, *, streams: bool = True) -> dict:
    data_hub.reset()
    seconds = minutes * 60.0
    out: Dict[str, Any] = {"scenario": {"chart": "1 active chart (5 s)", "watchlist": f"{len(WATCHLIST)} symbols (10 s)",
                                        "option_chain": "chain page (15 s): the chain + the future's price",
                                        "paper_engine": "1 strategy + 2 open legs (60 s)", "copilot": "1 question every 5 min (quote + chain)",
                                        "cap": session_cap(BROKER)},
                           "before": Sim("before", seconds).run(), "after": Sim("after", seconds).run()}
    if streams:
        out["reconnect"] = asyncio.run(reconnect_probe())
        out["token_expiry"] = asyncio.run(expiry_probe())
    return out


def table(result: dict) -> str:
    b, a = result["before"], result["after"]
    rows = [
        ("Broker calls asked per minute (refused included)", b["asked_per_minute"], a["asked_per_minute"]),
        ("Broker calls made per minute (all)", b["broker_calls_per_minute"], a["broker_calls_per_minute"]),
        ("  of which API process", b["api_calls_per_minute"], a["api_calls_per_minute"]),
        ("  of which worker", b["worker_calls_per_minute"], a["worker_calls_per_minute"]),
        ("Rate-limit errors: API budget refusals", b["rate_limit_errors"]["api_budget_refusals"], a["rate_limit_errors"]["api_budget_refusals"]),
        ("Rate-limit errors: broker 429", b["rate_limit_errors"]["broker_429"], a["rate_limit_errors"]["broker_429"]),
        ("Symbols on the stream", b["streamed_symbols"], a["streamed_symbols"]),
        ("One price: samples all equal (%)", b["one_price"]["all_equal_pct"], a["one_price"]["all_equal_pct"]),
        ("One price: mean / max spread (points)", f'{b["one_price"]["mean_spread_points"]} / {b["one_price"]["max_spread_points"]}',
         f'{a["one_price"]["mean_spread_points"]} / {a["one_price"]["max_spread_points"]}'),
    ]
    lines = [f"| Measure ({b['minutes']} min) | Before | After |", "|---|---|---|"]
    lines += [f"| {name} | {x} | {y} |" for name, x, y in rows]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="JSON only")
    parser.add_argument("--no-streams", action="store_true", help="skip the two real-time stream runs")
    parser.add_argument("--load", action="store_true", help="also the fan-out load run (500 connections x 50 symbols x 3 tabs)")
    args = parser.parse_args()
    result = run_all(args.minutes, streams=not args.no_streams)
    if args.load:
        result["load"] = asyncio.run(_in_memory(load_probe()))
    if not args.json:
        print(table(result))
        print()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
