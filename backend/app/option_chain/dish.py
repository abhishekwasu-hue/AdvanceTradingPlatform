"""MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 item 4): the Option chain "dish" - "What the chain shows", a short
summary in plain words at the top of the Option chain page, built only from the six analyses already computed for the
same chain (app/option_chain/analyses.py) and, for the IV change, the session's first stored snapshot of this expiry.

Five lines, each on its own (one missing number makes that line "I don't know ...", never a guess):
1. PCR zone - the overall PCR and the zone it sits in (the user's own edges, B4: prefs namespace `option_chain`; by
   default the OI banner's band edges, `oi_regime.PcrBands`: 0.70 / 0.90 / 1.00 / 1.30), in neutral words: which side
   holds more open interest;
2. OI walls - the strikes with the largest put and call open interest and where the spot is against them;
3. Max pain against the spot - points and %;
4. IV change - the ATM IV now against the session's first stored snapshot (same expiry);
5. Expected move - the ATM straddle as a +/- % range to expiry.

Information only: it describes the chain and never says what to trade (no buy / sell / target / stop-loss wording;
tests/test_mo4_markets.py checks every line against the platform's advice-word lists). Pure: no database, clock or
network.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.brokers.models import OptionChain
from app.option_chain import analyses as A
from app.option_chain.oi_regime import PcrBands

TITLE = "What the chain shows"
NOTE = "Information only: what the chain's numbers show right now. It never says what to trade."

# (upper edge, zone name, plain words); the last zone has no upper edge.
_B = PcrBands()
# B4 (UI-H bug lane): the DEFAULT edges - each user's own edges live in his settings (prefs namespace `option_chain`,
# app/me/prefs.py PcrZonesPrefs, the same values by default) and are passed in as `edges`.
DEFAULT_PCR_EDGES: Tuple[float, float, float, float] = (_B.oversold_below, _B.mild_bearish_below, _B.sideways_max, _B.mild_bullish_max)
_ZONE_WORDS: Tuple[Tuple[str, str], ...] = (
    ("very low", "call open interest is far larger than put open interest"),
    ("low", "call open interest is larger than put open interest"),
    ("balanced", "put and call open interest are close to each other"),
    ("high", "put open interest is larger than call open interest"),
    ("very high", "put open interest is far larger than call open interest"),
)


def pcr_zones(edges: Optional[Sequence[float]] = None) -> Tuple[Tuple[Optional[float], str, str], ...]:
    """The five zones for these four ascending edges (None: the defaults)."""
    four: List[Optional[float]] = []
    four.extend(float(e) for e in (edges if edges is not None else DEFAULT_PCR_EDGES))
    four = four[:4] + [None]
    return tuple((upper, name, words) for upper, (name, words) in zip(four, _ZONE_WORDS))


PCR_ZONES: Tuple[Tuple[Optional[float], str, str], ...] = pcr_zones()


def pcr_zone(pcr: Optional[float], edges: Optional[Sequence[float]] = None) -> Optional[Tuple[str, str, str]]:
    """(zone name, its range in words, plain words) for a PCR, or None. Edges as the OI banner's bands: below the first
    edge, below the second, up to and including the third and fourth, above the fourth. `edges`: the user's own."""
    if pcr is None:
        return None
    lower: Optional[float] = None
    for i, (upper, name, words) in enumerate(pcr_zones(edges)):
        inclusive = i >= 2
        if upper is None or (pcr <= upper if inclusive else pcr < upper):
            if lower is None:
                span = f"under {upper:.2f}" if upper is not None else ""
            elif upper is None:
                span = f"above {lower:.2f}"
            else:
                span = f"{lower:.2f} to {upper:.2f}"
            return name, span, words
        lower = upper
    return None  # pragma: no cover - the last zone has no upper edge


def _values(by: Dict[str, Dict[str, Any]], key: str) -> Dict[str, Any]:
    a = by.get(key) or {}
    return a.get("values") or {} if a.get("state") == "ok" else {}


def _point(key: str, label: str, text: str, known: bool) -> Dict[str, Any]:
    return {"key": key, "label": label, "text": text, "state": "ok" if known else "unknown"}


def _pcr_line(by: Dict[str, Dict[str, Any]], edges: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    overall = _values(by, "pcr").get("overall")
    zone = pcr_zone(overall, edges)
    if overall is None or zone is None:
        return _point("pcr_zone", "PCR zone", "I don't know the PCR yet: the chain has no call open interest.", False)
    name, span, words = zone
    return _point("pcr_zone", "PCR zone", f"PCR is {overall:.2f}, in the {name} zone ({span}): {words}.", True)


def _walls_line(by: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    v = _values(by, "support_resistance")
    put, call, spot = v.get("support"), v.get("resistance"), v.get("spot")
    if put is None and call is None:
        return _point("oi_walls", "OI walls", "I don't know the OI walls yet: no strike has open interest.", False)
    parts: List[str] = []
    if put is not None:
        parts.append(f"the largest put open interest is at {A.fmt_strike(put)}")
    if call is not None:
        parts.append(f"the largest call open interest is at {A.fmt_strike(call)}")
    text = " and ".join(parts)
    text = text[0].upper() + text[1:]
    if spot is not None:
        s = A.fmt_number(spot, 2)
        if put is not None and call is not None and put <= spot <= call:
            text += f"; the spot {s} is between them"
        elif call is not None and spot > call:
            text += f"; the spot {s} is above the call wall"
        elif put is not None and spot < put:
            text += f"; the spot {s} is below the put wall"
        else:
            text += f"; the spot is {s}"
    return _point("oi_walls", "OI walls", text + ".", True)


def _max_pain_line(by: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    v = _values(by, "max_pain")
    strike, spot, distance, pct = v.get("max_pain"), v.get("spot"), v.get("distance"), v.get("distance_pct")
    if strike is None:
        return _point("max_pain", "Max pain", "I don't know max pain yet: the chain has no open interest.", False)
    if spot is None or distance is None:
        return _point("max_pain", "Max pain", f"Max pain is {A.fmt_strike(strike)}; the spot is not known, so the distance is not shown.", True)
    if distance == 0:
        return _point("max_pain", "Max pain", f"Max pain is {A.fmt_strike(strike)}, exactly at the spot.", True)
    side = "above" if distance > 0 else "below"
    share = f" ({abs(pct):.2f}%)" if pct is not None else ""
    return _point("max_pain", "Max pain",
                  f"Max pain is {A.fmt_strike(strike)}, {A.fmt_number(abs(distance), 2)} points{share} {side} the spot {A.fmt_number(spot, 2)}.", True)


def first_atm_iv(chain: OptionChain, history: Sequence[A.Snapshot]) -> Tuple[Optional[float], Optional[datetime]]:
    """The ATM IV of the session's first stored snapshot of this chain's expiry, and when it was read."""
    past = A.session_history(chain, history)
    if not past:
        return None, None
    first = past[0]
    try:
        value = A.iv_skew(A.clean_spot(first.chain), [], first.at)["values"].get("atm_iv")
    except Exception:  # noqa: BLE001 - an odd stored snapshot only means "no comparison"
        value = None
    return value, first.at


def _iv_line(by: Dict[str, Dict[str, Any]], chain: OptionChain, history: Sequence[A.Snapshot], history_off: bool) -> Dict[str, Any]:
    now_iv = _values(by, "iv_skew").get("atm_iv")
    if now_iv is None:
        return _point("iv_change", "IV change", "I don't know the ATM IV: the broker quoted none and the prices could not be read as one.", False)
    head = f"ATM IV is {now_iv:.2f}%"
    if history_off:
        return _point("iv_change", "IV change", f"{head}; its change through the session is not shown while your own-broker data setting is on.", True)
    then, at = first_atm_iv(chain, history)
    if then is None or at is None:
        return _point("iv_change", "IV change", f"{head}; its change shows once an earlier snapshot of this session is stored.", True)
    diff = round(now_iv - then, 2)
    if diff == 0:
        return _point("iv_change", "IV change", f"{head}, unchanged since {A.fmt_time(at)} IST.", True)
    return _point("iv_change", "IV change",
                  f"{head}, {'up' if diff > 0 else 'down'} {abs(diff):.2f} points since {A.fmt_time(at)} IST ({then:.2f}% then).", True)


def _move_line(by: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    v = _values(by, "expected_move")
    pct, lower, upper = v.get("move_pct"), v.get("lower"), v.get("upper")
    if pct is None or lower is None or upper is None:
        return _point("expected_move", "Expected move", "I don't know the expected move: the ATM call or put has no price.", False)
    dte = v.get("dte")
    tail = f", with {dte} trading {'day' if dte == 1 else 'days'} to expiry" if isinstance(dte, int) and dte >= 0 else ""  # B3
    return _point("expected_move", "Expected move",
                  f"The ATM straddle prices a move of about {pct:.2f}% either way by expiry: {A.fmt_number(lower, 2)} to {A.fmt_number(upper, 2)}{tail}.",
                  True)


def build(results: Sequence[Dict[str, Any]], chain: OptionChain, history: Sequence[A.Snapshot], *, history_off: bool = False,
          pcr_edges: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """The summary from the six analyses of `chain` (A.compute_all's list) and the session's earlier snapshots.
    `pcr_edges`: the user's own PCR zone edges (B4), None for the defaults."""
    by: Dict[str, Dict[str, Any]] = {str(r.get("key")): r for r in results if isinstance(r, dict)}
    chain = A.clean_spot(chain)
    points = [_pcr_line(by, pcr_edges), _walls_line(by), _max_pain_line(by), _iv_line(by, chain, history, history_off), _move_line(by)]
    return {"title": TITLE, "points": points, "note": NOTE}


__all__ = ["TITLE", "NOTE", "PCR_ZONES", "DEFAULT_PCR_EDGES", "pcr_zones", "pcr_zone", "first_atm_iv", "build"]
