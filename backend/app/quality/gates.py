"""N3 U3 full gates: one manifest of the screener's launch gates, the tests that enforce each row, and the rows that are
not met yet (each with its OPEN_QUESTIONS id).

The U3 gates have no spec text of their own (OPEN_QUESTIONS Z10-1). The rows below are the screener spec's own
requirements that U3 gathers: the freeze's G1-G7 names (docs/specs/ATP_PHASE0_FREEZE.md Z10), SCREENER_SPEC §1.5
(performance), §5 (quality, observability, reproducibility, pitfalls) and §7 "Acceptance", and the evidence addendum's
"U3 definition of done" for registry entries. G3 is not named anywhere; it is read as §5 "Reproducibility" plus §7
"every number in an alert or explanation traceable to a data timestamp" (OPEN_QUESTIONS U3-1).

* `ROWS` - every row names its enforcing tests as "path::test name" (pytest functions, or a vitest title for a
  frontend file). `tests/test_u3_gates.py` fails when a named test does not exist, so the manifest cannot rot.
* A row that is not (fully) met carries `gap` = an OPEN_QUESTIONS id; the test checks the id exists there. A measured
  row (`measure`) also fails once its target is met while it still declares a gap, so a closed gap gets closed here.
* `definition_of_done()` checks every ScreenQL registry entry: a doc string, a flag that starts off, a Z10-a causal case
  (or a named exemption) and - once the evidence engine declares it (EV3, `evidence.DRAWN_AS`) - how it is drawn. The
  hand-computed runtime fixture per entry is enforced by tests/test_s1b_screen_runtime.py.

`python -m app.quality.gates` prints the report (exit 1 when a row's tests are missing or an entry is not done).
Nothing here runs a test, places an order or reads organisation data.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[3]

GATES: Dict[str, str] = {
    "G1": "Correctness of shipped indicators, patterns and screen functions (golden references, chart parity)",
    "G2": "Closed candles only, no look-ahead (truncation, future mutation, point-in-time data)",
    "G3": "Reproducible runs and traceable numbers (provisional meaning, OPEN_QUESTIONS U3-1)",
    "G4": "Performance targets of SCREENER_SPEC §1.5, measured in a CI perf job",
    "G5": "Stale data suppresses alerts; outbox reliability; data-quality gates; no duplicate alerts",
    "G6": "Tenancy and security; no API order path reachable",
    "G7": "Status page, runbooks, observability",
    "DoD": "Definition of done per registry entry (evidence addendum §4)",
    "ACC": "SCREENER_SPEC §7 acceptance rows not covered by a gate above",
}


@dataclass(frozen=True)
class Row:
    id: str
    gate: str
    text: str
    source: str
    tests: Tuple[str, ...]
    gap: Optional[str] = None          # OPEN_QUESTIONS id while the row is not (fully) met
    note: str = ""
    measure: Optional[str] = None      # a key of MEASURES: (value, target) - met when value >= target


Z10A_GOLD = "backend/tests/test_z10a_indicator_golden.py"
Z10A_LA = "backend/tests/test_z10a_no_lookahead.py"
Z10B = "backend/tests/test_z10b_stale_outbox.py"
Z10C = "backend/tests/test_z10c_tenancy_sweep.py"
Z10D = "backend/tests/test_z10d_perf.py"
Z10E = "backend/tests/test_z10e_rollout_status.py"
U3 = "backend/tests/test_u3_gates.py"

ROWS: Tuple[Row, ...] = (
    # ------------------------------------------------------------------------------------------------------------ G1
    Row("G1.indicators", "G1", "Indicators and ScreenQL functions match textbook reference formulas", "Freeze Z10 G1",
        (f"{Z10A_GOLD}::test_hand_checked_small_cases", f"{Z10A_GOLD}::test_indicators_match_the_reference_formulas",
         f"{Z10A_GOLD}::test_screenql_functions_match_the_reference", f"{Z10A_GOLD}::test_strategy_builder_operands_match_the_reference")),
    Row("G1.patterns", "G1", "Each candlestick pattern fires on its shape and not on a near miss", "Freeze Z10 G1",
        (f"{Z10A_GOLD}::test_each_pattern_fires_on_its_shape_and_not_on_a_near_miss", f"{Z10A_GOLD}::test_every_pattern_has_a_canonical_case")),
    Row("G1.chart_parity", "G1", "Chart indicators equal the engine's (parity tests green)", "SCREENER_SPEC §7",
        ("backend/tests/test_z10a_chart_parity.py::test_chart_parity_fixture_matches_the_backend",)),
    Row("G1.price_action", "G1", "Price-action screen functions agree bar by bar with the price_action engine on its fixtures",
        "SCREENER_SPEC §7",
        ("backend/tests/test_s5a_price_action.py::test_pattern_series_matches_the_detectors_bar_by_bar",
         f"{U3}::test_price_action_functions_match_the_engine_on_the_15m_fixture"),
        note="The 15m fixture is BANKNIFTY-like synthetic data; real BANKNIFTY bars are not in the repo (SC-12)."),
    # ------------------------------------------------------------------------------------------------------------ G2
    Row("G2.harness", "G2", "Every indicator, pattern, swing, zone and ScreenQL function passes the no-look-ahead harness",
        "Freeze Z10 G2", (f"{Z10A_LA}::test_no_look_ahead", f"{Z10A_LA}::test_every_shipped_function_is_registered",
                          f"{Z10A_LA}::test_the_harness_catches_planted_look_ahead")),
    Row("G2.closed_bars", "G2", "Screens and alerts decide on closed bars; a higher timeframe appears only after it closes",
        "BREAKOUT addendum (U3 G2)",
        ("backend/tests/test_s1d_screener_api.py::test_a_run_never_decides_on_the_bar_still_forming",
         "backend/tests/test_s1b_screen_runtime.py::test_higher_timeframe_values_appear_only_after_their_bar_closes",
         f"{Z10B}::test_todays_daily_bar_is_never_built_from_part_of_a_day")),
    Row("G2.point_in_time", "G2", "Point-in-time data: corrections and late rows are invisible before ingestion; universes keep "
        "delisted names", "SCREENER_SPEC §5, §7",
        ("backend/tests/test_b1_lake_schema.py::test_late_rows_and_corrections_are_invisible_before_they_were_ingested",
         "backend/tests/test_u1a_universe_securities.py::test_historical_universe_keeps_delisted_names_and_respects_listing_dates",
         "backend/tests/test_b4_lake_adjust.py::test_lake_view_respects_as_of_and_never_adjusts_derivatives")),
    Row("G2.fundamentals", "G2", "Fundamentals are read as of their announcement; a restatement only from when it was known",
        "SCREENER_SPEC §5 pitfalls", (), gap="U3-4",
        note="Built on claude/n3-lg3 (financial_period_versions, LG3-1); not on main yet."),
    # ------------------------------------------------------------------------------------------------------------ G3
    Row("G3.run_replay", "G3", "Every run stores AST version, universe and data version, and can be replayed", "SCREENER_SPEC §5",
        (f"{U3}::test_a_run_stores_what_a_replay_needs", f"{U3}::test_a_replay_reproduces_the_run_after_later_bars_arrive",
         f"{U3}::test_a_replay_names_symbols_whose_bars_changed", f"{U3}::test_replay_is_flagged_off_owner_scoped_and_refuses_old_runs")),
    Row("G3.alert_numbers", "G3", "Every number in an alert carries its data timestamp", "SCREENER_SPEC §7",
        ("backend/tests/test_s4a_barclose.py::test_an_instrument_rule_fires_once_per_closed_bar_and_never_on_the_forming_bar",)),
    Row("G3.explanations", "G3", "Explanations (evidence packs) show the engine's own numbers at a stated bar time", "SCREENER_SPEC §7",
        ("backend/tests/test_u8_evidence.py::test_verify_strip_numbers_are_the_engines",
         "backend/tests/test_u8_evidence.py::test_intraday_bar_time_reads_in_ist")),
    # ------------------------------------------------------------------------------------------------------------ G4
    Row("G4.eod", "G4", "EOD: 2,500 symbols x 50-factor screen < 3 s, cached < 200 ms (CI perf job)", "SCREENER_SPEC §1.5",
        (f"{Z10D}::test_eod_screen_and_its_cached_rerun_meet_the_targets",)),
    Row("G4.barclose", "G4", "Intraday bar-close scan on ~200 F&O symbols < 1 s after the bar", "SCREENER_SPEC §1.5",
        (f"{Z10D}::test_bar_close_scan_meets_the_target",)),
    Row("G4.live_latency", "G4", "Live alert latency (tick -> notification handoff) < 2 s p95", "SCREENER_SPEC §1.5", (), gap="U3-3"),
    Row("G4.alert_load", "G4", "Alert load test: 1,000 alerts x 200 symbols at bar close", "SCREENER_SPEC §4.9, §7",
        ("backend/tests/test_s4b_cycle_cache.py::test_the_same_screen_over_the_same_symbols_is_evaluated_once",), gap="U3-3",
        note="The perf job runs 4 rules x 50 symbols; the 1,000-rule load is not built."),
    # ------------------------------------------------------------------------------------------------------------ G5
    Row("G5.stale", "G5", "Stale data => zero alerts, and the page says so (banner / badge)", "SCREENER_SPEC §5, §7",
        (f"{Z10B}::test_the_bar_close_engine_records_nothing_on_stale_data", f"{Z10B}::test_an_event_held_past_its_bar_is_suppressed_not_sent",
         f"{Z10B}::test_the_badge_has_the_bt1_shape_and_fails_closed",
         "frontend/src/components/dataTier.test.ts::explains the tier, the reason and suppressed alerts")),
    Row("G5.outbox", "G5", "Outbox reliability: chaos (channel down, worker killed mid-send), dead letters", "SCREENER_SPEC §4.9",
        (f"{Z10B}::test_channel_down_ends_dead_with_one_key_on_every_attempt",
         f"{Z10B}::test_a_worker_killed_mid_send_leaves_a_lease_and_the_row_is_sent_again_once_it_expires")),
    Row("G5.duplicates", "G5", "No duplicate alerts: dedupe, cooldown, one message per rule and bar", "SCREENER_SPEC §5 pitfalls",
        ("backend/tests/test_s3a_notification_service.py::test_dedupe_and_cooldown",
         "backend/tests/test_s3a_notification_service.py::test_a_burst_is_one_message_per_rule_and_bar_with_tagged_deliveries")),
    Row("G5.data_quality", "G5", "Data-quality gates: spikes, gaps, duplicates raise an event once; history is honest about quality",
        "SCREENER_SPEC §5", ("backend/tests/test_b3_lake_quality.py::test_each_problem_raises_exactly_one_event",
                             "backend/tests/test_b5_history_api.py::test_history_is_point_in_time_adjusted_and_honest_about_quality")),
    # ------------------------------------------------------------------------------------------------------------ G6
    Row("G6.tenancy", "G6", "Every /api route is classified; another organisation never reads or changes rows", "Freeze Z10 G6",
        (f"{Z10C}::test_every_api_route_is_classified", f"{Z10C}::test_another_organisation_never_sees_or_changes_tenant_a_rows")),
    Row("G6.order_paths", "G6", "No API order path reachable; the Phase 0 lock refuses LIVE entries, never exits", "Freeze §5",
        ("backend/tests/test_z10c_order_paths.py::test_rule2_no_api_route_reaches_a_broker_entry",
         "backend/tests/test_z10c_phase0_lock.py::test_worker_exits_an_existing_live_position_while_locked")),
    # ------------------------------------------------------------------------------------------------------------ G7
    Row("G7.status", "G7", "Public status page and one runbook per alert", "BETA_LAUNCH_SPEC (U3 G7)",
        (f"{Z10E}::test_status_is_public_read_only_and_shaped", f"{Z10E}::test_every_alert_runbook_link_resolves_and_each_alert_has_a_page")),
    Row("G7.metrics", "G7", "Metrics: scan latency, cache hit rate, alerts delivered/failed per channel", "SCREENER_SPEC §5",
        (f"{U3}::test_scan_latency_and_cache_lookups_are_measured",)),
    Row("G7.ingest_lag", "G7", "Ingest-lag metric, structured logs with run_id, tracing", "SCREENER_SPEC §5", (), gap="U3-5"),
    # ----------------------------------------------------------------------------------------------------------- DoD
    Row("DoD.runtime", "DoD", "Every registry entry has a hand-computed runtime fixture", "ADR-0021 §2",
        ("backend/tests/test_s1b_screen_runtime.py::test_every_registry_entry_has_a_runtime_test",)),
    Row("DoD.entry", "DoD", "Every registry entry has a doc, a flag that starts off and a causal case or exemption",
        "Evidence addendum §4", (f"{U3}::test_every_registry_entry_meets_the_definition_of_done",)),
    Row("DoD.drawn", "DoD", "Every registry entry declares how it is drawn", "Evidence addendum §4", (), gap="U3-6",
        note="Declared by EV3 (`evidence.DRAWN_AS`, claude/n3-ev3); enforced here automatically once it is on main."),
    # ----------------------------------------------------------------------------------------------------------- ACC
    Row("ACC.prebuilt", "ACC", "50+ pre-built screens with docs", "SCREENER_SPEC §7",
        ("backend/tests/test_z8_tx1_taxonomy.py::test_every_pre_built_screen_is_filed_on_both_axes_with_known_tags",),
        gap="U3-2", measure="prebuilt_screens"),
    Row("ACC.fo_rrg_breadth", "ACC", "F&O buildup, RRG and breadth unit tests", "SCREENER_SPEC §7",
        ("backend/tests/test_o1_oi_regime.py::test_golden_classification_and_rotation",), gap="U3-7",
        note="RRG is on claude/n3-u5-d3b; a breadth panel is not built."),
    Row("ACC.pitfalls", "ACC", "Pitfalls: survivorship, corporate actions, indices without volume, over-fitted backtests, "
        "calendars, partial candles, symbol renames", "SCREENER_SPEC §5",
        ("backend/tests/test_u1a_universe_securities.py::test_rename_continuity_by_isin",
         "backend/tests/test_b4_lake_adjust.py::test_split_halves_prices_and_doubles_volume_before_the_ex_date_only",
         "backend/tests/test_z7_bk1_breakouts.py::test_an_index_without_volume_screens_with_the_volume_condition_off",
         "backend/tests/test_hc3a_research_ledger.py::test_noise_trials_are_deflated_and_flagged_as_overfit_risk",
         "backend/tests/test_s4a_barclose.py::test_expected_bar_follows_the_exchange_clock",
         "backend/tests/test_phase_o_hardening.py::test_session_family_and_per_exchange_clocks"),
        gap="U3-7", note="Not yet: index screens on futures volume, a minimum OI / premium for option strikes."),
)


# --------------------------------------------------------------------------------------------------------- measures
def _prebuilt_screens() -> Tuple[int, int]:
    from app.screener import taxonomy
    return len(taxonomy.load().screens), 50


MEASURES: Dict[str, Callable[[], Tuple[int, int]]] = {"prebuilt_screens": _prebuilt_screens}


# ----------------------------------------------------------------------------------------------- manifest checks
def _python_tests(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")}


def _vitest_titles(path: Path) -> set:
    return set(re.findall(r"""\b(?:it|test)\(\s*["'`](.+?)["'`]\s*,""", path.read_text(encoding="utf-8")))


def missing_tests(rows: Tuple[Row, ...] = ROWS, repo: Path = REPO) -> List[str]:
    """Every "path::test" a row names that does not exist (file missing, or no such test in it)."""
    out: List[str] = []
    cache: Dict[str, set] = {}
    for row in rows:
        for ref in row.tests:
            path, _, name = ref.partition("::")
            if path not in cache:
                file = repo / path
                cache[path] = (set() if not file.is_file() else
                               _python_tests(file) if path.endswith(".py") else _vitest_titles(file))
            if name not in cache[path]:
                out.append(f"{row.id}: {ref}")
    return out


def open_question_ids(repo: Path = REPO) -> set:
    text = (repo / "docs" / "OPEN_QUESTIONS.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\|\s*([A-Z][A-Za-z0-9]*-\d+[a-z]?)\s*\|", text, re.M))


def manifest_problems(rows: Tuple[Row, ...] = ROWS, repo: Path = REPO) -> List[str]:
    """Rows that cannot stand: unknown gate, duplicate id, neither tests nor a gap, a gap with no OPEN_QUESTIONS row,
    a measured row that meets its target but still declares a gap, or a named test that does not exist."""
    problems: List[str] = []
    ids = open_question_ids(repo)
    seen: set = set()
    for row in rows:
        if row.gate not in GATES:
            problems.append(f"{row.id}: unknown gate {row.gate}")
        if row.id in seen:
            problems.append(f"{row.id}: duplicate row id")
        seen.add(row.id)
        if not row.tests and not row.gap:
            problems.append(f"{row.id}: no enforcing test and no declared gap")
        if row.gap and row.gap not in ids:
            problems.append(f"{row.id}: gap {row.gap} has no row in docs/OPEN_QUESTIONS.md")
        if row.measure:
            value, target = MEASURES[row.measure]()
            if row.gap and value >= target:
                problems.append(f"{row.id}: {row.measure} = {value} meets {target}; close gap {row.gap}")
    return problems + [f"missing test - {m}" for m in missing_tests(rows, repo)]


# --------------------------------------------------------------------------------------------- definition of done
def definition_of_done() -> Dict[str, List[str]]:
    """{registry entry: what it lacks} for every ScreenQL function and field that is not done (empty = all done)."""
    from app.platform.controls import DEFAULT_OFF_FLAGS
    from app.quality import causal_cases as cc
    from app.screener import evidence
    from app.screener.registry import FIELDS, FUNCTIONS
    drawn = getattr(evidence, "DRAWN_AS", None)      # EV3; until it is on main the drawn row is a declared gap (U3-6)
    causal = set(cc.SCREENQL_SERIES) | set(cc.SCREENQL_LAST_BAR) | set(cc.SCREENQL_EXEMPT)
    out: Dict[str, List[str]] = {}
    for name, spec in {**FIELDS, **FUNCTIONS}.items():
        lacks: List[str] = []
        if not spec.doc.strip():
            lacks.append("a doc string")
        if spec.flag and spec.flag not in DEFAULT_OFF_FLAGS:
            lacks.append(f"its flag {spec.flag} in DEFAULT_OFF_FLAGS")
        if name in FUNCTIONS and name not in causal:
            lacks.append("a Z10-a causal case or a named exemption (app/quality/causal_cases.py)")
        if drawn is not None and name in FUNCTIONS and name not in drawn:
            lacks.append("a drawn-as declaration (evidence.DRAWN_AS)")
        if lacks:
            out[name] = lacks
    return out


def report() -> Tuple[str, bool]:
    """(text, ok). ok = no manifest problem and every registry entry done; declared gaps are listed, not failures."""
    problems = manifest_problems()
    undone = definition_of_done()
    lines = ["U3 gates (docs/design/Z10_LAUNCH_HARDENING.md, U3 full gates)", ""]
    for gate, meaning in GATES.items():
        lines.append(f"{gate}  {meaning}")
        for row in (r for r in ROWS if r.gate == gate):
            state = f"OPEN {row.gap}" if row.gap else "met"
            extra = ""
            if row.measure:
                value, target = MEASURES[row.measure]()
                extra = f" [{value} of {target}]"
            lines.append(f"  {row.id:<20} {state:<10} {row.text}{extra} ({len(row.tests)} tests)")
            if row.note:
                lines.append(f"  {'':<20} {'':<10} {row.note}")
    lines.append("")
    lines += [f"PROBLEM {p}" for p in problems]
    lines += [f"NOT DONE {name}: needs {', '.join(lacks)}" for name, lacks in sorted(undone.items())]
    ok = not problems and not undone
    lines.append("all rows have their tests; every registry entry is done" if ok else "U3 gates: problems above")
    return "\n".join(lines), ok


def main() -> int:
    text, ok = report()
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["GATES", "ROWS", "Row", "MEASURES", "missing_tests", "manifest_problems", "definition_of_done", "report", "open_question_ids"]
