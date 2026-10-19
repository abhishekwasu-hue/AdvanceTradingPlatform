"""Strategy framework: conditions, signals, context, transparent scoring and the base class.

A signal QUALIFIES only if every mandatory condition passed AND data quality is usable.
The confluence score never overrides a failed mandatory rule. Score is NOT a win probability.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..analysis.context import TFAnalysis
from ..analysis.indicators import relative_strength
from ..data.validation import STALE, TF_DELTA, QualityReport, usable

COMPONENTS = ["trend_mtf", "structure", "volume", "level_quality", "momentum", "relative_strength", "risk_reward"]
HIGHER = {"5m": ["15m", "1H", "1D"], "15m": ["1H", "1D"], "30m": ["1H", "1D"], "1H": ["1D"], "1D": ["1W"], "1W": []}


@dataclass
class Condition:
    name: str
    passed: bool | None          # None = could not be evaluated (missing data)
    mandatory: bool
    detail: str = ""

    def __post_init__(self):
        # normalise numpy bools so identity checks are reliable
        self.passed = None if self.passed is None else bool(self.passed)
        self.mandatory = bool(self.mandatory)


@dataclass
class Signal:
    symbol: str
    ticker: str
    strategy: str
    strategy_version: int
    setup: str
    timeframe: str
    direction: str               # bullish | bearish
    signal_time: str             # completed bar that produced the signal
    data_time: str               # last completed bar in the data
    status: str = "rejected"     # qualified | candidate | rejected
    entry_zone: tuple[float, float] | None = None
    entry_condition: str = ""
    invalidation: float | None = None
    stop: float | None = None
    stop_method: str = ""
    target: float | None = None
    target_method: str = ""
    rr: float | None = None
    conditions: list[Condition] = field(default_factory=list)
    components: dict[str, tuple[float | None, str]] = field(default_factory=dict)
    score: float = 0.0
    score_coverage: float = 0.0
    label: str = "Rejected"
    data_status: str = ""
    expiry: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        raw = f"{self.ticker}|{self.strategy}|{self.setup}|{self.timeframe}|{self.direction}|{self.signal_time}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def failed(self) -> list[Condition]:
        return [c for c in self.conditions if c.passed is False]

    @property
    def passed(self) -> list[Condition]:
        return [c for c in self.conditions if c.passed]

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["key"] = self.key
        d["entry_zone"] = f"{self.entry_zone[0]:.2f}-{self.entry_zone[1]:.2f}" if self.entry_zone else None
        d["conditions_passed"] = " | ".join(f"{c.name}: {c.detail}" for c in self.passed)
        d["conditions_failed"] = " | ".join(f"{'[M] ' if c.mandatory else ''}{c.name}: {c.detail}" for c in self.failed)
        d["conditions_na"] = " | ".join(c.name for c in self.conditions if c.passed is None)
        d["score_breakdown"] = " | ".join(f"{k}={'NA' if v[0] is None else f'{v[0]:.2f}'} ({v[1]})" for k, v in self.components.items())
        d["warnings"] = "; ".join(self.warnings)
        d.pop("conditions"), d.pop("components")
        return d


@dataclass
class StrategyContext:
    symbol: str
    ticker: str
    analyses: dict[str, TFAnalysis | None]
    quality: dict[str, QualityReport]
    benchmark: dict[str, pd.DataFrame | None]
    cfg: dict

    def get(self, tf: str) -> TFAnalysis | None:
        return self.analyses.get(tf)


def sgn(direction: str) -> int:
    return 1 if direction == "bullish" else -1


def directional(x: float | None, direction: str) -> float | None:
    return None if x is None else max(0.0, x * sgn(direction))


def mtf_alignment(ctx: StrategyContext, tf: str, direction: str, include_self: bool = True) -> tuple[float | None, str]:
    tfs = ([tf] if include_self else []) + HIGHER.get(tf, [])
    vals, parts = [], []
    for t in tfs:
        a = ctx.get(t)
        if a is not None and a.trend_score is not None:
            vals.append(directional(a.trend_score, direction))
            parts.append(f"{t}:{a.trend_label}")
    if not vals:
        return None, "no timeframe trend available"
    return float(np.mean(vals)), ", ".join(parts)


def momentum_score(t: TFAnalysis, direction: str) -> tuple[float | None, str]:
    r, h = t.last.get("rsi14"), t.last.get("macd_hist")
    if r is None and h is None:
        return None, "RSI/MACD NA"
    s, parts = 0.0, []
    if h is not None:
        ok = (h > 0) if direction == "bullish" else (h < 0)
        s += 0.5 if ok else 0
        parts.append(f"MACD hist {h:+.2f}")
    if r is not None:
        ok = (50 <= r <= 75) if direction == "bullish" else (25 <= r <= 50)
        s += 0.5 if ok else 0
        parts.append(f"RSI {r:.1f}")
    return s, ", ".join(parts)


def rs_score(ctx: StrategyContext, t: TFAnalysis, direction: str) -> tuple[float | None, str]:
    b = ctx.benchmark.get(t.tf)
    if b is None or len(b) == 0:
        return None, "benchmark data NA"
    rs = relative_strength(t.df.Close, b.Close, ctx.cfg["analysis"]["rs_lookback"])
    if rs is None:
        return None, "insufficient common history"
    return float(np.clip(0.5 + sgn(direction) * rs * 5, 0, 1)), f"RS vs benchmark {rs * 100:+.1f}% ({ctx.cfg['analysis']['rs_lookback']} bars)"


def rr_score(rr: float | None) -> tuple[float | None, str]:
    if rr is None or not np.isfinite(rr):
        return None, "R:R NA"
    return float(np.clip((rr - 1) / 2, 0, 1)), f"R:R {rr:.2f}"


def risk_reward(entry: float | None, stop: float | None, target: float | None, direction: str) -> float | None:
    if None in (entry, stop, target):
        return None
    risk = (entry - stop) * sgn(direction)
    reward = (target - entry) * sgn(direction)
    return float(reward / risk) if risk > 0 else None


def label_for(score: float, cfg: dict) -> str:
    lab = cfg["scoring"]["labels"]
    return ("A1 Candidate" if score >= lab["a1"] else "Strong Candidate" if score >= lab["strong"]
            else "Watchlist" if score >= lab["watch"] else "Low Confluence")


class Strategy:
    name = "base"
    title = "Base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.scfg = cfg["strategies"][self.name]
        self.p = self.scfg["params"]
        self.version = int(self.scfg.get("version", 1))
        w = self.scfg.get("weights") or cfg["scoring"]["weights"]
        self.weights = {k: float(w.get(k, 0)) for k in COMPONENTS}

    @property
    def timeframes(self) -> list[str]:
        return list(self.scfg["timeframes"])

    def new_signal(self, ctx: StrategyContext, t: TFAnalysis, setup: str, direction: str) -> Signal:
        return Signal(symbol=ctx.symbol, ticker=ctx.ticker, strategy=self.name, strategy_version=self.version, setup=setup,
                      timeframe=t.tf, direction=direction, signal_time=t.time(), data_time=t.time(),
                      data_status=ctx.quality[t.tf].status if t.tf in ctx.quality else "NA")

    def expiry(self, t: TFAnalysis, bars: int) -> str:
        approx = t.df.index[-1] + TF_DELTA[t.tf] * bars
        return f"{bars} completed {t.tf} bars (~{str(approx)[:16]}) or on invalidation close"

    def finalize(self, sig: Signal, ctx: StrategyContext) -> Signal:
        """Apply mandatory gates and compute the transparent score."""
        W = sum(self.weights.values()) or 1.0
        total = avail = 0.0
        for k in COMPONENTS:
            v, _ = sig.components.get(k, (None, "not evaluated"))
            sig.components.setdefault(k, (None, "not evaluated"))
            if v is None:
                continue
            avail += self.weights[k]
            total += self.weights[k] * float(np.clip(v, 0, 1))
        score = 100 * total / W
        rep = ctx.quality.get(sig.timeframe)
        if rep is not None and rep.status == STALE:
            score -= 5
            sig.warnings.append("STALE data: -5 score")
        sig.score = round(max(0.0, score), 1)
        sig.score_coverage = round(100 * avail / W, 1)
        mand_failed = [c for c in sig.conditions if c.mandatory and c.passed is not True]
        if rep is None or not usable(rep):
            sig.status, sig.label = "rejected", "Rejected (data)"
        elif mand_failed:
            sig.status, sig.label = "rejected", "Rejected"
        else:
            sig.status = "qualified" if sig.status != "candidate" else "candidate"
            sig.label = label_for(sig.score, self.cfg)
        return sig

    def evaluate(self, ctx: StrategyContext) -> list[Signal]:
        out: list[Signal] = []
        for tf in self.timeframes:
            t = ctx.get(tf)
            if t is None or t.atr is None or t.n < 30:
                continue
            for direction in ("bullish", "bearish"):
                for sig in self.evaluate_tf(ctx, t, direction):
                    out.append(self.finalize(sig, ctx))
        return out

    def evaluate_tf(self, ctx: StrategyContext, t: TFAnalysis, direction: str) -> list[Signal]:  # pragma: no cover
        raise NotImplementedError
