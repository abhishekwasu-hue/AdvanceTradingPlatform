"""Read-only event-time replay for authorized exported ATP candles."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from statistics import mean, median
from time import perf_counter

import pandas as pd

from app.screener.breakouts.amw_analysis import iter_snapshots
from app.screener.breakouts.amw_breakout import ResearchPolicy, Settings, replay
from app.screener.breakouts.session import calendar_for

HORIZONS = (1, 3, 5, 10, 20)
SENSITIVITY = {
    "buffer": (0.10, 0.20, 0.30), "max_extension": (1.0, 1.5, 2.0),
    "divergence_age": (5, 10, 15, 20), "zone_pass": (1.0, 1.5, 2.0, 2.5, 3.0),
    "max_age": (3, 5, 8, 10), "min_score": (65, 70, 75, 80, 85),
    "body_min": (0.50, 0.60, 0.70), "volume_reference": (1.2, 1.5, 2.0),
}


def _read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for name in ("start", "bar_end"):
        parsed = [pd.Timestamp(value) for value in frame[name]]
        if any(pd.isna(value) or value.tzinfo is None for value in parsed):
            raise ValueError(f"{path.name}: {name} must have an explicit timezone")
        frame[name] = pd.DatetimeIndex(parsed).tz_convert("UTC")
    if frame.empty:
        raise ValueError(f"{path.name}: empty frame")
    return frame.set_index("start")


def signal_outcome(snapshots, position, decision, horizon, *, stop=None):
    """Measure only subsequent complete outcome bars within the supplied boundary."""
    end = len(snapshots) if stop is None else stop
    future = snapshots[position + 1:min(position + horizon + 1, end)]
    if len(future) != horizon:
        return None
    side = decision.side
    price, atr, level = (decision.evidence[key] for key in ("reference_price", "previous_atr", "level"))
    points = side * (future[-1].candle.close - price)
    favorable = [side * ((row.candle.high if side == 1 else row.candle.low) - price) for row in future]
    adverse = [side * ((row.candle.low if side == 1 else row.candle.high) - price) for row in future]
    mfe, mae = max(0, max(favorable)), max(0, -min(adverse))
    first_failure = next((i for i, row in enumerate(future, 1) if side * (row.candle.close - level) < 0), None)
    return {
        "outcome_horizon": horizon, "directional_points": points,
        "directional_return_pct": 100 * points / price, "directional_atr": points / atr,
        "mfe_points": mfe, "mae_points": mae, "mfe_atr": mfe / atr, "mae_atr": mae / atr,
        "failed_breakout": first_failure is not None, "first_failure_bar": first_failure,
        "same_session": pd.Timestamp(decision.at).tz_convert("Asia/Kolkata").date()
        == pd.Timestamp(future[-1].at).tz_convert("Asia/Kolkata").date(),
        **{f"first_mfe_{target:g}_atr_bar": next(
            (i for i, movement in enumerate(favorable, 1) if movement >= target * atr), None
        ) for target in (0.5, 1, 1.5, 2, 3)},
    }


def summarize(outcomes):
    returns = [row["directional_atr"] for row in outcomes]
    return {
        "sample": len(returns), "mean_return_atr": mean(returns) if returns else None,
        "median_return_atr": median(returns) if returns else None,
        "mean_mfe_atr": mean(row["mfe_atr"] for row in outcomes) if outcomes else None,
        "mean_mae_atr": mean(row["mae_atr"] for row in outcomes) if outcomes else None,
        "failure_frequency": mean(row["failed_breakout"] for row in outcomes) if outcomes else None,
        "continuation_frequency": mean(value > 0 for value in returns) if returns else None,
    }


def signal_rows(snapshots, decisions, settings, instrument, revision):
    """Export chronological holdout labels and exclude cross-partition outcomes.

    Warmup runs before each boundary as part of the same chronological replay.
    """
    positions = {snapshot.at: i for i, snapshot in enumerate(snapshots)}
    dates = [pd.Timestamp(row.at).tz_convert("Asia/Kolkata").date() for row in snapshots]
    sessions = list(dict.fromkeys(dates))
    if len(sessions) < 5:
        partitions = [("unpartitioned", 0, len(snapshots))]
    else:
        end_research = dates.index(sessions[len(sessions) * 3 // 5])
        end_validation = dates.index(sessions[len(sessions) * 4 // 5])
        partitions = [
            ("research", 0, end_research),
            ("validation", end_research, end_validation),
            ("chronological_last", end_validation, len(snapshots)),
        ]
    for decision in decisions:
        if decision.state != "SIGNAL_GENERATED":
            continue
        position = positions[decision.at]
        partition, _, stop = next(part for part in partitions if part[1] <= position < part[2])
        e = decision.evidence
        meta = {
            "signal_id": f"{instrument}:{settings.version}:{decision.key}:{decision.detected_at.isoformat()}",
            "instrument": instrument, "dataset_revision": revision, "partition": partition,
            "strategy_profile": settings.profile, "direction": "LONG" if decision.side == 1 else "SHORT",
            "breakout_source": ",".join(e["sources"]), "breakout_timeframe": settings.breakout_tf,
            "rsi_timeframe": ",".join(settings.rsi_tfs), "demand_timeframe": ",".join(settings.demand_tfs),
            "supply_timeframe": ",".join(settings.supply_tfs),
            "original_breakout_timestamp": decision.detected_at.isoformat(),
            "confirmation_timestamp": decision.at.isoformat(), "signal_availability_timestamp": decision.at.isoformat(),
            "breakout_reference": e["level"], "confirmation_close": e["reference_price"],
            "previous_atr": e["previous_atr"], "breakout_buffer": settings.buffer,
            "extension_atr": e["extension"], "opposing_divergence": e["opposing_divergence"],
            "zone_clearance_points": e["clearance"], "zone_clearance_atr": e["clearance_atr"],
            "quality_score": decision.score, "quality_grade": decision.grade,
            "configuration_version": settings.version, "algorithm_version": e["rubric"],
        }
        for horizon in HORIZONS:
            outcome = signal_outcome(snapshots, position, decision, horizon, stop=stop)
            yield {**meta, **(outcome or {"outcome_horizon": horizon}), "outcome_available": outcome is not None}


def evaluate(frames, settings: Settings, *, instrument_is_index: bool = False, research: ResearchPolicy | None = None,
             export: list | None = None, instrument: str = "", revision: str = "") -> dict:
    start = perf_counter()
    snapshots = tuple(iter_snapshots(frames, settings, instrument_is_index=instrument_is_index))
    decisions = tuple(replay(snapshots, settings, research=research))
    if export is not None:
        export.extend(signal_rows(snapshots, decisions, settings, instrument, revision))
    positions = {snapshot.at: i for i, snapshot in enumerate(snapshots)}
    output = {}
    for side, label in ((1, "LONG"), (-1, "SHORT")):
        relevant = [item for item in decisions if item.side == side]
        emitted = [item for item in relevant if item.state == "SIGNAL_GENERATED"]
        analytics = {}
        for horizon in HORIZONS:
            outcomes = [
                row for item in emitted
                if (row := signal_outcome(snapshots, positions[item.at], item, horizon)) is not None
            ]
            analytics[horizon] = summarize(outcomes)
        output[label] = {
            "unique_setups": len({(item.key, item.detected_at) for item in relevant}),
            "emitted": len(emitted), "grades": dict(Counter(item.grade for item in emitted)),
            "decision_reasons": dict(Counter(item.reason for item in relevant)), "horizons": analytics,
        }
    return {"settings_version": settings.version, "elapsed_seconds": perf_counter() - start, "directions": output}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON with instrument, provenance and timeframe-to-CSV mapping")
    parser.add_argument("--sensitivity", action="store_true")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--sources", action="store_true", help="Compare eligible breakout sources independently")
    parser.add_argument("--signals-csv", type=Path, help="Write offline historical signal outcomes")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    instrument, provenance = manifest["instrument"], manifest["provenance"]
    if not instrument or not provenance:
        parser.error("Instrument and actual source provenance are required")
    frames = {tf: _read_frame(args.manifest.parent / name) for tf, name in manifest["frames"].items()}
    base = manifest.get("settings", {})
    for field in ("profile", "acceptance", "divergence_mode", "opposite_block", "candle_mode"):
        if field in base:
            parser.error(f"Manifest settings cannot override study profile field {field}")
    cases = {name: (Settings(profile=name, **base), None) for name in ("BO-01", "BO-02", "BO-03", "BO-04")}
    if args.ablation:
        first = cases["BO-01"][0]
        no_score = ResearchPolicy(apply_score_floor=False)
        no_zones = ResearchPolicy(zone_gate_enabled=False, apply_score_floor=False)
        cases.update({
            "R01_raw": (first, no_zones),
            "R02_rsi": (cases["BO-02"][0], no_zones),
            "R03_zones": (first, no_score),
            "R04_rsi_and_zones": (cases["BO-02"][0], no_score),
            "R05_momentum": (replace(first, candle_mode="MANDATORY"), no_zones),
            "R06_retest": (replace(first, acceptance="RETEST_BREAKOUT"), no_zones),
            **{f"R{6 + i:02}_{label}": pair for i, (label, pair) in enumerate(cases.items(), start=1)},
            **{f"R{11 + i:02}_{source}": (replace(first, sources=(source,)), None)
               for i, source in enumerate(("B1", "B2", "HORIZONTAL", "ZONE"))},
        })
    unavailable = {}
    if args.sources:
        first = cases["BO-01"][0]
        cases.update({f"source_{source}": (replace(first, sources=(source,)), None)
                      for source in ("B1", "B2", "HORIZONTAL", "ZONE")})
        sessions = [date.fromisoformat(value) for value in manifest.get("session_dates", [])]
        if first.breakout_tf == "1d":
            unavailable["source_SESSION"] = "Requires an intraday breakout timeframe"
        elif len(sessions) < 2:
            unavailable["source_SESSION"] = "Requires verified exchange session_dates in the manifest"
        elif sessions != sorted(set(sessions)):
            parser.error("session_dates must be distinct and ascending")
        else:
            frames[first.breakout_tf].attrs["session_calendar"] = calendar_for("NSE")
            frames[first.breakout_tf].attrs["previous_sessions"] = dict(zip(sessions[1:], sessions))
            cases["source_SESSION"] = (replace(first, sources=("SESSION",)), None)
    if args.sensitivity:
        for key, values in SENSITIVITY.items():
            profile = cases["BO-02"][0] if key == "divergence_age" else cases["BO-01"][0]
            cases.update({f"{key}={value}": (
                replace(profile, **{key: value}),
                ResearchPolicy(score_floor=value) if key == "min_score" else None,
            ) for value in values})
    exports = []
    results = {}
    for name, (cfg, policy) in cases.items():
        rows = [] if args.signals_csv else None
        results[name] = evaluate(
            frames, cfg, instrument_is_index=instrument.startswith("NSE_INDEX:"), research=policy,
            export=rows, instrument=instrument, revision=manifest.get("dataset_revision", ""),
        )
        exports.extend({"scenario": name, **row} for row in rows or ())
    if args.signals_csv and exports:
        fields = list(dict.fromkeys(key for row in exports for key in row))
        with args.signals_csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(exports)
    print(json.dumps({
        "instrument": instrument, "provenance": provenance, "replay": "EVENT_TIME",
        "coverage": {tf: {"bars": len(f), "first": f.index.min().isoformat(),
                           "last": f["bar_end"].max().isoformat()} for tf, f in frames.items()},
        "results": results, "unavailable": unavailable,
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
