"""OC2 (owner's order, PART C1): the built-in option chain analyses shown as tabs below the v2 option chain.

Six pure functions over ONE chain snapshot (the caller's own broker read) plus the session's EARLIER snapshots of the
same underlying and expiry (`oi_snapshots` / `strike_oi_snapshots`, app/option_chain/snapshots.py). Each returns one
analysis: tables, a small chart's series and 2-3 sentences built from the numbers with fixed templates (no AI). The
sentences describe the situation only - never advice and never a trade instruction (tests/test_oc2_option_chain_analyses.py checks
them against the platform's advice-word lists).

Definitions (fixed here and stated in each analysis's `method` line):
* ATM strike: the listed strike nearest the underlying price (a tie goes to the lower strike).
* Near-ATM window: the ATM strike and the 5 listed strikes on each side of it (up to 11 strikes), by position in the
  sorted strike list, so an uneven strike step is handled.
* Support / resistance: the strike with the highest put OI / highest call OI (a tie goes to the lower strike). The
  session path is computed over the strikes present in every snapshot, so a strike is never compared with a gap.
* Build-up: per strike and side, the option's last price change and OI change since the session's FIRST stored
  snapshot: price up + OI up = long build-up; price down + OI up = short build-up; price up + OI down = short covering;
  price down + OI down = long unwinding; either change exactly zero = no clear change; a missing value = not available.
* PCR: total put OI / total call OI - over every strike of the chain ("overall") and over the near-ATM window.
* Max pain: the strike K where the open options' total in-the-money value at expiry,
  sum(call OI x max(0, K - strike)) + sum(put OI x max(0, strike - K)), is smallest (a tie goes to the lower strike).
  Distance = max pain - spot, in points and in % of spot.
* Expected move: spot +/- the ATM straddle price (call + put last price at the ATM strike), with no adjustment factor.
  Under Black-Scholes an ATM straddle is about 0.8 of a one-standard-deviation move to expiry.
* IV smile / skew: call and put IV per strike over the near-ATM window: the broker's quoted IV (read as a fraction or a
  percentage) when it reprices the last price, otherwise solved from the last price; the quote alone when there is no
  last price (app/options_builder/chain.py `option_iv`). Skew = the mean, over k = 1..5,
  of (put IV at the k-th strike below ATM - call IV at the k-th strike above ATM), in volatility points; within
  +/-0.5 points it reads "about the same".

Nothing here reads a database, a clock or the network: the route passes the snapshots and the valuation time in.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.brokers.models import OptionChain, OptionChainRow
from app.core import config
from app.core.config import RISK_FREE_RATE
from app.market_data.calendar import trading_days_until
from app.option_chain import session_rows as SR
from app.option_chain.models import OptionType

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # noqa: BLE001 - tzdata missing: a fixed offset is exact for IST (no DST)
    IST = timezone(timedelta(hours=5, minutes=30))  # type: ignore[assignment]

NEAR_ATM_STRIKES = 5            # the near-ATM window: ATM and this many listed strikes either side
SKEW_PAIRS = NEAR_ATM_STRIKES   # k = 1..5 strike pairs either side of ATM
SKEW_FLAT_POINTS = 0.5          # |skew| below this many IV points reads "about the same"
TOP_N = 3                       # rows in the "highest OI" table

KEYS = ("support_resistance", "build_up", "pcr", "max_pain", "expected_move", "iv_skew")

LONG_BUILDUP = "LONG_BUILDUP"
SHORT_BUILDUP = "SHORT_BUILDUP"
SHORT_COVERING = "SHORT_COVERING"
LONG_UNWINDING = "LONG_UNWINDING"
NO_CLEAR_CHANGE = "NO_CLEAR_CHANGE"
NOT_AVAILABLE = "NOT_AVAILABLE"
BUILD_UP_LABELS: Dict[str, str] = {
    LONG_BUILDUP: "Long build-up", SHORT_BUILDUP: "Short build-up", SHORT_COVERING: "Short covering",
    LONG_UNWINDING: "Long unwinding", NO_CLEAR_CHANGE: "No clear change", NOT_AVAILABLE: "Not available",
}
_BUILD_UP_ORDER = (LONG_BUILDUP, SHORT_BUILDUP, SHORT_COVERING, LONG_UNWINDING, NO_CLEAR_CHANGE, NOT_AVAILABLE)

NO_HISTORY = "No earlier snapshot of this session is stored for this expiry yet, so the path through the session starts now."
# With the own-broker data setting on, the route passes no earlier snapshots on purpose (they come from a shared collector).
HISTORY_OFF = ("Earlier snapshots of the session are not used while your own-broker data setting is on, so the path through "
               "the session is not shown.")
BU_HISTORY_OFF = ("Build-up compares each strike with an earlier snapshot of this session, and earlier snapshots are not used "
                  "while your own-broker data setting is on.")
UNREADABLE = "This analysis could not be worked out from this chain."


@dataclass(frozen=True)
class Snapshot:
    """One earlier reading of the chain in this session: when it was read and the chain as stored."""
    at: datetime
    chain: OptionChain


# ---------------------------------------------------------------- formatting (sentences only; tables carry raw numbers)

def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: List[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def fmt_number(value: float, decimals: int = 0) -> str:
    """Indian digit grouping: 1250000 -> "12,50,000"; negatives get a hyphen-minus (plain ASCII text)."""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    sign = "-" if value < 0 and float(text) != 0 else ""
    return f"{sign}{_group_indian(whole)}{'.' + frac if frac else ''}"


def fmt_signed(value: float, decimals: int = 0) -> str:
    plain = fmt_number(value, decimals)
    return f"+{plain}" if value > 0 and float(f"{abs(value):.{decimals}f}") != 0 else plain


def fmt_strike(strike: float) -> str:
    """A strike as the exchange writes it: 22000, 122.5 (no grouping)."""
    return f"{strike:.2f}".rstrip("0").rstrip(".")


def fmt_time(at: datetime) -> str:
    aware = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return aware.astimezone(IST).strftime("%H:%M")


def _iso(at: datetime) -> str:
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


# ---------------------------------------------------------------- the strike window

def sorted_rows(chain: OptionChain) -> List[OptionChainRow]:
    return sorted(chain.rows, key=lambda r: r.strike)


def atm_index(rows: Sequence[OptionChainRow], spot: Optional[float]) -> Optional[int]:
    """Index (in `rows`, sorted by strike) of the strike nearest `spot`; a tie goes to the lower strike."""
    if not rows or spot is None:
        return None
    return min(range(len(rows)), key=lambda i: (abs(rows[i].strike - spot), rows[i].strike))


def near_atm(rows: Sequence[OptionChainRow], spot: Optional[float], each_side: int = NEAR_ATM_STRIKES) -> List[OptionChainRow]:
    """The ATM strike and `each_side` listed strikes either side (fewer at the chain's ends); [] without a spot."""
    i = atm_index(rows, spot)
    if i is None:
        return []
    return list(rows[max(0, i - each_side): i + each_side + 1])


def _oi(value: Optional[float]) -> float:
    return float(value or 0.0)


def _argmax_strike(rows: Sequence[OptionChainRow], side: str) -> Optional[OptionChainRow]:
    """The row with the highest OI on `side` ("call" / "put"); a tie goes to the lower strike; None when all are 0."""
    best: Optional[OptionChainRow] = None
    for r in sorted(rows, key=lambda r: r.strike):
        value = _oi(getattr(r, f"{side}_oi"))
        if value > 0 and (best is None or value > _oi(getattr(best, f"{side}_oi"))):
            best = r
    return best


def _same_expiry(a: str, b: str) -> bool:
    return (a or "")[:10] == (b or "")[:10]


def _usable_spot(spot: Optional[float]) -> bool:
    return isinstance(spot, (int, float)) and math.isfinite(spot) and spot > 0


def session_history(chain: OptionChain, history: Sequence[Snapshot]) -> List[Snapshot]:
    """The earlier snapshots that belong to this chain: same expiry, with a spot and strikes, oldest first."""
    kept = [s for s in history if _same_expiry(s.chain.expiry, chain.expiry) and _usable_spot(s.chain.underlying_ltp) and s.chain.rows]
    return sorted(kept, key=lambda s: s.at)


def _column(key: str, label: str, kind: str) -> Dict[str, str]:
    return {"key": key, "label": label, "kind": kind}


def _table(title: str, columns: List[Dict[str, str]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"title": title, "columns": columns, "rows": rows}


def _series(key: str, label: str, tone: str, points: List[Tuple[Any, Optional[float]]]) -> Dict[str, Any]:
    return {"key": key, "label": label, "tone": tone, "points": [{"x": x, "y": y} for x, y in points]}


def _chart(kind: str, x_kind: str, x_label: str, y_label: str, series: List[Dict[str, Any]],
           marker: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"type": kind, "x_kind": x_kind, "x_label": x_label, "y_label": y_label, "series": series, "marker": marker}


def _result(key: str, title: str, method: str, *, tables: Optional[List[Dict[str, Any]]] = None, chart: Optional[Dict[str, Any]] = None,
            sentences: Sequence[str] = (), values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"key": key, "title": title, "state": "ok", "method": method, "tables": tables or [], "chart": chart,
            "sentences": list(sentences), "values": values or {}}


def _no_data(key: str, title: str, method: str, *sentences: str) -> Dict[str, Any]:
    return {"key": key, "title": title, "state": "no_data", "method": method, "tables": [], "chart": None,
            "sentences": list(sentences), "values": {}}


def _spot_marker(spot: Optional[float]) -> Optional[Dict[str, Any]]:
    return {"x": spot, "label": "Spot"} if spot is not None else None


def _points_word(value: float) -> str:
    return "point" if abs(value) == 1 else "points"


# ---------------------------------------------------------------- 1. support / resistance from OI

SR_TITLE = "Support / Resistance from OI"
SR_METHOD = ("Support is the strike with the highest put OI and resistance the strike with the highest call OI (a tie goes to the lower "
             "strike), over every strike of the chain now. The session path uses only the strikes stored in every snapshot, so its "
             "highest strikes can differ from the whole chain's.")


def support_resistance(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False) -> Dict[str, Any]:
    rows = sorted_rows(chain)
    spot = chain.underlying_ltp
    support, resistance = _argmax_strike(rows, "put"), _argmax_strike(rows, "call")
    if support is None and resistance is None:
        return _no_data("support_resistance", SR_TITLE, SR_METHOD, "The chain has no open interest yet, so no strike stands out.")
    top_put = sorted([r for r in rows if _oi(r.put_oi) > 0], key=lambda r: (-_oi(r.put_oi), r.strike))[:TOP_N]
    top_call = sorted([r for r in rows if _oi(r.call_oi) > 0], key=lambda r: (-_oi(r.call_oi), r.strike))[:TOP_N]
    now_rows = [{"rank": i + 1,
                 "put_strike": top_put[i].strike if i < len(top_put) else None, "put_oi": _oi(top_put[i].put_oi) if i < len(top_put) else None,
                 "call_strike": top_call[i].strike if i < len(top_call) else None, "call_oi": _oi(top_call[i].call_oi) if i < len(top_call) else None}
                for i in range(max(len(top_put), len(top_call)))]
    tables = [_table("Highest OI now", [_column("rank", "Rank", "number"), _column("put_strike", "Put strike", "strike"),
                                        _column("put_oi", "Put OI", "oi"), _column("call_strike", "Call strike", "strike"),
                                        _column("call_oi", "Call OI", "oi")], now_rows)]

    sentences: List[str] = []
    if support is not None and resistance is not None:
        sentences.append(f"The highest put OI is at {fmt_strike(support.strike)} ({fmt_number(_oi(support.put_oi))}) and the highest call OI "
                         f"is at {fmt_strike(resistance.strike)} ({fmt_number(_oi(resistance.call_oi))}).")
    elif support is not None:
        sentences.append(f"The highest put OI is at {fmt_strike(support.strike)} ({fmt_number(_oi(support.put_oi))}); no call strike has open interest.")
    elif resistance is not None:
        sentences.append(f"The highest call OI is at {fmt_strike(resistance.strike)} ({fmt_number(_oi(resistance.call_oi))}); no put strike has open interest.")
    if spot is not None:
        sentences.append(_spot_between(spot, support.strike if support else None, resistance.strike if resistance else None))

    past = session_history(chain, history)
    if past:
        common = set(r.strike for r in rows)
        for s in past:
            common &= {r.strike for r in s.chain.rows}
        path: List[Dict[str, Any]] = []
        for at, c in [(s.at, s.chain) for s in past] + [(now, chain)]:
            window = [r for r in c.rows if r.strike in common]
            sup, res = _argmax_strike(window, "put"), _argmax_strike(window, "call")
            path.append({"time": _iso(at), "spot": _r(c.underlying_ltp), "support": sup.strike if sup else None,
                         "support_oi": _oi(sup.put_oi) if sup else None, "resistance": res.strike if res else None,
                         "resistance_oi": _oi(res.call_oi) if res else None})
        tables.append(_table(f"Through the session ({len(common)} strikes in every snapshot)",
                             [_column("time", "Time", "time"), _column("spot", "Spot", "price"), _column("support", "Highest put OI", "strike"),
                              _column("support_oi", "Put OI", "oi"), _column("resistance", "Highest call OI", "strike"),
                              _column("resistance_oi", "Call OI", "oi")], path))
        # The path is read over the strikes stored in every snapshot (the collector keeps a window around the money), which
        # may leave out the whole chain's highest strike above - so the sentence names that set rather than contradict it.
        sentences.append(f"Among the {len(common)} {'strike' if len(common) == 1 else 'strikes'} stored in every snapshot since "
                         f"{fmt_time(past[0].at)} IST, {_shift('the highest put OI strike', path[0]['support'], path[-1]['support'])} "
                         f"and {_shift('the highest call OI strike', path[0]['resistance'], path[-1]['resistance'])}.")
    else:
        sentences.append(HISTORY_OFF if history_off else NO_HISTORY)

    chart_rows = sorted({r.strike: r for r in near_atm(rows, spot) + [x for x in (support, resistance) if x is not None]}.values(),
                        key=lambda r: r.strike) if spot is not None else rows
    chart = _chart("bar", "strike", "Strike", "Open interest", [
        _series("put_oi", "Put OI", "up", [(r.strike, _oi(r.put_oi)) for r in chart_rows]),
        _series("call_oi", "Call OI", "down", [(r.strike, _oi(r.call_oi)) for r in chart_rows]),
    ], _spot_marker(spot))
    values = {"support": support.strike if support else None, "resistance": resistance.strike if resistance else None, "spot": spot}
    return _result("support_resistance", SR_TITLE, SR_METHOD, tables=tables, chart=chart, sentences=sentences[:3], values=values)


def _spot_between(spot: float, support: Optional[float], resistance: Optional[float]) -> str:
    s = fmt_number(spot, 2)
    if support is not None and resistance is not None and support <= spot <= resistance:
        return (f"Spot {s} lies between them, {fmt_number(spot - support, 2)} points above the put strike and "
                f"{fmt_number(resistance - spot, 2)} points below the call strike.")
    parts = []
    if support is not None:
        parts.append(f"{fmt_number(abs(spot - support), 2)} points {'above' if spot >= support else 'below'} the highest put OI strike")
    if resistance is not None:
        parts.append(f"{fmt_number(abs(resistance - spot), 2)} points {'below' if spot <= resistance else 'above'} the highest call OI strike")
    return f"Spot {s} is " + " and ".join(parts) + "."


def _shift(what: str, first: Optional[float], last: Optional[float]) -> str:
    if first is None or last is None:
        return f"{what} is not available at every point"
    if first == last:
        return f"{what} stayed at {fmt_strike(last)}"
    return f"{what} moved from {fmt_strike(first)} to {fmt_strike(last)}"


# ---------------------------------------------------------------- 2. OI build-up per strike

BU_TITLE = "OI build-up per strike"
BU_METHOD = ("Each option's last price change and OI change since the session's first stored snapshot: price up and OI up is long "
             "build-up, price down and OI up is short build-up, price up and OI down is short covering, price down and OI down is "
             "long unwinding; a change of exactly zero is no clear change. Strikes: ATM and 5 strikes either side.")


def classify_build_up(price_change: Optional[float], oi_change: Optional[float]) -> str:
    """The standard four-way reading of price change and OI change (exactly zero on either = no clear change)."""
    if price_change is None or oi_change is None:
        return NOT_AVAILABLE
    if price_change == 0 or oi_change == 0:
        return NO_CLEAR_CHANGE
    if price_change > 0:
        return LONG_BUILDUP if oi_change > 0 else SHORT_COVERING
    return SHORT_BUILDUP if oi_change > 0 else LONG_UNWINDING


def _diff(now: Optional[float], then: Optional[float]) -> Optional[float]:
    return None if now is None or then is None else float(now) - float(then)


def build_up(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False) -> Dict[str, Any]:
    rows = sorted_rows(chain)
    window = near_atm(rows, chain.underlying_ltp)
    if not window:
        return _no_data("build_up", BU_TITLE, BU_METHOD, "The chain has no underlying price, so the strikes around the money cannot be found.")
    past = session_history(chain, history)
    if not past and history_off:
        return _no_data("build_up", BU_TITLE, BU_METHOD, BU_HISTORY_OFF)
    if not past:
        return _no_data("build_up", BU_TITLE, BU_METHOD,
                        "Build-up compares each strike with an earlier snapshot of this session, and none is stored for this expiry yet.",
                        "It appears once the session has at least one earlier snapshot.")
    ref = past[0]
    before = {r.strike: r for r in ref.chain.rows}
    table_rows: List[Dict[str, Any]] = []
    for r in window:
        b = before.get(r.strike)
        item: Dict[str, Any] = {"strike": r.strike}
        for side in ("call", "put"):
            d_price = _diff(getattr(r, f"{side}_ltp"), getattr(b, f"{side}_ltp")) if b else None
            d_oi = _diff(getattr(r, f"{side}_oi"), getattr(b, f"{side}_oi")) if b else None
            label = classify_build_up(d_price, d_oi)
            item.update({f"{side}_price_change": _r(d_price), f"{side}_oi_change": _r(d_oi, 0), f"{side}_class": label,
                         f"{side}_label": BUILD_UP_LABELS[label]})
        table_rows.append(item)
    columns = [_column("strike", "Strike", "strike"),
               _column("call_price_change", "Call price chg", "change"), _column("call_oi_change", "Call OI chg", "oi_change"),
               _column("call_label", "Call reading", "text"),
               _column("put_price_change", "Put price chg", "change"), _column("put_oi_change", "Put OI chg", "oi_change"),
               _column("put_label", "Put reading", "text")]
    since = fmt_time(ref.at)
    n = len(table_rows)
    # Past tense on purpose: these lines describe what changed since the first snapshot. ("short" is a trade verb to
    # the platform's specific-call detector unless the sentence reads as a description of the past.)
    sentences = [
        f"Since the first snapshot at {since} IST, the call readings at the {n} strikes around the money were {_tally(table_rows, 'call')}.",
        f"The put readings were {_tally(table_rows, 'put')}.",
    ]
    largest = [_largest(table_rows, side) for side in ("call", "put")]
    parts = [f"the largest {side} OI change was {fmt_signed(row[f'{side}_oi_change'])} at {fmt_strike(row['strike'])} "
             f"({row[f'{side}_label'].lower()})" for side, row in zip(("call", "put"), largest) if row is not None]
    if parts:
        text = " and ".join(parts)
        sentences.append(text[0].upper() + text[1:] + ".")
    chart = _chart("bar", "strike", "Strike", "OI change", [
        _series("put_oi_change", "Put OI change", "up", [(x["strike"], x["put_oi_change"]) for x in table_rows]),
        _series("call_oi_change", "Call OI change", "down", [(x["strike"], x["call_oi_change"]) for x in table_rows]),
    ], _spot_marker(chain.underlying_ltp))
    return _result("build_up", BU_TITLE, BU_METHOD, tables=[_table(f"Since {since} IST", columns, table_rows)], chart=chart,
                   sentences=sentences, values={"reference_time": _iso(ref.at)})


def _tally(rows: Sequence[Dict[str, Any]], side: str) -> str:
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r[f"{side}_class"]] = counts.get(r[f"{side}_class"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], _BUILD_UP_ORDER.index(kv[0])))
    parts = [f"{BUILD_UP_LABELS[k].lower()} at {v} {'strike' if v == 1 else 'strikes'}" for k, v in ordered]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _largest(rows: Sequence[Dict[str, Any]], side: str) -> Optional[Dict[str, Any]]:
    """The strike with the largest absolute OI change on `side` (a tie goes to the lower strike); None when all are 0."""
    best: Optional[Dict[str, Any]] = None
    for r in rows:
        v = r[f"{side}_oi_change"]
        if v and (best is None or abs(v) > abs(best[f"{side}_oi_change"])):
            best = r
    return best


# ---------------------------------------------------------------- 3. PCR

PCR_TITLE = "PCR"
PCR_METHOD = ("PCR is total put OI divided by total call OI: overall over every strike of the chain, and near the money over the ATM "
              "strike and the 5 strikes on each side of it. The session path is the near-ATM PCR at each reading that changed it; "
              "only readings between the open and the close of a trading day count, and a reading with fewer strikes than the "
              "session's usual count (partial) is drawn hollow and left out of the comparison.")
PCR_NEAR_HELP = ("Near ATM: the ATM strike (the listed strike nearest the spot) and the {n} listed strikes on each side of it - "
                 "here {window}.")


def pcr_of(rows: Sequence[OptionChainRow]) -> tuple[float, float, float | None]:
    """(put OI, call OI, put / call) - the ratio is None when call OI is 0."""
    puts, calls = sum(_oi(r.put_oi) for r in rows), sum(_oi(r.call_oi) for r in rows)
    return puts, calls, (puts / calls if calls else None)


def fmt_count(value: float) -> str:
    """M61 OI-1: an OI figure as the app shows it - under 1 lakh in Indian grouping ("85,000"), then "12.35 L", then
    "1.24 Cr" (the frontend's formatCount; trailing zeros dropped, a figure under 1 crore never rounds up to "100 L")."""
    a = abs(float(value))
    sign = "-" if value < 0 and round(a) != 0 else ""

    def trim(text: str) -> str:
        return text.rstrip("0").rstrip(".") if "." in text else text

    if a >= 1e7:
        return f"{sign}{trim(fmt_number(a / 1e7, 2))} Cr"
    if a >= 1e5:
        return f"{sign}{trim(f'{min(round(a / 1e5, 2), 99.99):.2f}')} L"
    return f"{sign}{fmt_number(a, 0)}"


def _strike_grouped(strike: float) -> str:
    """A strike in a sentence with Indian grouping: 22,800; 122.5."""
    return fmt_number(strike, 0) if float(strike).is_integer() else fmt_strike(strike)


def _near_words(window: Sequence[OptionChainRow]) -> str:
    """"11 strikes 22,800-23,300" (plain ASCII, as every analysis text; the page shows the headline with its own dash)."""
    n = len(window)
    return f"{n} {'strike' if n == 1 else 'strikes'} {_strike_grouped(window[0].strike)}-{_strike_grouped(window[-1].strike)}"


def _one_per_change(path: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """M61 PCR-2: one row per change, not per poll - a reading is kept when its near-ATM put OI, call OI or ATM strike
    differs from the last kept reading (the first is always kept)."""
    kept: list[dict[str, Any]] = []
    for p in path:
        key = (p["put_oi"], p["call_oi"], p["atm"])
        if not kept or key != (kept[-1]["put_oi"], kept[-1]["call_oi"], kept[-1]["atm"]):
            kept.append(p)
    return kept


def pcr(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False, holidays: Iterable[date] = (),
        exchange: str = "NSE", partial_ratio: float | None = None) -> dict[str, Any]:
    ratio = config.OI_PARTIAL_SNAPSHOT_RATIO if partial_ratio is None else partial_ratio
    closed = set(holidays)
    rows = sorted_rows(chain)
    puts, calls, overall = pcr_of(rows)
    window = near_atm(rows, chain.underlying_ltp)
    n_puts, n_calls, near = pcr_of(window)
    if overall is None:
        return _no_data("pcr", PCR_TITLE, PCR_METHOD, "The chain has no call open interest, so the put-call ratio cannot be formed.")
    atm = rows[atm_index(rows, chain.underlying_ltp)].strike if window else None   # type: ignore[index]
    now_rows = [{"scope": f"Overall ({len(rows)} strikes)", "put_oi": puts, "call_oi": calls, "pcr": _r(overall)}]
    if window:
        now_rows.append({"scope": f"Near ATM ({_near_words(window)})", "put_oi": n_puts, "call_oi": n_calls, "pcr": _r(near)})
    now_table = _table("Now", [_column("scope", "Strikes", "text"), _column("put_oi", "Put OI", "oi"), _column("call_oi", "Call OI", "oi"),
                               _column("pcr", "PCR", "ratio")], now_rows)
    if window:
        now_table["help"] = PCR_NEAR_HELP.format(n=NEAR_ATM_STRIKES, window=_window_words(window, atm).removeprefix("the "))
    tables = [now_table]
    # M61 PCR-3: the headline is numbers only.
    headline = f"Overall PCR {overall:.2f}"
    if near is not None:
        headline += f" | Near-ATM PCR {near:.2f} ({_near_words(window)})"
    sentences = [headline]

    past = session_history(chain, history)
    raw: list[dict[str, Any]] = []
    for at, c in [(s.at, s.chain) for s in past] + [(now, chain)]:
        crow = sorted_rows(c)
        w = near_atm(crow, c.underlying_ltp)
        p_oi, c_oi, value = pcr_of(w)
        i = atm_index(crow, c.underlying_ltp)
        raw.append({"time": _iso(at), "phase": SR.phase(at, closed, exchange), "spot": _r(c.underlying_ltp),
                    "atm": crow[i].strike if i is not None else None, "put_oi": p_oi if w else None, "call_oi": c_oi if w else None,
                    "pcr": _r(value), "strikes": SR.strike_count(c)})
    for r, flag in zip(raw, SR.partial_flags([r["strikes"] for r in raw], [r["phase"] for r in raw], ratio)):
        r["partial"] = flag
        r["note"] = (f"Partial ({r['strikes']} strikes)" if flag else "" if r["phase"] == SR.SESSION else SR.PHASE_LABELS[r["phase"]])
    path = _one_per_change(raw)
    counted = [p for p in path if p["phase"] == SR.SESSION and not p["partial"]]
    if past:
        tables.append(_table("Through the session (near ATM)", [_column("time", "Time", "time"), _column("note", "Reading", "text"),
                                                              _column("spot", "Spot", "price"), _column("atm", "ATM", "strike"),
                                                              _column("put_oi", "Put OI", "oi"), _column("call_oi", "Call OI", "oi"),
                                                              _column("pcr", "PCR", "ratio")], path))
        first_at = datetime.fromisoformat(counted[0]["time"]) if counted else None
        if len(counted) < 2 or first_at is None:
            sentences.append("No two full session readings of the near-ATM PCR to compare yet.")
        elif counted[0]["pcr"] is None or counted[-1]["pcr"] is None:
            sentences.append(f"Since {fmt_time(first_at)} IST the near-ATM PCR was not available at every reading.")
        elif counted[0]["pcr"] == counted[-1]["pcr"]:
            sentences.append(f"Since {fmt_time(first_at)} IST the near-ATM PCR is unchanged at {counted[-1]['pcr']:.2f}.")
        else:
            sentences.append(f"Since {fmt_time(first_at)} IST the near-ATM PCR moved from {counted[0]['pcr']:.2f} to {counted[-1]['pcr']:.2f}.")
        outside = sum(1 for p in path if p["phase"] != SR.SESSION)
        partial = sum(1 for p in path if p["partial"])
        if outside or partial:
            parts = ([f"{outside} {'reading' if outside == 1 else 'readings'} outside the session"] if outside else []) + \
                    ([f"{partial} partial {'reading' if partial == 1 else 'readings'}"] if partial else [])
            sentences.append(f"Left out of the comparison: {' and '.join(parts)}.")
    else:
        sentences.append(HISTORY_OFF if history_off else NO_HISTORY)
    points = [{"x": p["time"], "y": p["pcr"], "phase": p["phase"], "hollow": p["partial"], "put_oi": p["put_oi"], "call_oi": p["call_oi"],
               "atm": p["atm"]} for p in path]
    chart = _chart("line", "time", "Time", "PCR", [{"key": "pcr_near", "label": "Near-ATM PCR", "tone": "brand", "points": points}])
    marks: list[dict[str, Any]] = []
    last_atm: float | None = None
    for p in path:
        if p["phase"] != SR.SESSION or p["atm"] is None:
            continue
        if last_atm is not None and p["atm"] != last_atm:
            marks.append({"x": p["time"], "label": f"ATM {_strike_grouped(p['atm'])}"})
        last_atm = p["atm"]
    chart["x_marks"] = marks
    return _result("pcr", PCR_TITLE, PCR_METHOD, tables=tables, chart=chart, sentences=sentences,
                   values={"overall": _r(overall, 4), "near_atm": _r(near, 4), "atm": atm, "window": [w.strike for w in window]})


def _window_words(window: Sequence[OptionChainRow], atm: Optional[float]) -> str:
    """The window as it really is, also when the chain's end clips it: "the 11 strikes from 120 to 220 around ATM 170"."""
    return (f"the {len(window)} {'strike' if len(window) == 1 else 'strikes'} from {fmt_strike(window[0].strike)} to "
            f"{fmt_strike(window[-1].strike)} around ATM {fmt_strike(atm) if atm is not None else '-'}")


def _larger(puts: float, calls: float) -> str:
    if puts > calls:
        return "put OI is larger than call OI"
    if puts < calls:
        return "call OI is larger than put OI"
    return "put OI and call OI are equal"


# ---------------------------------------------------------------- 4. max pain

MP_TITLE = "Max Pain"
MP_METHOD = ("Max pain is the strike K where the total in-the-money value of open options at expiry, call OI x max(0, K - strike) plus "
             "put OI x max(0, strike - K) summed over every strike, is smallest (a tie goes to the lower strike). Distance is max pain "
             "minus spot.")


def expiry_values(rows: Sequence[OptionChainRow]) -> List[Tuple[float, float, float]]:
    """For every listed strike K: (K, calls' in-the-money value, puts' in-the-money value) if the expiry price were K."""
    out = []
    for k in rows:
        calls = sum(_oi(r.call_oi) * max(0.0, k.strike - r.strike) for r in rows)
        puts = sum(_oi(r.put_oi) * max(0.0, r.strike - k.strike) for r in rows)
        out.append((k.strike, calls, puts))
    return out


def max_pain(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False) -> Dict[str, Any]:
    rows = sorted_rows(chain)
    if not rows or not any(_oi(r.call_oi) or _oi(r.put_oi) for r in rows):
        return _no_data("max_pain", MP_TITLE, MP_METHOD, "The chain has no open interest yet, so max pain cannot be found.")
    values = expiry_values(rows)
    best = min(range(len(values)), key=lambda i: (values[i][1] + values[i][2], values[i][0]))
    strike = values[best][0]
    lo, hi = max(0, best - NEAR_ATM_STRIKES), best + NEAR_ATM_STRIKES + 1
    table_rows = [{"strike": k, "call_value": c, "put_value": p, "total": c + p, "is_max_pain": k == strike} for k, c, p in values[lo:hi]]
    tables = [_table("Value of open options at expiry, around max pain (OI x points)",
                     [_column("strike", "Expiry at", "strike"), _column("call_value", "Calls", "oi"), _column("put_value", "Puts", "oi"),
                      _column("total", "Total", "oi")], table_rows)]
    sentences = [f"Max pain is {fmt_strike(strike)}: an expiry there leaves the smallest total in-the-money value of open options "
                 f"({fmt_number(values[best][1] + values[best][2])} OI x points) among the {len(rows)} strikes."]
    spot = chain.underlying_ltp
    distance = pct = None
    if spot:
        distance = strike - spot
        pct = distance / spot * 100
        if distance == 0:
            sentences.append(f"Spot {fmt_number(spot, 2)} is exactly at max pain.")
        else:
            sentences.append(f"Max pain is {fmt_number(abs(distance), 2)} {_points_word(distance)} ({abs(pct):.2f}% of spot) "
                             f"{'above' if distance > 0 else 'below'} spot {fmt_number(spot, 2)}.")
    chart = _chart("bar", "strike", "Expiry at", "OI x points", [_series("total", "Total value", "brand", [(r["strike"], r["total"]) for r in table_rows])],
                   _spot_marker(spot))
    return _result("max_pain", MP_TITLE, MP_METHOD, tables=tables, chart=chart, sentences=sentences,
                   values={"max_pain": strike, "spot": spot, "distance": _r(distance), "distance_pct": _r(pct)})


# ---------------------------------------------------------------- 5. expected move

EM_TITLE = "Expected move"
EM_METHOD = ("Expected move to expiry = spot plus or minus the ATM straddle price (call + put last price at the strike nearest spot), "
             "with no adjustment factor. Under Black-Scholes an ATM straddle is about 0.8 of a one-standard-deviation move, so this is "
             "a rough range that option prices imply, not a forecast.")


def straddle_of(chain: OptionChain) -> Optional[Tuple[float, float, float]]:
    """(ATM strike, call price, put price) when both prices are there and positive; None otherwise."""
    rows = sorted_rows(chain)
    i = atm_index(rows, chain.underlying_ltp)
    if i is None:
        return None
    r = rows[i]
    if not r.call_ltp or not r.put_ltp or r.call_ltp <= 0 or r.put_ltp <= 0:
        return None
    return r.strike, float(r.call_ltp), float(r.put_ltp)


def expected_move(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False,
                  holidays: Iterable[date] = ()) -> Dict[str, Any]:
    spot = chain.underlying_ltp
    found = straddle_of(chain)
    if spot is None or found is None:
        return _no_data("expected_move", EM_TITLE, EM_METHOD,
                        "The ATM call and put prices are not both available, so the straddle cannot be priced.")
    atm, c, p = found
    straddle = c + p
    lower, upper, pct = spot - straddle, spot + straddle, straddle / spot * 100
    dte = _dte(chain.expiry, now, holidays)
    tables = [_table("Now", [_column("atm", "ATM strike", "strike"), _column("call", "Call", "price"), _column("put", "Put", "price"),
                             _column("straddle", "Straddle", "price"), _column("lower", "Lower", "price"), _column("upper", "Upper", "price"),
                             _column("move_pct", "Move %", "pct")],
                      [{"atm": atm, "call": _r(c), "put": _r(p), "straddle": _r(straddle), "lower": _r(lower), "upper": _r(upper),
                        "move_pct": _r(pct)}])]
    sentences = [f"The ATM straddle at {fmt_strike(atm)} is {fmt_number(straddle, 2)} (call {fmt_number(c, 2)} + put {fmt_number(p, 2)}).",
                 f"Taken as the move to expiry, it gives a range of {fmt_number(lower, 2)} to {fmt_number(upper, 2)}, about "
                 f"{pct:.2f}% either side of spot {fmt_number(spot, 2)}"
                 + (f", with {dte} trading {'day' if dte == 1 else 'days'} to expiry." if dte is not None and dte >= 0 else ".")]
    past = session_history(chain, history)
    path: List[Dict[str, Any]] = []
    for at, ch in [(s.at, s.chain) for s in past] + [(now, chain)]:
        f = straddle_of(ch)
        s_spot = ch.underlying_ltp
        total = f[1] + f[2] if f else None
        path.append({"time": _iso(at), "spot": _r(s_spot), "atm": f[0] if f else None, "straddle": _r(total),
                     "lower": _r(s_spot - total) if total is not None and s_spot is not None else None,
                     "upper": _r(s_spot + total) if total is not None and s_spot is not None else None})
    if past:
        tables.append(_table("Through the session", [_column("time", "Time", "time"), _column("spot", "Spot", "price"), _column("atm", "ATM", "strike"),
                                                     _column("straddle", "Straddle", "price"), _column("lower", "Lower", "price"),
                                                     _column("upper", "Upper", "price")], path))
        first = next((x for x in path if x["straddle"] is not None), path[-1])       # the last point always has one
        since_at = fmt_time(datetime.fromisoformat(first["time"]))
        if first is path[-1]:
            sentences.append("No earlier snapshot of this session has both ATM prices, so the straddle's path starts now.")
        elif first["straddle"] == path[-1]["straddle"]:
            sentences.append(f"Since {since_at} IST the straddle is unchanged at {fmt_number(straddle, 2)}.")
        else:
            sentences.append(f"Since {since_at} IST the straddle moved from {fmt_number(first['straddle'], 2)} to {fmt_number(straddle, 2)}.")
    else:
        sentences.append(HISTORY_OFF if history_off else NO_HISTORY)
    chart = _chart("line", "time", "Time", "Price", [
        _series("upper", "Upper", "muted", [(x["time"], x["upper"]) for x in path]),
        _series("spot", "Spot", "brand", [(x["time"], x["spot"]) for x in path]),
        _series("lower", "Lower", "muted", [(x["time"], x["lower"]) for x in path]),
    ])
    return _result("expected_move", EM_TITLE, EM_METHOD, tables=tables, chart=chart, sentences=sentences,
                   values={"atm": atm, "straddle": _r(straddle), "lower": _r(lower), "upper": _r(upper), "move_pct": _r(pct), "dte": dte})


def _expiry_date(expiry: str) -> Optional[date]:
    try:
        return date.fromisoformat((expiry or "")[:10])
    except ValueError:
        return None


def _dte(expiry: str, now: datetime, holidays: Iterable[date] = ()) -> Optional[int]:
    """B3: trading days to expiry (app/market_data/calendar.py), the same count as the chain page's summary."""
    day = _expiry_date(expiry)
    aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return trading_days_until(aware.astimezone(IST).date(), day, holidays) if day else None


# ---------------------------------------------------------------- 6. IV smile / skew

IV_TITLE = "IV smile / skew"
IV_METHOD = ("Call and put implied volatility per strike for ATM and 5 strikes each side: the broker's quoted IV when it reprices the "
             "last price, otherwise solved from the last price (the quote alone when there is no last price). Skew is the average, "
             "over the 5 strike pairs either side of ATM, of the put IV k strikes below ATM minus the call IV k strikes above it, in IV "
             "points; within 0.5 points it reads about the same.")
PERCENT_CUTOFF = 3.0     # a quoted IV below this is a fraction (0.142), above it a percentage (14.2) - as options_builder reads it


def _iv_pct(quoted: Optional[float], ltp: Optional[float], spot: Optional[float], strike: float, years: Optional[float],
            ot: OptionType) -> Optional[float]:
    """The option's IV in percent, or None. With a spot and time left the shared reader decides (quote read both ways
    and kept if it reprices the LTP, else solved from the LTP); without them a quote below 3 is a fraction."""
    if spot and years and years > 0:
        from app.options_builder.chain import option_iv
        sigma = option_iv(quoted, ltp, spot, strike, years, RISK_FREE_RATE, ot)
    elif quoted is not None and quoted > 0:
        sigma = float(quoted) if quoted < PERCENT_CUTOFF else float(quoted) / 100.0
    else:
        sigma = None
    return None if sigma is None or sigma <= 0 else sigma * 100.0


def _years(expiry: str, now: datetime) -> Optional[float]:
    day = _expiry_date(expiry)
    if day is None:
        return None
    from app.options_builder.model import years_left
    return years_left(day, now if now.tzinfo else now.replace(tzinfo=timezone.utc))


def iv_skew(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False) -> Dict[str, Any]:
    rows = sorted_rows(chain)
    spot = chain.underlying_ltp
    window = near_atm(rows, spot)
    if not window:
        return _no_data("iv_skew", IV_TITLE, IV_METHOD, "The chain has no underlying price, so the strikes around the money cannot be found.")
    years = _years(chain.expiry, now)
    smile: List[Dict[str, Any]] = [{"strike": r.strike,
              "call_iv": _r(_iv_pct(r.call_iv, r.call_ltp, spot, r.strike, years, OptionType.CALL)),
              "put_iv": _r(_iv_pct(r.put_iv, r.put_ltp, spot, r.strike, years, OptionType.PUT))} for r in window]
    if all(x["call_iv"] is None and x["put_iv"] is None for x in smile):
        return _no_data("iv_skew", IV_TITLE, IV_METHOD, "No implied volatility is available for the strikes around the money: the broker "
                                                        "quoted none and the last prices could not be read as one.")
    atm_strike = rows[atm_index(rows, spot)].strike  # type: ignore[index]
    centre = next(i for i, x in enumerate(smile) if x["strike"] == atm_strike)
    pairs = []
    for k in range(1, SKEW_PAIRS + 1):
        below = smile[centre - k] if centre - k >= 0 else None
        above = smile[centre + k] if centre + k < len(smile) else None
        put_iv = below["put_iv"] if below else None
        call_iv = above["call_iv"] if above else None
        pairs.append({"k": k, "put_strike": below["strike"] if below else None, "put_iv": put_iv,
                      "call_strike": above["strike"] if above else None, "call_iv": call_iv,
                      "difference": _r(put_iv - call_iv) if put_iv is not None and call_iv is not None else None})
    diffs = [p["difference"] for p in pairs if p["difference"] is not None]
    skew = sum(diffs) / len(diffs) if diffs else None
    atm_ivs = [v for v in (smile[centre]["call_iv"], smile[centre]["put_iv"]) if v is not None]
    atm_iv = sum(atm_ivs) / len(atm_ivs) if atm_ivs else None

    sentences: List[str] = []
    if atm_iv is not None:
        sentences.append(f"ATM IV at {fmt_strike(atm_strike)} is {atm_iv:.2f}% "
                         f"(call {_pct_or_dash(smile[centre]['call_iv'])}, put {_pct_or_dash(smile[centre]['put_iv'])}).")
    if skew is None:
        sentences.append("No strike pair either side of ATM has both a put IV and a call IV, so the skew cannot be measured.")
    elif abs(skew) < SKEW_FLAT_POINTS:
        sentences.append(f"Across {len(diffs)} strike {'pair' if len(diffs) == 1 else 'pairs'} either side of ATM, out-of-the-money put IV is "
                         f"about the same as out-of-the-money call IV ({skew:+.2f} points on average).")
    else:
        sentences.append(f"Across {len(diffs)} strike {'pair' if len(diffs) == 1 else 'pairs'} either side of ATM, out-of-the-money put IV is "
                         f"{abs(skew):.2f} points {'above' if skew > 0 else 'below'} out-of-the-money call IV on average.")
    points = [(x["strike"], side, x[f"{side}_iv"]) for x in smile for side in ("call", "put") if x[f"{side}_iv"] is not None]
    lo = min(points, key=lambda t: (t[2], t[0]))
    hi = max(points, key=lambda t: (t[2], -t[0]))
    if lo[2] != hi[2]:
        sentences.append(f"In this window IV is lowest at {fmt_strike(lo[0])} ({lo[1]}, {lo[2]:.2f}%) and highest at "
                         f"{fmt_strike(hi[0])} ({hi[1]}, {hi[2]:.2f}%).")
    tables = [_table("Smile", [_column("strike", "Strike", "strike"), _column("call_iv", "Call IV %", "iv"), _column("put_iv", "Put IV %", "iv")], smile),
              _table("Skew pairs", [_column("k", "Strikes from ATM", "number"), _column("put_strike", "Put strike", "strike"),
                                    _column("put_iv", "Put IV %", "iv"), _column("call_strike", "Call strike", "strike"),
                                    _column("call_iv", "Call IV %", "iv"), _column("difference", "Put - call", "iv_diff")], pairs)]
    chart = _chart("line", "strike", "Strike", "IV %", [
        _series("put_iv", "Put IV", "up", [(x["strike"], x["put_iv"]) for x in smile]),
        _series("call_iv", "Call IV", "down", [(x["strike"], x["call_iv"]) for x in smile]),
    ], _spot_marker(spot))
    return _result("iv_skew", IV_TITLE, IV_METHOD, tables=tables, chart=chart, sentences=sentences[:3],
                   values={"atm": atm_strike, "atm_iv": _r(atm_iv), "skew": _r(skew)})


def _pct_or_dash(value: Optional[float]) -> str:
    return f"{value:.2f}%" if value is not None else "not available"


# ---------------------------------------------------------------- all six

ANALYSES: Tuple[Tuple[str, Callable[..., Dict[str, Any]]], ...] = (
    ("support_resistance", support_resistance), ("build_up", build_up), ("pcr", pcr),
    ("max_pain", max_pain), ("expected_move", expected_move), ("iv_skew", iv_skew),
)


def clean_spot(chain: OptionChain) -> OptionChain:
    """The chain with a spot that is not a finite number above 0 read as missing (some brokers send 0.0 for "no price")."""
    if chain.underlying_ltp is None or _usable_spot(chain.underlying_ltp):
        return chain
    return chain.model_copy(update={"underlying_ltp": None})


_TITLES: Dict[str, Tuple[str, str]] = {
    "support_resistance": (SR_TITLE, SR_METHOD), "build_up": (BU_TITLE, BU_METHOD), "pcr": (PCR_TITLE, PCR_METHOD),
    "max_pain": (MP_TITLE, MP_METHOD), "expected_move": (EM_TITLE, EM_METHOD), "iv_skew": (IV_TITLE, IV_METHOD),
}


def compute_all(chain: OptionChain, history: Sequence[Snapshot], now: datetime, *, history_off: bool = False,
                holidays: Iterable[date] = ()) -> List[Dict[str, Any]]:
    """The six analyses in tab order. `history` may hold other expiries or empty slots; each analysis keeps only the
    earlier snapshots of this chain's expiry (`session_history`). A spot of 0 or not a number is treated as missing, and
    one analysis that fails on an odd chain becomes that tab's `no_data` (logged by type only) - never a failed response.
    `history_off`: the caller left the earlier snapshots out on purpose (the own-broker data setting); the tabs say so."""
    chain = clean_spot(chain)
    history = [Snapshot(s.at, clean_spot(s.chain)) for s in history]
    out: List[Dict[str, Any]] = []
    for key, fn in ANALYSES:
        try:
            if fn is expected_move or fn is pcr:            # B3: the trading-day count needs the exchange's closed days;
                out.append(fn(chain, history, now, history_off=history_off, holidays=holidays))   # M61 PCR-1: session rows too
            else:
                out.append(fn(chain, history, now, history_off=history_off))
        except Exception as exc:  # noqa: BLE001 - one tab's failure must not take the other five down
            logger.warning("Option chain analysis %s failed: %s", key, type(exc).__name__)
            title, method = _TITLES[key]
            out.append(_no_data(key, title, method, UNREADABLE))
    return out


__all__ = ["Snapshot", "compute_all", "support_resistance", "build_up", "pcr", "max_pain", "expected_move", "iv_skew",
           "classify_build_up", "near_atm", "atm_index", "pcr_of", "expiry_values", "straddle_of", "session_history", "clean_spot",
           "BUILD_UP_LABELS", "NEAR_ATM_STRIKES", "KEYS", "HISTORY_OFF", "BU_HISTORY_OFF", "fmt_number", "fmt_strike"]
