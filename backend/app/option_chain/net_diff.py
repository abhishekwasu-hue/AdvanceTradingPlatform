"""M96 L1 (owner, 11 Nov: "taken from the Trade repo AS IS"): the option chain's OI-difference table - a port of the
Trade dashboard's chain table "Net Diff" (Trade page_dashboard.py, the block that reads `day_baseline_oi`).

Same definitions as Trade:
* the step: NIFTY 50, every other index Trade lists (BANKNIFTY, SENSEX) 100; any other underlying (Trade has none) the
  chain's own strike spacing;
* ATM = the spot rounded to the step (Python's round, as Trade); the window ATM - 6 steps .. ATM + 6 steps, inclusive;
* the baseline = the app's FIRST reading of the day per strike (not the exchange's previous-day change): a strike gets
  its baseline the first time it is read inside the window; a baseline whose call OI was 0 is replaced by the first later
  reading with a call OI above 0 (both sides, as Trade);
* per strike: CE Chg OI = call OI - its baseline, PE Chg OI = put OI - its baseline, Net Diff = PE Chg OI - CE Chg OI,
  Dominance by the sign of Net Diff: "Bulls" (> 0), "Bears" (< 0), "Neutral" (0) - Trade's words without the emoji;
* the TOTAL row: the sums of CE Chg OI, CE Total OI, PE Total OI, PE Chg OI and Net Diff (LTP and Dominance blank);
* the PCR of the table: total put OI / total call OI of the window, rounded to two places (Trade's banner reads the
  same window and rounds the same way; its zones are oi_regime.pcr_band with the default edges).
OI is read as Trade reads it: `int(oi or 0)`; a missing LTP stays missing here (Trade shows 0.0).

Pure functions; app/option_chain/routes.py keeps the baseline (a cache entry renewed on every read, seeded from the
day's stored chain reads of the same expiry - the collector's slots, or the user's own with the own-broker tier).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.brokers.models import OptionChainRow
from app.option_chain.oi_regime import infer_strike_step

ATM_RANGE = 6                                       # Trade: ATM +- 6 strikes (13 rows)
TRADE_STEPS = {"NIFTY": 50.0, "BANKNIFTY": 100.0, "SENSEX": 100.0}

Baseline = dict[float, tuple[int, int]]             # strike -> (initial call OI, initial put OI)


def trade_step(underlying: str, strikes: Sequence[float]) -> float | None:
    """Trade's step for the index it lists, else the chain's own strike spacing."""
    return TRADE_STEPS.get((underlying or "").strip().upper()) or infer_strike_step(strikes)


def trade_atm(spot: float | None, step: float | None) -> float | None:
    """Trade: `round(underlying_price / step) * step` (0 when the price is not known; None here)."""
    if not spot or spot <= 0 or not step:
        return None
    return float(round(spot / step) * step)


def _oi(value: float | None) -> int:
    return int(value or 0)


def window(rows: Iterable[OptionChainRow], atm: float, step: float, atm_range: int = ATM_RANGE) -> list[OptionChainRow]:
    lo, hi = atm - atm_range * step, atm + atm_range * step
    return [r for r in sorted(rows, key=lambda r: r.strike) if lo <= r.strike <= hi]


def update_baseline(baseline: Baseline, rows: Sequence[OptionChainRow]) -> bool:
    """Trade's baseline rule for one reading of the window (in place); True when anything was added or replaced."""
    changed = False
    for r in rows:
        ce, pe = _oi(r.call_oi), _oi(r.put_oi)
        key = float(r.strike)
        held = baseline.get(key)
        if held is None or (held[0] == 0 and ce > 0):
            baseline[key] = (ce, pe)
            changed = True
    return changed


def dominance(diff: float) -> str:
    return "Bulls" if diff > 0 else "Bears" if diff < 0 else "Neutral"


@dataclass(frozen=True)
class NetDiffRow:
    strike: float
    ce_chg: int
    ce_oi: int
    ce_ltp: float | None
    pe_ltp: float | None
    pe_oi: int
    pe_chg: int

    @property
    def diff(self) -> int:
        return self.pe_chg - self.ce_chg

    @property
    def dominance(self) -> str:
        return dominance(self.diff)


@dataclass(frozen=True)
class NetDiffTable:
    step: float
    atm: float
    rows: tuple[NetDiffRow, ...]

    @property
    def total(self) -> dict[str, int]:
        ce_chg = sum(r.ce_chg for r in self.rows)
        pe_chg = sum(r.pe_chg for r in self.rows)
        return {"ce_chg": ce_chg, "ce_oi": sum(r.ce_oi for r in self.rows), "pe_oi": sum(r.pe_oi for r in self.rows),
                "pe_chg": pe_chg, "diff": pe_chg - ce_chg}

    @property
    def pcr(self) -> float | None:
        t = self.total
        return round(t["pe_oi"] / t["ce_oi"], 2) if t["ce_oi"] > 0 else None


def table(rows: Sequence[OptionChainRow], atm: float, step: float, baseline: Baseline, atm_range: int = ATM_RANGE) -> NetDiffTable:
    """The table for one reading; `baseline` must already hold every window strike (update_baseline first)."""
    out: list[NetDiffRow] = []
    for r in window(rows, atm, step, atm_range):
        ce, pe = _oi(r.call_oi), _oi(r.put_oi)
        init_ce, init_pe = baseline.get(float(r.strike), (ce, pe))
        out.append(NetDiffRow(strike=float(r.strike), ce_chg=ce - init_ce, ce_oi=ce, ce_ltp=r.call_ltp, pe_ltp=r.put_ltp, pe_oi=pe,
                              pe_chg=pe - init_pe))
    return NetDiffTable(step=step, atm=atm, rows=tuple(out))


def table_json(t: NetDiffTable) -> dict:
    return {"step": t.step, "atm": t.atm, "window": ATM_RANGE, "pcr": t.pcr, "total": t.total,
            "rows": [{"strike": r.strike, "ce_chg": r.ce_chg, "ce_oi": r.ce_oi, "ce_ltp": r.ce_ltp, "pe_ltp": r.pe_ltp, "pe_oi": r.pe_oi,
                      "pe_chg": r.pe_chg, "diff": r.diff, "dominance": r.dominance} for r in t.rows]}


def baseline_json(baseline: Baseline, since: str) -> dict:
    return {"since": since, "oi": [[s, c, p] for s, (c, p) in sorted(baseline.items())]}


def baseline_from_text(text: str | None) -> tuple[str, Baseline] | None:
    """The cached baseline (None when absent or unreadable - it is then rebuilt)."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return str(data["since"]), {float(s): (int(c), int(p)) for s, c, p in data["oi"]}
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["ATM_RANGE", "TRADE_STEPS", "Baseline", "trade_step", "trade_atm", "window", "update_baseline", "dominance", "NetDiffRow",
           "NetDiffTable", "table", "table_json", "baseline_json", "baseline_from_text"]
