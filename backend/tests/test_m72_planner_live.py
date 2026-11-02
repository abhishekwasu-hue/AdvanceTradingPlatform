"""M72 CP-1 / M72Q-7: the owner-run live eval of the Copilot planner (evals/planner_live.py), driven here with a FAKE
provider only - no network, no key, no spend. Checks: every question and the safety block run through the production
planner on the recorded tools; refusals never reach the model; tokens and cost per answer are counted and priced from
the price table; nothing is written; the command stops (exit 2, nothing sent) when no key can be used."""
import json

import pytest

from app.ai import pricing
from app.ai.providers import Completion, ToolCall, ToolTurn
from evals import planner_live, trader_eval

MODEL = "claude-sonnet-5-5"


class FakeModel:
    """Scripted tool-using model with usage: S01 reads the chain analyses and answers its two figures; every other
    question answers at once with no figure. Each step reports 1000 tokens in and 100 out."""
    name, model, supports_tools = "anthropic", MODEL, True

    def __init__(self):
        self.prompt_version, self.questions = None, []
        self.s01 = next(c for c in trader_eval.load_cases() if c["id"] == "S01")

    async def complete_tools(self, system, messages, tools, *, max_tokens=4000):
        question = messages[0]["content"]
        usage = Completion("", input_tokens=1000, output_tokens=100, model=MODEL)
        if len(messages) == 1:
            self.questions.append(question)
            if question == self.s01["question"]:
                call = ToolCall("t1", "get_option_chain_analyses", {"symbol": "NIFTY"})
                return ToolTurn("", [call], [{"type": "tool_use", "id": "t1", "name": call.name, "input": call.arguments}], usage, "tool_use")
        if question == self.s01["question"]:
            text = "NIFTY: highest put OI at 24600, highest call OI at 25100."
        else:
            text = "The data for this is not available here."
        body = json.dumps({"text": text, "claims": []})
        return ToolTurn(body, [], [{"type": "text", "text": body}], usage, "end_turn")


@pytest.fixture(scope="module")
def live():
    fake = FakeModel()
    limits = planner_live.asyncio.run(planner_live._limits())[0]
    return fake, planner_live.run_eval(fake, limits)


def test_every_question_runs_through_the_planner(live):
    fake, out = live
    cases, safety = trader_eval.load_cases(), trader_eval.load_safety()
    assert len(out["report"]["cases"]) == len(cases) == 60
    assert out["report"]["safety"]["n"] == len(safety)
    assert set(out["runs"]) == {c["id"] for c in cases} | {c["id"] for c in safety}


def test_a_grounded_planner_answer_scores(live):
    _, out = live
    s01 = next(r for r in out["report"]["cases"] if r.id == "S01")
    assert s01.answered and s01.correct, s01.problems
    assert out["runs"]["S01"].tools == ["get_option_chain_analyses"]
    assert out["runs"]["S01"].stopped == "answered"


def test_refusals_never_reach_the_model(live):
    fake, out = live
    refused = [c for c in trader_eval.load_safety() if c["expect"] == "refused"]
    assert refused
    for case in refused:
        run = out["runs"][case["id"]]
        assert run.stopped == "refused" and run.usage.calls == 0
        assert case["question"] not in fake.questions
    assert out["report"]["safety"]["score"] == 1.0


def test_tokens_and_cost_per_answer_are_priced(live):
    _, out = live
    s01 = out["runs"]["S01"].usage
    assert (s01.calls, s01.input_tokens, s01.output_tokens) == (2, 2000, 200)
    one = pricing.cost_usd("anthropic", MODEL, input_tokens=1000, output_tokens=100).amount
    assert s01.cost_usd == pytest.approx(2 * one)
    total = out["total"]
    assert total.calls == sum(r.usage.calls for r in out["runs"].values())
    assert total.cost_usd == pytest.approx(sum(r.usage.cost_usd for r in out["runs"].values()))
    text = planner_live.render(out, "fake", "defaults")
    for part in ("## Trader eval", "## Planner score per job", "## Safety:", "## Tokens and cost per answer", "Total:", "INR"):
        assert part in text
    assert "| option chain |" in text


def test_no_database_is_used_for_the_questions(monkeypatch):
    # Every case runs on the no-write session: a database session opened during the run would fail here.
    import app.db.session as db

    def refuse():
        raise AssertionError("the eval opened a database session")
    monkeypatch.setattr(db, "_session_factory", refuse)
    out = planner_live.run_eval(FakeModel(), planner_live.agent_limits_default())
    assert len(out["report"]["cases"]) == 60


def test_dry_run_command_needs_no_key(capsys, monkeypatch):
    monkeypatch.delenv("AI_PLATFORM_API_KEY", raising=False)
    assert planner_live.main(["--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "dry run (offline fake model, no key)" in printed and "0.0000 USD" in printed


def test_no_key_means_nothing_is_sent(capsys, monkeypatch):
    monkeypatch.delenv("AI_PLATFORM_PROVIDER", raising=False)
    monkeypatch.delenv("AI_PLATFORM_API_KEY", raising=False)
    monkeypatch.delenv("AI_PLATFORM_API_KEY_FILE", raising=False)
    assert planner_live.main(["--key", "platform"]) == 2
    assert "nothing was sent" in capsys.readouterr().err


def test_json_report(capsys):
    assert planner_live.main(["--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["eval"]["overall"]["n"] == 60 and "total_inr" in data and len(data["runs"]) > 60


def test_calc_tools_run_for_real_in_the_eval():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.ai.tools import ToolContext
    from evals import runner
    seen = []
    tool = planner_live._recorded_tool(runner.recorded_runner({}), seen)
    ctx = ToolContext(planner_live._NoWriteSession(), 1, SimpleNamespace(id=1, tenant_id=1), datetime.now(UTC))

    async def go():
        return (await tool("calc_sum", {"values": [1.5, 2.5]}, ctx),
                await tool("calc_option_payoff", {"legs": [{"right": "CE", "side": "SELL", "strike": 25100, "premium": 40, "quantity": 75}]}, ctx),
                await tool("get_my_orders", {}, ctx))
    total, payoff, orders = planner_live.asyncio.run(go())
    assert total.ok and total.data["sum"] == 4.0 and payoff.ok and payoff.data["net_premium"] == 3000
    assert not orders.ok and orders.error == "no recorded result" and seen == ["calc_sum", "calc_option_payoff"]
