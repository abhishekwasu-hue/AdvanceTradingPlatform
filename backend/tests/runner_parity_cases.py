"""Runner S8 (docs/design/RUNNER_PARITY.md): the recorded bar series and the owner-style deployments both PAPER paths replay.

Pure data, shared by the server half (tests/test_runner_s8_parity.py: the trading worker's own cycle) and the runner half
(tests/runner_parity_job.py: the runner loop in its own process). Generic made-up numbers and synthetic contracts only
(no market data). Each case: one saved DSL strategy (CLOSE crosses above SMA(5), his own ATR stop), one deployment with
his own settings, the same bar series, the same price at each step and the same cycle times. Bars: closed bars decide;
the last bar of the series is still forming at the entry step and is never read (no look-ahead).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd

T1 = datetime(2031, 12, 1, 4, 30, tzinfo=UTC)            # a Monday, 10:00 IST: the signal bar has just closed
T2 = T1 + timedelta(minutes=1)                           # a price move (his stops)
T3 = datetime(2031, 12, 1, 9, 46, tzinfo=UTC)            # 15:16 IST: after his square-off time (15:15)
T4 = datetime(2031, 12, 3, 4, 30, tzinfo=UTC)            # two days later: after the near expiry's close
NEAR, NEXT = date(2031, 12, 2), date(2031, 12, 9)
INDEX = "NIFTY 50"
SQUARE_OFF = "15:15"                                     # his own square-off time (trade settings)

STRATEGY = dict(name="parity", timeframe="5min", stop_loss_atr_mult=1.0, atr_period=5,
                long_conditions=[{"left": {"type": "indicator", "indicator": "CLOSE"}, "operator": "CROSSES_ABOVE",
                                  "right": {"type": "indicator", "indicator": "SMA", "period": 5}}])


def bars(top: float, step: float, cross: float, forming: float) -> pd.DataFrame:
    """A fall, the cross bar (closed at T1), then the bar still forming at T1."""
    closes = [top - step * i for i in range(30)] + [cross, forming]
    index = pd.date_range(end=T1, periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame({"open": closes, "high": [c + step for c in closes], "low": [c - step for c in closes],
                         "close": closes, "volume": [1000.0] * len(closes)}, index=index)


def daily() -> pd.DataFrame:
    """The index's Daily bars: 2 Dec (the near expiry day) closes at 24700; 3 Dec is still forming at T4 (never read)."""
    starts = [pd.Timestamp(d, tz="Asia/Kolkata").tz_convert("UTC") for d in ("2031-12-01", "2031-12-02", "2031-12-03")]
    closes = [24500.0, 24700.0, 25000.0]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [0.0] * 3},
                        index=pd.DatetimeIndex(starts))


EXPIRY_CLOSE = 24700.0


def _name(strike: float, right: str, expiry: date) -> str:
    return f"NIFTY {int(strike)} {right} {expiry.strftime('%d %b %y').upper()}"


def contracts() -> list[dict]:
    """Synthetic index option contracts (ATP's instrument master spelling), two expiries."""
    rows, key = [], 880000
    for expiry in (NEAR, NEXT):
        for strike in range(24000, 25001, 50):
            for right in ("CE", "PE"):
                key += 1
                rows.append({"broker": "upstox", "exchange": "NFO", "segment": "NSE_FO", "instrument_key": f"NSE_FO|PAR{key}",
                             "tradingsymbol": _name(float(strike), right, expiry), "name": "NIFTY", "underlying": "NIFTY",
                             "instrument_type": right, "expiry": expiry.isoformat(), "strike": float(strike), "lot_size": 75,
                             "tick_size": 0.05, "weekly": True})
    return rows


CE_ATM, CE_OTM2 = _name(24500, "CE", NEAR), _name(24600, "CE", NEAR)
PE_SOLD, PE_BOUGHT = _name(24500, "PE", NEAR), _name(24400, "PE", NEAR)

_EQ_FRAMES = {"5min": bars(100.0, 0.5, 90.0, 80.0)}
_IDX_FRAMES = {"5min": bars(24600.0, 5.0, 24500.0, 24400.0), "day": daily()}
_OPTION = dict(instrument_kind="OPTION", option_position="BUY", expiry_rule="NEAREST", strike_rule="ATM", strike_offset=0)

# name -> the deployment's own settings, its symbol's frames, the steps and the price of each symbol at each step.
CASES: dict[str, dict] = {
    "equity_dsl_stop": dict(
        title="Equity DSL single leg: entry, then his own ATR stop",
        dep=dict(symbol="PAREQ", holding="SWING"), frames={"PAREQ": _EQ_FRAMES},
        steps=[(T1, {"PAREQ": 90.0}), (T2, {"PAREQ": 80.0})]),
    "equity_square_off": dict(
        title="Equity DSL single leg, INTRADAY: square-off at his own time (15:15)",
        dep=dict(symbol="PARSQ", holding="INTRADAY"), frames={"PARSQ": _EQ_FRAMES},
        steps=[(T1, {"PARSQ": 90.0}), (T3, {"PARSQ": 92.0})]),
    "option_premium_stop": dict(
        title="Index option single leg (ATM CE, 1 lot): his own 30% premium stop",
        dep=dict(symbol=INDEX, holding="INTRADAY", premium_stop_pct=30.0, **_OPTION), frames={INDEX: _IDX_FRAMES},
        steps=[(T1, {INDEX: 24500.0, CE_ATM: 100.0}), (T2, {INDEX: 24480.0, CE_ATM: 60.0})]),
    "two_leg_hedge_first": dict(
        title="Bull put spread (two legs, width 2): hedge leg first, square-off at his time",
        dep=dict(symbol=INDEX, holding="INTRADAY", **{**_OPTION, "option_position": None}, option_strategy="BULL_PUT_SPREAD",
                 spread_width=2), frames={INDEX: _IDX_FRAMES}, flags={"smart_execution": True},
        steps=[(T1, {INDEX: 24500.0, PE_SOLD: 120.0, PE_BOUGHT: 80.0}),
               (T3, {INDEX: 24550.0, PE_SOLD: 90.0, PE_BOUGHT: 60.0})]),
    "expiry_settlement": dict(
        title="SWING index CE (OTM 2) held past expiry: cash settlement at the expiry-day close",
        dep=dict(symbol=INDEX, holding="SWING", **{**_OPTION, "strike_rule": "OTM", "strike_offset": 2}), frames={INDEX: _IDX_FRAMES},
        steps=[(T1, {INDEX: 24500.0, CE_OTM2: 50.0}), (T4, {INDEX: 25000.0, CE_OTM2: 1.0})]),
}

# What is compared, per trade (both sides, the same fields), rounded so float noise never reads as a mismatch. Times: the
# cycle (step) at which the entry / the exit happened - a trade row's own entry / exit stamp is the wall clock of the run,
# except a settlement's, which is the expiry close (compared as is).
FIELDS = ("symbol", "direction", "quantity", "leg_role", "entry_step", "entry_price", "exit_step", "exit_price", "exit_reason",
          "settled_at", "pnl", "signal_bar")


def trade_row(t, signal_bar: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    def num(value):  # type: ignore[no-untyped-def]
        return None if value is None else round(float(value), 2)
    settled = None
    if t.exit_reason == "Expired - settled" and t.exit_time is not None:
        at = t.exit_time if t.exit_time.tzinfo else t.exit_time.replace(tzinfo=UTC)
        settled = at.astimezone(UTC).isoformat(timespec="seconds")
    return {"id": t.id, "symbol": t.symbol, "direction": str(getattr(t.direction, "value", t.direction)), "quantity": num(t.quantity),
            "leg_role": t.leg_role, "entry_price": num(t.entry_price), "exit_price": num(t.exit_price), "exit_reason": t.exit_reason,
            "settled_at": settled, "pnl": num(t.pnl), "signal_bar": signal_bar}


def signal_bar(key: str | None) -> str | None:
    """The signal bar from an entry's idempotency key (`deployment:<id>:<bar>[...]`); the id differs between the stores."""
    if not key or not key.startswith("deployment:"):
        return None
    return key.split(":", 2)[2]


def summarise(snapshots: list[list[dict]]) -> list[dict]:
    """Per trade (in entry order): the step it appeared at, the step it closed at and its last row (local id dropped)."""
    rows: dict[int, dict] = {}
    for step, trades in enumerate(snapshots, start=1):
        for row in trades:
            item = rows.setdefault(row["id"], {"entry_step": step, "exit_step": None})
            if row["exit_price"] is not None and item["exit_step"] is None:
                item["exit_step"] = step
            item.update({k: v for k, v in row.items() if k != "id"})
    return [{k: rows[i].get(k) for k in FIELDS} for i in sorted(rows)]


__all__ = ["CASES", "FIELDS", "snapshot", "T1", "T2", "T3", "T4", "NEAR", "NEXT", "INDEX", "SQUARE_OFF", "STRATEGY", "EXPIRY_CLOSE",
           "contracts", "trade_row", "signal_bar", "summarise"]


async def snapshot(session, tenant_id: int, trade_model, order_model) -> tuple[list[dict], list[list]]:  # type: ignore[no-untyped-def]
    """One organisation's trades (rows for `summarise`) and its orders in placement order (symbol, direction, quantity,
    status) - the same read on both sides."""
    from sqlalchemy import select
    orders = list(await session.scalars(select(order_model).where(order_model.tenant_id == tenant_id).order_by(order_model.id)))
    bars = {o.trade_id: signal_bar(o.idempotency_key) for o in orders if getattr(o, "trade_id", None)}
    trades = list(await session.scalars(select(trade_model).where(trade_model.tenant_id == tenant_id).order_by(trade_model.id)))
    return ([trade_row(t, bars.get(t.id)) for t in trades],
            [[o.symbol, str(o.direction), round(float(o.quantity), 2), str(o.status)] for o in orders])
