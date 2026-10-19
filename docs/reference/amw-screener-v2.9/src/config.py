"""Settings loader, validation and timeframe metadata."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "settings.yaml"
TIMEFRAMES = ["1W", "1D", "1H", "30m", "15m", "5m"]
STRATEGIES = ["breakout", "pullback", "reversal", "trend_continuation", "intraday", "swing"]

# Data-availability rules per timeframe for the yfinance provider (documented, shown in UI)
TIMEFRAME_INFO = {
    "1W": {"yf_interval": "1wk", "period": "5y", "max_history": "multi-year", "frequency": "1 bar / week"},
    "1D": {"yf_interval": "1d", "period": "2y", "max_history": "multi-year", "frequency": "1 bar / session"},
    "1H": {"yf_interval": "1h", "period": "1y", "max_history": "~730 days", "frequency": "~7 bars / session (last bar 15:15-15:30)"},
    "30m": {"yf_interval": "30m", "period": "60d", "max_history": "~60 days", "frequency": "~13 bars / session"},
    "15m": {"yf_interval": "15m", "period": "60d", "max_history": "~60 days", "frequency": "25 bars / session"},
    "5m": {"yf_interval": "5m", "period": "60d", "max_history": "~60 days", "frequency": "75 bars / session"},
}


def load_settings(path: Path | str | None = None) -> dict[str, Any]:
    with open(Path(path) if path else DEFAULT_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate_settings(cfg)
    return cfg


def default_settings() -> dict[str, Any]:
    return load_settings(DEFAULT_PATH)


def merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def validate_settings(cfg: dict) -> None:
    w = cfg["scoring"]["weights"]
    if any(not isinstance(v, (int, float)) or v < 0 for v in w.values()) or sum(w.values()) <= 0:
        raise ValueError("scoring weights must be non-negative with a positive sum")
    lab = cfg["scoring"]["labels"]
    if not (0 <= lab["watch"] <= lab["strong"] <= lab["a1"] <= 100):
        raise ValueError("labels must satisfy 0 <= watch <= strong <= a1 <= 100")
    if int(cfg["analysis"]["swing_k"]) < 1:
        raise ValueError("swing_k must be >= 1")
    for name, s in cfg["strategies"].items():
        if name not in STRATEGIES:
            raise ValueError(f"unknown strategy {name}")
        bad = [t for t in s["timeframes"] if t not in TIMEFRAMES]
        if bad:
            raise ValueError(f"{name}: unsupported timeframes {bad}")
        p = s["params"]
        if "min_rr" in p and p["min_rr"] < 0:
            raise ValueError(f"{name}: min_rr must be >= 0")
    if name := next((n for n in ("intraday",) if any(t in ("1D", "1W") for t in cfg["strategies"][n]["timeframes"])), None):
        raise ValueError("intraday strategy accepts only 5m/15m/30m/1H timeframes")
    r = cfg["risk"]
    if r["account_equity"] is not None and r["account_equity"] <= 0:
        raise ValueError("account_equity must be positive or null")
    if not (0 < r["risk_per_trade_pct"] <= 10):
        raise ValueError("risk_per_trade_pct must be in (0, 10]")
    if cfg["data"]["batch_size"] < 1 or cfg["data"]["max_retries"] < 0:
        raise ValueError("batch_size >= 1 and max_retries >= 0 required")
