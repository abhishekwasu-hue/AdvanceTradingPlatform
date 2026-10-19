"""In-process per-user broadcast hub for 1-minute bars.

Topics are user ids: a message published for user U reaches only U's subscribers (the data is licensed to U's broker
account). Subscribers are bounded queues; when a slow client's queue is full the OLDEST message is dropped and a
counter is kept (backpressure without blocking the collector). Works from threads (collector) to asyncio (WebSocket).
"""
from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass, field


@dataclass
class Subscription:
    user_id: int
    symbols: set[str] | None
    maxsize: int = 1000
    dropped: int = 0
    q: queue.Queue = field(default_factory=queue.Queue)
    loop: asyncio.AbstractEventLoop | None = None
    aq: asyncio.Queue | None = None

    def wants(self, msg: dict) -> bool:
        return not self.symbols or msg.get("symbol") is None or msg.get("symbol") in self.symbols


class BarHub:
    def __init__(self):
        self.subs: dict[int, list[Subscription]] = {}
        self.lock = threading.Lock()
        self.published = 0

    def subscribe(self, user_id: int, symbols=None, maxsize: int = 1000, loop=None) -> Subscription:
        s = Subscription(user_id, set(symbols) if symbols else None, maxsize)
        if loop is not None:
            s.loop, s.aq = loop, asyncio.Queue(maxsize)
        else:
            s.q = queue.Queue(maxsize)
        with self.lock:
            self.subs.setdefault(user_id, []).append(s)
        return s

    def unsubscribe(self, s: Subscription) -> None:
        with self.lock:
            lst = self.subs.get(s.user_id, [])
            if s in lst:
                lst.remove(s)

    def subscriber_count(self, user_id: int) -> int:
        return len(self.subs.get(user_id, []))

    @staticmethod
    def _put_async(s: Subscription, msg: dict) -> None:
        if s.aq.full():
            s.aq.get_nowait()
            s.dropped += 1
        s.aq.put_nowait(msg)

    def publish(self, user_id: int, msg: dict) -> int:
        """Deliver to user_id's subscribers only. Returns number of subscribers reached."""
        with self.lock:
            targets = [s for s in self.subs.get(user_id, []) if s.wants(msg)]
        for s in targets:
            if s.aq is not None:
                try:
                    s.loop.call_soon_threadsafe(self._put_async, s, msg)
                except RuntimeError:          # loop closed: client gone
                    self.unsubscribe(s)
            else:
                if s.q.full():
                    try:
                        s.q.get_nowait()
                        s.dropped += 1
                    except queue.Empty:
                        pass
                s.q.put_nowait(msg)
        self.published += 1
        return len(targets)
