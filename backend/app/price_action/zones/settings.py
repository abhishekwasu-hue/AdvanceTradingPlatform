"""Market-zone settings - one tenant's knobs, with the Trade repository's values as the defaults.

Ported from Trade@eafc872 (market_zones.py, signals.py, sr_dynamic.py, level_memory.py, sr_levels_v3.py SRConfig and
mtf_pullback_strategy.find_overnight_gaps): every number those modules hard-code (or take as a default argument) is a
field here, so a tenant can edit it. Layer platform defaults -> tenant -> symbol with `resolve_settings` (the same
pattern as the OI banner settings); unknown keys are refused, values are range-checked by pydantic.

Timeframe labels are ATP's ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"). Trade's "60M" dynamic S/R is ATP's "1h".
The memory fields (zone tolerance, retire days, max distance, max count) feed `memory.py` (Trade level_memory.py); the
lifecycle fields (flip closes / buffer, structural swing order / buffer) feed `lifecycle.track_zone`; the history fields
tell the storage job (`job.py`) how many days of bars to read per timeframe.
"""
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

ATP_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")
# Bar length in minutes - a bar stamped t (its open) is complete at t + length (used by every as_of cut).
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
KEY_LEVEL_NAMES = ("PDH", "PDL", "PDC", "PWH", "PWL")


def _check_tf_keys(name: str, value: Mapping[str, Any]) -> None:
    bad = [k for k in value if k not in ATP_TIMEFRAMES]
    if bad:
        raise ValueError(f"{name}: unknown timeframes {bad}; each one of {ATP_TIMEFRAMES}")


def _check_hhmm(v: str) -> str:
    parts = v.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise ValueError(f"{v!r} is not a valid HH:MM time")
    return v
# ATP timeframe -> the label Trade uses in its zone_type names (DYNAMIC_SR_SUPPORT_15M ...). Kept for exports and parity.
TRADE_TF_SUFFIX = {"1m": "1M", "5m": "5M", "15m": "15M", "30m": "30M", "1h": "60M"}


class SRV3Settings(BaseModel):
    """Scored, multi-timeframe S/R levels (Trade sr_levels_v3.SRConfig) - field for field, Trade defaults."""
    model_config = ConfigDict(extra="forbid")

    prd: Dict[str, int] = Field(default_factory=lambda: {"5m": 10, "15m": 10, "30m": 10, "1h": 10, "4h": 5, "1d": 5, "1w": 3})
    lookback_days: Dict[str, float] = Field(default_factory=lambda: {"5m": 3.0, "15m": 5.0, "30m": 8.0, "1h": 10.0, "4h": 45.0, "1d": 250.0,
                                                                     "1w": 1100.0})
    tf_factor: Dict[str, float] = Field(default_factory=lambda: {"5m": 0.6, "15m": 1.0, "30m": 1.4, "1h": 2.0, "4h": 2.5, "1d": 3.0,
                                                                 "1w": 3.5})
    tf_confluence_points: Dict[str, float] = Field(default_factory=lambda: {"5m": 3.0, "15m": 5.0, "30m": 7.0, "1h": 10.0, "4h": 12.0,
                                                                            "1d": 14.0, "1w": 16.0})
    recency_half_life_days: float = Field(default=2.5, ge=0)
    recency_half_life_by_tf: Dict[str, float] = Field(default_factory=lambda: {"4h": 7.0, "1d": 30.0, "1w": 120.0})
    atr_period: int = Field(default=14, ge=1, le=500)
    reaction_atr_full: float = Field(default=3.0, gt=0)
    tol_atr_mult: float = Field(default=0.5, ge=0)
    tol_min_pct: float = Field(default=0.08, ge=0)
    tol_max_pct: float = Field(default=0.40, ge=0)
    w_touch: float = Field(default=30.0, ge=0)
    touch_full: float = Field(default=3.0, gt=0)
    w_reaction: float = Field(default=20.0, ge=0)
    w_confluence_cap: float = Field(default=25.0, ge=0)
    w_key_cap: float = Field(default=40.0, ge=0)
    w_polarity: float = Field(default=5.0, ge=0)
    w_flip: float = Field(default=10.0, ge=0)
    w_retest: float = Field(default=5.0, ge=0)
    w_gap: float = Field(default=10.0, ge=0)
    w_reject: float = Field(default=10.0, ge=0)
    reject_full: float = Field(default=4.0, gt=0)
    reject_band_frac: float = Field(default=0.5, ge=0)
    reject_depart_atr: float = Field(default=0.5, ge=0)
    reject_follow_atr: float = Field(default=1.0, ge=0)
    reject_follow_bars: int = Field(default=4, ge=0, le=500)
    key_anchor_score: float = Field(default=1.5, ge=0)
    key_level_points: Dict[str, float] = Field(default_factory=lambda: {"PDH": 30.0, "PDL": 30.0, "PDC": 15.0, "PWH": 35.0, "PWL": 35.0})
    flip_accept_ratio: float = Field(default=0.6, ge=0, le=1)
    flip_min_beyond: int = Field(default=2, ge=0, le=500)
    flip_band_frac: float = Field(default=0.5, ge=0)
    zone_touch_pts: float = Field(default=5.0, ge=0)
    zone_touch_tol_frac: float = Field(default=0.15, ge=0)
    gap_min_pct: float = Field(default=0.20, ge=0)
    gap_max_age_days: int = Field(default=10, ge=0, le=10000)
    break_buffer_pct: float = Field(default=0.10, ge=0)
    break_confirm_bars: int = Field(default=2, ge=1, le=500)
    flip_frame: str = "15m"
    grade_a: float = Field(default=65.0, ge=0, le=100)
    grade_b: float = Field(default=45.0, ge=0, le=100)
    min_score: float = Field(default=25.0, ge=0, le=100)
    max_levels: int = Field(default=12, ge=0, le=500)
    max_distance_pct: float = Field(default=3.0, ge=0)
    session_end: str = Field(default="15:15", pattern=r"^\d{1,2}:\d{2}$")
    chop_filter: bool = False
    chop_lookback_days: float = Field(default=5.0, ge=0)
    chop_max_crossings: int = Field(default=6, ge=0, le=10000)
    reject_redepart: bool = False
    flip_min_break_atr: float = Field(default=0.0, ge=0)
    min_base_points: float = Field(default=0.0, ge=0)

    @field_validator("prd", "lookback_days", "tf_factor", "tf_confluence_points", "recency_half_life_by_tf")
    @classmethod
    def _tf_dicts(cls, v: Dict[str, Any], info: ValidationInfo) -> Dict[str, Any]:
        name = info.field_name or "per-timeframe setting"
        _check_tf_keys(name, v)
        if any(x < 0 for x in v.values()):
            raise ValueError(f"{name}: values must not be negative")
        if name == "prd" and any(x < 1 for x in v.values()):
            raise ValueError("prd: every pivot window must be >= 1")
        return v

    @field_validator("key_level_points")
    @classmethod
    def _key_names(cls, v: Dict[str, float]) -> Dict[str, float]:
        bad = [k for k in v if k not in KEY_LEVEL_NAMES]
        if bad or any(x < 0 for x in v.values()):
            raise ValueError(f"key_level_points: names must be among {KEY_LEVEL_NAMES} and points >= 0 (got {bad})")
        return v

    @field_validator("flip_frame")
    @classmethod
    def _flip_tf(cls, v: str) -> str:
        if v not in ATP_TIMEFRAMES:
            raise ValueError(f"unknown timeframe {v!r}; one of {ATP_TIMEFRAMES}")
        return v

    @field_validator("session_end")
    @classmethod
    def _session_end(cls, v: str) -> str:
        return _check_hhmm(v)


class ZoneSettings(BaseModel):
    """Detection knobs for one tenant (or one tenant + symbol). Defaults = the Trade values the owner checked on charts."""
    model_config = ConfigDict(extra="forbid")

    # --- impulse / order block / demand-supply (market_zones.detect_order_blocks / detect_demand_supply_zones) -------------
    impulse_mult: float = Field(default=1.5, gt=0)          # impulsive candle: body >= this x average range
    avg_window: int = Field(default=20, ge=2, le=500)       # average range over this many bars (current bar included)
    avg_min_periods: int = Field(default=5, ge=1, le=500)   # fewer bars than this -> no average yet
    base_lookback: int = Field(default=4, ge=1, le=50)      # a base is at most this many candles
    base_tightness_mult: float = Field(default=0.7, gt=0)   # each base candle's range <= this x the impulse bar's average
    # --- classical S/R (signals.find_support_resistance_levels) -----------------------------------------------------------
    sr_swing_order: int = Field(default=3, ge=1, le=100)
    sr_cluster_tolerance_pct: float = Field(default=0.3, ge=0)
    sr_top_n: int = Field(default=5, ge=1, le=100)
    sr_min_bars: int = Field(default=20, ge=1, le=10000)
    major_swing_min_move_pct: float = Field(default=0.5, ge=0)
    # --- dynamic S/R (sr_dynamic.compute_dynamic_sr - the Pine "Support Resistance - Dynamic v2" port) ---------------------
    dyn_prd: int = Field(default=10, ge=1, le=200)
    dyn_maxnumpp: int = Field(default=20, ge=1, le=500)
    dyn_channel_w_pct: float = Field(default=10.0, gt=0, le=100)
    dyn_channel_lookback: int = Field(default=300, ge=1, le=10000)
    dyn_maxnumsr: int = Field(default=5, ge=1, le=100)
    dyn_min_strength: int = Field(default=2, ge=1, le=500)
    dyn_min_bars: int = Field(default=100, ge=1, le=100000)
    dyn_mintick: Optional[float] = Field(default=None, gt=0)
    # --- unfilled gaps (mtf_pullback_strategy.find_overnight_gaps) --------------------------------------------------------
    gap_min_pct: float = Field(default=0.30, ge=0)
    session_timezone: str = "Asia/Kolkata"                   # tz-aware timestamps are read in this zone to find the session
    # --- order-block retest (signals.find_order_block / is_retesting_order_block) ----------------------------------------
    ob_impulse_lookforward: int = Field(default=5, ge=1, le=200)
    ob_impulse_min_move_pct: float = Field(default=0.3, ge=0)
    ob_search_lookback: int = Field(default=20, ge=1, le=1000)
    ob_retest_tolerance_pct: float = Field(default=0.1, ge=0)
    # --- 5m / 15m confluence table (market_zones.compute_5m_15m_confluence_row) -------------------------------------------
    confluence_swing_order: int = Field(default=3, ge=1, le=100)
    confluence_sr_top_n: int = Field(default=10, ge=1, le=100)
    confluence_levels_per_side: int = Field(default=3, ge=1, le=10)
    # --- timeframes -------------------------------------------------------------------------------------------------------
    sr_timeframe: str = "1h"                                 # classical S/R
    zone_timeframe: str = "1h"                               # order blocks + demand / supply
    gap_timeframe: str = "15m"                               # unfilled gaps
    dynamic_sr_timeframes: List[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "30m", "1h"])
    intraday_sr_timeframes: List[str] = Field(default_factory=lambda: ["15m", "30m", "1h"])
    confluence_timeframes: List[str] = Field(default_factory=lambda: ["5m", "15m"])
    # --- level memory (level_memory.zone_tolerance / merge_levels) - used by the Z7a-2 job ---------------------------------
    memory_atr_period: int = Field(default=14, ge=1, le=500)
    memory_atr_mult: float = Field(default=0.5, ge=0)
    memory_tol_min_pct: float = Field(default=0.08, ge=0)
    memory_tol_max_pct: float = Field(default=0.40, ge=0)
    memory_retire_days: int = Field(default=30, ge=0, le=3650)
    memory_retire_days_by_tf: Dict[str, int] = Field(default_factory=lambda: {"1m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 30})
    memory_max_distance_pct: float = Field(default=15.0, ge=0)
    memory_max_levels: int = Field(default=12, ge=1, le=500)
    memory_enabled: bool = True                              # Trade level_memory_enabled (default on)
    memory_families: List[str] = Field(default_factory=lambda: ["DYNAMIC_SR"])  # remembered line families (Trade: dynamic S/R
    #                                                          only); add "SR" to remember classical S/R too
    # --- zone lifecycle (ATP rule, Z7a-2; Trade has no lifecycle - defaults borrow sr_levels_v3's break rule) -------------
    flip_confirm_closes: int = Field(default=2, ge=1, le=50)       # closes beyond the far edge in a row = closed through and held
    flip_buffer_pct: float = Field(default=0.10, ge=0, le=10)      # a close counts as beyond when this % past the edge
    structure_swing_order: int = Field(default=3, ge=1, le=100)    # fractal swing order of the structural swing beyond a zone
    structure_buffer_pct: float = Field(default=0.10, ge=0, le=10) # a close must be this % past the structural swing to break it
    max_events_kept: int = Field(default=50, ge=0, le=1000)        # lifecycle events stored per zone (the most recent ones)
    # --- storage job (Z7a-2): history read from the lake per run --------------------------------------------------------
    history_days: int = Field(default=365, ge=5, le=3650)    # S/R, order blocks, demand / supply, gaps (Trade: one year)
    max_bars_per_frame: int = Field(default=20000, ge=100, le=200000)   # the job reads at most this many bars per frame
    warmup_bars: int = Field(default=50, ge=0, le=5000)      # stored zones formed in a frame's first bars are dropped (the
    #                                                          window start moves every night; an edge zone would flicker)
    recent_days_by_tf: Dict[str, int] = Field(default_factory=lambda: {"1m": 5, "5m": 20, "15m": 180, "30m": 180, "1h": 180})
    # --- scored multi-timeframe levels (sr_levels_v3) ---------------------------------------------------------------------
    sr_v3: SRV3Settings = Field(default_factory=SRV3Settings)

    @field_validator("sr_timeframe", "zone_timeframe", "gap_timeframe")
    @classmethod
    def _known_tf(cls, v: str) -> str:
        if v not in ATP_TIMEFRAMES:
            raise ValueError(f"unknown timeframe {v!r}; one of {ATP_TIMEFRAMES}")
        return v

    @field_validator("session_timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"{v!r} is not an IANA time zone") from exc
        return v

    @field_validator("memory_retire_days_by_tf")
    @classmethod
    def _retire_tfs(cls, v: Dict[str, int]) -> Dict[str, int]:
        _check_tf_keys("memory_retire_days_by_tf", v)
        if any(x < 0 for x in v.values()):
            raise ValueError("memory_retire_days_by_tf: days must not be negative")
        return v

    @field_validator("dynamic_sr_timeframes", "intraday_sr_timeframes", "confluence_timeframes")
    @classmethod
    def _known_tfs(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in ATP_TIMEFRAMES]
        if bad:
            raise ValueError(f"unknown timeframes {bad}; each one of {ATP_TIMEFRAMES}")
        return list(dict.fromkeys(v))


    @model_validator(mode="after")
    def _consistent(self) -> "ZoneSettings":
        if self.avg_min_periods > self.avg_window:
            raise ValueError("avg_min_periods must not exceed avg_window")
        if self.memory_tol_min_pct > self.memory_tol_max_pct or self.sr_v3.tol_min_pct > self.sr_v3.tol_max_pct:
            raise ValueError("a tolerance minimum must not exceed its maximum")
        bad_fam = [f for f in self.memory_families if f not in ("SR", "DYNAMIC_SR")]
        if bad_fam:
            raise ValueError(f"memory_families: unknown {bad_fam}; each one of SR, DYNAMIC_SR")
        bad_recent = [tf for tf, d in self.recent_days_by_tf.items() if tf not in ATP_TIMEFRAMES or not 1 <= int(d) <= 3650]
        if bad_recent:
            raise ValueError(f"recent_days_by_tf: unknown timeframe or days outside 1..3650 for {bad_recent}")
        if self.sr_v3.grade_b > self.sr_v3.grade_a:
            raise ValueError("sr_v3.grade_b must not exceed sr_v3.grade_a")
        return self


def _merge_layer(merged: Dict[str, Any], layer: Mapping[str, Any], model: Any, where: str) -> None:
    """Fold one override layer into `merged`: unknown keys refused; a dict-valued setting (per-timeframe maps) merges key
    by key over the value so far (the default first), so overriding one timeframe keeps the others."""
    defaults = model()
    for key, value in layer.items():
        if key not in model.model_fields:
            raise ValueError(f"unknown {where} setting {key!r}")
        current = getattr(defaults, key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            value = {**merged.get(key, current), **value}
        merged[key] = value


def resolve_settings(*layers: Optional[Mapping[str, Any]]) -> ZoneSettings:
    """Platform defaults, then each override layer in order (tenant, then symbol). Unknown keys are refused; the nested
    `sr_v3` section and every per-timeframe map merge key by key (e.g. {"sr_v3": {"prd": {"5m": 3}}} changes only 5m)."""
    merged: Dict[str, Any] = {}
    v3: Dict[str, Any] = {}
    for layer in layers:
        layer = dict(layer or {})
        if "sr_v3" in layer:
            sub = layer.pop("sr_v3")
            if not isinstance(sub, Mapping):
                raise ValueError("sr_v3 must be a mapping of settings")
            _merge_layer(v3, sub, SRV3Settings, "sr_v3")
        _merge_layer(merged, layer, ZoneSettings, "zone")
    if v3:
        merged["sr_v3"] = v3
    return ZoneSettings.model_validate(merged)
