#!/usr/bin/env python
"""Z11 / M3-M4: is it safe to restart the trading worker or swap the database now? Read-only; prints one JSON line.

    python scripts/session_check.py [--orders-since <ISO-8601 UTC>]

    {"open_sessions": ["NSE", "MCX", ...], "open_positions": 3, "orders_since": 12, "now_ist": "..."}

Sessions come from the app's own calendar (`all_session_statuses`: every exchange family - NSE/BSE, MCX until its
evening close, crypto - with the holiday tables), so the deploy script never guesses hours. Open positions are
`trades` without an exit time (PAPER and LIVE, every organisation). `--orders-since` counts orders created after a
backup was taken: what a database restore to that backup would lose. Counts only - no symbols, users or tenants.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def collect(orders_since: datetime | None) -> dict:
    from sqlalchemy import func, select

    from app.db.models import OrderRecord, TradeRecord
    from app.db.session import _session_factory
    from app.market_data.calendar import IST, all_session_statuses

    now = datetime.now(timezone.utc)
    async with _session_factory() as session:
        statuses = await all_session_statuses(session, now)
        positions = int(await session.scalar(select(func.count()).select_from(TradeRecord).where(TradeRecord.exit_time.is_(None))) or 0)
        orders = None
        if orders_since is not None:
            orders = int(await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.created_at > orders_since)) or 0)
    return {"open_sessions": sorted(name for name, st in statuses.items() if st.is_open), "open_positions": positions,
            "orders_since": orders, "now_ist": now.astimezone(IST).strftime("%Y-%m-%d %H:%M")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders-since", help="ISO-8601 UTC time, e.g. 2026-10-11T18:30:00Z")
    args = parser.parse_args()
    since = None
    if args.orders_since:
        since = datetime.fromisoformat(args.orders_since.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    print(json.dumps(asyncio.run(collect(since))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
