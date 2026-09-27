"""A synthetic Upstox-style instrument master for the F&O tests: NIFTY weekly/monthly options,
BANKNIFTY monthly options, NIFTY futures, RELIANCE equity and options, NIFTY 50 / NIFTY BANK
indices. Shapes follow Upstox's public JSON (expiry as epoch milliseconds, `trading_symbol`,
`strike_price`, `underlying_symbol`, `segment`, `weekly`)."""
import gzip
import json
from datetime import date, datetime, time, timezone
from typing import Dict, List

NIFTY_EXPIRIES = [date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 15), date(2026, 10, 29)]  # last = monthly
BANKNIFTY_EXPIRIES = [date(2026, 10, 29), date(2026, 11, 26)]
NIFTY_LOT = 75
BANKNIFTY_LOT = 35
RELIANCE_LOT = 500


def _ms(d: date) -> int:
    return int(datetime.combine(d, time(0, 0), tzinfo=timezone.utc).timestamp() * 1000)


def _option(underlying: str, expiry: date, strike: float, right: str, lot: int, key: int, weekly: bool, exch="NSE") -> Dict:
    return {
        "segment": f"{exch}_FO", "name": underlying, "exchange": exch, "expiry": _ms(expiry), "instrument_type": right,
        "underlying_symbol": underlying, "instrument_key": f"{exch}_FO|{key}", "lot_size": lot, "tick_size": 0.05,
        "trading_symbol": f"{underlying} {int(strike)} {right} {expiry.strftime('%d %b %y').upper()}",
        "strike_price": strike, "weekly": weekly,
    }


def build_master() -> List[Dict]:
    rows: List[Dict] = [
        {"segment": "NSE_INDEX", "name": "Nifty 50", "exchange": "NSE", "instrument_type": "INDEX",
         "instrument_key": "NSE_INDEX|Nifty 50", "trading_symbol": "NIFTY 50", "lot_size": 1, "tick_size": 0.05},
        {"segment": "NSE_INDEX", "name": "Nifty Bank", "exchange": "NSE", "instrument_type": "INDEX",
         "instrument_key": "NSE_INDEX|Nifty Bank", "trading_symbol": "NIFTY BANK", "lot_size": 1, "tick_size": 0.05},
        {"segment": "NSE_EQ", "name": "RELIANCE INDUSTRIES LTD", "exchange": "NSE", "instrument_type": "EQ",
         "instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE", "lot_size": 1, "tick_size": 0.05, "strike_price": 0},
    ]
    key = 1000
    for expiry in NIFTY_EXPIRIES:
        weekly = expiry != NIFTY_EXPIRIES[-1]
        for strike in range(24000, 25001, 50):
            for right in ("CE", "PE"):
                key += 1
                rows.append(_option("NIFTY", expiry, float(strike), right, NIFTY_LOT, key, weekly))
        key += 1
        rows.append({
            "segment": "NSE_FO", "name": "NIFTY", "exchange": "NSE", "expiry": _ms(expiry), "instrument_type": "FUT",
            "underlying_symbol": "NIFTY", "instrument_key": f"NSE_FO|{key}", "lot_size": NIFTY_LOT, "tick_size": 0.05,
            "trading_symbol": f"NIFTY FUT {expiry.strftime('%d %b %y').upper()}", "strike_price": 0, "weekly": False,
        }) if not weekly else None
    rows = [r for r in rows if r is not None]
    for expiry in BANKNIFTY_EXPIRIES:
        for strike in range(52000, 53001, 100):
            for right in ("CE", "PE"):
                key += 1
                rows.append(_option("BANKNIFTY", expiry, float(strike), right, BANKNIFTY_LOT, key, False))
    for strike in range(1400, 1501, 20):
        for right in ("CE", "PE"):
            key += 1
            rows.append(_option("RELIANCE", date(2026, 10, 29), float(strike), right, RELIANCE_LOT, key, False))
    return rows


def master_gzip() -> bytes:
    return gzip.compress(json.dumps(build_master()).encode())
