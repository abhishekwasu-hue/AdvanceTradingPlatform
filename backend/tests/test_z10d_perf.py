"""Z10-d (G4): the screener performance targets of ATP_SCREENER_SPEC §1.5, measured in the CI `perf` job.

* EOD screen, 2,500 symbols x 50 factors (daily bars): median under 3 s; served from the result cache: under 0.2 s.
* Bar-close scan, about 200 symbols (four alert rules of 50 symbols, the engine's cap) on 5-minute bars, through the
  bar-close engine itself (rules, cycle cache, events written to the database): median under 1 s.

Timed on the vectorised runtime (flag `screener_vectorized`). Every budget is the target times the CI factor
(scripts/bench_screener.py: a fixed workload's time on this machine over its time on the reference runner, never below
1), printed with the medians - and, on GitHub Actions, written as a notice so it shows on the check run. Each figure is
the median of several timed runs after a warm-up, so one slow run does not fail the job.

Skipped unless ATP_PERF=1 (timings do not belong in the functional suite, which runs alongside other jobs).
ATP_PERF_SYMBOLS / ATP_PERF_BARCLOSE_SYMBOLS run a smaller universe locally; the budget then scales with the count,
which is stricter than the target (the fixed cost of a run does not shrink).
"""
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_screener as bench  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("ATP_PERF") != "1", reason="performance job only (ATP_PERF=1)")

SYMBOLS = int(os.environ.get("ATP_PERF_SYMBOLS", bench.EOD_SYMBOLS))
BARCLOSE_SYMBOLS = int(os.environ.get("ATP_PERF_BARCLOSE_SYMBOLS", bench.BARCLOSE_SYMBOLS))
REPEAT = int(os.environ.get("ATP_PERF_REPEAT", "5"))


def _say(name: str, median: float, budget: float, runs, factor: float) -> None:
    line = f"{name}: median {median:.3f} s, budget {budget:.3f} s (target x CI factor {factor:.2f}), runs {runs}"
    print(line)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice title=G4 {name.split(' ')[0]}::{line}")


@pytest.fixture(scope="module")
def factor():
    calibration = bench.calibrate()
    value = bench.ci_factor(calibration)
    _say("calibration", calibration, bench.REFERENCE_CALIBRATION_SECONDS * value, [], value)
    return value


def test_eod_screen_and_its_cached_rerun_meet_the_targets(factor):
    result = bench.bench_eod(SYMBOLS, REPEAT, vectorized=True)
    budget = bench.TARGETS["eod_screen"] * factor * SYMBOLS / bench.EOD_SYMBOLS
    _say(f"eod_screen ({SYMBOLS} symbols x {result['factors']} factors, {result['bars']} bars)", result["eod_screen"]["median"], budget,
         result["eod_screen"]["runs"], factor)
    cached_budget = bench.TARGETS["eod_cached"] * factor
    _say("eod_cached (key: universe + as_of)", result["eod_cached"]["median"], cached_budget, result["eod_cached"]["runs"][:5], factor)
    _say("eod_cached (key: bar fingerprints)", result["eod_cached_by_bars"]["median"], cached_budget, result["eod_cached_by_bars"]["runs"], factor)
    sample = bench.with_tf(bench.universe(50, bench.daily_index()), "1d")          # the per-symbol runtime (flag off), for the record
    ast, validated = bench.compile_screen(bench.eod_text(), base_tf="1d", cost_cap=1e9)
    started = time.perf_counter()
    bench.run_screen(ast, validated, sample, base_tf="1d")
    per_symbol = (time.perf_counter() - started) / len(sample)
    _say(f"eod_per_symbol (one run over 50 symbols, not gated; x {bench.EOD_SYMBOLS} = {per_symbol * bench.EOD_SYMBOLS:.0f} s)", per_symbol * bench.EOD_SYMBOLS,
         budget, [round(per_symbol, 4)], factor)
    assert result["with_reason"] == 0 and 0 < result["matched"] <= SYMBOLS        # the screen really ran on every symbol
    assert result["eod_screen"]["median"] <= budget
    assert result["eod_cached"]["median"] <= cached_budget
    assert result["eod_cached_by_bars"]["median"] <= cached_budget


def _run(coro):
    return asyncio.run(coro)


def test_bar_close_scan_meets_the_target(factor):
    from sqlalchemy import update

    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord, ScreenRecord
    from app.market_data.calendar import IST
    from app.platform import controls
    from app.screener import compile_screen, nodes
    from tests.test_auth_api import _session_factory
    from tests.test_phase_l_ai import _owner

    _, me = _owner("z10d-perf-barclose@example.com")
    tenant = me["tenant_id"]
    data = bench.barclose_universe(BARCLOSE_SYMBOLS)
    frames = {d.symbol: d.frames["5m"] for d in data}
    last_day = data[0].frames["5m"].index[-1].astimezone(IST).date()
    now = datetime(last_day.year, last_day.month, last_day.day, 15, 31, tzinfo=IST)

    async def fetch(session, tenant_id, symbols, exchange, base_tf, lookback):
        return [barclose.SymbolData(s, {base_tf: frames[s]}) for s in symbols if s in frames], {}, "synthetic"

    async def setup():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["screener_v2"] = {"on": False, "tenants": [tenant]}
            flags["screener_vectorized"] = {"on": False, "tenants": [tenant]}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            ast, validated = compile_screen(bench.BARCLOSE_SCREEN, base_tf="5m")
            assert validated.ok
            screen = ScreenRecord(tenant_id=tenant, name="perf", source_text=bench.BARCLOSE_SCREEN, ast_json=json.dumps(nodes.to_json(ast)),
                                  base_tf="5m", params_json="{}")
            session.add(screen)
            await session.flush()
            ids = []
            symbols = list(frames)
            for i in range(0, len(symbols), bench.BARCLOSE_RULE_SIZE):
                rule = AlertRuleRecord(tenant_id=tenant, name=f"perf {i}", kind="screen", screen_id=screen.id,
                                       universe_json=json.dumps(symbols[i:i + bench.BARCLOSE_RULE_SIZE]), base_tf="5m", priority="normal",
                                       cooldown_minutes=0, mode="instant", digest_every="hourly", status="active",
                                       created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                session.add(rule)
                await session.flush()
                ids.append(rule.id)
            await session.commit()
            return ids

    rule_ids = _run(setup())

    async def cycle(vectorized: bool) -> float:
        async with _session_factory() as session:                         # untimed: the rules are due again
            await session.execute(update(AlertRuleRecord).where(AlertRuleRecord.id.in_(rule_ids)).values(last_bar_at=None, last_checked_at=None))
            await session.commit()
        async with _session_factory() as session:
            rules = [await session.get(AlertRuleRecord, rid) for rid in rule_ids]
            started = time.perf_counter()
            cache = barclose.CycleCache()
            states = [await barclose.evaluate_rule(session, rule, now, fetch, (), cache, vectorized=vectorized) for rule in rules]
            await session.commit()
            elapsed = time.perf_counter() - started
        assert [s for s, _fired in states] == ["done"] * len(rule_ids), states
        return elapsed

    _run(cycle(True))                                                         # warm-up (and the first events)
    runs = [_run(cycle(True)) for _ in range(REPEAT)]
    budget = bench.TARGETS["barclose_scan"] * factor * BARCLOSE_SYMBOLS / bench.BARCLOSE_SYMBOLS
    _say(f"barclose_scan ({BARCLOSE_SYMBOLS} symbols, {len(rule_ids)} rules, vectorised)", statistics.median(runs), budget,
         [round(r, 4) for r in runs], factor)
    per_symbol = _run(cycle(False))                                           # for the record (the flag-off default)
    _say(f"barclose_scan ({BARCLOSE_SYMBOLS} symbols, per-symbol, one run, not gated)", per_symbol, budget, [round(per_symbol, 4)], factor)

    async def events():
        async with _session_factory() as session:
            from sqlalchemy import func, select
            return await session.scalar(select(func.count()).select_from(AlertEventRecord).where(AlertEventRecord.rule_id.in_(rule_ids)))
    assert _run(events()) > 0                                                 # the scan matched and recorded something
    assert statistics.median(runs) <= budget
