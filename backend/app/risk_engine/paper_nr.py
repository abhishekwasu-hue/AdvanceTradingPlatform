"""PAPER-NR (owner, 1 Nov; corrected scope): in PAPER no new entry is refused or reduced because the PAPER money is
short - the practice balance, the paper capital of a simulated account or the margin it would block. Each such check
becomes an information line (margin vs free balance), and the practice balance may go below zero.

Everything else is PAPER = LIVE: the user's own limits (Settings > Risk limits, the guardian, the hierarchy, VIX,
opposite positions, experience gates, his own PAPER cap, daily loss, entries per day, loss streak, max lots, risk-based
sizing) refuse in PAPER exactly as in LIVE, and so do every platform safety check (kill switches, the Phase-0 lock,
admin controls, data and auth checks). LIVE money checks (the broker's margin) are unchanged.

One more rule lives here: a PAPER risk-limit block never engages a STRATEGY / TENANT kill switch (`engages_kill_switch`) -
the PAPER order is still refused, but practice cannot switch off the organisation's trading.

Exits never come here (ADR-0004): no exit path calls this module (tests/test_paper_nr.py checks the call graph).
"""
from __future__ import annotations

from app.core.enums import ExecutionMode

PAPER = ExecutionMode.PAPER.value
BELOW_ZERO = "below zero - practice only"


def advisory(mode: object) -> bool:
    """True only for a PAPER entry: its money checks (balance, paper capital, margin) are information, not refusals."""
    value = getattr(mode, "value", mode)
    return str(value or "").upper() == PAPER


def engages_kill_switch(mode: object) -> bool:
    """A PAPER risk-limit block refuses the order but never engages a STRATEGY / TENANT kill switch (owner, PAPER-NR (4))."""
    return not advisory(mode)


def margin_info(needed: float | None, free: float | None) -> str:
    """The margin vs free balance information line of one PAPER entry (owner, PAPER-NR (2)). `free` None: no practice
    balance to measure against (M68 PR-7: his paper capital is Not set where the paper-account feature is off)."""
    if free is None:
        return "Practice margin: " + (f"needs ₹{needed:,.0f}; " if needed is not None else "not estimated for this order; ") \
            + "no practice balance (no paper capital set)"
    if needed is None:
        return (f"Practice margin: not estimated for this order; ₹{free:,.0f} free in your practice account"
                + (f" ({BELOW_ZERO})" if free < 0 else ""))
    after = free - needed
    return (f"Practice margin: needs ₹{needed:,.0f}; ₹{free:,.0f} free, ₹{after:,.0f} after"
            + (f" ({BELOW_ZERO})" if after < 0 else ""))
