"""M72 PR-8 (CP-8): the Copilot coach. A week of the trader's OWN journal as numbers - his own checklist rules, his labels
and the journal's behaviour tags, his best / worst setups - and questions back built from his own rules (never an
instruction). Another member's trades never count. A planner answer after a review that reads as advice is refused.
No test calls a real provider (scripted models)."""
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ai import tools
from app.ai.tools import ToolContext, coach, run_tool
from app.db.models import TradeRecord, User
from app.journal import capture
from app.journal.review import advice_in
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_m72_pr5_chart_read import Recording, _agent, _final
from tests.test_ui2_4a1_prefs import _invite, _who

NOW = datetime.now(UTC)
IST = timezone(timedelta(hours=5, minutes=30))


def _d(i, setup, pnl):
    return SimpleNamespace(id=i, setup=setup, pnl=pnl, rows=(i,))


def test_rules_labels_and_setups_from_his_own_rows():
    settings = SimpleNamespace(checklist_rules=["Trend agrees", "Area marked"])
    decisions = [_d(1, "pullback", 500.0), _d(2, "breakout", -200.0), _d(3, "", None)]
    entries = {1: SimpleNamespace(checklist_json='[{"rule": "Trend agrees", "followed": true}]', user_tags_json="[]"),
               2: SimpleNamespace(checklist_json='[{"rule": "Trend agrees", "followed": false}]', user_tags_json='["late entry"]')}
    rules = coach.rule_rows(decisions, entries, settings)
    assert rules[0] == {"rule": "Trend agrees", "followed": 1, "not_followed": 1, "unanswered": 1,
                        "net_pnl_followed": 500.0, "net_pnl_not_followed": -200.0}
    assert rules[1]["unanswered"] == 3 and rules[1]["net_pnl_followed"] is None
    marks = coach.mistake_rows(decisions, entries, [(2, "MOVED_STOP")])
    assert {m["source"] for m in marks} == {"Your label", "Found by the journal"} and ("late entry", "Your label") in {(m["label"], m["source"]) for m in marks}
    assert all(m["net_pnl"] == -200.0 for m in marks)
    setups = coach.setup_rows(decisions)
    assert [s["setup"] for s in setups] == ["pullback", coach.NO_SETUP, "breakout"]
    assert setups[0]["win_rate_pct"] == 100.0 and setups[1]["closed"] == 0 and setups[2]["net_pnl"] == -200.0


def test_questions_back_are_his_own_and_never_advice():
    rules = [{"rule": "Trend agrees", "followed": 1, "not_followed": 2, "unanswered": 1}]
    qs = coach.questions("rules", rules, True)
    assert qs == ["Your rule \"Trend agrees\" was marked not followed on 2 trades. What led to those entries?",
                  "1 trade has no checklist answer. Was each of those entries in your plan?"]
    assert coach.questions("rules", [], False)[0].startswith("You have no checklist rules")
    assert coach.questions("setups", [{"setup": "pullback", "net_pnl": 5.0}, {"setup": "breakout", "net_pnl": -2.0}], True) == [
        "Was each \"pullback\" entry in your plan?", "What did the \"breakout\" entries have in common?"]
    for part, rows in (("rules", rules), ("mistakes", [{"label": "fear", "trades": 2}]), ("setups", [{"setup": "x", "net_pnl": 1.0}])):
        for q in coach.questions(part, rows, True):
            assert q.endswith("?") and not advice_in(q), q
    reg = tools.registry()["get_coach_week"]
    assert reg.kind == "read" and reg.flag == "discipline_engine" and reg.agent


@pytest.fixture
def journal():
    owner = _who("pr8-coach")
    member = _invite(owner, "USER")
    _run(_flag("discipline_engine", False, [owner["tenant_id"]]))
    capture.reset_flag_cache()
    yield owner, member
    _run(_flag("discipline_engine", False))
    _run(_flag("copilot_agent", False))
    capture.reset_flag_cache()


def _closed(who, setup, pnl):
    async def go():
        async with _session_factory() as session:
            rec = TradeRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol="PR8TEST", strategy_id=setup,
                              direction="LONG", entry_time=NOW - timedelta(minutes=50), entry_price=100.0, quantity=10.0, stop_loss=98.0,
                              target1=104.0, exit_time=NOW - timedelta(minutes=20), exit_price=100.0 + pnl / 10.0, pnl=pnl, charges=0.0)
            session.add(rec)
            await session.commit()
            return rec.id
    return _run(go())


def _call(who, args):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            return await run_tool("get_coach_week", args, ToolContext(session, user.tenant_id, user, NOW))
    return _run(go())


def _week():
    iso = (NOW - timedelta(minutes=50)).astimezone(IST).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def test_the_tool_reads_his_week_only(journal):
    owner, member = journal
    h = owner["headers"]
    assert client.put("/api/journal/settings", headers=h, json={"custom_tags": ["late entry"], "checklist_rules": ["Trend agrees"]}).status_code == 200
    won, lost = _closed(owner, "pullback", 500.0), _closed(owner, "breakout", -200.0)
    _closed(member, "teammate setup", 900.0)
    assert client.patch(f"/api/journal/trades/{won}/checklist", headers=h, json={"items": [{"rule": "Trend agrees", "followed": True}]}).status_code == 200
    assert client.patch(f"/api/journal/trades/{lost}/checklist", headers=h, json={"items": [{"rule": "Trend agrees", "followed": False}]}).status_code == 200
    assert client.patch(f"/api/journal/trades/{lost}/user-tags", headers=h, json={"tags": ["late entry"]}).status_code == 200
    rules = _call(owner, {"part": "rules", "week": _week()})
    assert rules.ok, rules.error
    assert rules.data["rules"] == [{"rule": "Trend agrees", "followed": 1, "not_followed": 1, "unanswered": 0,
                                    "net_pnl_followed": 500.0, "net_pnl_not_followed": -200.0}]
    assert "What led to those entries?" in rules.data["questions_for_you"][0]
    setups = _call(owner, {"part": "setups", "week": _week()}).data["setups"]
    assert [s["setup"] for s in setups] == ["pullback", "breakout"]                      # the member's setup never counts
    marks = _call(owner, {"part": "mistakes", "week": _week()}).data["mistakes"]
    assert {"label": "late entry", "source": "Your label", "trades": 1, "closed": 1, "net_pnl": -200.0} in marks
    assert not _call(owner, {"part": "rules", "week": "last week"}).ok                   # a malformed week is refused


def test_a_review_answer_that_instructs_is_refused(journal):
    owner, _member = journal
    _run(_flag("copilot_agent", False, [owner["tenant_id"]]))
    model = Recording([{"calls": [{"tool": "get_coach_week", "args": {"part": "setups"}}]},
                       _final("Next time, wait for the area before you enter.", "Your week"),
                       _final("This week has no closed trades in your journal yet. Was each entry in your plan?", "Your week")])
    ans = _agent(owner["user_id"], model, "Coach me on my week")
    assert ans.source == "ai", ans.note
    assert "reads as advice" in model.sent[-1]
    assert "Next time" not in ans.text
