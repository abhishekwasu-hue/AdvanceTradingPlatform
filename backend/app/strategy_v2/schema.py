"""SFC P0 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md section 3): the `atp.strategy/2` schema.

One saved strategy: conditions built from blocks, AND / OR groups (an explicit tree - the precedence is fixed when it is
saved, never re-parsed), a timeframe per condition, the entry (sides, an optional time window, an optional limit of
entries per day), the exits and the options mapping. Stored in `CustomStrategyRecord.config_json` (free text: no
migration).

DEF-0: nothing has a default value. A missing / null part is "off"; an incomplete rule (a stop kind without its value)
is refused, never filled in. The window and the entries-per-day limit are the user's own operational settings (OWNER
C.4): they never change how a condition reads the chart.

Operands name a registry function directly (`block` = the ScreenQL function, `params` = its arguments by name), so every
indicator and price-action function ATP computes is available without a hand-kept list; the builder (P5) shows them
with friendly names.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA = "atp.strategy/2"
TIMEFRAMES = ("1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "1d", "1w")
Timeframe = Literal["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "1d", "1w"]
Comparator = Literal["crosses_above", "crosses_below", "gt", "lt", "gte", "lte", "between", "rising", "falling", "is_true"]
PRICE_FIELDS = ("open", "high", "low", "close", "volume", "oi")
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_BLOCK = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
MAX_ITEMS = 20
MAX_DEPTH = 4
MAX_LEAVES = 40

ParamValue = Union[float, int, str, bool]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValueOperand(_Model):
    kind: Literal["value"]
    value: float


class PriceOperand(_Model):
    kind: Literal["price"]
    field: Literal["open", "high", "low", "close", "volume", "oi"]
    offset: int = Field(0, ge=0, le=500)


class BlockOperand(_Model):
    """An indicator or a price-action function of the registry, with its arguments by name."""
    kind: Literal["indicator", "pa"]
    block: str
    params: dict[str, ParamValue] = Field(default_factory=dict)
    offset: int = Field(0, ge=0, le=500)

    @model_validator(mode="after")
    def _names(self) -> "BlockOperand":
        if not _BLOCK.match(self.block):
            raise ValueError(f"block {self.block!r} is not a function name")
        for key in self.params:
            if not _BLOCK.match(key):
                raise ValueError(f"setting {key!r} is not a name")
        return self


Operand = Annotated[Union[ValueOperand, PriceOperand, BlockOperand], Field(discriminator="kind")]


class Condition(_Model):
    id: str
    timeframe: Timeframe | None = None
    left: Operand
    cmp: Comparator
    right: Operand | None = None
    low: Operand | None = None
    high: Operand | None = None
    bars: int | None = Field(None, ge=1, le=50)
    within_bars: int | None = Field(None, ge=1, le=500)

    @model_validator(mode="after")
    def _complete(self) -> "Condition":
        if not _ID.match(self.id):
            raise ValueError(f"condition id {self.id!r} must be 1-32 letters, digits, '-' or '_'")
        two_sided = ("crosses_above", "crosses_below", "gt", "lt", "gte", "lte")
        if self.cmp in two_sided and self.right is None:
            raise ValueError(f"condition {self.id}: '{self.cmp}' needs something to compare with")
        if self.cmp == "between" and (self.low is None or self.high is None):
            raise ValueError(f"condition {self.id}: 'between' needs both a low and a high")
        if self.cmp in ("rising", "falling") and self.bars is None:
            raise ValueError(f"condition {self.id}: '{self.cmp}' needs the number of bars")
        if self.cmp in ("rising", "falling", "is_true") and self.left.kind == "value":
            raise ValueError(f"condition {self.id}: a fixed number cannot be '{self.cmp}'")
        return self


class Group(_Model):
    id: str | None = None
    op: Literal["AND", "OR"]
    items: list[Union[Condition, "Group"]] = Field(min_length=1, max_length=MAX_ITEMS)


Group.model_rebuild()


class Window(_Model):
    start: str = Field(alias="from")
    end: str = Field(alias="to")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _times(self) -> "Window":
        if not _HHMM.match(self.start) or not _HHMM.match(self.end):
            raise ValueError("the window takes times as HH:MM")
        if self.start >= self.end:
            raise ValueError("the window must start before it ends")
        return self


class Entry(_Model):
    sides: Literal["long", "short", "both"]
    long: Group | None = None
    short: Group | None = None
    window: Window | None = None
    max_entries_per_day: int | None = Field(None, ge=1, le=100)

    @model_validator(mode="after")
    def _sides(self) -> "Entry":
        if self.sides in ("long", "both") and self.long is None:
            raise ValueError("long entries need their conditions")
        if self.sides in ("short", "both") and self.short is None:
            raise ValueError("short entries need their conditions")
        return self


class LevelRule(_Model):
    kind: Literal["points", "pct", "atr", "swing", "candle", "premium"]
    value: float | None = Field(None, gt=0)
    atr_period: int | None = Field(None, ge=1, le=500)
    swing_degree: int | None = Field(None, ge=0, le=3)
    candle: Literal["entry", "signal", "previous"] | None = None

    @model_validator(mode="after")
    def _complete(self) -> "LevelRule":
        needs = {"points": ("value",), "pct": ("value",), "premium": ("value",), "atr": ("value", "atr_period"),
                 "swing": ("swing_degree",), "candle": ("candle",)}[self.kind]
        missing = [k for k in needs if getattr(self, k) is None]
        if missing:
            raise ValueError(f"a '{self.kind}' level needs: {', '.join(missing)}")
        return self


class Trailing(_Model):
    kind: Literal["points", "pct", "atr", "swing"]
    value: float | None = Field(None, gt=0)
    atr_period: int | None = Field(None, ge=1, le=500)
    swing_degree: int | None = Field(None, ge=0, le=3)

    @model_validator(mode="after")
    def _complete(self) -> "Trailing":
        needs = {"points": ("value",), "pct": ("value",), "atr": ("value", "atr_period"), "swing": ("swing_degree",)}[self.kind]
        missing = [k for k in needs if getattr(self, k) is None]
        if missing:
            raise ValueError(f"a '{self.kind}' trailing stop needs: {', '.join(missing)}")
        return self


class TimeExit(_Model):
    after_bars: int | None = Field(None, ge=1, le=10_000)
    at: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "TimeExit":
        if self.after_bars is None and self.at is None:
            raise ValueError("a time exit needs a number of bars or a time")
        if self.at is not None and not _HHMM.match(self.at):
            raise ValueError("a time exit takes its time as HH:MM")
        return self


class Exit(_Model):
    stop: LevelRule | None = None
    target: LevelRule | None = None
    trailing: Trailing | None = None
    time_exit: TimeExit | None = None
    long_conditions: Group | None = None
    short_conditions: Group | None = None


class OptionLeg(_Model):
    action: Literal["buy", "sell"]
    right: Literal["CE", "PE"]
    ratio: int = Field(ge=1, le=10)
    strike_offset: int = Field(0, ge=-20, le=20)


class StrikeRule(_Model):
    rule: Literal["atm", "itm", "otm", "delta", "premium"]
    steps: int | None = Field(None, ge=0, le=20)
    delta: float | None = Field(None, gt=0, lt=1)
    premium: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _complete(self) -> "StrikeRule":
        needs = {"atm": (), "itm": ("steps",), "otm": ("steps",), "delta": ("delta",), "premium": ("premium",)}[self.rule]
        missing = [k for k in needs if getattr(self, k) is None]
        if missing:
            raise ValueError(f"the '{self.rule}' strike rule needs: {', '.join(missing)}")
        return self


class ExpiryRule(_Model):
    rule: Literal["current_week", "next_week", "current_month", "next_month"]


class Options(_Model):
    legs: list[OptionLeg] = Field(min_length=1, max_length=4)
    strike: StrikeRule
    expiry: ExpiryRule


class StrategyV2(_Model):
    schema_: Literal["atp.strategy/2"] = Field(alias="schema")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(None, max_length=2000)
    timeframe: Timeframe
    entry: Entry
    exit: Exit | None = None
    options: Options | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _tree(self) -> "StrategyV2":
        seen: set[str] = set()
        leaves = 0
        for group in self.groups().values():
            for cond, depth in walk(group):
                if depth > MAX_DEPTH:
                    raise ValueError(f"groups may nest at most {MAX_DEPTH} deep")
                if cond.id in seen:
                    raise ValueError(f"condition id {cond.id!r} is used twice")
                seen.add(cond.id)
                leaves += 1
        if leaves > MAX_LEAVES:
            raise ValueError(f"a strategy holds at most {MAX_LEAVES} conditions")
        return self

    def groups(self) -> dict[str, Group]:
        """Every condition tree by its role: entry_long / entry_short / exit_long / exit_short (only the ones set)."""
        out: dict[str, Group] = {}
        if self.entry.sides in ("long", "both") and self.entry.long is not None:
            out["entry_long"] = self.entry.long
        if self.entry.sides in ("short", "both") and self.entry.short is not None:
            out["entry_short"] = self.entry.short
        if self.exit is not None and self.exit.long_conditions is not None:
            out["exit_long"] = self.exit.long_conditions
        if self.exit is not None and self.exit.short_conditions is not None:
            out["exit_short"] = self.exit.short_conditions
        return out


def walk(group: Group, depth: int = 1) -> list[tuple[Condition, int]]:
    """Every condition of a tree with the depth of the group it sits in."""
    out: list[tuple[Condition, int]] = []
    for item in group.items:
        if isinstance(item, Condition):
            out.append((item, depth))
        else:
            out.extend(walk(item, depth + 1))
    return out


def is_v2(raw: Any) -> bool:
    """A stored config (a JSON object) is an `atp.strategy/2` strategy."""
    return isinstance(raw, dict) and raw.get("schema") == SCHEMA


__all__ = ["SCHEMA", "TIMEFRAMES", "PRICE_FIELDS", "StrategyV2", "Entry", "Exit", "Group", "Condition", "ValueOperand",
           "PriceOperand", "BlockOperand", "LevelRule", "Trailing", "TimeExit", "Options", "OptionLeg", "StrikeRule",
           "ExpiryRule", "Window", "walk", "is_v2"]
