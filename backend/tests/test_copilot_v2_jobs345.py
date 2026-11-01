"""Item 5 (owner's handover, the AI Copilot as a trader's brain), second slice: jobs 3 (pre-trade check), 4 (position
watch) and 5 (post-trade coach) - reading the trader's own idea, the jobs' answers on recorded fixtures, every number
grounded, the describe-only AI wording, and the three new read tools on a real database (own rows only, nothing written
to a trade or an order)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.ai import copilot_chat as chat
from app.ai import grounding, tools
from app.ai.output_filter import findings
from app.ai.tools import ToolContext, run_tool
from app.db.models import AiActionRecord, OrderRecord, TradeRecord, User
from evals import runner
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import _flag
from tests.test_phase_l_ai import _owner

FIX = runner.copilot_fixtures()
IDEA = ("Check my trade idea: bullish NIFTY from the demand area, pullback entry at 24,600, invalidation 24,480, objective 25,050, "
        "2 lots")


def _run(coro):
    return asyncio.run(coro)


def _answer(question, fixture="default"):
    calls = []
    out = _run(chat.answer(question, runner.recorded_runner(FIX[fixture], calls)))
    return out, [c["tool"] for c in calls]


# --- reading the trader's own idea ----------------------------------------------------------------------------------------

def test_the_idea_reader_takes_only_what_the_words_state():
    idea = chat.read_idea(IDEA)
    assert (idea.side, idea.entry, idea.invalidation, idea.objective, idea.lots, idea.shares, idea.model) == (
        "bullish", 24600.0, 24480.0, 25050.0, 2, None, "pullback")
    short = chat.read_idea("check my plan: short BANKNIFTY at 52,100 sl 52,400 qty 30, change of character on the 15 min")
    assert (short.side, short.entry, short.invalidation, short.shares, short.model) == ("bearish", 52100.0, 52400.0, 30, "change of character")
    bare = chat.read_idea("Check my trade idea")
    assert (bare.side, bare.entry, bare.invalidation, bare.lots, bare.shares, bare.model) == (None, None, None, None, None, None)
    both = chat.read_idea("check my trade idea: long or short NIFTY?")
    assert both.side is None                                       # two sides named: not stated, never guessed
    assert chat.symbol_in("Check my trade idea: long RELIANCE entry 2900 SL 2860") == "RELIANCE"


def test_the_starters_and_new_phrases_route_to_jobs_3_to_5():
    for q, job in (("Check my trade idea", "trade_check"), ("check my plan: long NIFTY at 24600", "trade_check"),
                   ("How are my open positions?", "positions"), ("what is the risk on my positions", "positions"),
                   ("Review my last trade", "review"), ("How was my week?", "review"), ("What is my discipline score?", "review")):
        assert chat.route(q) == job, q
    assert chat.BUILT >= {"trade_check", "positions", "review"}                  # job 6 joined in slice 3 (test_copilot_v2_job6.py)


# --- the answers on recorded fixtures -------------------------------------------------------------------------------------


def test_job3_an_unknown_lot_size_is_said_and_never_assumed_to_be_1():
    """M68: no pre-chosen trading values - without his own lot size the idea's risk is not worked out with a lot of 1."""
    import copy
    fixture = copy.deepcopy(FIX["default"])
    fixture["get_risk_limits"]["data"].pop("lot_size")
    out = _run(chat.answer(IDEA, runner.recorded_runner(fixture, [])))
    text = "\n".join(out.lines())
    assert "2 lots: lot size not known" in text and "at risk: above" not in text and "at risk: within" not in text
    assert [s.tool for s in out.worked if s.tool.startswith("calc:")] == ["calc:risk_budget", "calc:reward_to_risk"]

def test_job3_checks_the_idea_and_lists_what_is_missing_without_adding_rules():
    out, called = _answer(IDEA)
    assert called == ["get_chart_structure", "get_market_zones", "get_risk_limits", "get_market_context"]
    text = "\n".join(out.lines())
    assert "your bullish idea is with it" in text and "an area on your side" in text and "18,000 at risk: above your risk per trade" in text
    assert "What your plan does not say yet" not in [s["title"] for s in out.sections]       # everything was stated
    calcs = [s.tool for s in out.worked if s.tool.startswith("calc:")]
    assert calcs == ["calc:risk_budget", "calc:idea_risk", "calc:reward_to_risk"]
    bare, _ = _answer("Check my trade idea")
    missing = next(s for s in bare.sections if s["title"] == "What your plan does not say yet")["lines"]
    assert len(missing) == 5 and len(bare.summary) == 3                     # CU1: the third line says NIFTY was read by default
    assert bare.summary[-1] == f"You did not name a symbol, so I read {chat.DEFAULT_SYMBOL}."
    assert not any(w in text.lower() for w in ("take the trade", "good trade", "you should"))


def test_job4_warns_from_the_users_own_positions_and_settings():
    out, called = _answer("How are my open positions?")
    assert called[:3] == ["get_my_positions", "get_risk_limits", "get_market_context"]
    warnings = next(s for s in out.sections if s["title"] == "Warnings")["lines"]
    assert len(warnings) == 7 and out.action == {"label": "Open My positions", "to": "/positions"}
    assert any(b["type"] == "table" and b["table"]["title"] == "Your open positions" for b in out.blocks)
    assert {s.tool for s in out.worked if s.tool.startswith("calc:") and not s.tool.startswith("calc:news_count")} == {
        "calc:open_risk_share", "calc:daily_loss_used"}                       # 23 Oct: plus the news count per held symbol
    none, called = _answer("How are my open positions?", "no_data")
    assert called == ["get_my_positions", "get_risk_limits"] and none.summary[0].startswith("You have no open positions")


def test_job5_reviews_the_last_trade_the_week_and_the_repeated_habits():
    out, called = _answer("Review my last trade")
    assert called == ["get_weekly_review", "get_my_last_trade"]
    titles = [s["title"] for s in out.sections]
    assert titles[:3] == ["Your last trade", "Your week (2026-W43)", "Repeated habits"]
    assert out.summary[0].startswith("Your last trade: NIFTY26OCTFUT")
    week, _ = _answer("How was my week?")
    assert week.summary == ["2026-W43: 4 trades, 4 closed and 0 still open."]
    empty, _ = _answer("Review my last trade", "no_data")
    assert all(u.startswith("I don't know") for u in empty.unknowns) and len(empty.unknowns) == 2
    assert "flag" not in json.dumps(empty.as_dict()["unknowns"])


@pytest.mark.parametrize("question,fixture", [
    (IDEA, "default"), ("Check my trade idea", "default"), ("Check my trade idea: bearish NIFTY entry 24,600, invalidation 24,500, 1 lot", "default"),
    ("How are my open positions?", "default"), ("Review my last trade", "default"), ("How was my week?", "default"),
    (IDEA, "no_data"), ("How are my open positions?", "no_data"), ("Review my last trade", "no_data"),
])
def test_every_number_comes_from_a_tool_a_calculation_or_the_question(question, fixture):
    out, _ = _answer(question, fixture)
    allowed = grounding.allowed_from_text("\n".join([*out.trusted_text, chat.FIXED_TEXT, question]))
    for line in out.lines():
        assert grounding.check_numbers(line, allowed)[0], line
    assert "dropped_lines" not in out.diagnostics
    assert not findings("\n".join(out.lines()))
    from app.journal.review import advice_in
    describing = [x for s in out.sections if s["title"] not in ("How I check", "How I review", "What your plan does not say yet") for x in s["lines"]]
    assert not [a for a in advice_in("\n".join(describing)) if a.lower() not in ("check",)], describing


def test_the_ai_wording_of_jobs_3_to_5_may_only_describe():
    out, _ = _answer("How are my open positions?")
    ok = runner.ScriptedWriter("You have 2 open positions. If every stop is hit the loss would be 18,300, above your own limit of 6%.")
    assert _run(chat.narrate(ok, out, "How are my open positions?"))[0]
    for text in ("You have 2 open positions; consider reducing the NIFTY26OCTFUT size.", "Next time, keep the stop where it was."):
        shown, why = _run(chat.narrate(runner.ScriptedWriter(text), out, "How are my open positions?"))
        assert shown is None and "advice" in why, text
    brief, _ = _answer("Read the NIFTY chart")                   # the first slice's jobs keep their own checks only
    assert _run(chat.narrate(runner.ScriptedWriter("NIFTY last closed at 24,850.5; consider the protected level 24,310."), brief,
                             "Read the NIFTY chart"))[0]


def test_the_new_tools_are_read_only_and_strict():
    reg = tools.registry()
    for name, flag in (("get_my_positions", None), ("get_my_last_trade", None), ("get_weekly_review", "discipline_engine")):
        assert reg[name].kind == "read" and reg[name].flag == flag
    for spec in tools.specs(["get_my_positions", "get_my_last_trade", "get_weekly_review"]):
        assert spec["input_schema"]["additionalProperties"] is False
    assert reg["get_weekly_review"].input_model.model_validate({"week": "2026-W43"})
    with pytest.raises(Exception):
        reg["get_weekly_review"].input_model.model_validate({"week": "last week"})


# --- the tools on a real database -------------------------------------------------------------------------------------------

def _seed(user_id, tenant_id, rows):
    async def go():
        ids = []
        async with _session_factory() as session:
            for kw in rows:
                rec = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", strategy_id=kw.pop("strategy_id", "setupA"), **kw)
                session.add(rec)
                await session.flush()
                ids.append(rec.id)
            await session.commit()
        return ids
    return _run(go())


def _tools(user_id, *calls):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            ctx = ToolContext(session, user.tenant_id, user)
            return [await run_tool(n, a, ctx) for n, a in calls]
    return _run(go())


def test_the_tools_read_only_the_traders_own_rows():
    _, me = _owner("cop345-a@example.com")
    _, other = _owner("cop345-b@example.com")
    now = datetime.now(timezone.utc)

    async def same_tenant():                       # a teammate in the same organisation
        async with _session_factory() as session:
            (await session.get(User, other["id"])).tenant_id = me["tenant_id"]
            await session.commit()
    _run(same_tenant())
    mine = _seed(me["id"], me["tenant_id"], [
        dict(symbol="NIFTY26OCTFUT", direction="LONG", entry_time=now - timedelta(hours=2), entry_price=100.0, quantity=50, stop_loss=96.0,
             initial_stop_loss=97.0, mark_price=98.0, mark_time=now, instrument_kind="FUT", underlying_symbol="NIFTY"),
        dict(symbol="SBIN", direction="SHORT", entry_time=now - timedelta(hours=5), entry_price=200.0, quantity=10, stop_loss=204.0,
             initial_stop_loss=204.0, exit_time=now - timedelta(hours=1), exit_price=203.0, exit_reason="Closed by hand", pnl=-35.0, charges=5.0),
    ])
    _seed(other["id"], me["tenant_id"], [dict(symbol="INFY", direction="LONG", entry_time=now - timedelta(hours=1), entry_price=10.0, quantity=1,
                                              stop_loss=9.0, exit_time=now, exit_price=11.0, exit_reason="Target", pnl=1.0)])
    pos, last, weekly = _tools(me["id"], ("get_my_positions", {}), ("get_my_last_trade", {}), ("get_weekly_review", {}))
    assert pos.ok and pos.data["count"] == 1
    p = pos.data["positions"][0]
    assert (p["id"], p["side"], p["marked_pnl"], p["loss_if_stop_hit"], p["moved_to_stop_pct"], p["stop_widened"]) == (mine[0], "bullish", -100.0, 200.0, 50.0, True)
    assert pos.data["realised_today"] in (-35.0, 0.0) and pos.data["bullish"] == 1
    t = last.data["trade"]
    assert last.ok and t["id"] == mine[1] and t["symbol"] == "SBIN" and t["side"] == "bearish" and t["exit_words"] == "you closed it by hand"
    assert t["risk_planned"] == 40.0 and t["r_multiple"] == -0.88 and t["held_minutes"] == 240
    assert not weekly.ok and chat.plain_reason(weekly.error) == chat.PLAIN_FAILURE              # discipline engine off by default
    _run(_flag("discipline_engine", True))
    try:
        (weekly,) = _tools(me["id"], ("get_weekly_review", {}))
    finally:
        _run(_flag("discipline_engine", False))
    # A trade belongs to the IST week of its entry: in the first hours of an IST Monday the seeded trades fall in the
    # previous week, so count the review's current week together with its history weeks.
    read = weekly.data["totals"]["trades"] + sum(h["trades"] for h in weekly.data["history"])
    assert weekly.ok and weekly.data["weeks_read"] == 4 and weekly.data["lines"] and read >= 1
    (empty,) = _tools(other["id"], ("get_my_positions", {}))
    assert empty.ok and empty.data["count"] == 0                                              # never the teammate's rows


def test_the_route_answers_jobs_3_to_5_on_the_real_tools_and_writes_no_order():
    headers, me = _owner("cop345-route@example.com")
    now = datetime.now(timezone.utc)
    _seed(me["id"], me["tenant_id"], [dict(symbol="NIFTY26OCTFUT", direction="LONG", entry_time=now - timedelta(hours=2), entry_price=100.0,
                                           quantity=50, stop_loss=96.0, mark_price=99.0, mark_time=now, instrument_kind="FUT",
                                           underlying_symbol="NIFTY")])

    async def snapshot():
        async with _session_factory() as session:
            trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.user_id == me["id"])))
            return ([(t.id, t.stop_loss, t.exit_time, t.quantity) for t in trades],
                    await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == me["tenant_id"])),
                    await session.scalar(select(func.count()).select_from(AiActionRecord).where(AiActionRecord.tenant_id == me["tenant_id"])))
    before = _run(snapshot())
    _run(_flag("ui_v2", True))
    try:
        for q, job in (("How are my open positions?", "positions"), ("Review my last trade", "review"),
                       ("Check my trade idea: long NIFTY entry 24600 stop 24480 1 lot", "trade_check")):
            r = client.post("/api/ai/chat", headers=headers, json={"message": q})
            assert r.status_code == 200, (q, r.text)
            body = r.json()
            assert body["job"] == job and body["job_number"] in (3, 4, 5) and all(s["ok"] or s["note"] for s in body["worked"])
            assert "diagnostics" not in body and "flag" not in json.dumps(body["unknowns"])
        home = client.get("/api/ai/chat/home", headers=headers).json()
        assert [j["built"] for j in home["jobs"]] == [True] * 8            # jobs 9-10 (23 Oct order) are behind OFF switches here: hidden (CU7)
    finally:
        _run(_flag("ui_v2", False))
    assert _run(snapshot()) == before                     # the Copilot read; it changed no trade and created no order or proposal
