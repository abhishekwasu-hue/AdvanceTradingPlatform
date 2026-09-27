"""Phase H1: the strike-selection pipeline (master prompt section 23, V2.1).

    Spot -> ATM -> Expiry -> Option Chain -> Liquidity filter -> OI -> IV -> Delta -> Premium -> Strike

Phase F2 picked the strike purely by rule (ATM / ITM n / OTM n) from the instrument master. With
`StrikeFilters` on the deployment, the rule strike becomes the *centre of a search*: every listed
strike within `search_steps` of it is looked up in the live option chain and judged on

* liquidity - open interest >= `min_oi`, volume >= `min_volume`, bid/ask spread <= `max_spread_pct`
  of the mid (an option nobody quotes tightly fills badly and exits worse);
* IV band - implied volatility (the chain's own, else solved from the premium) between
  `min_iv_pct` and `max_iv_pct`;
* delta - when `target_delta` is set, the passing strike whose |delta| is nearest to it wins
  (the chain's delta when the broker sends one, else Black-Scholes from IV); otherwise the
  passing strike nearest to the rule strike wins;
* premium band - last price between `min_premium` and `max_premium`.

Every candidate's verdict is kept (`SelectionResult.candidates`) so the preview and the order
trail can say *why* 24550 was chosen over 24500. A chain that cannot be fetched, or no strike
passing, is a `StrikeSelectionError`: filters the user configured are a promise, and trading a
strike that was never checked would break it.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from app.brokers.models import OptionChain, OptionChainRow
from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import BSInputs, black_scholes, implied_volatility, time_to_expiry_years
from app.option_chain.models import OptionType


class StrikeSelectionError(ValueError):
    """No strike satisfies the deployment's filters right now (or the chain is unavailable)."""


@dataclass(frozen=True)
class StrikeFilters:
    min_oi: Optional[float] = None
    min_volume: Optional[float] = None
    max_spread_pct: Optional[float] = None      # (ask - bid) / mid * 100
    min_iv_pct: Optional[float] = None
    max_iv_pct: Optional[float] = None
    target_delta: Optional[float] = None        # absolute delta, e.g. 0.30
    delta_tolerance: float = 0.10               # |delta| must be within this of target_delta
    min_premium: Optional[float] = None
    max_premium: Optional[float] = None
    search_steps: int = 5                       # listed strikes either side of the rule strike

    @property
    def active(self) -> bool:
        return any(getattr(self, f) is not None for f in (
            "min_oi", "min_volume", "max_spread_pct", "min_iv_pct", "max_iv_pct", "target_delta", "min_premium", "max_premium",
        ))

    def to_json(self) -> Optional[str]:
        data = {k: v for k, v in asdict(self).items() if v is not None and not (k == "delta_tolerance" and v == 0.10) and not (k == "search_steps" and v == 5)}
        return json.dumps(data, sort_keys=True) if data else None

    @classmethod
    def from_json(cls, text: Optional[str]) -> "StrikeFilters":
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return cls()
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)

    def describe(self) -> str:
        parts = []
        if self.min_oi is not None:
            parts.append(f"OI>={self.min_oi:g}")
        if self.min_volume is not None:
            parts.append(f"vol>={self.min_volume:g}")
        if self.max_spread_pct is not None:
            parts.append(f"spread<={self.max_spread_pct:g}%")
        if self.min_iv_pct is not None or self.max_iv_pct is not None:
            parts.append(f"IV {self.min_iv_pct if self.min_iv_pct is not None else '..'}-{self.max_iv_pct if self.max_iv_pct is not None else '..'}%")
        if self.target_delta is not None:
            parts.append(f"delta~{self.target_delta:g}")
        if self.min_premium is not None or self.max_premium is not None:
            parts.append(f"premium {self.min_premium if self.min_premium is not None else '..'}-{self.max_premium if self.max_premium is not None else '..'}")
        return ", ".join(parts) if parts else "no filters"


@dataclass
class StrikeCandidate:
    strike: float
    ltp: Optional[float] = None
    oi: Optional[float] = None
    volume: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_pct: Optional[float] = None
    iv_pct: Optional[float] = None
    delta: Optional[float] = None
    passes: bool = False
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SelectionResult:
    strike: float
    rule_strike: float
    candidates: List[StrikeCandidate]
    notes: List[str]

    def as_dict(self) -> Dict:
        return {"strike": self.strike, "rule_strike": self.rule_strike, "notes": self.notes,
                "candidates": [c.as_dict() for c in self.candidates]}


def _side(row: OptionChainRow, right: str) -> Dict[str, Optional[float]]:
    if right.upper() == "CE":
        return {"ltp": row.call_ltp, "oi": row.call_oi, "volume": row.call_volume, "bid": row.call_bid, "ask": row.call_ask,
                "iv": row.call_iv, "delta": row.call_delta}
    return {"ltp": row.put_ltp, "oi": row.put_oi, "volume": row.put_volume, "bid": row.put_bid, "ask": row.put_ask,
            "iv": row.put_iv, "delta": row.put_delta}


def _iv_pct(raw: Optional[float]) -> Optional[float]:
    """Brokers quote IV as a percentage (14.2) or a fraction (0.142); normalise to percent."""
    if raw is None or raw <= 0:
        return None
    return raw * 100.0 if raw < 3.0 else raw


def candidate_for(row: OptionChainRow, right: str, spot: Optional[float], expiry: Optional[date], as_of: date,
                  risk_free_rate: float = RISK_FREE_RATE) -> StrikeCandidate:
    side = _side(row, right)
    c = StrikeCandidate(strike=row.strike, ltp=side["ltp"], oi=side["oi"], volume=side["volume"], bid=side["bid"], ask=side["ask"])
    if c.bid is not None and c.ask is not None and c.bid > 0 and c.ask > 0:
        mid = (c.bid + c.ask) / 2
        c.spread_pct = round((c.ask - c.bid) / mid * 100.0, 2) if mid > 0 else None
    c.iv_pct = _iv_pct(side["iv"])
    option_type = OptionType.CALL if right.upper() == "CE" else OptionType.PUT
    t = time_to_expiry_years(expiry, as_of) if expiry is not None else None
    if c.iv_pct is None and c.ltp and spot and t:
        solved = implied_volatility(c.ltp, spot, row.strike, t, risk_free_rate, option_type)
        c.iv_pct = round(solved * 100.0, 2) if solved else None
    if side["delta"] is not None:
        c.delta = float(side["delta"])
    elif c.iv_pct and spot and t:
        try:
            c.delta = round(black_scholes(BSInputs(spot, row.strike, t, risk_free_rate, c.iv_pct / 100.0, option_type)).delta, 4)
        except ValueError:
            c.delta = None
    return c


def _judge(c: StrikeCandidate, f: StrikeFilters) -> None:
    fails: List[str] = []
    if f.min_oi is not None and (c.oi is None or c.oi < f.min_oi):
        fails.append(f"OI {c.oi if c.oi is not None else 'n/a'} < {f.min_oi:g}")
    if f.min_volume is not None and (c.volume is None or c.volume < f.min_volume):
        fails.append(f"volume {c.volume if c.volume is not None else 'n/a'} < {f.min_volume:g}")
    if f.max_spread_pct is not None and (c.spread_pct is None or c.spread_pct > f.max_spread_pct):
        fails.append(f"spread {c.spread_pct if c.spread_pct is not None else 'n/a'}% > {f.max_spread_pct:g}%")
    if f.min_iv_pct is not None and (c.iv_pct is None or c.iv_pct < f.min_iv_pct):
        fails.append(f"IV {c.iv_pct if c.iv_pct is not None else 'n/a'}% < {f.min_iv_pct:g}%")
    if f.max_iv_pct is not None and (c.iv_pct is None or c.iv_pct > f.max_iv_pct):
        fails.append(f"IV {c.iv_pct if c.iv_pct is not None else 'n/a'}% > {f.max_iv_pct:g}%")
    if f.target_delta is not None and (c.delta is None or abs(abs(c.delta) - f.target_delta) > f.delta_tolerance):
        fails.append(f"|delta| {abs(c.delta) if c.delta is not None else 'n/a'} not within {f.delta_tolerance:g} of {f.target_delta:g}")
    if f.min_premium is not None and (c.ltp is None or c.ltp < f.min_premium):
        fails.append(f"premium {c.ltp if c.ltp is not None else 'n/a'} < {f.min_premium:g}")
    if f.max_premium is not None and (c.ltp is None or c.ltp > f.max_premium):
        fails.append(f"premium {c.ltp if c.ltp is not None else 'n/a'} > {f.max_premium:g}")
    if c.ltp is None or c.ltp <= 0:
        fails.append("no last price")
    c.passes = not fails
    c.reasons = fails or ["passes"]


def select_strike_with_chain(
    listed_strikes: Sequence[float], rule_strike: float, right: str, chain: OptionChain, filters: StrikeFilters, *,
    spot: Optional[float], expiry: Optional[date], as_of: date,
) -> SelectionResult:
    """Apply `filters` to the strikes within `search_steps` of `rule_strike` and pick one."""
    listed = sorted(set(listed_strikes))
    if rule_strike not in listed:
        listed = sorted(set(listed) | {rule_strike})
    centre = listed.index(rule_strike)
    window = listed[max(0, centre - filters.search_steps): centre + filters.search_steps + 1]
    rows = {r.strike: r for r in chain.rows}
    candidates: List[StrikeCandidate] = []
    for strike in window:
        row = rows.get(strike)
        if row is None:
            candidates.append(StrikeCandidate(strike=strike, passes=False, reasons=["not in option chain"]))
            continue
        c = candidate_for(row, right, spot or chain.underlying_ltp, expiry, as_of)
        _judge(c, filters)
        candidates.append(c)

    passing = [c for c in candidates if c.passes]
    if not passing:
        summary = "; ".join(f"{int(c.strike)}: {c.reasons[0]}" for c in candidates[:6])
        raise StrikeSelectionError(f"No {right} strike within {filters.search_steps} steps of {int(rule_strike)} passes ({filters.describe()}): {summary}")

    if filters.target_delta is not None:
        chosen = min(passing, key=lambda c: (abs(abs(c.delta) - filters.target_delta), abs(c.strike - rule_strike), c.strike))
        why = f"|delta| {abs(chosen.delta):.2f} nearest to {filters.target_delta:g}"
    else:
        chosen = min(passing, key=lambda c: (abs(c.strike - rule_strike), c.strike))
        why = "nearest passing strike to the rule strike"
    notes = [f"Strike {int(chosen.strike)} {right}: {why} ({filters.describe()}); {len(passing)}/{len(candidates)} candidates passed"]
    if chosen.strike != rule_strike:
        rule = next((c for c in candidates if c.strike == rule_strike), None)
        if rule is not None and not rule.passes:
            notes.append(f"Rule strike {int(rule_strike)} skipped: {rule.reasons[0]}")
    return SelectionResult(strike=chosen.strike, rule_strike=rule_strike, candidates=candidates, notes=notes)
