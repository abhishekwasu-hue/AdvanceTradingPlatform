"""AMW informational breakout policy. Consumes ATP closed-bar facts only."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
from json import dumps
from math import isfinite
from typing import Iterable, Literal

VERSION = "amw-breakout/2"
BREAKOUT_SOURCES = frozenset(("B1", "B2", "HORIZONTAL", "ZONE", "SESSION"))
TIMEFRAMES = frozenset(("1m", "3m", "5m", "15m", "30m", "1h", "1d"))
COMPONENTS = ("level", "candle", "structure", "momentum", "rsi", "retest", "zones", "mtf")
WEIGHTS = {
    "BO-01": (20, 20, 20, 10, 0, 0, 20, 10),
    "BO-02": (15, 15, 15, 10, 15, 0, 20, 10),
    "BO-03": (15, 10, 15, 10, 10, 15, 15, 10),
    "BO-04": (15, 20, 15, 20, 0, 0, 20, 10),
}


@dataclass(frozen=True)
class Settings:
    profile: str = "BO-01"
    direction: str = "BOTH"
    acceptance: str | None = None
    divergence_mode: str | None = None
    opposite_block: bool | None = None
    divergence_types: str = "BOTH_TYPES"
    candle_mode: str = "QUALITY_FACTOR"
    breakout_tf: str = "15m"
    rsi_tfs: tuple[str, ...] = ("15m",)
    demand_tfs: tuple[str, ...] = ("1h",)
    supply_tfs: tuple[str, ...] = ("1h",)
    structure_tf: str = "15m"
    htf_tfs: tuple[str, ...] = ()
    confirmation_tf: str | None = None
    sources: tuple[str, ...] = ("B1", "B2")
    buffer: float = 0.20
    max_extension: float = 1.50
    body_min: float = 0.60
    close_max: float = 0.25
    volume_reference: float = 1.50
    volume_mandatory: bool = False
    permit_proxy_volume: bool = False
    divergence_age: int = 15
    max_age: int = 5
    follow_bars: int = 1
    retest_tolerance: float = 0.25
    zone_block: float = 0.5
    zone_pass: float = 2.0
    swing_degree: int = 1
    rsi_period: int = 14
    min_score: int = 70
    range_mode: str = "fixed"
    range_n: int = 20
    confluence_atr: float = 0.10
    horizontal_lookback: int = 100
    horizontal_tolerance_pct: float = 0.15
    horizontal_min_touches: int = 2

    def __post_init__(self) -> None:
        if self.profile not in WEIGHTS or self.direction not in {"BOTH", "LONG_ONLY", "SHORT_ONLY"}:
            raise ValueError("Unsupported breakout profile or direction")
        acceptance = self.acceptance if self.acceptance is not None else (
            "RETEST_BREAKOUT" if self.profile == "BO-03" else "DIRECT_BREAKOUT"
        )
        divergence = self.divergence_mode if self.divergence_mode is not None else {
            "BO-01": "OFF", "BO-02": "MANDATORY", "BO-03": "QUALITY_FACTOR", "BO-04": "OPPOSITE_BLOCK",
        }[self.profile]
        if acceptance not in {"DIRECT_BREAKOUT", "FOLLOW_THROUGH", "RETEST_BREAKOUT"}:
            raise ValueError("Unsupported acceptance mode. AUTO_SELECT is disabled.")
        if divergence not in {"OFF", "QUALITY_FACTOR", "MANDATORY", "OPPOSITE_BLOCK"}:
            raise ValueError("Unsupported divergence mode")
        if self.divergence_types not in {"REGULAR_ONLY", "HIDDEN_ONLY", "BOTH_TYPES"}:
            raise ValueError("Unsupported divergence types")
        if self.candle_mode not in {"OFF", "QUALITY_FACTOR", "MANDATORY"}:
            raise ValueError("Unsupported candle quality mode")
        if self.profile == "BO-02" and divergence != "MANDATORY":
            raise ValueError("BO-02 requires compatible divergence")
        if self.profile == "BO-03" and acceptance != "RETEST_BREAKOUT":
            raise ValueError("BO-03 requires retest confirmation")
        if self.profile == "BO-04" and divergence != "OPPOSITE_BLOCK":
            raise ValueError("BO-04 requires opposite divergence blocking")
        object.__setattr__(self, "acceptance", acceptance)
        object.__setattr__(self, "divergence_mode", divergence)
        if self.opposite_block is None:
            object.__setattr__(self, "opposite_block", self.profile == "BO-02")
        for name in ("rsi_tfs", "demand_tfs", "supply_tfs", "htf_tfs", "sources"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or len(set(values)) != len(values):
                raise ValueError(f"{name} must be a sequence of distinct values")
            object.__setattr__(self, name, tuple(values))
        for name in ("breakout_tf", "structure_tf", "confirmation_tf"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, value.lower())
        for name in ("rsi_tfs", "demand_tfs", "supply_tfs", "htf_tfs"):
            values = tuple(value.lower() for value in getattr(self, name))
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicate timeframes")
            object.__setattr__(self, name, values)
        tfs = (self.breakout_tf, self.structure_tf, *self.rsi_tfs, *self.demand_tfs, *self.supply_tfs, *self.htf_tfs)
        if not set(tfs) <= TIMEFRAMES or self.confirmation_tf not in (None, self.breakout_tf):
            raise ValueError("Unsupported timeframe or independent confirmation clock")
        if not self.demand_tfs or not self.supply_tfs or (divergence != "OFF" and not self.rsi_tfs):
            raise ValueError("Required sources must have at least one timeframe")
        if not self.sources or not set(self.sources) <= BREAKOUT_SOURCES:
            raise ValueError("Unsupported breakout source")
        if "SESSION" in self.sources and self.breakout_tf == "1d":
            raise ValueError("Previous-session levels require an intraday breakout timeframe")
        for name, low, high in (
            ("max_age", 0, 32), ("divergence_age", 0, 200), ("follow_bars", 1, 32),
            ("swing_degree", 0, 3), ("rsi_period", 2, 200), ("min_score", 55, 100), ("range_n", 2, 500), ("horizontal_lookback", 2, 500), ("horizontal_min_touches", 2, 20),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid {name}")
        if acceptance == "FOLLOW_THROUGH" and self.follow_bars > self.max_age:
            raise ValueError("Follow-through exceeds setup validity")
        if acceptance == "RETEST_BREAKOUT" and self.max_age < 1:
            raise ValueError("Retest requires a subsequent candle")
        if self.range_mode not in {"fixed", "auto"}:
            raise ValueError("Unsupported B1 range mode")
        for name, low, high in (
            ("buffer", 0, 5), ("max_extension", 0, 20), ("body_min", 0, 1), ("close_max", 0, 1),
            ("confluence_atr", 0, 2), ("horizontal_tolerance_pct", 0, 5), ("volume_reference", 0, 20), ("zone_block", 0, 20), ("zone_pass", 0, 20), ("retest_tolerance", 0, 5),
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid {name}")
        if self.zone_pass <= self.zone_block or self.max_extension <= self.buffer:
            raise ValueError("Invalid ordered zone or extension thresholds")

    @property
    def version(self) -> str:
        return sha256(dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    atr: float | None
    relative_volume: float | None = None
    volume_source: str = "unavailable"


@dataclass(frozen=True)
class Candidate:
    key: str
    side: Literal[1, -1]
    level: float
    sources: tuple[str, ...]
    level_quality: float = 1.0


@dataclass(frozen=True)
class Zone:
    key: str
    side: Literal[1, -1]
    low: float
    high: float
    tf: str


@dataclass(frozen=True)
class Divergence:
    side: Literal[1, -1]
    kind: str
    active: bool
    key: str = ""


@dataclass(frozen=True)
class Snapshot:
    at: datetime
    candle: Candle
    candidates: tuple[Candidate, ...] = ()
    zones: tuple[Zone, ...] = ()
    divergences: tuple[Divergence, ...] = ()
    zones_ready: bool = False
    rsi_ready: bool = False
    structure: int = 0
    htf: float = 0.0
    retested_keys: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Decision:
    key: str
    side: int
    detected_at: datetime
    at: datetime
    state: str
    reason: str
    score: int = 0
    grade: str = "REJECT"
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchPolicy:
    """Offline replay overrides, deliberately absent from persisted Settings."""

    zone_gate_enabled: bool = True
    apply_score_floor: bool = True
    score_floor: int | None = None


@dataclass
class _Pending:
    candidate: Candidate
    detected_at: datetime
    detected_index: int
    breakout_price: float
    initial_extension: float
    trigger: Candle
    references: tuple[Candidate, ...]
    follow: int = 0
    retested: bool = False


def candle_metrics(candle: Candle, side: int) -> dict[str, float] | None:
    o, h, lo, c = candle.open, candle.high, candle.low, candle.close
    if not all(isfinite(x) for x in (o, h, lo, c)) or not lo <= min(o, c) <= max(o, c) <= h or h <= lo:
        return None
    width = h - lo
    return {
        "body": abs(c - o) / width, "close": (h - c if side == 1 else c - lo) / width,
        "upper_wick": (h - max(o, c)) / width, "lower_wick": (min(o, c) - lo) / width,
        "signed_body": side * (c - o),
    }


def zone_gate(snapshot: Snapshot, side: int, settings: Settings) -> tuple[str, str, Zone | None, float | None]:
    c = snapshot.candle
    opposite = [z for z in snapshot.zones if z.side == -side]
    if not snapshot.zones_ready or c.atr is None or not isfinite(c.atr) or c.atr <= 0 or not isfinite(c.close):
        return "DATA_UNAVAILABLE", "ZONE_DATA_UNAVAILABLE", None, None
    if any(not isfinite(z.low) or not isfinite(z.high) or z.low >= z.high for z in opposite):
        return "DATA_UNAVAILABLE", "INVALID_ZONE_GEOMETRY", None, None
    contained = sorted((z for z in opposite if z.low <= c.close <= z.high), key=lambda z: (z.tf, z.key))
    if contained:
        return "BLOCK", "INSIDE_OPPOSING_ZONE", contained[0], None
    ahead = [
        (side * ((z.low if side == 1 else z.high) - c.close), z)
        for z in opposite
    ]
    ahead = [(distance, zone) for distance, zone in ahead if distance > 0]
    if not ahead:
        return "PASS", "NO_OPPOSING_ZONE_IN_SCOPE", None, None
    distance, nearest = min(ahead, key=lambda item: (item[0], item[1].tf, item[1].key))
    clearance = distance / c.atr
    if clearance <= settings.zone_block:
        return "BLOCK", "OPPOSING_ZONE_CLOSE", nearest, clearance
    if clearance <= settings.zone_pass:
        return "WAIT", "WAIT_FOR_ZONE_CLEARANCE", nearest, clearance
    return "PASS", "ZONE_CLEARANCE", nearest, clearance


def _decision(p: _Pending, snapshot: Snapshot, state: str, reason: str, **kwargs) -> Decision:
    return Decision(p.candidate.key, p.candidate.side, p.detected_at, snapshot.at, state, reason, **kwargs)


def _evaluate(p: _Pending, snap: Snapshot, index: int, cfg: Settings, research: ResearchPolicy | None) -> Decision:
    c, candidate = snap.candle, p.candidate
    side, level, age = candidate.side, candidate.level, index - p.detected_index
    if age > cfg.max_age:
        return _decision(p, snap, "EXPIRED", "SETUP_AGE")
    if not isfinite(c.close):
        return _decision(p, snap, "WAITING_FOR_DATA", "BREAKOUT_PRICE_UNAVAILABLE")
    distance = side * (c.close - level)
    if distance < 0:
        return _decision(p, snap, "INVALIDATED", "CLOSED_RECLAIM")
    metrics = candle_metrics(c, side)
    if metrics is None or c.atr is None or not isfinite(c.atr) or c.atr <= 0:
        return _decision(p, snap, "WAITING_FOR_DATA", "BREAKOUT_DATA_UNAVAILABLE")
    extension = distance / c.atr
    directional = metrics["signed_body"] > 0
    candle_pass = directional and metrics["body"] >= cfg.body_min and metrics["close"] <= cfg.close_max
    if age > 0 and cfg.acceptance == "FOLLOW_THROUGH":
        p.follow = p.follow + 1 if distance > 0 and directional else 0
    if age > 0 and cfg.acceptance == "RETEST_BREAKOUT":
        extreme = c.low if side == 1 else c.high
        # Existing B9 uses a touch from the broken side, followed by a hold.
        touch = side * (extreme - level) <= cfg.retest_tolerance * c.atr
        if snap.retested_keys is not None and candidate.key.startswith(("B1:", "B2:")):
            touch = candidate.key in snap.retested_keys
        p.retested = p.retested or (touch and distance > 0 and directional)
    if (distance <= 0 or
        (cfg.acceptance == "DIRECT_BREAKOUT" and distance <= cfg.buffer * c.atr) or
        (cfg.acceptance == "FOLLOW_THROUGH" and p.follow < cfg.follow_bars) or
        (cfg.acceptance == "RETEST_BREAKOUT" and not p.retested)):
        return _decision(p, snap, "WAITING_FOR_CONFIRMATION", "ACCEPTANCE_PENDING")
    if extension > cfg.max_extension:
        return _decision(p, snap, "WAITING_FOR_CONFIRMATION", "EXCESSIVE_EXTENSION")
    gate, reason, nearest, clearance = zone_gate(snap, side, cfg)
    if research is not None and not research.zone_gate_enabled:
        gate, reason, nearest, clearance = "PASS", "RESEARCH_ZONE_GATE_DISABLED", None, None
    if gate != "PASS":
        state = "WAITING_FOR_DATA" if gate == "DATA_UNAVAILABLE" else "WAITING_FOR_ZONE_CLEARANCE"
        return _decision(p, snap, state, reason, evidence={"zone_gate": gate, "zone": nearest, "clearance_atr": clearance})
    if not snap.rsi_ready and (cfg.divergence_mode in {"MANDATORY", "OPPOSITE_BLOCK"} or
                               (cfg.divergence_mode != "OFF" and cfg.opposite_block)):
        return _decision(p, snap, "WAITING_FOR_DATA", "DIVERGENCE_DATA_UNAVAILABLE")
    allowed_kind = {"regular", "hidden"} if cfg.divergence_types == "BOTH_TYPES" else {
        cfg.divergence_types.split("_")[0].lower()
    }
    relevant = tuple(d for d in snap.divergences if d.active and d.kind in allowed_kind)
    compatible = snap.rsi_ready and cfg.divergence_mode != "OFF" and any(d.side == side for d in relevant)
    opposing = snap.rsi_ready and cfg.divergence_mode != "OFF" and any(d.side == -side for d in relevant)
    if opposing and (cfg.divergence_mode == "OPPOSITE_BLOCK" or cfg.opposite_block):
        return _decision(p, snap, "WAITING_FOR_CONFIRMATION", "OPPOSITE_DIVERGENCE")
    if cfg.divergence_mode == "MANDATORY" and not compatible:
        return _decision(p, snap, "WAITING_FOR_CONFIRMATION", "COMPATIBLE_DIVERGENCE_REQUIRED")
    if (cfg.profile == "BO-04" or cfg.candle_mode == "MANDATORY") and not candle_pass:
        return _decision(p, snap, "WAITING_FOR_CONFIRMATION", "DIRECTIONAL_CANDLE_REQUIRED")
    volume = c.relative_volume if c.volume_source == "genuine" or (c.volume_source == "proxy" and cfg.permit_proxy_volume) else None
    volume_pass = volume is not None and isfinite(volume) and volume >= cfg.volume_reference
    if cfg.volume_mandatory and not volume_pass:
        return _decision(p, snap, "WAITING_FOR_DATA" if volume is None else "WAITING_FOR_CONFIRMATION", "VOLUME_REQUIRED")
    fractions = (
        candidate.level_quality,
        float(candle_pass and cfg.candle_mode != "OFF"),
        float(snap.structure == side),
        (0.75 if directional else 0) + (0.25 if volume_pass else 0),
        float(compatible and not opposing), float(p.retested),
        1.0, max(0.0, side * snap.htf),
    )
    breakdown = {
        name: {"maximum": weight, "awarded": round(weight * max(0.0, min(1.0, value)), 2)}
        for name, weight, value in zip(COMPONENTS, WEIGHTS[cfg.profile], fractions, strict=True)
    }
    score = round(sum(v["awarded"] for v in breakdown.values()))
    grade = "A1" if score >= 85 else "STRONG" if score >= 70 else "WATCHLIST" if score >= 55 else "REJECT"
    evidence = {
        "rubric": VERSION, "breakdown": breakdown, "config_version": cfg.version,
        "profile": cfg.profile, "sources": candidate.sources, "level": level,
        "breakout_price": p.breakout_price, "reference_price": c.close, "setup_age": age,
        "previous_atr": c.atr, "buffer": cfg.buffer, "initial_extension": p.initial_extension, "extension": extension,
        "candle": metrics, "structure": snap.structure, "zone": nearest, "zone_gate": gate,
        "zone_reason": reason, "clearance_atr": clearance,
        "clearance": None if clearance is None else clearance * c.atr,
        "divergence_conflict": compatible and opposing, "volume_source": c.volume_source,
        "acceptance": cfg.acceptance, "compatible_divergence": compatible,
        "opposing_divergence": opposing, "retested": p.retested, "volume_confirmed": volume_pass,
        "divergence_mode": cfg.divergence_mode,
        "divergences": [asdict(item) for item in relevant],
        "structure_tf": cfg.structure_tf,
        "trigger_ohlc": {name: getattr(p.trigger, name) for name in ("open", "high", "low", "close")},
        "frozen_reference": {
            "id": candidate.key, "price": level, "tolerance_atr": cfg.confluence_atr,
            "sources": [{"kind": source, "id": ref.key, "price": ref.level, "at": p.detected_at.isoformat()}
                        for ref in p.references for source in ref.sources],
        },
        "gates": [
            {"name": "Breakout acceptance", "status": "PASS", "reason": cfg.acceptance},
            {"name": "Extension", "status": "PASS", "reason": "WITHIN_EXTENSION_LIMIT"},
            {"name": "Opposing zone", "status": gate, "reason": reason},
            {"name": "RSI divergence",
             "status": "PASS" if cfg.divergence_mode in {"MANDATORY", "OPPOSITE_BLOCK"} or
             (cfg.divergence_mode != "OFF" and cfg.opposite_block) else "NOT_APPLICABLE",
             "reason": cfg.divergence_mode},
            {"name": "Directional candle",
             "status": "PASS" if cfg.profile == "BO-04" or cfg.candle_mode == "MANDATORY" else "NOT_APPLICABLE",
             "reason": "DIRECTIONAL_CANDLE_REQUIRED" if cfg.profile == "BO-04" else cfg.candle_mode},
            {"name": "Volume", "status": "PASS" if cfg.volume_mandatory else "NOT_APPLICABLE",
             "reason": "MANDATORY" if cfg.volume_mandatory else "OPTIONAL"},
        ],

    }
    floor = max(70, cfg.min_score)
    if research is not None:
        if not research.apply_score_floor:
            floor = 0
        elif research.score_floor is not None:
            floor = research.score_floor
    publish = score >= floor
    return _decision(p, snap, "CONFIRMED" if publish else "WAITING_FOR_CONFIRMATION",
                     "ELIGIBLE" if publish else "SCORE_BELOW_PUBLICATION", score=score, grade=grade, evidence=evidence)


def replay(snapshots: Iterable[Snapshot], settings: Settings, *, research: ResearchPolicy | None = None) -> Iterable[Decision]:
    """Replay ascending closed decisions, reconstructing pending state without I/O."""
    pending: dict[str, _Pending] = {}
    previous: datetime | None = None
    terminal: dict[str, int] = {}
    for index, snapshot in enumerate(snapshots):
        if snapshot.at.tzinfo is None or snapshot.at.utcoffset() is None or (previous is not None and snapshot.at <= previous):
            raise ValueError("Expected strictly increasing timezone-aware decision closes")
        previous = snapshot.at
        terminal = {key: i for key, i in terminal.items() if index - i <= settings.max_age}
        grouped: dict[int, list[Candidate]] = {}
        for candidate in snapshot.candidates:
            if candidate.side not in (-1, 1) or not isfinite(candidate.level):
                raise ValueError("Invalid breakout candidate")
            if settings.direction == ("LONG_ONLY" if candidate.side == -1 else "SHORT_ONLY"):
                continue
            if candidate.key in pending or candidate.key in terminal:
                continue
            atr = snapshot.candle.atr
            if atr is None or not isfinite(atr) or atr <= 0:
                continue
            if candidate.side * (snapshot.candle.close - candidate.level) > settings.buffer * atr:
                grouped.setdefault(candidate.side, []).append(candidate)
        for side, candidates in grouped.items():
            assert snapshot.candle.atr is not None
            tolerance = settings.confluence_atr * snapshot.candle.atr
            ordered = sorted(candidates, key=lambda c: (side * c.level, c.key))
            clusters: list[list[Candidate]] = []
            for candidate in ordered:
                if not clusters or abs(candidate.level - clusters[-1][0].level) > tolerance:
                    clusters.append([])
                clusters[-1].append(candidate)
            for cluster in clusters:
                canonical = min(cluster, key=lambda c: ("B2" not in c.sources, c.key))
                sources = tuple(sorted({source for c in cluster for source in c.sources}))
                event = Candidate(canonical.key, side, canonical.level, sources, canonical.level_quality)
                terminal.update({c.key: index for c in cluster if c.key != event.key})
                pending[event.key] = _Pending(event, snapshot.at, index, snapshot.candle.close,
                                              side * (snapshot.candle.close - event.level) / snapshot.candle.atr,
                                              snapshot.candle, tuple(cluster))
        decisions = [_evaluate(item, snapshot, index, settings, research) for item in pending.values()]
        eligible = [d for d in decisions if d.state == "CONFIRMED"]
        conflict = len({d.side for d in eligible}) > 1
        for decision in decisions:
            if decision.state in {"EXPIRED", "INVALIDATED"}:
                pending.pop(decision.key)
                terminal[decision.key] = index
            if decision.state == "CONFIRMED":
                if conflict:
                    yield _decision(pending[decision.key], snapshot, "WAITING_FOR_CONFIRMATION", "DIRECTION_CONFLICT",
                                    score=decision.score, grade=decision.grade, evidence=decision.evidence)
                    continue
                item = pending.pop(decision.key)
                terminal[decision.key] = index
                yield _decision(item, snapshot, "SIGNAL_GENERATED", "ELIGIBLE",
                                score=decision.score, grade=decision.grade, evidence=decision.evidence)
                continue
            yield decision
