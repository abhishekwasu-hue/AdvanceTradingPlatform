#!/usr/bin/env python
"""M85 U1: why does the app ask for the Upstox Analytics Token again? Read-only; prints one JSON line per Upstox
credential row (counts and states only - never a token, a name, an e-mail or an account id).

    python scripts/upstox_token_check.py

Each row: the daily login state (token_status + whether it is usable now), the analytics token (set, expiry date,
expired, last probe time, the status of every endpoint class), what the app would decide from it (`data_usable`:
market data without the daily login; `hub_renew`: the "login ended" notice), the flags that pick the path
(`data_hub`, `byob_data_tier`) and the live-price status the worker last published for the organisation.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def collect() -> list:
    from sqlalchemy import select

    from app.brokers import upstox_analytics
    from app.brokers.token_lifecycle import data_is_usable, token_is_usable
    from app.db.models import BrokerCredentialRecord
    from app.db.session import _session_factory
    from app.market_data import hub
    from app.market_data.fanin import FLAG as BYOB_FLAG
    from app.platform.controls import flag_enabled

    now = datetime.now(timezone.utc)
    out = []
    async with _session_factory() as session:
        rows = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name == "upstox")
                                          .order_by(BrokerCredentialRecord.tenant_id, BrokerCredentialRecord.id)))
        for i, r in enumerate(rows, 1):
            view = upstox_analytics.record_view(r, now)
            coverage = {c: (v or {}).get("status") for c, v in (view.get("analytics_coverage") or {}).items()}
            status = await hub.read_status(hub.org_namespace(r.tenant_id))
            daily_ok = token_is_usable(r, now)
            data_ok = data_is_usable(r, now)
            byob = await flag_enabled(session, BYOB_FLAG, r.tenant_id)
            out.append({
                "row": i, "account": r.account_label or "primary",
                "daily_status": r.token_status, "daily_usable": daily_ok,
                "analytics_set": view["analytics_token_set"],
                "analytics_expires": (view.get("analytics_token_expires_at") or "")[:10] or None,
                "analytics_expiry_estimated": view.get("analytics_token_expiry_estimated"),
                "analytics_expired": view.get("analytics_token_expired"),
                "analytics_probed_at": (view.get("analytics_probed_at") or "")[:16] or None,
                "coverage": coverage,
                "data_usable": data_ok,
                "data_hub": await hub.hub_on(session, r.tenant_id), "byob_data_tier": byob,
                # what the browser is shown before this fix: byob reads the daily login only, else the data rule
                "hub_renew": (not daily_ok) if byob else (not data_ok),
                "published_state": status.state if status else None,
            })
    return out


def main() -> int:
    for row in asyncio.run(collect()):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
