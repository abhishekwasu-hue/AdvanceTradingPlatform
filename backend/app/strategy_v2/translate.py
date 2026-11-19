"""SFC P0 (plan OQ-6): a saved v1 no-code strategy (`CustomStrategyConfig`) read as `atp.strategy/2`.

Used to OPEN an old strategy in the new builder and to prove the two engines agree (tests/test_sfc_p0.py). A saved v1
strategy keeps RUNNING on DeclarativeStrategy until a parity check shows identical signals and the owner is told.

v1 had flat AND conditions per side, operands with their own higher timeframe. A v2 condition has ONE timeframe; a v1
condition whose two operands read different timeframes has no exact v2 form, so `translate` returns None for that
strategy (it stays v1 - nothing is approximated). v1's stop (ATR multiple) maps to an ATR stop; v1's target R:R range
has no v2 form (a v2 target is a level rule) and is left out - the strategy then shows no target until he sets one.
"""
from __future__ import annotations

from typing import Any, Optional

from app.strategy_engine.declarative import Condition as V1Condition
from app.strategy_engine.declarative import CustomStrategyConfig
from app.strategy_engine.declarative import Operand as V1Operand
from app.strategy_v2.schema import StrategyV2

TF = {"1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", "1hour": "1h", "day": "1d", "1d": "1d"}
CMP = {"GT": "gt", "LT": "lt", "GTE": "gte", "LTE": "lte", "CROSSES_ABOVE": "crosses_above", "CROSSES_BELOW": "crosses_below"}
PRICE = {"CLOSE": "close", "OPEN": "open", "HIGH": "high", "LOW": "low", "VOLUME": "volume"}


def _operand(op: V1Operand) -> Optional[dict[str, Any]]:
    if op.type == "value":
        return {"kind": "value", "value": float(op.value)}
    name, p, k = op.indicator, int(op.period), float(op.multiplier)
    if name in PRICE:
        return {"kind": "price", "field": PRICE[name]}
    block: dict[str, tuple[str, dict[str, Any]]] = {
        "EMA": ("EMA", {"x": "close", "n": p}), "SMA": ("SMA", {"x": "close", "n": p}), "RSI": ("RSI", {"n": p}),
        "ADX": ("ADX", {"n": p}), "PLUS_DI": ("PlusDI", {"n": p}), "MINUS_DI": ("MinusDI", {"n": p}), "ATR": ("ATR", {"n": p}),
        "SUPERTREND": ("Supertrend", {"n": p, "k": k}), "VWAP": ("VWAP", {}), "DAY_OPEN": ("DayOpen", {}),
        "OR_HIGH": ("ORHigh", {"minutes": p}), "OR_LOW": ("ORLow", {"minutes": p}), "PDH": ("PDH", {}), "PDL": ("PDL", {}),
        "PDC": ("PDC", {}), "BB_UPPER": ("BBUpper", {"n": p, "k": k}), "BB_MID": ("BBMid", {"n": p, "k": k}),
        "BB_LOWER": ("BBLower", {"n": p, "k": k}), "VOLUME_SMA": ("SMA", {"x": "volume", "n": p}),
    }
    if name not in block:
        return None
    fn, params = block[name]
    return {"kind": "indicator", "block": fn, "params": params}


def _condition(c: V1Condition, i: int, side: str, base: str) -> Optional[dict[str, Any]]:
    tfs = {TF.get(o.timeframe or "", None) if o.timeframe else base for o in (c.left, c.right) if o.type != "value"}
    if len(tfs) > 1 or None in tfs:
        return None                                    # two timeframes in one condition: no exact v2 form
    left, right = _operand(c.left), _operand(c.right)
    if left is None or right is None or c.operator not in CMP:
        return None
    tf = next(iter(tfs)) if tfs else base
    out: dict[str, Any] = {"id": f"{side[0]}{i + 1}", "left": left, "cmp": CMP[c.operator], "right": right}
    if tf != base:
        out["timeframe"] = tf
    return out


def translate(config: CustomStrategyConfig) -> Optional[StrategyV2]:
    """The v2 form of a v1 strategy, or None when it has no exact one (it then stays v1)."""
    base = TF.get(config.timeframe)
    if base is None:
        return None
    entry: dict[str, Any] = {}
    for side, conds in (("long", config.long_conditions), ("short", config.short_conditions)):
        if conds:
            items = [_condition(c, i, side, base) for i, c in enumerate(conds)]
            if any(x is None for x in items):
                return None
            entry[side] = {"op": "AND", "items": items}
    entry["sides"] = "both" if "long" in entry and "short" in entry else ("long" if "long" in entry else "short")
    raw: dict[str, Any] = {"schema": "atp.strategy/2", "version": 1, "name": config.name, "timeframe": base, "entry": entry}
    if config.stop_loss_atr_mult is not None:
        raw["exit"] = {"stop": {"kind": "atr", "value": float(config.stop_loss_atr_mult), "atr_period": int(config.atr_period)}}
    return StrategyV2.model_validate(raw)


__all__ = ["translate"]
