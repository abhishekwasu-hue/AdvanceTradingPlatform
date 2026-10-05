"""Domain models for the News & Event engine - structured, cited entries for macro/market news
(RBI policy, Union Budget, government policy, broad corporate news, global macro events, sector
developments) that a human enters and cites. There is no live news feed wired in, so nothing
here is scraped or invented: every entry is exactly as reliable as its cited source, and `source`
is mandatory (unlike most fundamentals inputs, where it's optional) since the entire point of
this table is a sourced claim.
"""
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.enums import Bias
from app.fundamentals.models import SourceCitation


class NewsEventCategory(str, Enum):
    RBI_POLICY = "RBI_POLICY"
    UNION_BUDGET = "UNION_BUDGET"
    GOVT_POLICY = "GOVT_POLICY"
    CORPORATE = "CORPORATE"
    GLOBAL_MACRO = "GLOBAL_MACRO"
    SECTOR = "SECTOR"
    OTHER = "OTHER"


class NewsEvent(BaseModel):
    category: NewsEventCategory
    headline: str
    description: Optional[str] = None
    event_date: date
    # Empty = market-wide (e.g. an RBI repo rate decision affects every symbol, not a named few).
    affected_symbols: List[str] = Field(default_factory=list)
    sentiment: Bias = Bias.NEUTRAL
    source: SourceCitation


class NewsEventResponse(NewsEvent):
    id: int
    created_by: Optional[int] = None
    created_at: datetime
    # Phase BB: MANUAL (a person's cited entry) or FEED (public feed item, never verified by the
    # platform - shown as "unverified feed"); the shared keyword classification when present.
    origin: str = "MANUAL"
    verified: bool = True
    source_url: Optional[str] = None
    feed_id: Optional[str] = None
    published_at: Optional[datetime] = None
    classification: Optional[dict] = None
