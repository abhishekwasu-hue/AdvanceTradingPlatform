from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    MISSING_AT_BROKER = "MISSING_AT_BROKER"  # platform shows an open position, broker doesn't
    UNTRACKED_AT_BROKER = "UNTRACKED_AT_BROKER"  # broker shows a position the platform has no record of


class ReconciliationItem(BaseModel):
    symbol: str
    internal_net_quantity: Optional[float] = None
    broker_net_quantity: Optional[float] = None
    status: ReconciliationStatus
    internal_trade_ids: List[int] = []
    detail: str = ""


class ReconciliationReport(BaseModel):
    broker_name: str
    checked_at: str
    items: List[ReconciliationItem]
    mismatched_count: int
