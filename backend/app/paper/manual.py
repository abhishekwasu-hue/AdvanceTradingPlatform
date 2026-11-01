"""Z3-b: the manual PAPER trigger (Trade repo `paper/manual.py`, Telegram `/paper`), ported to ATP's flow.

    /paper RELIANCE long SL 2380 T 2460

The message becomes an ENTRY proposal (source "manual") on the same approval flow as every other entry: buttons on
Telegram / in the app, Approve places one PAPER order at the re-quoted price (Z3-a). Validation happens before anything
is raised, as in the Trade bot: the side of the levels against the live price (long: stop below, target above; short:
the reverse), and the reward:risk from that price, with a warning below the sender's OWN minimum reward:risk when he set
one (still allowed). M68 PR-8 (TS1-080): the Trade bot's fixed 3.0 is the owner's own number - it is not imposed on
anyone here; no minimum of his own = no warning.

Differences from the Trade bot, on purpose: the underlying only (no option spreads in Phase 0), and the target is
required (ATP's exit check needs a target level on an underlying position). Places nothing itself.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def rr_warning(rr: float | None, own_min: float | None) -> str:
    """The note under a /paper reply: the reward:risk below his own minimum (a number > 0 he set), else nothing."""
    if rr is None or own_min is None or own_min <= 0 or rr >= own_min:
        return ""
    return f" Reward:risk {rr:g} is below your minimum {own_min:g} - Approve still places it."


DIRECTIONS = {"long": "LONG", "buy": "LONG", "bullish": "LONG", "short": "SHORT", "sell": "SHORT", "bearish": "SHORT"}
USAGE = "Usage: /paper <symbol> long|short SL <price> T <price> - e.g. /paper RELIANCE long SL 2380 T 2460"


def parse(text: str) -> Tuple[Optional[Dict], Optional[str]]:
    """(setup, None) or (None, what is wrong). Same grammar as the Trade bot's `parse`, with long/short for the side."""
    words = (text or "").replace(",", " ").split()
    if len(words) < 7 or words[0].split("@")[0].lower() != "/paper":
        return None, USAGE
    symbol, side = words[1].upper(), words[2].lower()
    if side not in DIRECTIONS:
        return None, f"Side '{side}' is not long or short. {USAGE}"
    try:
        if words[3].upper() != "SL":
            return None, f"SL is needed. {USAGE}"
        stop = float(words[4])
        if words[5].upper() != "T":
            return None, f"'{words[5]}' not understood. {USAGE}"
        target = float(words[6])
    except (ValueError, IndexError):
        return None, f"The prices could not be read. {USAGE}"
    if len(words) != 7:
        return None, f"Extra words. {USAGE}"
    if stop <= 0 or target <= 0:
        return None, f"Prices must be positive. {USAGE}"
    return {"symbol": symbol, "direction": DIRECTIONS[side], "stop_loss": stop, "target": target}, None


def side_errors(direction: str, price: float, stop_loss: float, target: Optional[float] = None) -> List[str]:
    """Trade `side_errors` (bull put <-> long, bear call <-> short), levels only: what is on the wrong side of the price."""
    errors = []
    if direction == "LONG":
        if not stop_loss < price:
            errors.append(f"long: SL ({stop_loss:g}) must be below the price ({price:,.2f})")
        if target is not None and not target > price:
            errors.append(f"long: T ({target:g}) must be above the price ({price:,.2f})")
    else:
        if not stop_loss > price:
            errors.append(f"short: SL ({stop_loss:g}) must be above the price ({price:,.2f})")
        if target is not None and not target < price:
            errors.append(f"short: T ({target:g}) must be below the price ({price:,.2f})")
    return errors


def reward_risk(price: float, stop_loss: float, target: Optional[float]) -> Optional[float]:
    """Trade `rr`: |T - price| / |price - SL|, two decimals; None without a target or with no risk."""
    if target is None:
        return None
    risk = abs(float(price) - float(stop_loss))
    return round(abs(float(target) - float(price)) / risk, 2) if risk > 0 else None
