"""Runner S4b: options / futures and multi-leg deployments on the runner - the server's own derivative pipeline.

Nothing here decides a trade. The contract and the structure are resolved by the server's own code
(`instruments.contracts.resolve_contract`, `instruments.spreads.resolve_structure`: expiry, strike, side and legs from
the deployment's OWN settings) against the contracts ATP sent the runner (S4 `sync.apply_reference`, ATP's instrument
master), and executed by the server's own pipelines (`execute_signal_for_user` with the contract, `multileg.execute_structure`
for a structure: lots, hedge-first ordering, incomplete-structure handling, the F&O ban list for entries only).

What this module adds is the runner's side of the broker:
- `ContractBinder`: ATP's master names a contract in its own spelling; Angel One quotes and orders by its own
  symboltoken. The binder finds the same contract in Angel One's scrip master (its `get_instruments("NFO")`) by the
  contract's facts - underlying, expiry, strike, right - checks the lot size agrees, and registers ATP's name as an alias
  on the adapter (`alias_symbol`). Trades keep ATP's name (the mirror, the expiry settlement and the journal read it).
  A contract it cannot match is refused for an entry in plain words; for an open position it is logged and retried
  (exits are never blocked - ADR-0004). An adapter without `alias_symbol` (Upstox: ATP's master is its own; a test
  feed) uses the names as stored.
- `chain_provider`: the option chain a deployment's strike filters are judged against, from his own broker session
  (as the worker's `_chain_provider`).
- `daily_close`: the expiry-day close of an index for the PAPER expiry settlement - the server reads its licensed
  exchange files; the runner has none and reads his own broker's Daily bar of that day, only once that bar has closed
  (closed bars only, never a later bar: no look-ahead).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.instruments.master as master
from app.db.models import InstrumentRecord, TradeRecord
from app.instruments.contracts import ContractResolutionError, ResolvedContract
from app.market_data.calendar import IST
from app.market_data.freshness import closed_frames

logger = logging.getLogger("app.runner")

DERIVATIVE_KINDS = ("OPTION", "FUTURE")


@dataclass(frozen=True)
class ContractFacts:
    exchange: str            # NFO / BFO
    symbol: str              # ATP's spelling (its instrument master's trading symbol)
    underlying: str          # NIFTY, BANKNIFTY, a stock's name
    expiry: date
    strike: float | None
    right: str               # CE / PE / FUT
    lot_size: int | None


def facts_of(contract: ResolvedContract) -> ContractFacts:
    return ContractFacts(exchange=contract.exchange, symbol=contract.tradingsymbol, underlying=contract.underlying,
                         expiry=contract.expiry, strike=contract.strike, right=contract.right or "FUT",
                         lot_size=contract.lot_size)


async def facts_of_trade(session: AsyncSession, trade: TradeRecord) -> ContractFacts | None:
    """An open derivative position's contract: ATP's stored row by its name (or key), else what its name says."""
    row = await session.scalar(select(InstrumentRecord).where(
        (InstrumentRecord.tradingsymbol == (trade.symbol or "").upper())
        | (InstrumentRecord.instrument_key == (trade.instrument_key or "-"))).limit(1))
    exchange = (getattr(trade, "exchange", None) or "NFO").upper()
    if row is not None and row.instrument_type in ("CE", "PE", "FUT") and row.expiry is not None and row.underlying:
        return ContractFacts(exchange=row.exchange or exchange, symbol=(trade.symbol or "").upper(), underlying=row.underlying.upper(),
                             expiry=row.expiry, strike=float(row.strike) if row.strike is not None else None,
                             right=row.instrument_type, lot_size=int(row.lot_size) if row.lot_size else None)
    from app.paper.fno_contracts import parse_symbol
    parsed = parse_symbol(trade.symbol or "")
    expiry = getattr(trade, "expiry", None) or (parsed.expiry if parsed is not None else None)
    if parsed is None or expiry is None:
        return None
    return ContractFacts(exchange=exchange, symbol=(trade.symbol or "").upper(), underlying=parsed.underlying.upper(), expiry=expiry,
                         strike=parsed.strike, right=parsed.right, lot_size=trade.lot_size)


def _matches(instrument: Any, facts: ContractFacts) -> bool:
    if (getattr(instrument, "name", None) or "").strip().upper() != facts.underlying:
        return False
    if str(getattr(instrument, "expiry", None) or "") != facts.expiry.isoformat():
        return False
    kind = (getattr(instrument, "instrument_type", None) or "").upper()
    symbol = (getattr(instrument, "tradingsymbol", None) or "").upper()
    if facts.right == "FUT":
        return kind.startswith("FUT")
    if not kind.startswith("OPT") or not symbol.endswith(facts.right):
        return False
    strike = getattr(instrument, "strike", None)
    return strike is not None and facts.strike is not None and abs(float(strike) - float(facts.strike)) < 1e-6


class ContractBinder:
    """ATP's contract names -> his broker's own instrument (see the module docstring). One per runner; the aliases are
    registered again on a new adapter (the daily login builds a new one)."""

    def __init__(self) -> None:
        self._bound: dict[int, set[str]] = {}

    @staticmethod
    def _adapter(broker: Any) -> Any:
        return getattr(broker, "inner", broker)

    async def bind(self, broker: Any, contracts: Iterable[ContractFacts]) -> None:
        """Registers every contract on the adapter; raises ContractResolutionError (plain words) for the first one its
        broker does not list as the same contract."""
        adapter = self._adapter(broker)
        if broker is None or not callable(getattr(adapter, "alias_symbol", None)):
            return                                               # names as stored (Upstox's own master; a test feed)
        done = self._bound.setdefault(id(adapter), set())
        name = getattr(adapter, "name", "your broker")
        for facts in contracts:
            if facts.symbol in done:
                continue
            listed = await broker.get_instruments(facts.exchange)
            found = [i for i in listed if _matches(i, facts)]
            if len(found) != 1:
                raise ContractResolutionError(
                    f"{facts.symbol} is {'not' if not found else 'more than once'} in {name}'s instrument list - nothing was placed")
            lot = getattr(found[0], "lot_size", None)
            if facts.lot_size is not None and lot is not None and int(lot) != int(facts.lot_size):
                raise ContractResolutionError(f"{facts.symbol}: ATP's lot size {int(facts.lot_size)} and {name}'s {int(lot)} differ - "
                                              "nothing was placed (the contract lists are checked again with the next sync)")
            adapter.alias_symbol(facts.exchange, facts.symbol, str(found[0].instrument_token), str(found[0].tradingsymbol))
            done.add(facts.symbol)

    async def bind_open(self, session: AsyncSession, broker: Any, trades: Iterable[TradeRecord]) -> list[str]:
        """Before the exits: every open derivative position's name on today's adapter. Never raises (ADR-0004): what
        could not be bound is returned in plain words, and its exit is tried again next cycle."""
        if broker is None or not callable(getattr(self._adapter(broker), "alias_symbol", None)):
            return []
        problems: list[str] = []
        for trade in trades:
            if (trade.instrument_kind or "") not in DERIVATIVE_KINDS:
                continue
            try:
                facts = await facts_of_trade(session, trade)
                if facts is None:
                    problems.append(f"{trade.symbol}: the contract could not be read from its name")
                    continue
                await self.bind(broker, [facts])
            except Exception as exc:  # noqa: BLE001 - an exit is never stopped here; retried next cycle
                problems.append(f"{trade.symbol}: {exc}")
        return problems


def chain_provider(source: Any) -> Callable[[str, date], Awaitable[Any]]:
    """Phase H1 (as the worker's `_chain_provider`): the chain from his own broker session, read only when the
    deployment's own strike filters need it."""
    async def provider(underlying_symbol: str, expiry: date) -> Any:
        get = getattr(source, "get_option_chain", None)
        if get is None:
            raise ContractResolutionError("this runner's feed has no option chain for your strike filters - nothing was placed")
        return await get(underlying_symbol, expiry)
    return provider


def daily_close(frames_of: Callable[..., Awaitable[dict]], now: datetime) -> Callable[[str, date], Awaitable[float | None]]:
    """The expiry-day close of `underlying` from his broker's Daily bars (see the module docstring). None until that
    day's bar has closed - the settlement then waits, as on the server while the day's file is not stored."""
    async def close_of(underlying: str, expiry: date) -> float | None:
        symbol = master.INDEX_SYMBOLS.get(underlying, underlying)
        exchange = master.INDEX_EXCHANGE.get(underlying, "NSE")
        frames = closed_frames(await frames_of(symbol, exchange, ["day"], now), now)
        bars = frames.get("day")
        if bars is None or len(bars) == 0:
            return None
        for ts, row in bars[::-1].iterrows():
            if ts.to_pydatetime().astimezone(IST).date() == expiry:
                return float(row["close"])
        return None
    return close_of


__all__ = ["ContractBinder", "ContractFacts", "chain_provider", "daily_close", "facts_of", "facts_of_trade", "DERIVATIVE_KINDS"]
