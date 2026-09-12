from abc import ABC, abstractmethod
from typing import List

from app.fundamentals.models import CompanyProfile, CorporateAction, ShareholdingSnapshot


class FundamentalDataProvider(ABC):
    """A source of real, citable fundamental data for one company. Mirrors
    `app.brokers.base.BrokerInterface`: the rest of the platform only ever talks to this
    interface, never a provider-specific client directly, so a new provider (a commercial data
    vendor, a different exchange) can be added without touching upstream code.

    Every method returns a model whose `source` field is filled in by the implementation - the
    caller never has to (and never should) fabricate one.
    """

    name: str

    @abstractmethod
    async def get_company_profile(self, symbol: str) -> CompanyProfile: ...

    @abstractmethod
    async def get_shareholding_pattern(self, symbol: str) -> ShareholdingSnapshot: ...

    @abstractmethod
    async def get_corporate_announcements(self, symbol: str, limit: int = 20) -> List[CorporateAction]: ...
