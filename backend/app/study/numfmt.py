"""M77 SX-1: the ONE backend Indian number formatter (server-rendered PDF / PNG / CSV text and sentences).

It mirrors the frontend's formatter (frontend/src/ui/format.ts `formatNumber` / `formatINR` / `formatSignedINR`):
- Indian digit grouping: the last three digits, then pairs (1,24,50,000 - lakh / crore places). Written out here, never
  left to a locale, so the output does not depend on the server's locale data.
- fixed decimals; `trim` makes them an upper bound and drops trailing zeros ("24,812.5", "24,812");
- a negative number takes the true minus sign "−" (U+2212); a figure that rounds to zero is never signed;
- money takes the rupee sign "₹" before the digits ("−₹700"); whole rupees by default, paise on request,
  `compact` writes one crore and up as "₹2.4 Cr";
- a missing (None) or non-finite value is NOT_AVAILABLE ("not available"), never 0 and never a made-up figure.
- rounding as the browser's `toFixed`: the exact binary value, a tie rounds away from zero ("half_up").

The two older helpers are thin wrappers over this module and keep their own output byte for byte
(tests/test_m77_sx1_numfmt.py): `journal.export.indian` / `rupees` (hyphen-minus, "Rs ", "-" when missing) and
`option_chain.analyses.fmt_number` / `fmt_signed` (hyphen-minus). They pass `minus="-"`, `rounding="half_even"`
(Python's own float formatting, what they always used) and `finite_only=False` (a non-finite value prints as Python
prints it, as before).
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

MINUS = "−"
RUPEE = "₹"
NOT_AVAILABLE = "not available"

Rounding = Literal["half_up", "half_even"]

_PRECISION = 400      # digits of the Decimal context: a float's integer part has at most 309, so no quantize overflows


def group_indian(digits: str) -> str:
    """Indian grouping of a non-negative whole-number string: "12450000" -> "1,24,50,000"."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def _fixed(abs_value: float, decimals: int, rounding: Rounding) -> str:
    """|value| written with exactly `decimals` places, no grouping and no sign."""
    if rounding == "half_even" or not math.isfinite(abs_value):
        return f"{abs_value:.{decimals}f}"
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        quantum = Decimal(1).scaleb(-decimals)
        return f"{Decimal(abs_value).quantize(quantum, rounding=ROUND_HALF_UP):f}"


def _trim_zeros(text: str) -> str:
    """Trailing zeros of a decimal off: "2.40" -> "2.4", "3.00" -> "3"."""
    return text.rstrip("0").rstrip(".") if "." in text else text


def _grouped(fixed: str) -> str:
    whole, _, frac = fixed.partition(".")
    return f"{group_indian(whole)}.{frac}" if frac else group_indian(whole)


def _is_zero(fixed: str) -> bool:
    return fixed.strip("0.") == ""


def format_number(value: float | None, decimals: int = 0, *, trim: bool = False, signed: bool = False,
                  minus: str = MINUS, missing: str = NOT_AVAILABLE, rounding: Rounding = "half_up",
                  finite_only: bool = True) -> str:
    """A plain number with Indian grouping: format_number(1234567.8, 1) -> "12,34,567.8"; negatives get `minus`.

    `signed` adds "+" to a positive figure that does not round to zero (a change, a P&L)."""
    if value is None or (finite_only and not math.isfinite(value)):
        return missing
    fixed = _fixed(abs(value), decimals, rounding)
    zero = _is_zero(fixed)
    body = _grouped(fixed)
    if trim:
        body = _trim_zeros(body)
    if value < 0 and not zero:
        return f"{minus}{body}"
    return f"+{body}" if signed and value > 0 and not zero else body


def format_inr(value: float | None, *, paise: bool = False, compact: bool = False, signed: bool = False,
               decimals: int | None = None, symbol: str = RUPEE, minus: str = MINUS, missing: str = NOT_AVAILABLE,
               rounding: Rounding = "half_up", finite_only: bool = True) -> str:
    """Rupees with Indian grouping: format_inr(123456.4) -> "₹1,23,456"; paise=True -> "₹1,23,456.40".

    `compact` (narrow tiles, ignored with paise): one crore and up as "₹2.4 Cr". `signed`: "+₹3,120" for a
    profit, as formatSignedINR. `decimals` overrides the whole-rupee / paise choice (the journal PDF writes 2)."""
    if value is None or (finite_only and not math.isfinite(value)):
        return missing
    places = decimals if decimals is not None else (2 if paise else 0)
    abs_value = abs(value)
    fixed = _fixed(abs_value, places, rounding)
    zero = _is_zero(fixed)
    if compact and not paise and decimals is None and math.isfinite(abs_value) \
            and Decimal(_fixed(abs_value, 0, rounding)) >= 10_000_000:
        body = f"{_trim_zeros(_grouped(_fixed(abs_value / 1e7, 2, rounding)))} Cr"
    else:
        body = _grouped(fixed)
    text = f"{symbol}{body}"
    if value < 0 and not zero:
        return f"{minus}{text}"
    return f"+{text}" if signed and value > 0 and not zero else text


__all__ = ["MINUS", "NOT_AVAILABLE", "RUPEE", "format_inr", "format_number", "group_indian"]
