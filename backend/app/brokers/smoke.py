"""Phase AJ: a read-only smoke test of one broker session, run from Settings.

Every adapter and the Phase AI symbol translator are verified against mocked transports; the
gap analysis has said "first live confirmation pending" since the first adapter landed. This is
the operator's way to get that confirmation without risking an order: profile, funds, instrument
list, an index quote, the derivatives list, one option contract resolved and quoted through the
same translation the worker uses, open positions and today's order book. Nothing here places,
modifies or cancels anything.

Each step is timed, isolated (one failure never stops the next) and reported with the exception
type and message when it fails, so the report reads as a checklist the operator can act on.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional

from app.brokers.base import BrokerInterface
from app.brokers.contract_symbols import contract_keys
from app.core.config import QUOTE_MAX_STALE_SECONDS
from app.instruments.master import INDEX_SYMBOLS, derivatives_exchange, underlying_of

STEP_TIMEOUT_SECONDS = 25.0
CRITICAL_STEPS = ("profile", "funds", "quote")
# Venues that are not NSE-shaped: what to quote, which instrument list to read, and that there are no derivatives.
VENUE_PROFILES = {"coindcx": {"quote_symbol": "BTCINR", "quote_exchange": "CRYPTO", "instrument_exchange": "CRYPTO", "derivatives": False}}


@dataclass
class Step:
    name: str
    status: str            # ok | fail | skip
    detail: str
    ms: int = 0

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail, "ms": self.ms}


@dataclass
class SmokeReport:
    broker: str
    account_label: str
    steps: List[Step] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        by_name = {s.name: s for s in self.steps}
        return all(by_name.get(n) is not None and by_name[n].status == "ok" for n in CRITICAL_STEPS)

    @property
    def summary(self) -> str:
        ok = sum(1 for s in self.steps if s.status == "ok")
        fail = [s.name for s in self.steps if s.status == "fail"]
        skipped = sum(1 for s in self.steps if s.status == "skip")
        text = f"{ok} ok, {len(fail)} failed, {skipped} skipped"
        return text + (f" (failed: {', '.join(fail)})" if fail else "")

    def as_dict(self) -> dict:
        return {"broker": self.broker, "account_label": self.account_label, "ok": self.ok, "summary": self.summary,
                "started_at": self.started_at.isoformat(), "steps": [s.as_dict() for s in self.steps], "read_only": True}


async def _step(report: SmokeReport, name: str, fn: Callable[[], Awaitable[str]], *, timeout: float) -> Optional[str]:
    """Run one probe; record ok/fail with timing. Returns the detail on success, None on failure."""
    started = time.monotonic()
    try:
        detail = await asyncio.wait_for(fn(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        elapsed = time.monotonic() - started
        if elapsed >= timeout * 0.9:                     # our own deadline, not a TimeoutError the adapter raised
            report.steps.append(Step(name, "fail", f"no answer within {timeout:.0f}s", int(elapsed * 1000)))
            return None
        message = str(exc).strip() or exc.__class__.__name__
        report.steps.append(Step(name, "fail", f"{exc.__class__.__name__}: {message[:300]}", int(elapsed * 1000)))
        return None
    except Exception as exc:  # noqa: BLE001 - every failure mode is a report line, never a 500
        message = str(exc).strip() or exc.__class__.__name__
        report.steps.append(Step(name, "fail", f"{exc.__class__.__name__}: {message[:300]}", int((time.monotonic() - started) * 1000)))
        return None
    report.steps.append(Step(name, "ok", detail, int((time.monotonic() - started) * 1000)))
    return detail


def _age_seconds(ts: Optional[datetime], now: datetime) -> Optional[float]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds())


async def run_smoke(adapter: BrokerInterface, *, account_label: str = "primary", underlying_symbol: str = "NIFTY 50",
                    today: Optional[date] = None, timeout: float = STEP_TIMEOUT_SECONDS) -> SmokeReport:
    """Read-only probes against a live broker session. Never places, modifies or cancels an order."""
    report = SmokeReport(broker=getattr(adapter, "name", "broker"), account_label=account_label)
    today = today or datetime.now(timezone.utc).date()
    venue = VENUE_PROFILES.get(report.broker, {})
    if venue and underlying_symbol == "NIFTY 50":
        underlying_symbol = venue["quote_symbol"]
    quote_exchange = venue.get("quote_exchange", "NSE")
    instrument_exchange = venue.get("instrument_exchange", "NSE")
    has_derivatives = venue.get("derivatives", True)
    underlying = underlying_of(underlying_symbol)
    state: dict = {}

    async def profile() -> str:
        p = await adapter.get_profile()
        return f"{p.broker} account {p.user_id}" + (f" ({p.name})" if p.name else "")

    async def funds() -> str:
        m = await adapter.get_balance()
        return f"available cash {m.available_cash:,.2f}, available margin {m.available_margin:,.2f}, used {m.used_margin:,.2f}"

    async def instruments() -> str:
        rows = await adapter.get_instruments(instrument_exchange)
        if not rows:
            raise RuntimeError(f"{instrument_exchange} instrument list came back empty")
        state["nse"] = len(rows)
        return f"{len(rows)} {instrument_exchange} instruments"

    async def quote() -> str:
        now = datetime.now(timezone.utc)
        q = await adapter.get_quote_for_symbol(underlying_symbol, quote_exchange)
        if q is not None and q.ltp > 0:
            state["spot"] = float(q.ltp)
            age = _age_seconds(q.timestamp, now)
            if age is None:
                return f"{underlying_symbol} {q.ltp:,.2f} (no exchange timestamp)"
            note = "" if age <= QUOTE_MAX_STALE_SECONDS else f"; older than the {QUOTE_MAX_STALE_SECONDS}s staleness gate - fine outside market hours"
            return f"{underlying_symbol} {q.ltp:,.2f}, exchange time {int(age)}s ago{note}"
        ltp = float(await adapter.get_ltp_for_symbol(underlying_symbol, quote_exchange))
        if ltp <= 0:
            raise RuntimeError(f"LTP for {underlying_symbol} is {ltp}")
        state["spot"] = ltp
        return f"{underlying_symbol} {ltp:,.2f} (LTP only)"

    async def derivatives() -> str:
        exchange = derivatives_exchange(underlying)
        rows = await adapter.get_instruments(exchange)
        candidates = []
        for row in rows:
            for key in contract_keys(row):
                if key.underlying == underlying and key.right == "CE" and key.expiry >= today and key.strike:
                    candidates.append(key)
        if not candidates:
            state["contract"] = None
            return f"{len(rows)} {exchange} instruments, no {underlying} call listed on or after {today.isoformat()}"
        expiry = min(k.expiry for k in candidates)
        spot = state.get("spot")
        at_expiry = [k for k in candidates if k.expiry == expiry]
        chosen = min(at_expiry, key=lambda k: abs((k.strike or 0) - spot)) if spot else sorted(at_expiry, key=lambda k: k.strike or 0)[len(at_expiry) // 2]
        state["contract"] = (chosen.canonical, exchange)
        return f"{len(rows)} {exchange} instruments; nearest {underlying} expiry {expiry.isoformat()}, probe {chosen.canonical}"

    async def contract_quote() -> str:
        canonical, exchange = state["contract"]
        ltp = float(await adapter.get_ltp_for_symbol(canonical, exchange))
        if ltp <= 0:
            raise RuntimeError(f"premium for {canonical} is {ltp}")
        via = " via the broker's own symbol" if getattr(adapter, "translations", 0) else ""
        return f"{canonical} premium {ltp:,.2f}{via}"

    async def positions() -> str:
        rows = await adapter.get_positions()
        open_rows = [p for p in rows if p.quantity]
        return f"{len(open_rows)} open position(s)" + (": " + ", ".join(f"{p.symbol} {p.quantity:g}" for p in open_rows[:5]) if open_rows else "")

    async def orders() -> str:
        rows = await adapter.get_order_book()
        return f"{len(rows)} order(s) in today's book"

    for name, fn in (("profile", profile), ("funds", funds), ("instruments", instruments), ("quote", quote)):
        await _step(report, name, fn, timeout=timeout)
    if has_derivatives:
        await _step(report, "derivatives", derivatives, timeout=timeout)
    else:
        report.steps.append(Step("derivatives", "skip", f"{report.broker} is a spot venue - no derivatives list"))
    if state.get("contract"):
        await _step(report, "contract_quote", contract_quote, timeout=timeout)
    else:
        report.steps.append(Step("contract_quote", "skip", "no contract to quote (derivatives list empty or failed)"))
    for name, fn in (("positions", positions), ("orders", orders)):
        await _step(report, name, fn, timeout=timeout)
    return report
