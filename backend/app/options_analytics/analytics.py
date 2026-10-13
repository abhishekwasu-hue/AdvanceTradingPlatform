"""N7 I (first slice): options analytics from recorded option-chain quotes, point in time - pure functions.

Input: rows shaped like `OptionChainSnapshotRow` (`Row` below; the chain recorder's captures and an organisation's own
uploads) with `iv` in PERCENT (14.2) - the stored unit, declared per source in `routes.IV_UNIT`; nothing here guesses. Everything is computed
AS OF an instant `at`: only rows captured at or before `at` are read, so a value at `at` never changes when later
captures arrive (tested). For each contract the latest quote within `max_age` of `at` counts.

* the ATM strike: nearest to the underlying's price on the quotes (`underlying_ltp`, else put-call parity at the strike
  where |CE - PE| is smallest);
* ATM IV per expiry (mean of the ATM call's and put's IV; percent in, fraction out), the term structure (DTE, ATM IV);
* the strikes each expiry's figures rest on (count, lowest, highest): PCR and max pain cover the strikes quoted, not
  necessarily the whole chain (the recorder keeps a window around the money);
* skew of an expiry: IV by strike (calls above, puts below the money) and a 5 %-moneyness risk reversal (put IV - call IV);
* expected move to an expiry from the ATM straddle (CE + PE premium; and as a % of spot);
* PCR (put / call open interest) and max pain (the strike where option writers pay least at expiry) per expiry;
* IV rank / IV percentile: the front expiry's ATM IV at each past session's last capture over `lookback_days`, compared
  with today's (rank = (now - min) / (max - min); percentile = share of sessions below now) - None, with the reason,
  under MIN_HISTORY sessions.
Information only - not a recommendation; no order path.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Protocol, Sequence, Tuple

from app.market_data.calendar import IST

MAX_AGE = timedelta(minutes=30)
MIN_HISTORY = 20                   # sessions of ATM IV before IV rank / percentile are shown
LOOKBACK_DAYS = 365
RR_MONEYNESS = 0.05


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class RowLike(Protocol):
    """A quoted option at one moment (`OptionChainSnapshotRow` or `Row`); `iv` in percent."""
    @property
    def timestamp(self) -> datetime: ...
    @property
    def expiry(self) -> date: ...
    @property
    def strike(self) -> float: ...
    @property
    def right(self) -> str: ...
    @property
    def ltp(self) -> float: ...
    @property
    def iv(self) -> Optional[float]: ...
    @property
    def oi(self) -> Optional[float]: ...
    @property
    def underlying_ltp(self) -> Optional[float]: ...


class Row(NamedTuple):
    """The route's light row (a tuple, not an ORM object or a validated model)."""
    timestamp: datetime
    expiry: date
    strike: float
    right: str
    ltp: float
    iv: Optional[float]
    oi: Optional[float]
    underlying_ltp: Optional[float]


def _iv(value: Optional[float]) -> Optional[float]:
    """Percent (14.2) in -> fraction (0.142) out; None / non-positive -> None. The unit is declared, never guessed (a 2.5 %
    IV is 0.025)."""
    if value is None:
        return None
    v = float(value)
    return v / 100.0 if v > 0 else None


@dataclass(frozen=True)
class Quote:
    expiry: date
    strike: float
    right: str
    ltp: float
    iv: Optional[float]
    oi: Optional[float]
    underlying: Optional[float]
    at: datetime


def snapshot(rows: Iterable[RowLike], at: datetime, max_age: timedelta = MAX_AGE) -> Dict[Tuple[date, float, str], Quote]:
    """The latest quote of each contract captured in [at - max_age, at] - never a row after `at`."""
    at = _utc(at)
    out: Dict[Tuple[date, float, str], Quote] = {}
    for r in rows:
        ts = _utc(r.timestamp)
        if ts > at or ts < at - max_age:
            continue
        key = (r.expiry, float(r.strike), r.right.upper())
        if key not in out or out[key].at <= ts:
            out[key] = Quote(r.expiry, float(r.strike), r.right.upper(), float(r.ltp), _iv(r.iv), r.oi, r.underlying_ltp, ts)
    return out


def spot_of(snap: Dict[Tuple[date, float, str], Quote]) -> Optional[float]:
    """The underlying's price on the quotes, else put-call parity at the strike where |CE - PE| is smallest."""
    latest = sorted((q for q in snap.values() if q.underlying), key=lambda q: q.at)
    if latest:
        return float(latest[-1].underlying)  # type: ignore[arg-type]
    best: Optional[Tuple[float, float]] = None
    for (e, k, right), q in snap.items():
        if right != "CE" or (e, k, "PE") not in snap:
            continue
        gap = q.ltp - snap[(e, k, "PE")].ltp
        if best is None or abs(gap) < abs(best[1]):
            best = (k, gap)
    return None if best is None else best[0] + best[1]


def expiries(snap: Dict[Tuple[date, float, str], Quote], today: date) -> List[date]:
    return sorted({e for e, _, _ in snap if e >= today})


def atm_strike(snap: Dict[Tuple[date, float, str], Quote], expiry: date, spot: float) -> Optional[float]:
    strikes = sorted({k for e, k, _ in snap if e == expiry and (e, k, "CE") in snap and (e, k, "PE") in snap})
    return min(strikes, key=lambda k: (abs(k - spot), k)) if strikes else None


def atm_iv(snap: Dict[Tuple[date, float, str], Quote], expiry: date, spot: float) -> Optional[float]:
    k = atm_strike(snap, expiry, spot)
    if k is None:
        return None
    ivs = [q.iv for q in (snap[(expiry, k, "CE")], snap[(expiry, k, "PE")]) if q.iv is not None]
    return round(sum(ivs) / len(ivs), 6) if ivs else None


def expected_move(snap: Dict[Tuple[date, float, str], Quote], expiry: date, spot: float) -> Optional[Dict[str, float]]:
    k = atm_strike(snap, expiry, spot)
    if k is None:
        return None
    straddle = snap[(expiry, k, "CE")].ltp + snap[(expiry, k, "PE")].ltp
    return {"strike": k, "straddle": round(straddle, 2), "pct": round(100.0 * straddle / spot, 4) if spot else 0.0}


def pcr(snap: Dict[Tuple[date, float, str], Quote], expiry: date) -> Optional[float]:
    ce = sum(q.oi or 0.0 for (e, _, r), q in snap.items() if e == expiry and r == "CE")
    pe = sum(q.oi or 0.0 for (e, _, r), q in snap.items() if e == expiry and r == "PE")
    return round(pe / ce, 4) if ce > 0 else None


def max_pain(snap: Dict[Tuple[date, float, str], Quote], expiry: date) -> Optional[float]:
    """The settlement strike where option writers pay least (ties: the lower strike). Prefix sums: O(strikes log strikes)."""
    call_oi: Dict[float, float] = {}
    put_oi: Dict[float, float] = {}
    for (e, k, r), q in snap.items():
        if e == expiry and (q.oi or 0) > 0:
            book = call_oi if r == "CE" else put_oi
            book[k] = book.get(k, 0.0) + float(q.oi or 0.0)
    strikes = sorted(set(call_oi) | set(put_oi))
    if not strikes:
        return None
    n = len(strikes)
    # calls pay sum_{k < s} oi_k (s - k); puts pay sum_{k > s} oi_k (k - s)
    c_oi = c_koi = 0.0
    call_paid = []
    for k in strikes:
        call_paid.append((c_oi, c_koi))
        c_oi += call_oi.get(k, 0.0)
        c_koi += call_oi.get(k, 0.0) * k
    p_oi = p_koi = 0.0
    put_paid: List[Tuple[float, float]] = [(0.0, 0.0)] * n
    for i in range(n - 1, -1, -1):
        put_paid[i] = (p_oi, p_koi)
        p_oi += put_oi.get(strikes[i], 0.0)
        p_koi += put_oi.get(strikes[i], 0.0) * strikes[i]
    best_i = min(range(n), key=lambda i: (strikes[i] * call_paid[i][0] - call_paid[i][1] + put_paid[i][1] - strikes[i] * put_paid[i][0], strikes[i]))
    return strikes[best_i]


def skew(snap: Dict[Tuple[date, float, str], Quote], expiry: date, spot: float) -> Dict[str, Any]:
    """IV by strike (out-of-the-money side: puts below spot, calls at or above) and a 5 %-moneyness risk reversal."""
    curve: List[Dict[str, Any]] = []
    for k in sorted({k for e, k, _ in snap if e == expiry}):
        q = snap.get((expiry, k, "PE" if k < spot else "CE"))
        if q is not None and q.iv is not None:
            curve.append({"strike": k, "moneyness": round(k / spot - 1.0, 6), "iv": q.iv, "right": q.right})

    def nearest(target: float, right: str) -> Optional[float]:
        pts = [(abs(float(c["strike"]) - target), float(c["strike"]), float(c["iv"])) for c in curve if c["right"] == right]
        return min(pts)[2] if pts else None                              # ties: the lower strike
    put, call = nearest(spot * (1 - RR_MONEYNESS), "PE"), nearest(spot * (1 + RR_MONEYNESS), "CE")
    return {"curve": curve, "risk_reversal": round(put - call, 6) if put is not None and call is not None else None}


def front_atm_iv(snap: Dict[Tuple[date, float, str], Quote], day: date) -> Optional[float]:
    """The ATM IV of the nearest expiry at least one day out (an expiring series' IV is not comparable)."""
    spot = spot_of(snap)
    if spot is None:
        return None
    for e in expiries(snap, day + timedelta(days=1)):
        v = atm_iv(snap, e, spot)
        if v is not None:
            return v
    return None


def iv_history(rows: Sequence[RowLike], at: datetime, lookback_days: int = LOOKBACK_DAYS) -> List[Dict[str, Any]]:
    """The front ATM IV at each IST session's last capture in [at - lookback, at] (the session of `at` up to `at`).
    One sort, then each session's snapshot reads only the rows within MAX_AGE of its last capture."""
    at = _utc(at)
    start = at - timedelta(days=lookback_days)
    timed = sorted(((_utc(r.timestamp), i) for i, r in enumerate(rows) if start - MAX_AGE <= _utc(r.timestamp) <= at), key=lambda x: x[0])
    stamps = [ts for ts, _ in timed]
    last: Dict[date, datetime] = {}
    for ts in stamps:
        if ts >= start:
            last[ts.astimezone(IST).date()] = ts                          # ascending: the last one wins
    out = []
    for d in sorted(last):
        lo, hi = bisect_left(stamps, last[d] - MAX_AGE), bisect_right(stamps, last[d])
        v = front_atm_iv(snapshot([rows[i] for _, i in timed[lo:hi]], last[d]), d)
        if v is not None:
            out.append({"day": d.isoformat(), "atm_iv": v})
    return out


def iv_rank(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(history) < MIN_HISTORY:
        return {"iv_rank": None, "iv_percentile": None, "sessions": len(history),
                "note": f"IV rank needs at least {MIN_HISTORY} sessions of recorded ATM IV ({len(history)} so far)"}
    values = [h["atm_iv"] for h in history]
    now, lo, hi = values[-1], min(values), max(values)
    return {"iv_rank": round(100.0 * (now - lo) / (hi - lo), 2) if hi > lo else 0.0,
            "iv_percentile": round(100.0 * sum(1 for v in values[:-1] if v < now) / (len(values) - 1), 2),
            "sessions": len(values), "low": lo, "high": hi, "current": now, "note": None}


def analytics(rows: Sequence[RowLike], at: datetime, *, lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    """Everything above for one underlying as of `at` (rows after `at` are never read)."""
    at = _utc(at)
    usable = [r for r in rows if _utc(r.timestamp) <= at]
    snap = snapshot(usable, at)
    today = at.astimezone(IST).date()
    spot = spot_of(snap)
    if spot is None or not snap:
        return {"at": at.isoformat(), "spot": None, "expiries": [], "note": "No recorded quotes within "
                f"{int(MAX_AGE.total_seconds() // 60)} minutes of this time"}
    by_expiry: Dict[date, Dict[Tuple[date, float, str], Quote]] = {}
    for key, q in snap.items():
        by_expiry.setdefault(key[0], {})[key] = q                       # each expiry's functions read only its own quotes
    per_expiry = []
    for e in expiries(snap, today):
        sub = by_expiry[e]
        quoted = sorted({k for _, k, _ in sub})
        per_expiry.append({"expiry": e.isoformat(), "dte": (e - today).days, "strikes": {"count": len(quoted), "low": quoted[0], "high": quoted[-1]},
                           "atm_strike": atm_strike(sub, e, spot), "atm_iv": atm_iv(sub, e, spot),
                           "expected_move": expected_move(sub, e, spot), "pcr": pcr(sub, e), "max_pain": max_pain(sub, e),
                           "skew": skew(sub, e, spot)})
    history = iv_history(usable, at, lookback_days)
    captured = max(q.at for q in snap.values())
    return {"at": at.isoformat(), "captured_at": captured.isoformat(), "spot": round(spot, 2), "expiries": per_expiry,
            "term_structure": [{"expiry": x["expiry"], "dte": x["dte"], "atm_iv": x["atm_iv"]} for x in per_expiry if x["atm_iv"] is not None],
            "iv_history": history, **{"iv": iv_rank(history)}}
