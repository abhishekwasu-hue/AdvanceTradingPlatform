"""Broker timestamp parsing shared by the adapters (Phase G1).

Upstox sends ISO-8601 with an offset ("2026-09-25T10:29:58+05:30"); Kite sends an IST wall time
with no offset ("2026-09-25 10:29:58"). Both become aware UTC datetimes; anything unparseable is
None, which the staleness gate treats as "cannot judge, accept".
"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def parse_broker_timestamp(value) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, (int, float)):
        seconds = float(value) / (1000.0 if value > 1e11 else 1.0)
        ts = datetime.fromtimestamp(seconds, tz=timezone.utc)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            try:
                ts = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return ts.astimezone(timezone.utc)
