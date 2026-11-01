"""M68 PR-7 (TS1-069 / 070): the trader's own paper charges model - what a paper fill pays beyond the statutory charges
(STT, exchange, stamp duty, GST, SEBI fee: set by law, a fact). Pure (no database): the backtest engines, the paper broker's
callers and the cost lines import it; the user's saved values are read by app/paper/paper_settings.py.

Not set = statutory charges only: no brokerage, no slippage, labelled so on the screen. "Statutory charges + my brokerage"
adds his own brokerage per order; his own slippage (each side) moves a paper fill when he saved one. ATP chooses neither.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

WHERE = "Settings › Trade settings › Paper account"
CHARGES_KEY = "paper_account.charges_model"
BROKERAGE_KEY = "paper_account.brokerage_per_order"
SLIPPAGE_KEY = "paper_account.slippage_pct"
WITH_BROKERAGE = "STATUTORY_BROKERAGE"


@dataclass(frozen=True)
class PaperCharges:
    """What a paper fill pays beyond the statutory charges (a fact): his own brokerage per order and slippage per side.
    The default (nothing saved) is statutory charges only - no brokerage, no slippage."""
    brokerage_per_order: float = 0.0
    slippage_pct: float = 0.0
    with_brokerage: bool = False          # he chose "Statutory charges + my brokerage"
    brokerage_saved: bool = False
    slippage_saved: bool = False

    @property
    def words(self) -> str:
        """The label the screen shows next to a paper cost or fill."""
        parts = ["Statutory charges"]
        if self.with_brokerage and self.brokerage_saved:
            parts.append(f"your brokerage ₹{self.brokerage_per_order:g} per order")
        elif self.with_brokerage:
            parts.append("no brokerage (your brokerage per order is not set)")
        else:
            parts.append("no brokerage")
        parts.append(f"your slippage {self.slippage_pct:g}% each side" if self.slippage_saved else "no slippage")
        if not self.with_brokerage and not self.slippage_saved:
            return "Statutory charges only: no brokerage, no slippage"
        return ", ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {"brokerage_per_order": self.brokerage_per_order, "slippage_pct": self.slippage_pct,
                "with_brokerage": self.with_brokerage, "brokerage_saved": self.brokerage_saved,
                "slippage_saved": self.slippage_saved, "words": self.words, "where": WHERE}

    def broker(self) -> Any:
        """A PaperBroker that fills and costs with these charges."""
        from app.execution.paper_broker import PaperBroker
        return PaperBroker(brokerage_per_order=self.brokerage_per_order, slippage_pct=self.slippage_pct)


STATUTORY_ONLY = PaperCharges()


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def charges_from(values: dict[str, Any]) -> PaperCharges:
    """The charges of a cleaned Trade settings `values` map (the Trade settings registry's `clean`). Pure."""
    with_brokerage = values.get(CHARGES_KEY) == WITH_BROKERAGE
    brokerage = number(values.get(BROKERAGE_KEY))
    slippage = number(values.get(SLIPPAGE_KEY))
    return PaperCharges(brokerage_per_order=brokerage if with_brokerage and brokerage is not None else 0.0,
                        slippage_pct=slippage if slippage is not None else 0.0, with_brokerage=with_brokerage,
                        brokerage_saved=brokerage is not None, slippage_saved=slippage is not None)
