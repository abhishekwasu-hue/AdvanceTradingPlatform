"""Strategy Lab - unified, language-independent strategy contract (schema version 1).

INPUT CONTEXT (identical for every language; built ONLY by the host, never by a runtime)
  meta    : {symbol, ticker, exchange, timeframe, timezone, provider, data_kind, data_status,
             last_completed_bar (ISO), bars (n), schema_version}
  series  : arrays of equal length n, oldest -> newest, index n-1 = LAST COMPLETED BAR (never a forming bar):
             time, open, high, low, close, volume, ema20, ema50, ema200, sma20, sma50, rsi14, macd, macd_signal,
             macd_hist, atr14, adx, plus_di, minus_di, rvol, vwap, bb_up, bb_low
             missing value = null (Python None / JS null / Lua NaN / R NA). Warm-up bars are missing, never 0.
  structure: {trend_score, trend_label, structure_trend, swings:[{index,kind,label,price,confirmed_index}],
              events:[{index,type,direction,level}], zones:[{kind,name,top,bottom,status}],
              sr:[{level,role,touches}]}   (only CONFIRMED swings; index refers to `series`)
  htf     : {tf: {trend_score, trend_label, structure_trend, close, ema20, ema50, rsi14}} for supported higher TFs
  params  : validated parameters from the strategy's parameter schema

RESULT (validated by `validate_result`; malformed / non-finite -> rejected with a validation error)
  {status: "qualified"|"rejected", bias: "long"|"short"|"neutral", classification: str,
   rules: [{name: str, passed: bool|null (omitted = null), detail: str}], entry: str|null, invalidation: number|null,
   stop: number|null, target: number|null, explanation: str}
  The signal timestamp is ALWAYS the host's last completed bar; runtimes cannot set timestamps.
"""
from __future__ import annotations

import math
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator

SCHEMA_VERSION = 1
API_VERSION = "1.0"
SERIES = ["open", "high", "low", "close", "volume", "ema20", "ema50", "ema200", "sma20", "sma50", "rsi14", "macd",
          "macd_signal", "macd_hist", "atr14", "adx", "plus_di", "minus_di", "rvol", "vwap", "bb_up", "bb_low",
          "supertrend", "st_dir"]
_COL = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
LANGUAGES = ["python", "pine", "javascript", "typescript", "sql", "lua", "r", "visual"]


# ------------------------------------------------------------------ strategy spec
class ParamSpec(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
    type: Literal["int", "float", "bool", "str"] = "float"
    default: Any = None
    min: float | None = None
    max: float | None = None
    description: str = ""


class ResourceLimits(BaseModel):
    timeout_s: float = Field(default=10, gt=0, le=120)
    memory_mb: int = Field(default=256, ge=32, le=2048)
    max_output_kb: int = Field(default=256, ge=1, le=4096)


class StrategySpec(BaseModel):
    schema_version: int = SCHEMA_VERSION
    api_version: str = API_VERSION
    language: Literal["python", "pine", "javascript", "typescript", "sql", "lua", "r", "visual"]
    timeframes: list[Literal["1W", "1D", "1H", "30m", "15m", "5m"]] = ["1D"]
    htf: list[Literal["1W", "1D", "1H", "30m", "15m"]] = []
    lookback: int = Field(default=200, ge=30, le=1000)       # bars passed to the runtime (incl. warm-up)
    min_bars: int = Field(default=60, ge=1, le=1000)          # fewer valid bars -> INSUFFICIENT_DATA, not evaluated
    params: list[ParamSpec] = []
    universe: str = "any NSE equity"
    data_quality: Literal["OK_only", "OK_or_STALE"] = "OK_only"
    permissions: list[str] = []                                # always empty: no network/fs/broker access is grantable
    limits: ResourceLimits = ResourceLimits()

    @field_validator("permissions")
    @classmethod
    def _noperm(cls, v):
        if v:
            raise ValueError("custom strategies cannot be granted permissions (network/filesystem/broker)")
        return v


def resolve_params(spec: StrategySpec, values: dict | None) -> dict:
    out = {}
    values = values or {}
    for p in spec.params:
        v = values.get(p.name, p.default)
        try:
            v = {"int": int, "float": float, "bool": lambda x: str(x).lower() in ("1", "true", "yes") if isinstance(x, str) else bool(x),
                 "str": str}[p.type](v)
        except (TypeError, ValueError):
            raise ValueError(f"parameter {p.name}: invalid {p.type} value {v!r}")
        if p.type in ("int", "float") and ((p.min is not None and v < p.min) or (p.max is not None and v > p.max)):
            raise ValueError(f"parameter {p.name}={v} outside [{p.min}, {p.max}]")
        out[p.name] = v
    return out


# ------------------------------------------------------------------ input context
def _arr(x) -> list:
    a = np.asarray(x, dtype=float)
    return [None if not np.isfinite(v) else round(float(v), 6) for v in a]


def build_context(t, params: dict, meta: dict, htf: dict | None = None, lookback: int = 200) -> dict:
    """t = TFAnalysis of COMPLETED bars (validated). Truncation to `lookback` keeps index n-1 = last completed bar."""
    df = t.df.iloc[-lookback:]
    off = len(t.df) - len(df)
    series = {"time": [str(x) for x in df.index]}
    for k in SERIES:
        col = _COL.get(k, k)
        series[k] = _arr(df[col]) if col in df else [None] * len(df)
    sw = [{"index": s.i - off, "kind": s.kind, "label": s.label, "price": s.price, "confirmed_index": s.confirmed_at - off}
          for s in t.swings if s.i >= off]
    ev = [{"index": e.i - off, "type": e.type, "direction": e.direction, "level": e.level} for e in t.events if e.i >= off]
    return {
        "meta": {**meta, "bars": len(df), "last_completed_bar": series["time"][-1], "schema_version": SCHEMA_VERSION},
        "series": series,
        "structure": {"trend_score": t.trend_score, "trend_label": t.trend_label, "structure_trend": t.structure_trend,
                      "swings": sw, "events": ev,
                      "zones": [{k: z[k] for k in ("kind", "name", "top", "bottom", "status")} for z in t.zones],
                      "sr": [{k: s[k] for k in ("level", "role", "touches", "origin", "role_reversal", "bars_since_touch")
                              if k in s} for s in t.sr],
                      "candles": [{"index": c["i"] - off, "pattern": c["pattern"], "bias": c["bias"], "status": c["status"]}
                                  for c in t.candles if c["i"] >= off],
                      "gaps": [{"index": g["i"] - off, "direction": g["direction"], "size_atr": g["size_atr"],
                                "filled": g["filled"]} for g in getattr(t, "gaps", []) if g["i"] >= off]},
        "htf": {tf: ({"trend_score": a.trend_score, "trend_label": a.trend_label, "structure_trend": a.structure_trend,
                      "close": a.close, "ema20": a.last.get("ema20"), "ema50": a.last.get("ema50"), "rsi14": a.last.get("rsi14")}
                     if a is not None else None) for tf, a in (htf or {}).items()},
        "params": params,
    }


# ------------------------------------------------------------------ result schema
class RuleOut(BaseModel):
    name: str = Field(max_length=200)
    passed: bool | None = None          # omitted = null (missing) - needed for Lua where nil removes the key
    detail: str = Field(default="", max_length=500)


class ResultOut(BaseModel):
    status: Literal["qualified", "rejected"]
    bias: Literal["long", "short", "neutral"] = "neutral"
    classification: str = Field(default="custom", max_length=100)
    rules: list[RuleOut] = Field(default_factory=list, max_length=100)
    entry: str | None = Field(default=None, max_length=300)
    invalidation: float | None = None
    stop: float | None = None
    target: float | None = None
    explanation: str = Field(default="", max_length=2000)

    @field_validator("invalidation", "stop", "target")
    @classmethod
    def _finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("non-finite number")
        return v


@dataclass
class LabResult:
    ok: bool                       # result passed schema validation
    result: dict | None
    signal_time: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    logs: str = ""
    runtime: str = ""
    elapsed_s: float = 0.0


def validate_result(raw: Any, close: float | None = None) -> tuple[dict | None, list[str], list[str]]:
    """Return (normalised result, errors, warnings)."""
    if not isinstance(raw, dict):
        return None, [f"strategy must return an object/dict, got {type(raw).__name__}"], []
    try:
        r = ResultOut.model_validate(raw).model_dump()
    except ValidationError as e:
        return None, [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()][:10], []
    warns = []
    if r["status"] == "qualified" and r["bias"] == "neutral":
        warns.append("qualified with neutral bias - cannot be backtested as a trade")
    if r["stop"] is not None and close is not None and r["bias"] in ("long", "short"):
        wrong = (r["bias"] == "long" and r["stop"] >= close) or (r["bias"] == "short" and r["stop"] <= close)
        if wrong:
            return None, [f"stop {r['stop']} is on the wrong side of close {close} for a {r['bias']} result"], warns
    return r, [], warns


# ------------------------------------------------------------------ release gating
def enabled_languages(cfg: dict | None = None) -> list[str]:
    """Released modes from settings, optionally restricted by the operator via AMW_LAB_LANGUAGES."""
    import os
    if cfg is None:
        from ..config import default_settings
        cfg = default_settings()
    langs = [x for x in cfg.get("strategy_lab", {}).get("enabled_languages", ["python", "pine"]) if x in LANGUAGES]
    env = os.environ.get("AMW_LAB_LANGUAGES")
    if env:
        allow = {x.strip().lower() for x in env.split(",") if x.strip()}
        langs = [x for x in langs if x in allow]
    return langs


def language_enabled(lang: str, cfg: dict | None = None) -> bool:
    return lang in enabled_languages(cfg)


# ------------------------------------------------------------------ capability matrix (truthful, computed)
def capabilities() -> dict[str, dict]:
    """Capabilities are DETECTED at runtime (tool availability + sandbox self-test), not hard-coded claims."""
    from .sandbox import sandbox_status
    sb = sandbox_status()
    node = shutil.which("node") is not None
    rs = shutil.which("Rscript") is not None
    try:
        import lupa  # noqa: F401
        lua = True
    except ImportError:
        lua = False
    try:
        import sqlglot  # noqa: F401
        sql = True
    except ImportError:
        sql = False
    exec_ok = sb["verified"] or sb["unsandboxed_allowed"]

    def row(edit, syntax, strat, native, sandbox, scan, bt, limits):
        return {"editing": edit, "syntax_validation": syntax, "strategy_validation": strat, "native_execution": native,
                "sandbox_verified": sandbox, "scanner_integration": scan, "backtesting": bt, "limitations": limits}

    return {
        "visual": row(True, True, True, True, "n/a (no user code; host rule engine)", True, True,
                      "Fixed operator/field/predicate catalogue; nesting depth limited."),
        "pine": row(True, True, True, "subset -> visual IR (no Pine runtime)", "n/a (translated, not executed)", True, True,
                    "Supported subset only (see PINE_SUBSET). No strategy.*, request.*, var, loops, user functions. NOT TradingView-compatible in general."),
        "python": row(True, True, True, exec_ok, sb["verified"], exec_ok, exec_ok,
                      "Std-lib subset (math, statistics); no imports beyond allowlist; audit-hook + process sandbox."),
        "javascript": row(True, node, node, node and exec_ok, node and sb["verified"], node and exec_ok, node and exec_ok,
                          "Node vm context, no require/modules; Node permission model + process sandbox."),
        "typescript": row(True, node, node, node and exec_ok, node and sb["verified"], node and exec_ok, node and exec_ok,
                          "Type-checked against the AMW SDK typings, transpiled (TypeScript pinned) then run as JavaScript."),
        "sql": row(True, sql, sql, sql and exec_ok, sql and sb["verified"], sql and exec_ok, sql and exec_ok,
                   "Single read-only SELECT over approved views `bars` and `latest`. Per-symbol in scans; no stateful trade logic."),
        "lua": row(True, lua, lua, lua and exec_ok, lua and sb["verified"], lua and exec_ok, lua and exec_ok,
                   "Lua 5.4 (lupa) with os/io/package/debug/load removed, memory + instruction limits. Arrays are 1-based; missing = NaN."),
        "r": row(True, rs, rs, rs and exec_ok, rs and sb["verified"], rs and exec_ok, rs and exec_ok,
                 "Base R + stats only; library() allowlist; process sandbox. No package installation. Missing = NA."),
        "sandbox_status": sb,
        "_enabled": enabled_languages(),
        "_python": sys.version.split()[0],
    }
