"""Runner S4: S5's keystore and daily login wired into S2's loop (docs/design/USER_RUNNER.md, steps S2 / S5).

Each cycle, before anything else, the loop asks this for the feed: the daily login runs when due (`DailyLogin.run_due`,
idempotent while today's session is stored; a failure is a plain status line and never blocks exits, ADR-0004), and the
broker adapter is rebuilt from the keystore (`broker_login.build_adapter`, today's session) whenever the day or the
stored session changes. The adapter goes out behind the same protection as before (`feed.protected`: rate budget, the
Phase 0 chokepoint on new entries, the OPS throttle - rule R-9).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.market_data.calendar import IST
from app.runner.broker_login import DailyLogin, build_adapter
from app.runner.feed import BarFeed, BrokerFeed, protected
from app.runner.keystore import Keystore, KeystoreError

logger = logging.getLogger("app.runner")


class BrokerSession:
    def __init__(self, settings: Any, keystore: Keystore, broker: str = "angel_one", *, login: DailyLogin | None = None,
                 adapter_factory: Callable[..., Any] = build_adapter) -> None:
        self.settings = settings
        self.keystore = keystore
        self.broker = broker
        self.login = login or DailyLogin(settings.home, keystore, broker)
        self.adapter_factory = adapter_factory
        self.feed: BarFeed | None = None
        self._built_for: tuple[str, bool] | None = None

    def status_line(self, now: datetime) -> str:
        return self.login.status_line(now)

    def _has_session(self, day: str) -> bool:
        try:
            return self.keystore.session(self.broker, day) is not None
        except KeystoreError:
            return False

    async def refresh(self, now: datetime) -> BarFeed | None:
        try:
            await self.login.run_due(now)
        except Exception as exc:  # noqa: BLE001 - a login problem is a status line, never a stop
            logger.warning("Runner: daily login step failed (%s)", type(exc).__name__)
        day = now.astimezone(IST).date().isoformat()
        key = (day, self._has_session(day))
        if self.feed is None or key != self._built_for:
            try:
                adapter = protected(self.adapter_factory(self.keystore, self.broker, now), ops_throttle=self.settings.ops_throttle)
            except Exception as exc:  # noqa: BLE001 - keystore not set up / locked: keep the last feed (exits go on)
                logger.warning("Runner: broker adapter not built (%s)", type(exc).__name__)
                return self.feed
            self.feed = BrokerFeed(adapter, history_days_intraday=self.settings.history_days_intraday,
                                   history_days_daily=self.settings.history_days_daily)
            self._built_for = key
        return self.feed


__all__ = ["BrokerSession"]
