"""M71 PL-3 + PL-1(a): the one store-first read service every feature reads bars through (read side only).

`read_bars` (one symbol) and `read_many` (a scan: many symbols, one interval) - see `reader` for the read order, the
data terms (M71Q-2) and the gaps. Inventory of the features and their migration: docs/design/M71_READ_PATHS.md.
"""
from app.market_store.models import FLAG, BarsResult, Gap, RestSource, canonical
from app.market_store.reader import read_bars, read_many

__all__ = ["FLAG", "BarsResult", "Gap", "RestSource", "canonical", "read_bars", "read_many"]
