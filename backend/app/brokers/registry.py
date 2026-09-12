from typing import Dict, Optional, Type

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerCredentials
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.stubs import AngelOneBroker, DhanBroker, FyersBroker
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker

BROKER_ADAPTERS: Dict[str, Type[BrokerInterface]] = {
    ZerodhaBroker.name: ZerodhaBroker,
    UpstoxBroker.name: UpstoxBroker,
    ShoonyaBroker.name: ShoonyaBroker,
    AngelOneBroker.name: AngelOneBroker,
    FyersBroker.name: FyersBroker,
    DhanBroker.name: DhanBroker,
}


def available_brokers() -> list[str]:
    return list(BROKER_ADAPTERS.keys())


def get_broker_adapter(
    name: str, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None
) -> BrokerInterface:
    try:
        adapter_cls = BROKER_ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown broker '{name}'. Available: {', '.join(available_brokers())}") from exc
    return adapter_cls(credentials, client)
