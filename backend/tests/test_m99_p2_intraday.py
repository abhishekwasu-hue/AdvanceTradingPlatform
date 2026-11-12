"""M99 P2: the intraday pack (M94) upgrade - trade-management options for the intraday templates and the key-level grade.

Management (app/strategy_templates/management.py, stored as TemplateExits.management, migration b4d6f8a0c2e3):
* every option is off by default; all off = the bot / backtest exactly as before;
* synthetic fixtures per option (trail by swing / ATR / average, no new extreme, bars in the trade), each with its mirror
  for a downward idea; no second partial exit (the ExitRules scale-out of M99 P7 is the one);
* a stop only ever tightens and never into the price; no option ever blocks or delays an exit (a trade of the same entry
  is never out later than without the options);
* backtest = worker: the engine's exits and stops equal a replay of the same `management_step` with the worker's
  timing (the bars that closed after the entry), and the worker applies a step to its own PAPER trades only;
* no look-ahead: a backtest cut at any bar gives the same closed trades before the cut;
* the checks and the plain refusals (family H, a bot that trades the symbol itself, the numbers' bounds), the migration.

Key-level grade (app/screener/level_grade.py, `LevelGrade`): one synthetic fixture per component, causality by cutting the
bars, the registry entry behind the pack's flags.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import run_backtest
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.strategy_templates import compile as C
from app.strategy_templates import exits as X
from app.strategy_templates import library
from app.strategy_templates import management as M
from app.strategy_templates.adapter import TemplateStrategy

VENUE_24X7 = "BTCINR"           # no NSE session rules: the trades run long enough to reach every option
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5, min_risk_reward=0.5)
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "b4d6f8a0c2e3_m99_p2_template_management.py"


def _exits(**kw) -> X.TemplateExits:
    return X.TemplateExits(management=M.Management(**kw).to_json())


def _strategy(tid="D1", tf="15m", m: M.Management | None = None):
    t = library.get(tid)
    s = TemplateStrategy(t, C.resolve_settings(t, tf))
    return s.use_exits(X.TemplateExits(management=m.to_json())) if m is not None else s


def _bars(seed, total=900):
    return synthetic_bars(total, tf="15m", seed=seed)


def _step(m, up=True, highs=(), lows=(), closes=(), **kw):
    base = dict(direction=1 if up else -1, entry=100.0, initial_stop=98.0 if up else 102.0, current_stop=98.0 if up else 102.0)
    base.update(kw)
    return M.step(m, highs=list(highs), lows=list(lows), closes=list(closes), **base)


def _mirror(xs):
    return [200.0 - x for x in xs]


# ============================================================================================ the pure function, per option
def test_all_options_off_is_nothing():
    assert not M.NONE.active and M.NONE.to_json() is None and M.Management.from_json(None) == M.NONE
    assert _step(M.NONE, highs=[110], lows=[90], closes=[105]) == M.Step()
    assert M.Management.from_json("not json") == M.NONE and M.Management.from_json("[1]") == M.NONE


@pytest.mark.parametrize("up", [True, False])
def test_swing_and_average_trail_only_tighten_and_stay_behind_the_close(up):
    f = (lambda x: x) if up else (lambda x: 200.0 - x)
    for m in (M.Management(trail="SWING", trail_degree=0), M.Management(trail="MA", trail_n=20, trail_ma="EMA")):
        bar = dict(highs=[f(104.0)], lows=[f(101.0)], closes=[f(103.0)])
        assert _step(m, up, level=f(99.5), **bar).stop == f(99.5)                       # tighter than 98: moves
        assert _step(m, up, level=f(97.0), **bar).stop is None                          # looser: never widens
        assert _step(m, up, level=f(103.5), **bar).stop is None                         # beyond the close: not into the price
        assert _step(m, up, level=float("nan"), **bar).stop is None                     # no level yet: nothing
        assert _step(m, up, level=f(101.0), current_stop=f(101.5), **bar).stop is None   # the stop already tighter


@pytest.mark.parametrize("up", [True, False])
def test_atr_trail_is_the_best_price_since_the_entry_less_k_atr(up):
    m = M.Management(trail="ATR", trail_n=22, trail_k=3.0)
    f = (lambda xs: xs) if up else _mirror
    highs, lows = [103.0, 108.0, 106.5], [101.0, 104.0, 104.5]
    hl = dict(highs=highs, lows=lows) if up else dict(highs=_mirror(lows), lows=_mirror(highs))     # a mirror swaps the sides
    got = _step(m, up, closes=f([102.0, 107.0, 106.0]), level=1.0, **hl)
    assert got.stop == pytest.approx(f([105.0])[0]) and got.stop_reason == "trail"          # 108 - 3 x 1 (mirror: 92 + 3)


@pytest.mark.parametrize("up", [True, False])
def test_no_new_extreme_and_bars_in_the_trade_exits(up):
    f = (lambda xs: xs) if up else _mirror
    stall = M.Management(stall_bars=2)
    rising = dict(highs=f([101, 102, 103]), lows=f([100, 101, 102]), closes=f([100.5, 101.5, 102.5]))
    assert _step(stall, up, **rising).exit_reason is None
    flat = dict(highs=f([101, 103, 102.5, 102.9]), lows=f([100, 101, 101, 101]), closes=f([100.5, 102, 101.5, 102]))
    assert _step(stall, up, **flat).exit_reason == f"No new {'high' if up else 'low'} in 2 bars"
    one = dict(highs=f([101, 103, 102.5]), lows=f([100, 101, 101]), closes=f([100.5, 102, 101.5]))
    assert _step(stall, up, **one).exit_reason is None                                  # only one bar without a new extreme
    bars = M.Management(max_bars=3)
    assert _step(bars, up, **one).exit_reason == "Bars in the trade (3)"
    assert _step(bars, up, highs=one["highs"][:2], lows=one["lows"][:2], closes=one["closes"][:2]).exit_reason is None


def test_the_checks_name_each_problem_in_plain_words():
    ok = [M.Management(trail="SWING", trail_degree=0), M.Management(trail="ATR", trail_n=22, trail_k=3),
          M.Management(trail="MA", trail_n=20, trail_ma="SMA"), M.Management(stall_bars=2), M.Management(max_bars=30)]
    assert all(m.problems() == [] for m in ok)
    bad = {M.Management(trail="FAST"): "from the list",
           M.Management(trail="SWING", trail_degree=5): "swing size", M.Management(trail="ATR", trail_n=22): "average true ranges",
           M.Management(trail="MA", trail_n=20, trail_ma="WMA"): "kind of average", M.Management(trail_n=20): "trailing stop only",
           M.Management(stall_bars=0): "without a new high", M.Management(max_bars=2.5): "bars to stay"}  # type: ignore[arg-type]
    for m, words in bad.items():
        assert any(words in p for p in m.problems()), (m, m.problems())


# ======================================================================================== the exit settings and the routes
def test_the_options_fit_only_an_intraday_template_on_the_symbol_itself():
    h = TemplateStrategy(library.get("H2"), C.resolve_settings(library.get("H2"), "5m"))
    d = _strategy("D1")
    choice = _exits(stall_bars=2)
    assert choice.problems(X.UNDERLYING, h.capabilities()) == []
    assert choice.problems(X.UNDERLYING, d.capabilities()) == ["The trade-management options are for the intraday templates only"]
    for cls_ in (X.FUTURE, X.OPTION_SINGLE, X.OPTION_STRUCTURE):
        assert "symbol itself" in " ".join(choice.problems(cls_, h.capabilities())), cls_
    assert _exits(trail="SWING", trail_degree=9).problems(X.UNDERLYING, h.capabilities()) == [
        "Pick the swing size the stop trails (0 to 3)"]
    with pytest.raises(ValueError):
        X.checked(d, X.TemplateExitsBody(management=X.ManagementBody(stall_bars=2)), "UNDERLYING", None)
    assert X.checked(h, X.TemplateExitsBody(management=X.ManagementBody(stall_bars=2)), "UNDERLYING", None).manage() == M.Management(stall_bars=2)


def test_the_request_shape_round_trips_and_all_off_is_stored_as_none():
    body = X.TemplateExitsBody(management=X.ManagementBody(trail="MA", trail_n=20, trail_ma="EMA", stall_bars=2))
    choice = body.to_exits()
    assert json.loads(choice.management or "") == {"stall_bars": 2, "trail": "MA", "trail_ma": "EMA", "trail_n": 20}
    assert X.TemplateExitsBody(management=X.ManagementBody()).to_exits().is_default
    assert X.TemplateExitsBody().to_exits() == X.DEFAULT
    assert "trail the stop at the 20-bar EMA" in choice.describe() and "out after 2 bars without a new high or low" in choice.describe()
    with pytest.raises(Exception):                     # no second partial exit here: the scale-out (M99 P7) is the one
        X.ManagementBody(partial_pct=50)  # type: ignore[call-arg]


def test_the_template_offers_the_options_to_the_form_only_on_family_h():
    from app.strategy_templates.routes import _exit_choices
    h = TemplateStrategy(library.get("H6"), C.resolve_settings(library.get("H6"), "5m"))
    got = _exit_choices(h)["management"]
    assert got["classes"] == ["UNDERLYING"] and [t["value"] for t in got["trails"]] == ["SWING", "ATR", "MA"]
    assert got["reference_label"] == "Reference - edit before use" and got["reference"]["trail_k"] == 3.0
    assert not any("partial" in k for k in got["reference"]) and not any("partial" in k for k in got["bounds"])
    assert _exit_choices(_strategy("D1"))["management"] is None


# ================================================================================================== the backtest engine
def _plain_rows(result):
    return list(result.trades)


def test_all_off_is_the_backtest_exactly_as_before():
    df = _bars(3)
    before = run_backtest(_strategy(), df, VENUE_24X7, "15min", RISK)
    same = run_backtest(_strategy().use_exits(X.TemplateExits(management=None)), df, VENUE_24X7, "15min", RISK)
    assert before.trades and [t.model_dump() for t in before.trades] == [t.model_dump() for t in same.trades]
    assert not _strategy().manages and _strategy(m=M.Management(stall_bars=3)).manages


@pytest.mark.parametrize("m", [M.Management(stall_bars=2), M.Management(max_bars=4), M.Management(trail="SWING", trail_degree=0),
                               M.Management(trail="ATR", trail_n=14, trail_k=1.5), M.Management(trail="MA", trail_n=10, trail_ma="EMA"),
                               M.Management(trail="SWING", trail_degree=1, stall_bars=3)],
                         ids=["stall", "bars", "swing", "atr", "ma", "swing-stall"])
def test_an_option_never_holds_a_trade_longer_and_never_widens_its_stop(m):
    """Exits are never blocked: each managed trade is out no later than the bar its own first stop (the template's
    invalidation, D1 has no target) would have closed it, and its stop is never wider than that first stop."""
    df = _bars(3, total=1600)
    pos = {ts: i for i, ts in enumerate(df.index)}
    lows, highs = df["low"].to_numpy(float), df["high"].to_numpy(float)
    managed = run_backtest(_strategy(m=m), df, VENUE_24X7, "15min", RISK)
    plain = _strategy()
    rows = [t for t in _plain_rows(managed) if t.exit_reason != "End of backtest"]
    assert len(rows) >= 3
    reached = 0
    for t in rows:
        e, x = pos[pd.Timestamp(t.entry_time)], pos[pd.Timestamp(t.exit_time)]
        first = float(plain.analyze({"15min": df.iloc[:e + 1]}, "S").stop_loss)
        long = t.direction.value == "LONG"
        hit = next((j for j in range(e + 1, len(df)) if (lows[j] <= first if long else highs[j] >= first)), None)
        assert hit is None or x <= hit, (t.entry_time, t.exit_reason, x, hit)                              # never later
        assert (t.stop_loss >= first - 1e-9) if long else (t.stop_loss <= first + 1e-9)                    # never wider
        reached += int(t.exit_reason != "Stop Loss" or abs(t.stop_loss - first) > 1e-9)
    assert reached >= 1, "the fixture must reach the option"


def test_a_management_exit_is_at_the_next_open_and_never_on_the_entry_bar():
    df = _bars(5)
    pos = {ts: i for i, ts in enumerate(df.index)}
    result = run_backtest(_strategy(m=M.Management(stall_bars=2, max_bars=6)), df, VENUE_24X7, "15min", RISK)
    seen = 0
    for t in result.trades:
        if t.exit_reason in ("No new high in 2 bars", "No new low in 2 bars", "Bars in the trade (6)"):
            seen += 1
            e, x = pos[pd.Timestamp(t.entry_time)], pos[pd.Timestamp(t.exit_time)]
            assert x >= e + 2 and t.exit_price == pytest.approx(float(df["open"].iloc[x]), rel=2e-3)
            if t.exit_reason.startswith("Bars"):
                assert x == e + 7                         # six closed bars after the entry, out at the seventh's open
    assert seen >= 3


def test_no_look_ahead_a_cut_backtest_has_the_same_closed_trades():
    df = _bars(7)
    m = M.Management(trail="SWING", trail_degree=0, stall_bars=3)
    risk = RiskConfig(capital=10_000_000, risk_per_trade_pct=2.0, min_risk_reward=0.5)
    full = run_backtest(_strategy(m=m), df, VENUE_24X7, "15min", risk)
    for cut in (400, 550, 700):
        part = run_backtest(_strategy(m=m), df.iloc[:cut], VENUE_24X7, "15min", risk)
        end = df.index[cut - 2]
        want = [t.model_dump() for t in full.trades if pd.Timestamp(t.exit_time) <= end]
        got = [t.model_dump() for t in part.trades if pd.Timestamp(t.exit_time) <= end]
        assert got == want and want, cut


# ========================================================================================== backtest = worker (the replay)
def test_backtest_equals_the_workers_replay_of_the_same_step():
    """Each backtest trade replayed the worker's way: on every bar close after the entry the frames up to that bar, `after` =
    the bars whose close is after the entry time (the entry is placed just after its signal bar closed), the stop as the
    worker stores it. The engine's exit bar and reason, and the last stop must be the replay's."""
    from app.backtest.windows import bar_length
    df = _bars(3)
    m = M.Management(trail="SWING", trail_degree=0, stall_bars=3)
    risk = RiskConfig(capital=10_000_000, risk_per_trade_pct=2.0, min_risk_reward=0.5)
    result = run_backtest(_strategy(m=m), df, VENUE_24X7, "15min", risk)
    pos = {ts: i for i, ts in enumerate(df.index)}
    length = bar_length("15min")
    checked = 0
    worker = _strategy(m=m)
    for t in _plain_rows(result):
        if t.exit_reason == "End of backtest":
            continue
        e, x = pos[pd.Timestamp(t.entry_time)], pos[pd.Timestamp(t.exit_time)]
        entered = df.index[e] + length + pd.Timedelta(seconds=1)
        long = t.direction.value == "LONG"
        stop, initial, mgmt_exit = None, None, None
        for j in range(e + 1, x):
            frame = df.iloc[:j + 1]
            after = int(((frame.index + length) > entered).sum())
            if stop is None:
                initial = stop = float(worker.analyze({"15min": df.iloc[:e + 1]}, "S").stop_loss)
            step = worker.management_step({"15min": frame}, "LONG" if long else "SHORT", entry=t.entry_price, initial_stop=initial,
                                          current_stop=stop, after=after)
            if step.stop is not None:
                stop = step.stop
            if step.exit_reason:
                mgmt_exit = (j, step.exit_reason)
                break
        if mgmt_exit is not None:
            assert (x, t.exit_reason) == (mgmt_exit[0] + 1, mgmt_exit[1]), (t.entry_time, t.exit_reason)
        else:
            assert not t.exit_reason.startswith(("No new", "Bars in")), t.exit_reason
        assert t.stop_loss == pytest.approx(stop), (t.entry_time, t.stop_loss, stop)
        checked += 1
    assert checked >= 5


# ================================================================================================== the worker (DB)
class _Market:
    def __init__(self, price):
        self.price, self.asked = price, []

    async def get_ltp(self, symbol, exchange="NSE"):
        self.asked.append(symbol)
        return self.price


def _worker_case(m: M.Management, *, price=101.0, stop=90.0, direction="LONG", qty=10.0):
    from sqlalchemy import select

    from app.db.models import StrategyDeploymentRecord, TradeRecord
    from app.workers.trading_worker import TradingWorker
    from tests.test_auth_api import _session_factory
    from tests.test_trading_worker import _tenant

    df = _bars(3).iloc[:420]
    bar_start = df.index[-1].to_pydatetime()
    a = _tenant(f"m99p2-a-{uuid.uuid4().hex[:6]}@example.com")
    b = _tenant(f"m99p2-b-{uuid.uuid4().hex[:6]}@example.com")
    strat = _strategy(m=m)

    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=a["tenant_id"], strategy_id=strat.id,
                                           symbol="RELIANCE", exchange="NSE", timeframe="15min", mode="PAPER", broker_name="upstox",
                                           status="ACTIVE", created_by=a["user_id"], management=m.to_json())
            session.add(dep)
            await session.flush()

            def trade(tenant, **kw):
                row = TradeRecord(tenant_id=tenant["tenant_id"], user_id=tenant["user_id"], deployment_id=dep.id, symbol="RELIANCE",
                                  strategy_id=dep.strategy_id, direction=kw.pop("direction", direction), mode=kw.pop("mode", "PAPER"),
                                  entry_time=kw.pop("entry_time", bar_start - timedelta(minutes=45)), entry_price=100.0, quantity=qty,
                                  stop_loss=stop, initial_stop_loss=stop, target1=None)
                session.add(row)
                return row
            mine = trade(a)
            live = trade(a, mode="LIVE")
            late = trade(a, entry_time=bar_start + timedelta(minutes=16))          # entered after the last bar closed
            other_org = trade(b)
            await session.commit()
            market = _Market(price)
            closed = await TradingWorker(_session_factory)._template_manage(session, dep, strat, {"15min": df}, market, a["user_id"])
            await session.commit()
            rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.deployment_id == dep.id)))
            return closed, rows, (mine.id, live.id, late.id, other_org.id)
    return asyncio.run(go()), df, strat


def test_the_worker_tightens_the_stop_of_its_own_paper_trade_only():
    m = M.Management(trail="MA", trail_n=5, trail_ma="SMA")
    df = _bars(3).iloc[:420]
    strat = _strategy(m=m)
    side, far = next((d, f) for d, f in (("LONG", 0.01), ("SHORT", 1e6))
                     if strat.management_step({"15min": df}, d, entry=100.0, initial_stop=f, current_stop=f, after=3).stop)
    want = strat.management_step({"15min": df}, side, entry=100.0, initial_stop=far, current_stop=far, after=3)
    (closed, rows, (mine, live, late, other)), _, _ = _worker_case(m, stop=far, direction=side)
    by = {r.id: r for r in rows}
    assert closed == 0 and want.stop is not None
    assert by[mine].stop_loss == pytest.approx(want.stop) and by[mine].exit_time is None
    assert by[live].stop_loss == pytest.approx(far) and by[late].stop_loss == pytest.approx(far)
    assert other not in by or by[other].stop_loss == pytest.approx(far)


def test_the_worker_closes_its_own_paper_trade_on_a_management_exit():
    (closed, rows, (mine, live, late, other)), _, _ = _worker_case(M.Management(max_bars=2), stop=0.01)
    by = {r.id: r for r in rows}
    assert closed == 1 and by[mine].exit_reason == "Bars in the trade (2)" and by[mine].exit_price == pytest.approx(101.0, abs=0.2)
    assert by[live].exit_time is None and by[late].exit_time is None


def test_a_management_failure_never_stops_the_other_exits(monkeypatch):
    """The worker's call is wrapped: an error in the options is logged and the cycle goes on to the template exit and
    the position monitor (the code path is the try / except around `_template_manage`)."""
    import inspect

    from app.workers import trading_worker as tw
    src = inspect.getsource(tw.TradingWorker._evaluate_deployment)
    assert "managed_out = await self._template_manage(" in src and "trade management skipped this bar" in src


# =========================================================================================================== migration
def _module(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_is_additive_rollback_safe_and_on_the_one_head_chain(tmp_path):
    import sqlalchemy as sa
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations
    module = _module("m99_p2_migration")
    assert module.revision == "b4d6f8a0c2e3" and module.down_revision == "a3c5e7f9b1d4" and module.ROLLBACK_SAFE is True
    source = MIGRATION.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not [p for p in lint() if p.startswith(MIGRATION.name)]
    assert source.count("nullable=True") == 1 and "server_default" not in source
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1 and "b4d6f8a0c2e3" in {r.revision for r in script.walk_revisions("base", heads[0])}
    from app.db.models import StrategyDeploymentRecord
    assert StrategyDeploymentRecord.__table__.c["management"].nullable
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, target_rule VARCHAR(24))"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, target_rule) VALUES (1, 'NONE')"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()
        assert tuple(conn.execute(sa.text("SELECT target_rule, management FROM strategy_deployments")).one()) == ("NONE", None)
        with Operations.context(ctx):
            module.downgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")} == {"id", "target_rule"}


# ====================================================================================================== the level grade
from app.screener import level_grade as LG  # noqa: E402


def _levels(n, **named):
    out = {k: np.full(n, np.nan) for k in LG.LEVEL_NAMES}
    out.update({k: np.asarray(v, dtype=float) for k, v in named.items()})
    return out


def _grade(levels, name, high, low, what, **kw):
    atr = np.full(len(high), 1.0)
    return LG.grade_series(levels, name, np.asarray(high, float), np.asarray(low, float), atr, what=what, **kw)


def test_touches_count_separate_visits_of_the_band():
    # level 105: visits at bars 1-2 (one visit), 5 (only with a band), 8; with a band of 0 a bar must reach 105 itself
    high = [103, 105.2, 105.0, 103, 103, 104.8, 103, 103, 105.5, 103]
    low = [101, 103.0, 103.5, 101, 101, 103.0, 101, 101, 103.0, 101]
    lv = _levels(10, PDH=[105.0] * 10)
    assert _grade(lv, "PDH", high, low, "touches")[-1] == 2                     # bars 1-2 (one visit) and 8; 104.8 does not reach 105
    assert _grade(lv, "PDH", high, low, "touches", near=0.25)[-1] == 3           # within a quarter ATR it does
    assert _grade(lv, "PDH", high, low, "touches", look=5)[-1] == 1              # only the last five bars counted
    assert _grade(lv, "PDH", high, low, "bars_since")[-1] == 1


def test_source_round_and_confluence():
    n = 3
    high, low = [101, 101, 101], [99, 99, 99]
    lv = _levels(n, PDC=[24000.0] * n, PDH=[24102.0] * n, PREOPEN_HIGH=[24100.5] * n, SESSION_HIGH=[np.nan] * n)
    assert _grade(lv, "PDC", high, low, "source")[-1] == 1 and _grade(lv, "PDH", high, low, "source")[-1] == 2
    assert _grade(lv, "PREOPEN_HIGH", high, low, "source")[-1] == 3
    assert _grade(lv, "PDC", high, low, "round", round_steps=[100])[-1] == 1
    assert _grade(lv, "PDH", high, low, "round", round_steps=[100])[-1] == 0
    assert _grade(lv, "PDH", high, low, "round", round_steps=[100], near=2.0)[-1] == 1     # 2 within a band of 2 ATR
    assert _grade(lv, "PDH", high, low, "confluence", near=2.0)[-1] == 1                  # the pre-open high 1.5 away
    assert _grade(lv, "PDH", high, low, "confluence", near=1.0)[-1] == 0
    assert np.isnan(_grade(lv, "SESSION_HIGH", high, low, "grade")[-1])                    # no level yet: missing


def test_the_grade_is_the_count_of_the_five_checks():
    high = [105.0, 103, 105.0, 103]
    low = [103.0, 101, 104.0, 101]
    lv = _levels(4, PDC=[105.0] * 4, PREOPEN_LOW=[105.2] * 4)
    # touches 2 (bars 0 and 2) >= 2, recent (1 bar ago <= 200), source 1 <= 2, round off (step 0 / none), confluence 1
    assert _grade(lv, "PDC", high, low, "grade", near=0.5)[-1] == 4
    assert _grade(lv, "PDC", high, low, "grade", near=0.5, round_steps=[5])[-1] == 5
    assert _grade(lv, "PDC", high, low, "grade", near=0.5, touches=3, recent=0, tier=1, round_steps=[8])[-1] == 2


def test_level_grade_reads_only_closed_bars_up_to_each_bar():
    from app.screener import parse
    from app.screener.runtime import SymbolData, evaluate
    from tests.breakout_fixtures import history, sessions_frame
    df = sessions_frame(history(4))
    for what in LG.WHATS:
        text = f'LevelGrade("PDH", "{what}", 0.5, 50)'
        full = np.asarray(evaluate(parse(text), SymbolData("NIFTY", {"5m": df}), base_tf="5m", params={}), dtype=float)
        assert np.isfinite(full).sum() > 50, what
        for cut in (120, 200, len(df) - 1):
            part = np.asarray(evaluate(parse(text), SymbolData("NIFTY", {"5m": df.iloc[:cut + 1]}), base_tf="5m", params={}), dtype=float)
            np.testing.assert_array_equal(part, full[:cut + 1], err_msg=f"{what} {cut}")


def test_level_grade_is_registered_behind_the_pack_flags():
    from app.screener.breakouts.gate import SESSION_FUNCTIONS
    from app.screener.registry import FUNCTIONS, INTRADAY_FLAG
    spec = FUNCTIONS["LevelGrade"]
    assert spec.flag == "screener_breakouts" and spec.also_flags == (INTRADAY_FLAG,) and spec.kind == "factor"
    assert [a.name for a in spec.args] == ["level", "what", "near", "look", "touches", "recent", "tier", "step"]
    assert set(spec.args[0].choices) == set(LG.LEVEL_NAMES) and set(spec.args[1].choices) == set(LG.WHATS)
    assert "LevelGrade" in SESSION_FUNCTIONS and "Information only" in spec.doc
