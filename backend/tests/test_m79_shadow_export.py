"""M79 W10: the signal engine's day export for the shadow comparison (engine_runner.export_day, scripts/
trade_engine_export.py) on the EN-3a test double. Covered: a written signal is exported with its area state and write
time; every bar's FINAL run record is kept in the day log (also after the state row's RUNS_KEPT); a blocking gap is
exported with its data problems; another day and another organisation are left out."""
from datetime import datetime, timedelta

from tests import test_m66_en3a_runner as T
from tests.test_m66_en3a_runner import D1, KEY, FakeCollect, FakeEngine, M, R, _due, _fresh, _org, _run, _session_factory, at  # noqa: F401


def _export(tenant_id, day):
    async def go():
        async with _session_factory() as session:
            return await R.export_day(session, tenant_id, day)
    return _run(go())


def test_a_signal_and_its_run_are_exported_for_their_day():
    tenant_id, _ = _org()
    other, _ = _org(flag=False)
    _due(FakeEngine(), FakeCollect(at(D1, 10, 0)), at(D1, 10, 0, 10))
    lines = _export(tenant_id, D1)
    [sig] = [x for x in lines if x["kind"] == "signal"]
    assert sig["model_id"] == "model-test" and datetime.fromisoformat(sig["bar_end"]) == at(D1, 10, 0) and sig["written_at"]
    assert "area_state" in sig
    [run] = [x for x in lines if x["kind"] == "run"]
    assert run["status"] == R.SIGNAL and datetime.fromisoformat(run["bar_end"]) == at(D1, 10, 0)
    assert _export(tenant_id, D1 + timedelta(days=1)) == []
    assert [x for x in _export(other, D1) if x["kind"] == "signal"] == []


def test_every_final_record_of_the_day_is_kept():
    tenant_id, _ = _org()
    engine = FakeEngine()
    for minute in range(10, 10 + 5 * (R.RUNS_KEPT + 3), 5):
        hh, mm = 10 + minute // 60, minute % 60
        _due(engine, FakeCollect(at(D1, hh, mm)), at(D1, hh, mm, 10))
    runs = [x for x in _export(tenant_id, D1) if x["kind"] == "run"]
    assert len(runs) == R.RUNS_KEPT + 3
    assert len(T._records(tenant_id)[0]["runs"]) == R.RUNS_KEPT


def test_a_blocking_gap_is_exported_with_its_problems():
    tenant_id, _ = _org(max_attempts=1)
    hole = {at(D1, 9, 30) + i * M for i in range(5)}
    _due(FakeEngine(), FakeCollect(at(D1, 10, 0), skip=hole), at(D1, 10, 0, 10))
    [run] = [x for x in _export(tenant_id, D1) if x["kind"] == "run"]
    assert run["status"] == R.DATA_GAP and "missing_bars:5m" in run["problems"] and run["symbol"] == KEY
