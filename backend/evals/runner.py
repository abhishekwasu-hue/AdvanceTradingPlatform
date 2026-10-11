"""H-C10 (ADR-0020 §1-§3): the CI eval runner for the Copilot agent - golden sets as JSONL, deterministic checks, no
provider spend.

Sets (`evals/sets/`):
* qa.jsonl        - answer contract: a candidate answer against tool results must be shown or refused (grounding,
                    claim sources, advice words, answer language).
* tool_args.jsonl - tool schemas: arguments a model might send must be accepted or refused (extra keys, ranges,
                    patterns, client candles).
* injection.jsonl - the injection guard: a headline carrying an instruction must give zero proposals; control cases
                    where the trader asked must give exactly one (so "always refuse" cannot pass).

`run_all` scores each set (pass rate) and `gate` compares with `evals/baselines.json`:
* the baseline names the agent prompt version it was measured on - a PR that changes the prompt (its hash) must
  update the baseline in the same PR (ADR-0020 §3);
* a score below baseline minus the tolerance fails.
Baselines are measured, never tuned to one run (§14). The nightly real-provider runner (flag + budget cap, off until
the operator sets it) reuses these sets and checks; it is a later slice.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
SETS = ROOT / "sets"
BASELINES = ROOT / "baselines.json"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass
class SetScore:
    name: str
    passed: int = 0
    total: int = 0
    failures: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def record(self, case_id: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append({"id": case_id, "detail": detail})


def load(name: str) -> List[Dict[str, Any]]:
    path = SETS / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("//")]


def run_qa(cases: List[Dict[str, Any]]) -> SetScore:
    """Each case: tool results by call id, a candidate answer, and whether it may be shown."""
    from app.ai import agent, grounding
    out = SetScore("qa")
    for case in cases:
        by_call = {cid: grounding.numbers_in_values(data) for cid, data in case["tool_results"].items()}
        from_question = set(grounding.allowed_from_text(case["question"]))
        allowed = set(from_question).union(*by_call.values()) if by_call else set(from_question)
        parsed = agent.parse_answer(case["answer"] if isinstance(case["answer"], str) else json.dumps(case["answer"], ensure_ascii=False))
        problem, _ = agent._verify(parsed, allowed, by_call, from_question, case.get("lang", "en"))
        shown = problem is None
        if shown and case.get("lang") == "mr" and not DEVANAGARI.search(parsed.text):
            shown, problem = False, "a Marathi request answered without Devanagari"
        want = case["expect"] == "shown"
        detail = f"expected {case['expect']}, got {'shown' if shown else 'refused'}" + (f" ({problem})" if problem else "")
        if want and shown and case.get("contract"):
            if parsed.contract != case["contract"]:
                shown, detail = False, f"expected contract {case['contract']}, got {parsed.contract}"
        out.record(case["id"], shown == want, detail)
    return out


def run_tool_args(cases: List[Dict[str, Any]]) -> SetScore:
    """Each case: a tool, the arguments a model might send, and whether the input model accepts them."""
    from pydantic import ValidationError

    from app.ai import tools
    reg = tools.registry()
    out = SetScore("tool_args")
    for case in cases:
        tool = reg.get(case["tool"])
        if tool is None:
            out.record(case["id"], case["expect"] == "unknown", f"no tool {case['tool']}")
            continue
        try:
            tool.input_model.model_validate(case["args"])
            accepted = True
        except ValidationError:
            accepted = False
        out.record(case["id"], accepted == (case["expect"] == "accept"), f"expected {case['expect']}, accepted={accepted}")
    return out


async def run_injection(cases: List[Dict[str, Any]], session_factory: Callable[[], Any], user_id: int, deployment_id: int) -> SetScore:
    """Each case runs the real agent loop with a scripted model and a patched news feed; counts the proposals made."""
    from app.ai import agent
    from app.ai.providers import Completion, ToolCall, ToolTurn
    from app.ai.tools import ToolContext
    from app.db.models import User
    from app.news_feed import service as news

    class Scripted:
        name, model, supports_tools = "eval", "eval-scripted", True

        def __init__(self, steps):
            self.steps = list(steps)

        async def complete_tools(self, system, messages, tools_, *, max_tokens=4000):
            step = self.steps.pop(0) if self.steps else {"text": "{\"text\": \"Done.\", \"claims\": []}"}
            if "calls" in step:
                calls = [ToolCall(f"e{len(self.steps)}_{i}", c["tool"], _fill(c["args"], deployment_id)) for i, c in enumerate(step["calls"])]
                return ToolTurn("", calls, [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls], Completion(""), "tool_use")
            return ToolTurn(step["text"], [], [{"type": "text", "text": step["text"]}], Completion(step["text"]), "end_turn")

    original = news.items
    out = SetScore("injection")
    now = datetime.now(timezone.utc)
    try:
        for case in cases:
            headline = _fill(case.get("headline", ""), deployment_id)

            async def items(session, tenant_id, _h=headline, **kw):
                return [{"id": 1, "headline": _h, "source": "eval-feed", "published_at": now.isoformat(), "symbols": [],
                         "classification": {"severity": 3}, "verified": False}] if _h else []
            news.items = items
            async with session_factory() as session:
                user = await session.get(User, user_id)
                ans = await agent.run_agent(Scripted(case["script"]), ToolContext(session, user.tenant_id, user, now), _fill(case["question"], deployment_id))
            made = len(ans.proposals)
            out.record(case["id"], made == case["expect_proposals"], f"expected {case['expect_proposals']} proposal(s), got {made}")
            if made:
                await _withdraw(session_factory, [p["proposal_id"] for p in ans.proposals])     # keep cases independent (dedupe)
    finally:
        news.items = original
    return out


def _fill(value: Any, deployment_id: int) -> Any:
    """`{dep}` in a case stands for the eval deployment's id."""
    if isinstance(value, str):
        return value.replace("{dep}", str(deployment_id))
    if isinstance(value, dict):
        return {k: (deployment_id if v == "{dep}" else _fill(v, deployment_id)) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, deployment_id) for v in value]
    return value


async def _withdraw(session_factory: Callable[[], Any], ids: List[int]) -> None:
    from app.db.models import AiActionRecord
    async with session_factory() as session:
        for i in ids:
            row = await session.get(AiActionRecord, i)
            if row is not None and row.status == "PROPOSED":
                row.status, row.result = "REJECTED", "eval case withdrawn"
        await session.commit()


def run_static() -> Dict[str, SetScore]:
    return {"qa": run_qa(load("qa")), "tool_args": run_tool_args(load("tool_args"))}


def gate(scores: Dict[str, SetScore], baselines: Optional[Dict[str, Any]] = None) -> List[str]:
    """Problems that fail CI: a changed prompt without a new baseline, or a score below baseline - tolerance."""
    from app.ai import agent
    base = baselines if baselines is not None else json.loads(BASELINES.read_text(encoding="utf-8"))
    problems: List[str] = []
    if base.get("agent_prompt_version") != agent.PROMPT_VERSION:
        problems.append(f"agent prompt changed ({base.get('agent_prompt_version')} -> {agent.PROMPT_VERSION}): run the evals and update "
                        "evals/baselines.json in the same PR")
    tolerance = float(base.get("tolerance", 0.0))
    for name, score in scores.items():
        floor = base.get("sets", {}).get(name)
        if floor is None:
            problems.append(f"set {name} has no baseline")
        elif score.score < float(floor) - tolerance:
            problems.append(f"{name}: {score.score:.4f} < baseline {float(floor):.4f} - {tolerance:g}; failures: {score.failures[:5]}")
    return problems


def report(scores: Dict[str, SetScore]) -> Dict[str, Any]:
    from app.ai import agent
    return {"agent_prompt_version": agent.PROMPT_VERSION,
            "sets": {n: {"score": s.score, "passed": s.passed, "total": s.total, "failures": s.failures} for n, s in scores.items()}}


if __name__ == "__main__":                                                  # python -m evals.runner: the static sets
    print(json.dumps(report(run_static()), indent=2, ensure_ascii=False))
