"""BT1: the broker data-terms registry the code reads (docs/BROKER_DATA_TERMS.md, beta spec §2 and §4a).

A broker's market data is shown to its own user inside ATP (Tier A, bring your own broker) only when its status here is
"confirmed". "execution-only" (the broker's API terms forbid display on third-party platforms - Zerodha Kite Connect)
and "pending" (written confirmation not yet received) both mean Tier C (EOD) for that user. A broker missing from the
registry is treated as pending: nothing is shown live without a recorded confirmation.

`symbol_cap` is the broker's own per-connection instrument limit, from its documentation or written confirmation;
None means "to confirm" and the ATP per-session budget (`BYOB_SESSION_SYMBOL_CAP`, beta spec §4 capacity model) is the
only cap applied. A number is never guessed here.

The values live in `broker_data_terms.json` next to this module; a test keeps that file and the Markdown table in step.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

TERMS_FILE = Path(__file__).with_name("broker_data_terms.json")


class DataTermsStatus(str, Enum):
    CONFIRMED = "confirmed"
    EXECUTION_ONLY = "execution-only"
    PENDING = "pending"


@dataclass(frozen=True)
class BrokerDataTerms:
    broker: str
    display_name: str
    status: DataTermsStatus
    symbol_cap: Optional[int]
    requires_user_data_subscription: bool
    note: str

    @property
    def tier_a_allowed(self) -> bool:
        return self.status is DataTermsStatus.CONFIRMED

    def as_dict(self) -> dict:
        return {"broker": self.broker, "display_name": self.display_name, "status": self.status.value,
                "symbol_cap": self.symbol_cap, "symbol_cap_note": None if self.symbol_cap is not None else "to confirm",
                "requires_user_data_subscription": self.requires_user_data_subscription, "note": self.note}


def _parse(raw: dict) -> Dict[str, BrokerDataTerms]:
    out: Dict[str, BrokerDataTerms] = {}
    for name, entry in (raw.get("brokers") or {}).items():
        cap = entry.get("symbol_cap")
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0):
            raise ValueError(f"{name}: symbol_cap must be a positive whole number or null (to confirm)")
        out[name] = BrokerDataTerms(broker=name, display_name=str(entry.get("display_name") or name), status=DataTermsStatus(entry["status"]),
                                    symbol_cap=cap, requires_user_data_subscription=bool(entry.get("requires_user_data_subscription")),
                                    note=str(entry.get("note") or ""))
    return out


@lru_cache(maxsize=1)
def registry() -> Dict[str, BrokerDataTerms]:
    return _parse(json.loads(TERMS_FILE.read_text(encoding="utf-8")))


def terms_for(broker: str) -> BrokerDataTerms:
    """The broker's recorded terms; an unknown broker is pending (no live display without a recorded confirmation)."""
    found = registry().get((broker or "").lower())
    if found is not None:
        return found
    return BrokerDataTerms(broker=broker, display_name=broker, status=DataTermsStatus.PENDING, symbol_cap=None,
                           requires_user_data_subscription=False, note="Not recorded in docs/BROKER_DATA_TERMS.md.")


def confirmed_brokers() -> frozenset:
    return frozenset(name for name, terms in registry().items() if terms.tier_a_allowed)
