"""N3 S6b: the rolling screen backtest (flag screener_backtest on top of screener_history; both off by default).

Decided at a rebalance date's close on bars closed by then, filled at the next session's open with the part C slippage
and India's delivery charges, marked at every close. The tests:
- check the arithmetic on hand-built prices with the charges switched off, then with them on;
- show that the equity up to any day does not move when every bar after that day is rewritten (no look-ahead);
- cover the choice rule (rank order, NaN last, ties, top N, rank weights) and a delisted holding;
- run the route on the shared lake (ledger trial, deflation, refusals)."""
import asyncio
import json
import uuid

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import ResearchTrialRecord
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener import backtest as B
from app.screener import compile_screen
from app.screener import history as H
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_n3_s6_history import SCREEN, _lake_rows, _seed, _sessions, _universe
from tests.test_trading_worker import _tenant


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n3s6b-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _bars(opens, closes, days):
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes), "close": closes,
                         "volume": 1000.0}, index=H.bar_index(days[:len(closes)]))


def _no_charges(monkeypatch):
    from app.execution import india_costs
    monkeypatch.setattr(india_costs, "leg_cost", lambda *a, **k: {"total": 0.0})


FREE = B.Settings(capital=100_000.0, slippage_pct=0.0, brokerage=0.0)


# --- arithmetic ------------------------------------------------------------------------------------------------------------------

def test_next_open_fills_close_marks_and_a_rebalance(monkeypatch):
    _no_charges(monkeypatch)
    days = _sessions(6)
    frames = {"A": _bars([100, 101, 110, 120, 130, 140], [100, 105, 115, 125, 135, 145], days),
              "B": _bars([50, 50, 50, 40, 40, 40], [50, 50, 50, 40, 40, 40], days)}
    sig = pd.DataFrame(False, index=pd.Index([days[0], days[2]]), columns=["A", "B"])
    sig.loc[days[0], "A"] = True                                   # decided at day0's close: buy A at day1's open (101)
    sig.loc[days[2], "B"] = True                                   # decided at day2's close: switch to B at day3's open
    res = B.simulate(sig, None, frames, days, days[-1], FREE)
    qty_a = int(100_000 * (1 - B.CASH_BUFFER) // 101)
    cash = 100_000 - qty_a * 101
    assert res.days[0] == days[1] and res.equity[0] == pytest.approx(cash + qty_a * 105)          # marked at day1's close
    assert res.equity[1] == pytest.approx(cash + qty_a * 115)
    equity_day3_open = cash + qty_a * 120                                                         # sold at day3's open
    qty_b = int(equity_day3_open * (1 - B.CASH_BUFFER) // 40)
    assert res.equity[2] == pytest.approx(equity_day3_open - qty_b * 40 + qty_b * 40)
    assert [r["held"] for r in res.rebalances] == [["A"], ["B"]] and res.holdings == [1, 1]
    # review of S6b (MED): day 1 is measured from the capital, so its move (and costs) count
    assert len(res.returns) == len(res.days) and res.costs == 0.0
    assert res.returns[0] == pytest.approx(res.equity[0] / 100_000 - 1.0)


def test_charges_and_slippage_are_paid():
    days = _sessions(6)
    frames = {"A": _bars([100.0] * 6, [100.0] * 6, days)}
    sig = pd.DataFrame(True, index=pd.Index([days[0], days[2]]), columns=["A"])
    sig.loc[days[2], "A"] = False                                  # bought, then sold
    res = B.simulate(sig, None, frames, days, days[-1], B.Settings(capital=100_000.0))
    assert res.costs > 2 * B.DEFAULT_BROKERAGE                     # brokerage both ways plus STT, stamp, exchange, GST
    flat_price_loss = 100_000.0 - res.equity[-1]
    assert flat_price_loss == pytest.approx(res.costs + 2 * 0.0002 * 100.0 * int(100_000 * (1 - B.CASH_BUFFER) // (100 * 1.0002)), rel=0.05)


def test_a_delisted_holding_is_marked_and_sold_at_its_last_close(monkeypatch):
    _no_charges(monkeypatch)
    days = _sessions(6)
    frames = {"A": _bars([100, 100, 90], [100, 100, 80], days), "B": _bars([10.0] * 6, [10.0] * 6, days)}
    sig = pd.DataFrame(False, index=pd.Index([days[0], days[3]]), columns=["A", "B"])
    sig.loc[days[0], "A"] = True
    sig.loc[days[3], "B"] = True
    res = B.simulate(sig, None, frames, days, days[-1], FREE)
    qty_a = int(100_000 * (1 - B.CASH_BUFFER) // 100)
    cash = 100_000 - qty_a * 100
    assert res.equity[2] == pytest.approx(cash + qty_a * 80)       # day3: A is gone, marked at its last close
    assert res.rebalances[1]["held"] == ["B"] and res.equity[-1] == pytest.approx(cash + qty_a * 80)


def test_the_choice_rule():
    s = B.Settings(top_n=2, weighting="rank")
    ranks = pd.Series({"A": 1.0, "B": 3.0, "C": np.nan, "D": 3.0})
    assert B.choose(["A", "B", "C", "D"], ranks, s) == {"B": 2 / 3, "D": 1 / 3}                 # ties by symbol; NaN last
    assert B.choose(["A", "C"], ranks, B.Settings(top_n=3, descending=False)) == {"A": 0.5, "C": 0.5}
    assert B.choose(["C", "A"], None, B.Settings()) == {"A": 0.5, "C": 0.5} and B.choose([], ranks, s) == {}
    assert B.FLAG in DEFAULT_OFF_FLAGS


# --- no look-ahead ---------------------------------------------------------------------------------------------------------------

def _pipeline(frames, days, start, end):
    ast, validated = compile_screen(SCREEN, base_tf="1d")
    rank_ast, _ = compile_screen("PctChange(close, 20)", base_tf="1d")
    dates = H.rebalance_dates(days, start, end, 5)
    sig = H.signals(ast, validated, frames, dates)
    ranks = B.rank_table(rank_ast, frames, dates)
    return B.simulate(sig, ranks, frames, days, end, B.Settings(top_n=3, weighting="rank"))


def test_the_equity_up_to_a_day_ignores_every_later_bar():
    frames, days = _universe(n=10, bars=200)
    full = _pipeline(frames, days, days[110], days[-1])
    cut_day = days[160]
    future = {s: f.copy() for s, f in frames.items()}
    for f in future.values():
        later = np.array([x > cut_day for x in H.session_dates(f.index)])
        f.loc[later, ["open", "high", "low", "close"]] *= 2.5
    rewritten = _pipeline(future, days, days[110], days[-1])
    k = full.days.index(cut_day)
    assert full.equity[:k + 1] == pytest.approx(rewritten.equity[:k + 1]) and full.costs > 0
    assert full.equity[k + 5:] != pytest.approx(rewritten.equity[k + 5:])                          # the later part does move
    assert [r for r in full.rebalances if r["filled"] <= str(cut_day)] == [r for r in rewritten.rebalances if r["filled"] <= str(cut_day)]


def test_ranks_read_only_bars_closed_by_the_date():
    frames, days = _universe(n=5, bars=80)
    rank_ast, _ = compile_screen("PctChange(close, 10)", base_tf="1d")
    d = days[50]
    table = B.rank_table(rank_ast, frames, [d])
    for s, f in frames.items():
        upto = H.cut(f, d)
        assert table.at[d, s] == pytest.approx(upto["close"].iloc[-1] / upto["close"].iloc[-11] * 100 - 100)


# --- the route -------------------------------------------------------------------------------------------------------------------

def _org(tag, backtest=True):
    t = _tenant(_email(tag), with_credentials=False)
    for name in ("screener_v2", H.FLAG) + ((B.FLAG,) if backtest else ()):
        _flag(name, t["tenant_id"])
    return t


def _symbols(n):
    stem = f"B{uuid.uuid4().hex[:10].upper()}"  # wide enough that two runs never share a symbol in the shared DB
    frames, days = _universe(n=n, bars=200, seed=21)
    renamed = {f"{stem}{i}": f for i, f in enumerate(frames.values())}
    for s, f in renamed.items():
        _seed(*_lake_rows(s, f))
    return renamed, days


def test_the_backtest_route_records_a_trial_and_needs_its_flag():
    off = _org("off", backtest=False)
    body = {"source": SCREEN, "start": "2025-06-02", "end": "2025-09-30", "universe": {"symbols": ["X"]}}
    r = client.post("/api/screener/history/backtest", headers=off["headers"], json=body)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == B.FLAG
    t = _org("run")
    frames, days = _symbols(8)
    body = {"source": SCREEN, "start": str(days[110]), "end": str(days[190]), "every": "weekly", "universe": {"symbols": list(frames)},
            "top_n": 3, "rank_by": "PctChange(close, 20)", "weighting": "rank"}
    r = client.post("/api/screener/history/backtest", headers=t["headers"], json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["rebalances"] >= 10 and out["strategy"]["sessions"] > 50 and out["costs"] > 0
    assert set(out["benchmarks"]) == {"equal_weight_universe"} and "NIFTY 50 closes are not in the lake" in out["notes"][-1]
    assert out["deflation"]["n_trials"] == 1 and "not a recommendation" in out["disclaimer"] and out["settings"]["top_n"] == 3

    async def trials():
        async with _session_factory() as session:
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == t["tenant_id"])))
    rows = _run(trials())
    assert len(rows) == 1 and json.loads(rows[0].dsl_json)["kind"] == "screen_backtest"
    again = client.post("/api/screener/history/backtest", headers=t["headers"], json={**body, "weighting": "equal"}).json()
    assert again["deflation"]["n_trials"] == 2


def test_backtest_refusals(monkeypatch):
    t = _org("refuse")
    frames, days = _symbols(2)
    base = {"source": SCREEN, "start": str(days[110]), "end": str(days[150]), "universe": {"symbols": list(frames)}}
    assert client.post("/api/screener/history/backtest", headers=t["headers"], json={**base, "top_n": 2}).status_code == 422
    cross = client.post("/api/screener/history/backtest", headers=t["headers"], json={**base, "top_n": 1, "rank_by": "Rank(close)"})
    assert cross.status_code == 422 and "rank_by" in cross.text
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", str(days[140]))
    sealed = client.post("/api/screener/history/backtest", headers=t["headers"], json=base)
    assert sealed.status_code == 422 and "holdout" in sealed.text


# --- review of S6b -----------------------------------------------------------------------------------------------------------------

def test_rank_by_passes_the_screen_gates_and_must_be_a_number():
    t = _org("rankgate")
    frames, days = _symbols(2)
    base = {"source": SCREEN, "start": str(days[110]), "end": str(days[150]), "universe": {"symbols": list(frames)}, "top_n": 1}
    for rank_by, why in (("RelVolume(20)", "screener_breakouts"), ("PCR()", "PCR()"), ("close > open", "a number"),
                         ('Trend(3) == "UPTREND"', "last bar only")):
        r = client.post("/api/screener/history/backtest", headers=t["headers"], json={**base, "rank_by": rank_by})
        assert r.status_code == 422 and why in r.text, (rank_by, r.text)
    ok = client.post("/api/screener/history/backtest", headers=t["headers"], json={**base, "rank_by": "PctChange(close, 20)"})
    assert ok.status_code == 200, ok.text


def test_a_holding_without_an_open_is_kept_not_sold_at_a_stale_close(monkeypatch):
    _no_charges(monkeypatch)
    from tests.test_n3_s6_history import _sessions
    sess = _sessions(8)
    a = _bars([100.0] * 8, [100.0] * 8, sess)
    b = _bars([50.0] * 8, [50.0] * 8, sess)
    a = a.drop(a.index[3])                                            # A does not trade on day 3 (suspended)
    frames = {"A": a, "B": b}
    sig = pd.DataFrame(False, index=pd.Index([sess[0], sess[2]]), columns=["A", "B"])
    sig.loc[sess[0], "A"] = True                                      # bought day 1
    sig.loc[sess[2], "B"] = True                                      # switch to B at day 3's open - A has no open then
    res = B.simulate(sig, None, frames, sess, sess[-1], FREE)
    assert res.rebalances[1]["filled"] == str(sess[3])
    assert res.holdings[1] == 2                                       # A kept (could not be sold), B bought with the cash left


def test_benchmarks_share_the_basis_and_a_missing_close_is_skipped(monkeypatch):
    _no_charges(monkeypatch)
    from tests.test_n3_s6_history import _sessions
    sess = _sessions(6)
    frames = {"A": _bars([100, 102, 104, 106, 108, 110], [101, 103, 105, 107, 109, 111], sess)}
    sig = pd.DataFrame(True, index=pd.Index([sess[0]]), columns=["A"])
    nifty = pd.Series({sess[0]: 100.0, sess[1]: 101.0, sess[3]: 103.0, sess[4]: 104.0, sess[5]: 105.0})   # sess[2] missing
    res = B.simulate(sig, None, frames, sess, sess[-1], FREE, benchmark_closes={"nifty_50": nifty})
    assert res.benchmark_returns["equal_weight_universe"][0] == pytest.approx(103 / 102 - 1)      # day 1 from the open
    assert res.benchmark_returns["nifty_50"][0] == pytest.approx(101 / 100 - 1)
    assert res.benchmark_missing == {"nifty_50": 1} and len(res.benchmark_returns["nifty_50"]) == len(res.days) - 1
    assert B.report(res, FREE)["benchmark_missing_sessions"] == {"nifty_50": 1}


def test_a_kept_holding_is_not_cash_so_the_book_never_borrows(monkeypatch):
    """Re-check of S6b (MED): the targets share the equity left after a holding that cannot trade - sized on the whole
    equity, the buy of B would have spent the value still tied up in A and taken the cash far below 0."""
    _no_charges(monkeypatch)
    sess = _sessions(8)
    a = _bars([100.0] * 8, [100.0] * 8, sess).drop(H.bar_index([sess[3]])[0])    # A does not trade on day 3
    frames = {"A": a, "B": _bars([50.0] * 8, [50.0] * 8, sess)}
    sig = pd.DataFrame(False, index=pd.Index([sess[0], sess[2]]), columns=["A", "B"])
    sig.loc[sess[0], "A"] = True
    sig.loc[sess[2], "B"] = True
    res = B.simulate(sig, None, frames, sess, sess[-1], FREE)
    qty_a = int(100_000 * (1 - B.CASH_BUFFER) // 100)
    left = 100_000 - qty_a * 100
    qty_b = int(left * (1 - B.CASH_BUFFER) // 50)
    assert res.holdings[1] == 2 and res.cash[2] == pytest.approx(left - qty_b * 50) and min(res.cash) >= 0


def test_cash_never_goes_below_zero_with_gaps_and_charges():
    rng = np.random.default_rng(11)
    sess = _sessions(80)
    frames = {}
    for i in range(8):
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.03, len(sess)))
        opens = closes * (1 + rng.normal(0, 0.01, len(sess)))
        f = _bars(opens, closes, sess)
        frames[f"S{i}"] = f.drop(f.index[rng.choice(len(sess), 12, replace=False)])     # suspensions: no bar, no open
    dates = sess[:-1:3]
    sig = pd.DataFrame(rng.random((len(dates), 8)) > 0.4, index=pd.Index(dates), columns=list(frames))
    res = B.simulate(sig, None, frames, sess, sess[-1], B.Settings(capital=100_000.0, top_n=3))
    assert len(res.rebalances) == len(dates) and min(res.cash) >= 0


def test_an_unadjusted_symbol_is_not_held_across_a_split(monkeypatch):
    """Re-check of S6b (LOW): A's unadjusted bars halve at a 1:1 bonus on day 3; with the (symbol, session) pairs next to
    it excluded, A is not bought for a period touching it (and leaves the equal-weight universe for that period)."""
    _no_charges(monkeypatch)
    sess = _sessions(6)
    frames = {"A": _bars([100, 100, 100, 50, 50, 50], [100, 100, 100, 50, 50, 50], sess),
              "B": _bars([20, 20, 20, 21, 21, 21], [20, 20, 20, 21, 21, 21], sess)}
    sig = pd.DataFrame(True, index=pd.Index([sess[0]]), columns=["A", "B"])
    naive = B.simulate(sig, None, frames, sess, sess[-1], FREE)
    assert naive.equity[-1] < 80_000                                                  # the bonus read as a 50 % loss
    res = B.simulate(sig, None, frames, sess, sess[-1], FREE, excluded={("A", sess[2]), ("A", sess[3])})
    assert res.rebalances[0]["held"] == ["A", "B"] and res.holdings == [1]           # chosen, but only B is bought
    assert res.equity[-1] > 100_000 and min(res.cash) >= 0
    assert res.benchmark_returns["equal_weight_universe"][2] == pytest.approx(21 / 20 - 1)


def test_the_benchmark_s_first_day_is_measured_from_its_open(monkeypatch):
    _no_charges(monkeypatch)
    sess = _sessions(4)
    frames = {"A": _bars([100, 102, 104, 106], [101, 103, 105, 107], sess)}
    sig = pd.DataFrame(True, index=pd.Index([sess[0]]), columns=["A"])
    closes = pd.Series({sess[0]: 100.0, sess[1]: 101.0, sess[2]: 102.0, sess[3]: 103.0})
    opens = pd.Series({sess[1]: 100.5})                                               # an overnight gap from 100
    res = B.simulate(sig, None, frames, sess, sess[-1], FREE, benchmark_closes={"nifty_50": closes},
                     benchmark_opens={"nifty_50": opens})
    assert res.benchmark_returns["nifty_50"][:2] == pytest.approx([101 / 100.5 - 1, 102 / 101 - 1])


@pytest.mark.parametrize("slip", [0.5, 1.0, 2.0])
def test_buys_come_from_the_cash_the_sells_left(slip):
    """Re-verify of S6b (MED): rotating A -> B at slippage the API allows. Sized on the book valued at the open, the buy
    of B spent cash the sale of A never brought in (its slippage and charges): -2,164 at 0.5 %. Now the buys come from
    the cash after the sells, scaled down until they fit."""
    sess = _sessions(6)
    frames = {"A": _bars([100.0] * 6, [100.0] * 6, sess), "B": _bars([100.0] * 6, [100.0] * 6, sess)}
    sig = pd.DataFrame(False, index=pd.Index([sess[0], sess[2]]), columns=["A", "B"])
    sig.loc[sess[0], "A"] = True
    sig.loc[sess[2], "B"] = True
    res = B.simulate(sig, None, frames, sess, sess[-1], B.Settings(capital=1_000_000.0, slippage_pct=slip, brokerage=20.0))
    assert [r["held"] for r in res.rebalances] == [["A"], ["B"]] and res.holdings == [1, 1]
    assert min(res.cash) >= 0 and res.cash[-1] < 0.01 * 1_000_000                  # still invested, not left in cash


@pytest.mark.parametrize("capital,brokerage", [(1_000_000.0, 20.0), (10_000.0, 1_000.0)])
def test_random_books_never_borrow_at_the_highest_slippage(capital, brokerage):
    for seed in range(30):
        rng = np.random.default_rng(seed)
        sess = _sessions(60)
        frames = {}
        for i in range(6):
            closes = 100 * np.cumprod(1 + rng.normal(0, 0.03, len(sess)))
            f = _bars(closes * (1 + rng.normal(0, 0.01, len(sess))), closes, sess)
            frames[f"S{i}"] = f.drop(f.index[rng.choice(len(sess), 8, replace=False)])
        dates = sess[:-1:2]
        sig = pd.DataFrame(rng.random((len(dates), 6)) > 0.5, index=pd.Index(dates), columns=list(frames))
        res = B.simulate(sig, None, frames, sess, sess[-1],
                         B.Settings(capital=capital, top_n=3, slippage_pct=2.0, brokerage=brokerage))
        assert min(res.cash) >= 0, (seed, min(res.cash))


def test_an_order_whose_charges_exceed_its_value_is_not_placed():
    sess = _sessions(4)
    frames = {"A": _bars([100.0] * 4, [100.0] * 4, sess)}
    sig = pd.DataFrame(True, index=pd.Index([sess[0]]), columns=["A"])
    res = B.simulate(sig, None, frames, sess, sess[-1], B.Settings(capital=1_000.0, slippage_pct=0.0, brokerage=5_000.0))
    assert res.holdings == [0] and res.costs == 0.0 and res.cash == [1_000.0] * len(res.days)


def test_a_kept_holding_over_a_split_is_counted(monkeypatch):
    _no_charges(monkeypatch)
    sess = _sessions(6)
    a = _bars([100, 100, 100, 50, 50, 50], [100, 100, 100, 50, 50, 50], sess).drop(H.bar_index([sess[3]])[0])
    frames = {"A": a, "B": _bars([20.0] * 6, [20.0] * 6, sess)}
    sig = pd.DataFrame(False, index=pd.Index([sess[0], sess[2]]), columns=["A", "B"])
    sig.loc[sess[0], "A"] = True
    sig.loc[sess[2], "B"] = True                                       # A has no open on day 3 (its ex-date): kept
    res = B.simulate(sig, None, frames, sess, sess[-1], FREE, excluded={("A", sess[3]), ("A", sess[4])})
    assert res.stuck_near_split == 1 and B.report(res, FREE)["stuck_near_split"] == 1
