"""SFC P6 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): a strategy built on the chart runs in his bot
on the same path as every other one (PAPER; LIVE only by the owner's own switch - nothing here changes it).

Checks: the strategy list every page reads (GET /api/strategies, the public API's list) shows an atp.strategy/2
strategy with its name and timeframe and never fails on it (before P6 one such record failed the whole list); a PAPER
deployment of it is created on the existing path and resolves to the ONE evaluator the chart draws with (the parity of
chart = backtest = worker on the same bars is test_sfc_p0.py). Made-up data only."""
from __future__ import annotations

import asyncio
import json

from app.custom_strategies.resolver import custom_strategy_info, resolve_strategy
from app.db.models import CustomStrategyRecord, User
from app.strategy_v2.adapter import StrategyV2Strategy
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _store_broker, _upgrade_plan
from tests.test_sfc_p1 import RAW, _org


def _saved(org) -> tuple[str, int]:
    made = client.post("/api/strategies/v2", headers=org["headers"], json={"strategy": RAW})
    assert made.status_code == 201, made.text
    return made.json()["id"], made.json()["record_id"]


def test_the_strategy_list_shows_a_chart_built_strategy_and_never_fails_on_it():
    me = _org("p6-list")
    sid, rid = _saved(me)
    listed = client.get("/api/strategies", headers=me["headers"])
    assert listed.status_code == 200, listed.text
    row = next(s for s in listed.json() if s["id"] == sid)
    assert row["name"] == "EMA cross + RSI" and row["timeframes"] == ["5min"]
    info = custom_strategy_info(CustomStrategyRecord(id=rid, tenant_id=me["tenant_id"], user_id=me["user_id"], name="x",
                                                     config_json=json.dumps(RAW)))
    assert (info.id, info.name, info.timeframes) == (sid, "EMA cross + RSI", ["5min"])           # the public API's list too


def test_a_paper_deployment_of_it_is_created_and_runs_on_the_one_evaluator():
    me = _org("p6-deploy")
    _upgrade_plan(me["tenant_id"])
    _store_broker(me["headers"])
    sid, _rid = _saved(me)
    made = client.post("/api/deployments", headers=me["headers"], json={"strategy_id": sid, "symbol": "reliance"})
    assert made.status_code == 201, made.text
    assert made.json()["strategy_id"] == sid and made.json()["mode"] == "PAPER"              # PAPER unless he picks LIVE

    async def resolve():
        async with _session_factory() as session:
            user = await session.get(User, me["user_id"])
            return await resolve_strategy(sid, user, session)
    strategy = asyncio.run(resolve())
    assert isinstance(strategy, StrategyV2Strategy) and strategy.strategy.name == "EMA cross + RSI"
