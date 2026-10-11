"""H-C10 (ADR-0020): the agent's golden sets run in CI with scripted providers (no spend) and gate on the committed
baselines - a prompt change without a new baseline, or a score below it, fails."""
import asyncio
import copy
import json

from evals import runner
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_trading_worker import _deploy


def _run(coro):
    return asyncio.run(coro)


def _scores():
    _, me = _owner("hc10-evals@example.com")
    dep = _deploy({"tenant_id": me["tenant_id"], "user_id": me["id"]})
    scores = runner.run_static()
    scores["injection"] = _run(runner.run_injection(runner.load("injection"), _session_factory, me["id"], dep))
    return scores


def test_golden_sets_meet_the_baseline():
    scores = _scores()
    problems = runner.gate(scores)
    assert problems == [], problems
    assert all(s.total >= 10 for s in scores.values())


def test_the_sets_are_not_vacuous():
    qa = runner.load("qa")
    assert {c["expect"] for c in qa} == {"shown", "refused"} and any(c.get("lang") == "mr" for c in qa)
    assert {c["expect"] for c in runner.load("tool_args")} == {"accept", "refuse", "unknown"}
    injection = runner.load("injection")
    assert {c["expect_proposals"] for c in injection} == {0, 1}                              # "always refuse" cannot pass
    assert sum(1 for c in injection if c["headline"] and c["expect_proposals"] == 0) >= 5


def test_the_gate_catches_a_prompt_change_and_a_regression():
    base = json.loads(runner.BASELINES.read_text(encoding="utf-8"))
    scores = runner.run_static()
    changed = copy.deepcopy(base)
    changed["agent_prompt_version"] = "agent-00000000"
    assert any("prompt changed" in p for p in runner.gate(scores, changed))
    worse = runner.SetScore("qa", passed=9, total=10)
    assert any(p.startswith("qa:") for p in runner.gate({**scores, "qa": worse}, base))
    assert any("no baseline" in p for p in runner.gate({**scores, "new_set": worse}, base))
