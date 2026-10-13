"""N1 F1: the portfolio risk model of one user's own PAPER book (pure: records and lookups in, a view out).

Shared by `GET /api/portfolio/risk`, the F2 entry caps and the F3 cards, so they never disagree. Nothing here reads
the database or a broker; `risk_data` gathers the inputs.

- **Positions**: open trades; the legs of one multi-leg structure count as one position, at its structure's risk
  (Guardian R4's rule: `risk_per_unit` x the 1x quantity from `group_meta`, else the short legs' risk).
- **Notional**: quantity x the mark price (the worker's last mark of the PAPER trade), else the entry price (flagged).
- **Open risk at the stops**: Guardian R4's measure - |entry - stop| x quantity per position, as opened (Trade's
  `max_loss`), so the F2 caps and R4 count the same thing.
- **Buckets**: every index in one `INDEX` bucket (R4); a stock and its F&O by the NSE sector of the underlying on the
  day (U1-b); no classification -> its own `UNCLASSIFIED:<name>` bucket, never pooled with another.
- **Beta to NIFTY 50**: per underlying from `risk_data`; unknown -> 1.0 and listed. Options are not beta-weighted (a
  delta needs the chain); they are listed instead.
- **Direction**: bullish / bearish / neutral on the underlying, and Trade's one-sided warning
  (`database.compute_portfolio_risk_summary`: two or more directional positions, all on one side).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

INDEX_BUCKET = "INDEX"
UNCLASSIFIED = "UNCLASSIFIED"
BULLISH, BEARISH, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"


@dataclass(frozen=True)
class Beta:
    value: float
    sessions: int
    known: bool
    source: str = ""                  # "definition" (NIFTY itself), "lake", or why it is unknown


@dataclass
class Position:
    key: str                          # the trade id, or "group:<leg_group_id>"
    symbol: str                       # the traded symbol (a structure: its underlying)
    underlying: str
    kind: str                         # UNDERLYING / FUT / OPTION / STRUCTURE
    direction: str                    # BULLISH / BEARISH / NEUTRAL on the underlying
    long_notional: float
    short_notional: float
    risk: float                       # loss if the stop hits, as opened
    priced_at_entry: bool
    trade_ids: List[int] = field(default_factory=list)

    @property
    def gross(self) -> float:
        return self.long_notional + self.short_notional

    @property
    def net(self) -> float:
        return self.long_notional - self.short_notional


def _f(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trade_risk(trade: Any) -> float:
    """Guardian R4's per-trade risk: |entry - stop| x quantity."""
    return abs(_f(trade.entry_price) - _f(trade.stop_loss)) * _f(trade.quantity)


def _price(trade: Any) -> Tuple[float, bool]:
    mark = _f(getattr(trade, "mark_price", None))
    return (mark, False) if mark > 0 else (_f(trade.entry_price), True)


def underlying_of_trade(trade: Any) -> str:
    from app.instruments.master import underlying_of
    return underlying_of(trade.underlying_symbol or trade.symbol)


def direction_of(trade: Any) -> str:
    """The position's direction on its underlying: the stored underlying direction (options, structures), else the
    trade's own for a stock or a future; an option without one is neutral (unknown)."""
    stored = (getattr(trade, "underlying_direction", None) or "").upper()
    if stored in ("LONG", "BULLISH"):
        return BULLISH
    if stored in ("SHORT", "BEARISH"):
        return BEARISH
    if (trade.instrument_kind or "UNDERLYING") in ("UNDERLYING", "FUT"):
        return BULLISH if (trade.direction or "").upper() == "LONG" else BEARISH
    return NEUTRAL


def positions_of(trades: Iterable[Any]) -> List[Position]:
    """Open trades -> positions (a multi-leg group is one position)."""
    trades = list(trades)
    out: List[Position] = []
    groups: Dict[str, Position] = {}
    for t in trades:
        price, at_entry = _price(t)
        notional = abs(_f(t.quantity) * price)
        long_n, short_n = (notional, 0.0) if (t.direction or "").upper() == "LONG" else (0.0, notional)
        if t.leg_group_id:
            pos = groups.get(t.leg_group_id)
            if pos is None:
                legs = [x for x in trades if x.leg_group_id == t.leg_group_id]
                try:
                    meta = json.loads(t.group_meta or "{}")
                except ValueError:
                    meta = {}
                per_unit, base_qty = meta.get("risk_per_unit"), meta.get("quantity")
                if per_unit is not None and base_qty:
                    risk = _f(per_unit) * _f(base_qty)
                else:
                    risk = sum(trade_risk(x) for x in legs if x.leg_role == "SHORT") or sum(trade_risk(x) for x in legs)
                pos = groups[t.leg_group_id] = Position(key=f"group:{t.leg_group_id}", symbol=underlying_of_trade(t),
                                                        underlying=underlying_of_trade(t), kind="STRUCTURE",
                                                        direction=direction_of(t), long_notional=0.0, short_notional=0.0,
                                                        risk=risk, priced_at_entry=False)
                out.append(pos)
            pos.long_notional += long_n
            pos.short_notional += short_n
            pos.priced_at_entry = pos.priced_at_entry or at_entry
            pos.trade_ids.append(int(t.id))
            continue
        out.append(Position(key=str(t.id), symbol=t.symbol, underlying=underlying_of_trade(t), kind=t.instrument_kind or "UNDERLYING",
                            direction=direction_of(t), long_notional=long_n, short_notional=short_n, risk=trade_risk(t),
                            priced_at_entry=at_entry, trade_ids=[int(t.id)]))
    return out


def bucket_label(underlying: str, sector: Optional[str], index_underlyings: Iterable[str]) -> str:
    if underlying in set(index_underlyings):
        return INDEX_BUCKET
    if sector:
        return sector
    return f"{UNCLASSIFIED}:{underlying}"


def one_sided_warning(bullish: int, bearish: int) -> Optional[str]:
    """Trade's rule: two or more directional positions, all on one side (neutral ones are not counted)."""
    directional = bullish + bearish
    if directional >= 2 and (bullish == directional or bearish == directional):
        side = "bullish" if bullish == directional else "bearish"
        return (f"All {directional} directional positions are {side}: one large move against them hits them together "
                "(correlated risk, no diversification).")
    return None


def _pct(value: float, capital: float) -> Optional[float]:
    return round(value / capital * 100.0, 2) if capital > 0 else None


def view(positions: List[Position], buckets: Dict[str, str], betas: Dict[str, Beta], capital: float) -> Dict[str, Any]:
    """The risk view of a book. `buckets` and `betas` are keyed by underlying; a missing bucket is UNCLASSIFIED and a
    missing beta unknown. `capital` <= 0 leaves the percentages out (None)."""
    by_bucket: Dict[str, Dict[str, Any]] = {}
    gross = net = long_n = short_n = risk = 0.0
    beta_net = beta_gross = 0.0
    unknown_beta: List[str] = []
    options: List[str] = []
    counts = {BULLISH: 0, BEARISH: 0, NEUTRAL: 0}
    at_entry: List[str] = []
    for p in positions:
        bucket = buckets.get(p.underlying) or f"{UNCLASSIFIED}:{p.underlying}"
        row = by_bucket.setdefault(bucket, {"bucket": bucket, "positions": 0, "gross_notional": 0.0, "net_notional": 0.0,
                                            "open_risk": 0.0, "symbols": []})
        row["positions"] += 1
        row["gross_notional"] += p.gross
        row["net_notional"] += p.net
        row["open_risk"] += p.risk
        if p.symbol not in row["symbols"]:
            row["symbols"].append(p.symbol)
        gross += p.gross
        net += p.net
        long_n += p.long_notional
        short_n += p.short_notional
        risk += p.risk
        counts[p.direction] = counts.get(p.direction, 0) + 1
        if p.priced_at_entry and p.symbol not in at_entry:
            at_entry.append(p.symbol)
        if p.kind in ("OPTION", "STRUCTURE"):
            if p.symbol not in options:
                options.append(p.symbol)
            continue
        beta = betas.get(p.underlying)
        b = beta.value if beta is not None and beta.known else 1.0
        if (beta is None or not beta.known) and p.underlying not in unknown_beta:
            unknown_beta.append(p.underlying)
        beta_net += b * p.net
        beta_gross += b * p.gross
    rows = []
    for row in by_bucket.values():
        row.update(gross_notional=round(row["gross_notional"], 2), net_notional=round(row["net_notional"], 2),
                   open_risk=round(row["open_risk"], 2), gross_pct=_pct(row["gross_notional"], capital),
                   risk_pct=_pct(row["open_risk"], capital))
        rows.append(row)
    rows.sort(key=lambda r: (-r["gross_notional"], r["bucket"]))
    warning = one_sided_warning(counts[BULLISH], counts[BEARISH])
    return {
        "capital": round(capital, 2), "positions": len(positions),
        "exposure": {"gross": round(gross, 2), "net": round(net, 2), "long": round(long_n, 2), "short": round(short_n, 2),
                     "gross_pct": _pct(gross, capital), "net_pct": _pct(net, capital)},
        "open_risk": {"total": round(risk, 2), "pct": _pct(risk, capital)},
        "buckets": rows,
        "beta": {"net": round(beta_net, 2), "gross": round(beta_gross, 2), "net_pct": _pct(beta_net, capital),
                 "unknown": sorted(unknown_beta), "not_weighted": sorted(options),
                 "per_underlying": {k: {"value": round(v.value, 3), "sessions": v.sessions, "known": v.known, "source": v.source}
                                    for k, v in sorted(betas.items())}},
        "direction": {"bullish": counts[BULLISH], "bearish": counts[BEARISH], "neutral": counts[NEUTRAL], "warning": warning},
        "priced_at_entry": sorted(at_entry),
    }


def beta_from_closes(stock: Dict[str, float], index: Dict[str, float], window: int, min_sessions: int) -> Beta:
    """Beta of daily simple returns on the sessions both series have (date strings -> closes), over the last `window`
    shared returns; fewer than `min_sessions` -> unknown."""
    days = sorted(set(stock) & set(index))
    rs: List[float] = []
    ri: List[float] = []
    for a, b in zip(days, days[1:]):
        s0, s1, i0, i1 = stock[a], stock[b], index[a], index[b]
        if s0 > 0 and i0 > 0:
            rs.append(s1 / s0 - 1.0)
            ri.append(i1 / i0 - 1.0)
    rs, ri = rs[-window:], ri[-window:]
    n = len(rs)
    if n < min_sessions:
        return Beta(1.0, n, False, f"{n} shared sessions (< {min_sessions})")
    ms, mi = sum(rs) / n, sum(ri) / n
    var = sum((x - mi) ** 2 for x in ri) / (n - 1)
    if var <= 0:
        return Beta(1.0, n, False, "the index did not move")
    cov = sum((x - ms) * (y - mi) for x, y in zip(rs, ri)) / (n - 1)
    return Beta(cov / var, n, True, "lake")
