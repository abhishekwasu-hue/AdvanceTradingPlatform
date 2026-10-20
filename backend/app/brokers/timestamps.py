"""Broker timestamp parsing shared by the adapters (Phase G1).

Upstox sends ISO-8601 with an offset ("2026-09-25T10:29:58+05:30"); Kite sends an IST wall time
with no offset ("2026-09-25 10:29:58"). Both become aware UTC datetimes; anything unparseable is
None, which the staleness gate treats as "cannot judge, accept". Epoch times - numbers, or strings of digits as Upstox's
quote `last_trade_time` ("1760935500000") and Noren's `lut` are (nine digits or more) - read as milliseconds above 1e11,
else as seconds.
"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def parse_broker_timestamp(value) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) >= 9:
        # UI2-4a2-pre review: epoch seconds / ms sent as a string (9+ digits, so a compact date "20260925" stays a date)
        value = int(value.strip())
        if value == 0:
            return None
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, (int, float)):
        seconds = float(value) / (1000.0 if value > 1e11 else 1.0)
        try:
            ts = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            parsed: Optional[datetime] = None
            # Kite's wall time; Noren's (Shoonya) order-book `norentm`, "HH:MM:SS DD-MM-YYYY".
            for fmt in ("%Y-%m-%d %H:%M:%S", "%H:%M:%S %d-%m-%Y"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
            ts = parsed
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return ts.astimezone(timezone.utc)
