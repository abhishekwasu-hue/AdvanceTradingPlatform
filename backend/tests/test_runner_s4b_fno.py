"""Runner S4b (docs/design/USER_RUNNER.md, step S4b): options / futures and multi-leg deployments on the runner.

1. The loop (a separate process, tests/runner_s4b_fno_job.py, local SQLite file, no Redis, the contracts from S4's
   reference sync, a PAPER-only fake NFO feed):
   - an index option single leg: PAPER entry on the contract his own settings pick, exit on his own premium stop;
   - a two-leg bull put spread: the hedge (bought) leg is placed before the sold leg, both squared off at his own time;
   - a stock in the F&O ban list: a new entry is refused in plain words, the open position's exit still runs;
   - a SWING index option past its expiry: settled in cash at the expiry-day close of his broker's Daily bar (the forming
     bar after it is never read), exit time at the expiry close;
   - an index future: PAPER entry and square-off;
   - the process never loads a forbidden module; the audit chain is intact.
2. The broker side: ATP's contract names are bound to Angel One's own scrip-master rows (the real adapter, a fake scrip
   master over httpx's mock transport); a contract it does not list, or lists with another lot size, is refused.
3. LIVE: a runner without ATP's state (not paired) refuses every LIVE entry in plain words (S4c: tests/test_runner_s4c_state.py).
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.runner.manifest import RUNTIME_FORBIDDEN, is_forbidden
from app.runner.settings import RunnerSettings

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def job(tmp_path_factory) -> dict:
    home = tmp_path_factory.mktemp("runner-s4b-home")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL", "ATP_RUNNER"))}
    env.update(PYTHONPATH=str(BACKEND), FO_BAN_CHECK_ENABLED="true")
    run = subprocess.run([sys.executable, "tests/runner_s4b_fno_job.py", str(home)], cwd=BACKEND, env=env, capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    line = next(x for x in run.stdout.splitlines() if x.startswith("RUNNER_S4B "))
    return json.loads(line[len("RUNNER_S4B "):])


def _trades(step: dict, dep: str) -> list[dict]:
    return [t for t in step["trades"] if t["dep"] == dep]


def test_an_option_single_leg_paper_entry_and_its_own_stop_exit(job):
    t1, t2 = job["t1"], job["t2"]
    assert t1["errors"] == [] and t1["errors_by_dep"]["A"] is None
    (entry,) = _trades(t1, "A")
    assert entry["symbol"] == "NIFTY 24500 CE 17 NOV 26" and entry["kind"] == "OPTION" and entry["mode"] == "PAPER"
    assert entry["quantity"] == 75 and entry["entry"] == 100 and entry["stop"] == 70   # his 1 lot; his own 30% premium stop
    (closed,) = _trades(t2, "A")
    assert closed["exit_reason"] == "Premium floor" and closed["exit_price"] == 60


def test_a_two_leg_structure_places_the_hedge_first_and_squares_off_at_his_time(job):
    legs = _trades(job["t1"], "B")
    assert {(t["symbol"], t["role"]) for t in legs} == {("NIFTY 24500 PE 17 NOV 26", "SHORT"), ("NIFTY 24400 PE 17 NOV 26", "LONG")}
    assert all(t["group"] and t["exit_reason"] is None for t in legs)
    order = [o for o in job["hedge_order"] if " PE " in o[0]]
    assert order == [["NIFTY 24400 PE 17 NOV 26", "BUY"], ["NIFTY 24500 PE 17 NOV 26", "SELL"]]    # hedge first
    assert all(t["exit_reason"] is None for t in _trades(job["t2"], "B"))                       # before his time: open
    assert [t["exit_reason"] for t in _trades(job["t3"], "B")] == ["Intraday square-off"] * 2


def test_a_banned_symbol_refuses_the_entry_and_its_open_position_still_exits(job):
    assert job["fo_ban_check"] is True and job["reference"]["ban list"] == 0
    assert len(_trades(job["t1"], "C")) == 1 and job["t1"]["errors_by_dep"]["D"] is None       # entered before the ban
    t2 = job["t2"]
    assert "F&O ban period" in t2["errors_by_dep"]["D"] and "exits allowed" in t2["errors_by_dep"]["D"]
    assert _trades(t2, "D") == [] and t2["placed"] == 0
    (c,) = _trades(t2, "C")
    assert c["exit_reason"] == "Premium floor" and c["exit_price"] == 10                        # the exit ran with the ban on


def test_expiry_settlement_on_paper_reads_the_expiry_day_close_only(job):
    (held,) = _trades(job["t3"], "E")
    assert held["holding"] == "SWING" and held["exit_reason"] is None                           # not squared off
    (settled,) = _trades(job["t4"], "E")
    assert settled["exit_reason"] == "Expired - settled"
    assert settled["exit_price"] == 100                  # 24700 (the expiry day's close) - 24600; not the forming 25000 bar
    assert settled["exit_time"].startswith("2026-11-17 10:00")                                  # 15:30 IST, the expiry close


def test_a_future_paper_entry_and_square_off(job):
    (entry,) = _trades(job["t1"], "F")
    assert entry["symbol"] == "NIFTY FUT 17 NOV 26" and entry["kind"] == "FUTURE" and entry["entry"] == 24520
    assert [t["exit_reason"] for t in _trades(job["t3"], "F")] == ["Intraday square-off"]


def test_every_step_is_paper_audited_and_the_process_loads_no_forbidden_module(job):
    for step in ("t1", "t2", "t3", "t4"):
        assert job[step]["errors"] == [] and job[step]["order_modes"] == ["PAPER"]
    assert job["audit_ok"] is True
    assert job["audit_kinds"].count("entry_intent") == job["audit_kinds"].count("entry_result")
    assert [m for m in job["loaded"] if is_forbidden(m, RUNTIME_FORBIDDEN)] == []


# --- the broker side: ATP's names on Angel One's own rows -------------------------------------------------------------
def _angel(rows: list[dict]):
    from app.brokers.angel_one import AngelOneBroker
    from app.brokers.models import BrokerCredentials
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=rows))
    client = httpx.AsyncClient(transport=transport, base_url="https://angel.test")
    return AngelOneBroker(BrokerCredentials(api_key="k", access_token="t"), client=client)


ANGEL_ROWS = [
    {"token": "71001", "symbol": "NIFTY17NOV2624500CE", "name": "NIFTY", "expiry": "17NOV2026", "strike": "2450000.000000",
     "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"token": "71002", "symbol": "NIFTY17NOV2624500PE", "name": "NIFTY", "expiry": "17NOV2026", "strike": "2450000.000000",
     "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"token": "71003", "symbol": "NIFTY17NOV26FUT", "name": "NIFTY", "expiry": "17NOV2026", "strike": "-1.000000",
     "lotsize": "75", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "10.000000"},
]


def _facts(symbol: str, right: str, strike: float | None, lot: int = 75):
    from app.runner.derivatives import ContractFacts
    return ContractFacts(exchange="NFO", symbol=symbol, underlying="NIFTY", expiry=date(2026, 11, 17), strike=strike, right=right,
                         lot_size=lot)


def test_atps_contract_names_reach_angel_ones_own_tokens():
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
    from app.runner.derivatives import ContractBinder
    angel = _angel(ANGEL_ROWS)
    wrapped = RateLimitedBroker(angel, RateBudget(limits_for(angel.name)))                 # as the runner wraps his session
    binder = ContractBinder()
    asyncio.run(binder.bind(wrapped, [_facts("NIFTY 24500 PE 17 NOV 26", "PE", 24500.0), _facts("NIFTY FUT 17 NOV 26", "FUT", None)]))
    assert asyncio.run(angel._resolve("NIFTY 24500 PE 17 NOV 26", "NFO")) == ("71002", "NIFTY17NOV2624500PE")
    assert asyncio.run(angel._resolve("NIFTY FUT 17 NOV 26", "NFO")) == ("71003", "NIFTY17NOV26FUT")


def test_a_contract_angel_one_does_not_list_the_same_way_is_refused():
    from app.instruments.contracts import ContractResolutionError
    from app.runner.derivatives import ContractBinder
    angel = _angel(ANGEL_ROWS)
    with pytest.raises(ContractResolutionError, match="not in angel_one's instrument list"):
        asyncio.run(ContractBinder().bind(angel, [_facts("NIFTY 24600 CE 17 NOV 26", "CE", 24600.0)]))
    with pytest.raises(ContractResolutionError, match="lot size 50 and angel_one's 75 differ"):
        asyncio.run(ContractBinder().bind(angel, [_facts("NIFTY 24500 CE 17 NOV 26", "CE", 24500.0, lot=50)]))


def test_a_feed_without_broker_names_keeps_atps_names():
    from app.runner.derivatives import ContractBinder
    from app.runner.feed import FakeFeed
    asyncio.run(ContractBinder().bind(FakeFeed().broker, [_facts("NIFTY 24500 CE 17 NOV 26", "CE", 24500.0)]))   # no error


# --- LIVE ------------------------------------------------------------------------------------------------------------
def test_live_is_refused_on_a_runner_that_has_no_atp_state(tmp_path):
    """S4c replaced the S4b refusal: LIVE needs ATP's signed state (tests/test_runner_s4c_state.py); unpaired = unknown."""
    from app.core import runtime_mode
    from app.runner.feed import FakeFeed
    from app.runner.loop import LIVE_NO_ATP_STATE, Runner
    settings = RunnerSettings.load(tmp_path)
    assert Runner(settings, FakeFeed())._atp_live_refusal(None, 1) == LIVE_NO_ATP_STATE       # type: ignore[arg-type]
    assert runtime_mode.live_open() == frozenset()
    assert "nothing was placed" in LIVE_NO_ATP_STATE and "exits continue" in LIVE_NO_ATP_STATE
