"""N2-1 (P1 spec 1.4 / 1.5, docs/design/N2_P1FGH.md): ladder evidence behind the `ladder_evidence` flag (off by default).

Covers:
- the pure figures (`app/decision_tools/own_stats.py`): expectancy, profit factor, payoff ratio, the Kelly reference and
  the risk-of-ruin bound, with gambler's-ruin parity;
- the flag: off leaves the Z3-b ladder unchanged;
- the discipline criterion: equal to the journal's own weekly scores, current week left out, not measured while the
  discipline engine is off;
- user scope, and no advice wording anywhere in the answer.
"""
import asyncio
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import config as app_config
from app.db.models import TradeRecord
from app.decision_tools import own_stats
from app.journal import capture
from app.journal import service as journal
from app.journal.review import advice_in
from app.paper import service
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member, _paper_flag

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n2-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _set_flag(name: str, tenant_id=None, *, on: bool = True, everyone: bool = False) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            if tenant_id is not None:
                (tenants.add if on else tenants.discard)(tenant_id)
            flags[name] = {"on": bool(on and everyone), "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())
    capture.reset_flag_cache()


@pytest.fixture
def discipline_on():
    _set_flag("discipline_engine", on=True, everyone=True)
    yield
    _set_flag("discipline_engine", on=False)


def _trade(t, pnl, *, entry_ago=timedelta(days=1), held=timedelta(hours=1), price_source=None):
    async def go():
        async with _session_factory() as session:
            entry = datetime.now(UTC) - entry_ago
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol="RELIANCE", strategy_id="manual",
                              direction="LONG", entry_time=entry, entry_price=100.0, quantity=10.0, stop_loss=95.0, target1=110.0,
                              exchange="NSE", pnl=pnl, charges=0.0, price_source=price_source, exit_time=entry + held,
                              exit_price=100.0 + pnl / 10.0, exit_reason="Target 1" if pnl > 0 else "Stop loss")
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# --- pure figures --------------------------------------------------------------------------------------------------------

def test_trade_stats_and_the_kelly_reference_by_hand():
    pnls = [300.0, -100.0, 200.0, -100.0, 0.0, 100.0]
    s = own_stats.trade_stats(pnls)
    assert (s["trades"], s["wins"], s["losses"], s["flat"]) == (6, 3, 2, 1)
    assert s["net"] == 400.0 and s["expectancy"] == pytest.approx(400.0 / 6)
    assert s["avg_win"] == 200.0 and s["avg_loss"] == 100.0 and s["payoff_ratio"] == 2.0
    assert s["profit_factor"] == 3.0 and s["win_rate"] == pytest.approx(0.5)
    # p = 3/5, q = 2/5 (a flat trade is neither), b = 2: (0.6 * 2 - 0.4) / 2 = 0.4
    assert own_stats.kelly_fraction(3, 2, 2.0) == pytest.approx(0.4)
    assert own_stats.kelly_fraction(1, 1, 1.0) == pytest.approx(0.0)
    assert own_stats.kelly_fraction(1, 3, 1.0) == pytest.approx(-0.5)            # textbook p - q / b = 0.25 - 0.75
    assert own_stats.kelly_fraction(0, 3, None) is None and own_stats.kelly_fraction(3, 0, None) is None
    empty = own_stats.trade_stats([])
    assert empty["trades"] == 0 and empty["expectancy"] is None and empty["profit_factor"] is None


def test_kelly_reference_maximises_the_mean_log_growth_of_the_past_outcomes():
    p, q, b = 0.55, 0.45, 1.5
    f = own_stats.kelly_fraction(55, 45, b)
    assert f is not None

    def growth(x):
        return p * math.log(1 + x * b) + q * math.log(1 - x)
    assert growth(f) > growth(f - 0.01) and growth(f) > growth(f + 0.01)


def test_ruin_bound_matches_gamblers_ruin_for_unit_steps():
    pnls = [1.0] * 6 + [-1.0] * 4                       # p = 0.6, q = 0.4
    r = own_stats.adjustment_coefficient(pnls)
    assert r == pytest.approx(math.log(1.5), rel=1e-9)
    for d in (1, 5, 12):
        out = own_stats.ruin_bound(pnls, d)
        assert out["status"] == "ok" and out["probability"] == pytest.approx((0.4 / 0.6) ** d, rel=1e-9)
    # Scale-free: rupee P&Ls 1000x with a 1000x distance give the same bound.
    assert own_stats.ruin_bound([x * 1000 for x in pnls], 5000)["probability"] == pytest.approx((2 / 3) ** 5, rel=1e-9)


def test_ruin_bound_edges():
    assert own_stats.ruin_bound([10.0, -20.0], 100.0) == {"status": "certain", "probability": 1.0}     # expectancy < 0
    assert own_stats.ruin_bound([10.0, -10.0], 100.0) == {"status": "certain", "probability": 1.0}     # expectancy = 0
    assert own_stats.ruin_bound([10.0, 0.0], 100.0) == {"status": "no_losses", "probability": None}
    assert own_stats.ruin_bound([], 100.0) == {"status": "certain", "probability": 1.0}
    # A huge edge: the coefficient is capped, the bound stays valid (tiny), never NaN.
    tiny = own_stats.ruin_bound([1000.0] * 999 + [-1.0], 50_000.0)
    assert tiny["status"] == "ok" and 0.0 <= tiny["probability"] < 1e-12
    # More distance never raises the bound; a bigger edge never raises it either.
    pnls = [120.0, -100.0, 80.0, -60.0, 150.0]
    a, b = own_stats.ruin_bound(pnls, 200)["probability"], own_stats.ruin_bound(pnls, 400)["probability"]
    assert 0 < b < a < 1
    assert own_stats.ruin_bound([x + 20 for x in pnls], 200)["probability"] < a


def test_evidence_block_waits_for_enough_trades_and_reads_its_inputs():
    few = own_stats.evidence([100.0, -50.0], 100_000, min_trades=20, ruin_pct=50)
    assert few["status"] == "too_few" and few["enough"] is False and "expectancy" not in few and few["note"].startswith("2 of 20")
    pnls = [200.0] * 12 + [-100.0] * 8
    ev = own_stats.evidence(pnls, 10_000, min_trades=20, ruin_pct=10)
    assert ev["status"] == "ok" and ev["trades"] == 20 and ev["win_rate"] == 0.6 and ev["expectancy"] == 80.0
    assert ev["profit_factor"] == 3.0 and ev["payoff_ratio"] == 2.0
    assert ev["kelly"]["fraction"] == pytest.approx(0.4) and ev["kelly"]["status"] == "ok"
    assert ev["ruin"]["distance"] == 1000.0 and ev["ruin"]["status"] == "ok"
    assert ev["ruin"]["probability"] == pytest.approx(own_stats.ruin_bound(pnls, 1000.0)["probability"], abs=1e-4)
    losing = own_stats.evidence([100.0] * 5 + [-200.0] * 15, 10_000, min_trades=20, ruin_pct=50)
    assert losing["kelly"]["status"] == "negative" and losing["ruin"]["status"] == "certain" and losing["ruin"]["probability"] == 1.0
    for text in _strings(ev):
        assert advice_in(text) == [], text


# --- API: flag, discipline criterion, scope ----------------------------------------------------------------------------

def test_flag_is_registered_off_and_the_ladder_is_unchanged_while_off():
    assert service.EVIDENCE_FLAG == "ladder_evidence"
    assert "ladder_evidence" in controls.FEATURE_FLAGS and "ladder_evidence" in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    _paper_flag(t["tenant_id"])
    _trade(t, 100.0)
    body = client.get("/api/paper/ladder", headers=t["headers"]).json()
    assert "evidence" not in body
    disc = {c["key"]: c for c in body["criteria"]}["discipline_score"]
    assert disc == {"key": "discipline_score", "label": "Discipline score", "value": None, "target": None, "comparison": None,
                    "met": None, "note": "Not measured yet (journal behaviour tags come with P1-e)"}
    assert body["measured"] == 3
    # Still 404 with ladder_evidence on but paper_account off.
    other = _tenant(_email("nopaper"))
    _set_flag("ladder_evidence", other["tenant_id"])
    assert client.get("/api/paper/ladder", headers=other["headers"]).status_code == 404


def test_flag_on_without_the_discipline_engine_shows_figures_and_says_why_discipline_is_not_measured():
    t = _tenant(_email("noengine"))
    _paper_flag(t["tenant_id"])
    _set_flag("ladder_evidence", t["tenant_id"])
    client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 100_000})
    for i in range(3):
        _trade(t, 100.0 if i % 2 == 0 else -40.0, entry_ago=timedelta(days=i + 1))
    body = client.get("/api/paper/ladder", headers=t["headers"]).json()
    disc = {c["key"]: c for c in body["criteria"]}["discipline_score"]
    assert disc["status"] == "engine_off" and disc["met"] is None and "discipline score is off" in disc["note"]
    assert body["measured"] == 3
    ev = body["evidence"]
    assert ev["status"] == "too_few" and ev["trades"] == 3 and ev["min_trades"] == app_config.PAPER_EVIDENCE_MIN_TRADES

    for i in range(app_config.PAPER_EVIDENCE_MIN_TRADES):
        _trade(t, 300.0 if i % 3 else -150.0, entry_ago=timedelta(days=1, hours=i))
    _trade(t, 5_000.0, price_source="client")                        # priced from the user's own inputs: left out
    ev = client.get("/api/paper/ladder", headers=t["headers"]).json()["evidence"]
    assert ev["status"] == "ok" and ev["trades"] == 3 + app_config.PAPER_EVIDENCE_MIN_TRADES
    pnls = [100.0, -40.0, 100.0] + [300.0 if i % 3 else -150.0 for i in range(app_config.PAPER_EVIDENCE_MIN_TRADES)]
    expected = own_stats.evidence(pnls, 100_000, min_trades=app_config.PAPER_EVIDENCE_MIN_TRADES, ruin_pct=app_config.PAPER_EVIDENCE_RUIN_PCT)
    assert ev == json.loads(json.dumps(expected))


def test_discipline_criterion_is_the_journals_own_weekly_scores_and_leaves_the_current_week_out(discipline_on):
    owner = _tenant(_email("disc"))
    _paper_flag(owner["tenant_id"])
    _set_flag("ladder_evidence", owner["tenant_id"])
    member = _member(owner)
    now = datetime.now(UTC)
    # Exactly 1, 2 and 3 weeks back: the same IST weekday and time, so each lands in its own completed week.
    for weeks_back in (1, 2, 3):
        _trade(owner, 100.0, entry_ago=timedelta(weeks=weeks_back))
        _trade(owner, -50.0, entry_ago=timedelta(weeks=weeks_back))
    _trade(owner, -500.0, entry_ago=timedelta(minutes=5), held=timedelta(minutes=1))           # this week: not in the criterion
    _trade(member, 900.0, entry_ago=timedelta(weeks=4))              # another user's week: never the owner's

    body = client.get("/api/paper/ladder", headers=owner["headers"]).json()
    disc = {c["key"]: c for c in body["criteria"]}["discipline_score"]
    labels = [journal.week_bounds(None, now=now - timedelta(weeks=k))[0] for k in (3, 2, 1)]
    assert [w["week"] for w in disc["weeks"]] == labels
    journal_scores = [client.get(f"/api/journal/week?week={w}", headers=owner["headers"]).json()["score"]["score"] for w in labels]
    assert [w["score"] for w in disc["weeks"]] == journal_scores and all(s is not None for s in journal_scores)
    assert disc["value"] == round(sum(journal_scores) / 3, 1)
    assert disc["target"] == app_config.PAPER_LADDER_MIN_DISCIPLINE and disc["comparison"] == "at_least"
    assert disc["weeks_needed"] == app_config.PAPER_LADDER_DISCIPLINE_WEEKS
    if app_config.PAPER_LADDER_DISCIPLINE_WEEKS > 3:
        assert disc["met"] is False and disc["status"] == "too_few_weeks" and disc["note"].startswith("3 of ")
    assert body["measured"] == 4

    # The member's card has only the member's own week.
    _paper_flag(member["tenant_id"])
    mine = {c["key"]: c for c in client.get("/api/paper/ladder", headers=member["headers"]).json()["criteria"]}["discipline_score"]
    assert [w["week"] for w in mine["weeks"]] == [journal.week_bounds(None, now=now - timedelta(weeks=4))[0]]


def test_discipline_criterion_with_no_completed_week_and_no_advice_wording(discipline_on):
    t = _tenant(_email("fresh"))
    _paper_flag(t["tenant_id"])
    _set_flag("ladder_evidence", t["tenant_id"])
    _trade(t, 100.0, entry_ago=timedelta(minutes=10), held=timedelta(minutes=1))
    body = client.get("/api/paper/ladder", headers=t["headers"]).json()
    disc = {c["key"]: c for c in body["criteria"]}["discipline_score"]
    assert disc["status"] == "no_weeks" and disc["value"] is None and disc["met"] is False and disc["weeks"] == []
    for text in _strings(body):
        assert advice_in(text) == [], text
    lowered = json.dumps(body).lower()
    for word in ("should", "recommend", "advice", "advise", "buy ", "sell "):
        assert word not in lowered, word
