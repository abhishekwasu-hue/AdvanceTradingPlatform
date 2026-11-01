"""M72 PR-6 (CP-5): plain English -> an editable draft. A screen the planner builds is a draft (nothing saved or run);
every value the trader did not state stays an empty $parameter (M68) - a number he never gave is refused before the
tool runs; a proposed alert is a draft that waits for his approval. No test calls a real provider (scripted models)."""
import json
from datetime import UTC, datetime

import pytest

from app.ai import drafts, tools
from app.ai.tools import ToolResult
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1a_safety import _org
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_m72_pr5_chart_read import Recording, _agent, _final

NOW = datetime.now(UTC)


def _allowed(question):
    from app.ai import grounding
    return set(grounding.allowed_from_text(question))


def test_numbers_and_parameters_of_a_screen():
    text = 'close > SMA(close, 50) AND RSI(14) > $rsi_level AND sector == "IT 2" AND ema20 > 0.5'
    assert drafts.numbers_in_screen(text) == [50.0, 14.0, 0.5]
    assert drafts.params_in_screen(text) == ["rsi_level"]


def test_an_unstated_value_is_refused_a_stated_one_or_a_parameter_runs():
    q = "Daily screen: RSI(14) above 60"
    assert drafts.number_problem("build_screen", {"text": "RSI(14) > 60"}, _allowed(q)) is None
    problem = drafts.number_problem("build_screen", {"text": "RSI(14) > 60 AND close > SMA(close, 50)"}, _allowed(q))
    assert problem and problem.startswith("50 is a value the user did not state")
    assert drafts.number_problem("build_screen", {"text": "RSI(14) > 60 AND close > SMA(close, $period)"}, _allowed(q)) is None
    assert drafts.number_problem("build_screen", {"text": "RSI(14) > $x", "params": {"x": 70}}, _allowed(q)) is not None
    assert drafts.number_problem("run_screen", {"text": "close > SMA(close, 50)", "symbols": ["X"]}, _allowed(q)) is not None
    assert drafts.number_problem("get_quote", {"symbol": "X", "n": 5}, set()) is None       # other tools: not a draft
    assert drafts.number_problem("build_screen", {"text": "RSI(14) > 60 AND change > 0"}, _allowed(q)) is None   # a zero line


def test_drafts_come_from_the_calls_own_results():
    calls = [{"id": "c1", "name": "build_screen", "ok": True, "arguments": {"text": "RSI(14) > 60 AND close > SMA(close, $period)", "base_tf": "1d"}},
             {"id": "c2", "name": "propose_alert", "ok": True, "arguments": {}},
             {"id": "c3", "name": "build_screen", "ok": False, "arguments": {"text": "x"}}]
    results = {"c1": {"ok": False, "text": "RSI(14) > 60 AND close > SMA(close, $period)", "base_tf": "1d", "stages": [{"title": "RSI"}],
                      "problems": [{"kind": "parameter", "message": "parameter $period has no value"}, {"kind": "unit", "message": "Unit mismatch"}]},
               "c2": {"created": True, "proposal_id": 41, "action": "CREATE_ALERT", "status": "PROPOSED"}}
    out = drafts.build_drafts(calls, results)
    assert out[0] == {"kind": "alert", "status": "proposed", "proposal_id": 41}
    assert out[1] == {"kind": "screen", "text": "RSI(14) > 60 AND close > SMA(close, $period)", "base_tf": "1d",
                      "params": [{"name": "period", "value": None}], "stages": ["RSI"], "problems": ["Unit mismatch"], "ok": False}
    assert drafts.build_drafts([{**calls[1]}], {"c2": {"created": False}}) == []          # a refused duplicate is no draft


@pytest.fixture
def screens(monkeypatch):
    tenant_id, a, _b = _org(f"pr6-{datetime.now().timestamp()}")
    seen: list[dict] = []
    reg = tools.registry()

    async def build(ctx, args):
        seen.append(args.model_dump())
        return ToolResult(True, {"ok": "$" not in args.text, "text": args.text, "base_tf": args.base_tf, "stages": [{"title": "Momentum"}],
                                 "problems": [{"kind": "parameter", "message": "no value"}] if "$" in args.text else []}, "now", "screenql_validator")
    monkeypatch.setitem(tools._REGISTRY, "build_screen", reg["build_screen"].__class__(**{**reg["build_screen"].__dict__, "fn": build, "flag": None}))
    yield a, seen
    _run(_flag("copilot_agent", False))


def test_the_planner_leaves_an_unstated_value_empty(screens):
    a, seen = screens
    model = Recording([{"calls": [{"tool": "build_screen", "args": {"text": "RSI(14) > 60 AND close > SMA(close, 50)", "base_tf": "1d"}}]},
                       {"calls": [{"tool": "build_screen", "args": {"text": "RSI(14) > 60 AND close > SMA(close, $period)", "base_tf": "1d"}}]},
                       _final("A draft screen: RSI(14) above 60 and the close above its average; you decide the average's period.",
                              "Screen draft: RSI(14) above 60")])
    ans = _agent(a, model, "Make a daily screen: RSI(14) above 60 and the close above its moving average")
    assert ans.source == "ai", ans.note
    assert [s["text"] for s in seen] == ["RSI(14) > 60 AND close > SMA(close, $period)"]       # the unstated 50 never ran
    assert "50 is a value the user did not state" in model.sent[1]
    assert ans.drafts == [{"kind": "screen", "text": "RSI(14) > 60 AND close > SMA(close, $period)", "base_tf": "1d",
                           "params": [{"name": "period", "value": None}], "stages": ["Momentum"], "problems": [], "ok": False}]
    assert json.dumps(ans.drafts).count("50") == 0


def test_drafts_reach_the_chat_shape():
    import inspect

    from app.ai import chat_routes
    assert "drafts=ans.drafts" in inspect.getsource(chat_routes._agent_answer)
    assert tools.registry()["build_screen"].kind == "read" and tools.registry()["propose_alert"].kind == "proposal"
