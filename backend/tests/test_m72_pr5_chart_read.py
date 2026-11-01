"""M72 PR-5 (CP-4, finding F9, M72Q-3 default): the Copilot's chart reading.

* `get_engine_read` is offered only with the flag `trade_engine` ON and the private engine installed; it reads only the
  engine runner's own user and his listed symbols; everyone else's chart questions use ATP's zone layer.
* Only generic public words reach the model and the answer: areas as price ranges, side, a public state, the higher
  timeframe's side and swing labels. Never a model id, an evidence token, the internal invalidation reference, an area
  id, the entry zone's figures, a package state token or the engine settings - checked on every message sent to the
  (scripted) model and on the answer, with a synthetic test-double engine.
* A planner answer that read a chart is drawn by code (headline row + structure card) and passes one wording guard: no
  buy / sell / target / stop / invalidation words.
No test calls a real provider and nothing here uses the private package (a made-up engine only).
"""
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.ai import agent, chart_answer, chat_routes, tools
from app.ai.tools import ToolContext, ToolResult, engine_read, run_tool
from app.db.models import User
from app.engine_provider import contract as c
from app.engine_provider import loader
from app.signals.language import SignalStructure
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1a_safety import _org
from tests.test_m72_pr1c_planner import Scripted, _flag, _run

NOW = datetime.now(UTC)
AS_OF = datetime(2026, 10, 20, 15, 30, tzinfo=UTC)
# The made-up engine's private values - none of them may ever leave the tool
SECRETS = ("model-secret-7", "inst-secret-9", "area-secret-1", "evidence-secret-a", "state-secret-x", "98.765", "99.21", "99.79",
           "engine-setting-secret", "problem-secret", "0.0.0-secret")


class FakeEngine:
    """A synthetic test double: two areas (one with a public state, one with a package-only state), two swings, the higher
    side and one signal carrying private references. Nothing here is a reading rule."""
    name, version, interface, timeframes = "test-double-engine", "0.0.0-secret", c.INTERFACE_VERSION, ("15m",)

    def __init__(self, problems=()):
        self.problems = tuple(problems)
        self.requests: list[c.EngineRequest] = []

    def read(self, request: c.EngineRequest) -> c.EngineResult:
        self.requests.append(request)
        at = request.as_of
        areas = (c.Area("area-secret-1", "buyer", "fresh", c.PriceRange(99.0, 100.0), "15m", at),
                 c.Area("area-secret-2", "seller", "state-secret-x", c.PriceRange(104.0, 105.0), "15m", at))
        swings = (c.Swing("HL", at - timedelta(hours=2), 99.5, "15m"), c.Swing("HH", at - timedelta(hours=1), 103.9, "15m"))
        signal = c.Signal(model_id="model-secret-7", timeframe="15m", bar_end=at, side="buyer", entry_zone=c.PriceRange(99.21, 99.79),
                          card=SignalStructure(trend="up", steps=("pullback_reached_area",)), area_id="area-secret-1", invalidation=98.765,
                          evidence=(c.Evidence("evidence-secret-a", True),), instance_id="inst-secret-9")
        return c.EngineResult(interface=c.INTERFACE_VERSION, version=self.version, areas=areas, swings=swings,
                              higher_side=c.HigherTimeframeSide("up", "1d"), signals=(signal,), data_problems=self.problems)


def _request():
    bar = c.Bar(AS_OF - timedelta(minutes=15), AS_OF, 101.0, 102.0, 100.5, 101.5, 10.0)
    cal = c.MarketCalendar("Asia/Kolkata", datetime(2026, 1, 1).time(), datetime(2026, 1, 1, 15, 30).time())
    return c.EngineRequest(symbol="NSE:EEESTOCK", as_of=AS_OF, frames={"15m": c.Frame("15m", (bar,))}, calendar=cal,
                           settings={"engine-setting-secret": 1})


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    loader.set_for_tests(fake)
    built = {"complete": True}

    async def fake_build(ctx, cfg, watch, eng):
        return _request(), built["complete"], 101.5
    monkeypatch.setattr(engine_read, "build_request", fake_build)
    yield fake, built
    loader.reset_for_tests()


def _runner(tenant_id, user_id, symbols=("EEESTOCK",)):
    from app.workers import engine_runner as R

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            await R.save_settings(session, tenant_id, {"user_id": user_id, "signal_timeframe": "15m",
                                                       "symbols": [{"key": f"NSE:{s}", "name": s} for s in symbols],
                                                       "engine_settings": {"engine-setting-secret": 1}}, user)
    _run(go())


@pytest.fixture
def owner():
    tenant_id, a, b = _org(f"pr5-{datetime.now().timestamp()}")
    _run(_flag("trade_engine", False))          # off for everyone; `_on` lists this organisation
    _runner(tenant_id, a)
    yield tenant_id, a, b
    _run(_flag("trade_engine", False))
    _run(_flag("copilot_agent", False))


def _on(name, tenant_id):
    _run(_flag(name, False, [tenant_id]))       # global off, ON for this organisation only


def _ctx_call(user_id, name, args):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await run_tool(name, args, ToolContext(session, user.tenant_id, user, NOW))
    return _run(go())


def _offered(user_id):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await tools.available(ToolContext(session, user.tenant_id, user, NOW), tools.names("read", agent=True))
    return _run(go())


def _clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, secret


# --- the gate -------------------------------------------------------------------------------------------------------------------

def test_offered_only_with_the_flag_and_the_installed_engine(owner, engine):
    tenant_id, a, _b = owner
    tool = tools.registry()[engine_read.NAME]
    assert tool.kind == "read" and tool.flag == "trade_engine" and tool.agent and tool.schema()["additionalProperties"] is False
    assert engine_read.NAME not in _offered(a)                               # the flag is off for this organisation
    _on("trade_engine", tenant_id)
    assert engine_read.NAME in _offered(a)
    loader.set_for_tests(None)                                               # not installed: never offered, never run
    assert engine_read.NAME not in _offered(a)
    assert not _ctx_call(a, engine_read.NAME, {"symbol": "EEESTOCK"}).ok
    from app.platform import controls
    assert "trade_engine" in controls.DEFAULT_OFF_FLAGS


def test_only_the_runner_user_and_his_listed_symbols(owner, engine):
    tenant_id, a, b = owner
    _on("trade_engine", tenant_id)
    member = b if isinstance(b, int) else b["id"]
    res = _ctx_call(member, engine_read.NAME, {"symbol": "EEESTOCK"})
    assert not res.ok and "not set up for this user" in res.error           # a teammate never reads the owner's engine
    res = _ctx_call(a, engine_read.NAME, {"symbol": "FFFSTOCK"})
    assert not res.ok and "EEESTOCK" in res.error and "get_market_zones" in res.error


# --- public words only ----------------------------------------------------------------------------------------------------------

def test_the_result_carries_public_words_only(owner, engine):
    tenant_id, a, _b = owner
    _on("trade_engine", tenant_id)
    res = _ctx_call(a, engine_read.NAME, {"symbol": "EEESTOCK"})
    assert res.ok, res.error
    _clean(res.text())
    data = res.data
    assert [(x["side"], x["low"], x["high"], x["state"], x["where"]) for x in data["areas"]] == [
        ("buyer", 99.0, 100.0, "fresh", "below"), ("seller", 104.0, 105.0, None, "above")]     # a package-only state is dropped
    assert data["areas"][0]["distance_points"] == 1.5
    assert data["structure"] == {"higher_timeframe": "1d", "higher_side": "up", "swings": [{"timeframe": "15m", "latest": ["HL", "HH"]}]}
    assert data["signal_on_last_bar"] == {"side": "buyer", "timeframe": "15m", "area_low": 99.0, "area_high": 100.0}
    assert not any(k in json.dumps(data) for k in engine_read.PRIVATE_FIELDS)
    assert "103.9" not in res.text() and "99.5" not in res.text()           # no swing price


def test_a_data_problem_is_no_reading(owner, engine):
    tenant_id, a, _b = owner
    _on("trade_engine", tenant_id)
    fake, built = engine
    built["complete"] = False
    res = _ctx_call(a, engine_read.NAME, {"symbol": "EEESTOCK"})
    assert not res.ok and "not complete" in res.error
    built["complete"] = True
    fake.problems = ("problem-secret",)
    res = _ctx_call(a, engine_read.NAME, {"symbol": "EEESTOCK"})
    assert not res.ok and "not complete" in res.error and "problem-secret" not in res.error


# --- the planner: nothing private in a prompt or an answer ---------------------------------------------------------------------

class Recording(Scripted):
    def __init__(self, steps):
        super().__init__(steps)
        self.sent: list[str] = []

    async def complete_tools(self, system, messages, tools_, *, max_tokens=4000):
        self.sent.append(system + json.dumps(messages, default=str) + json.dumps(tools_, default=str))
        return await super().complete_tools(system, messages, tools_, max_tokens=max_tokens)


def _final(text, headline="", cards=()):
    return {"text": json.dumps({"headline": headline, "text": text, "claims": [], "cards": list(cards), "chips": ["chart"], "disclaimers": []})}


def _agent(user_id, provider, question):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await agent.run_agent(provider, ToolContext(session, user.tenant_id, user, NOW), question)
    return _run(go())


def test_the_planner_sends_and_shows_no_private_engine_content(owner, engine):
    tenant_id, a, _b = owner
    _on("trade_engine", tenant_id)
    _on("copilot_agent", tenant_id)
    model = Recording([{"calls": [{"tool": "get_engine_read", "args": {"symbol": "EEESTOCK"}}]},
                       _final("The buyer area of interest 99 - 100 is fresh, 1.5 points below the last close 101.5.",
                              "EEESTOCK: buyer area 99 - 100, 1.5 below", [{"call": "c1_0", "title": "Engine areas"}])])
    ans = _agent(a, model, "Read the EEESTOCK chart")
    assert ans.source == "ai", ans.note
    assert '"name": "get_engine_read"' in model.sent[0]                  # offered here (flag on, engine installed)
    for sent in model.sent:
        _clean(sent)
    shown = json.dumps({"text": ans.text, "cards": ans.cards, "headline": ans.chart_headline, "chips": ans.chips}, default=str)
    _clean(shown)
    assert ans.chart_headline["area"] == {"side": "demand", "from": 99.0, "to": 100.0, "where": "below", "distance": 1.5,
                                          "distance_pct": 1.48, "timeframe": "15m", "state": "fresh"}
    assert ans.chart_headline["trend"] == "up" and ans.structure_card is None
    assert all(c["kind"] != "chart" for c in ans.chips)                    # the headline's own "Open chart" opens it
    rows = ans.cards[0]["table"]["rows"]
    assert {(r["side"], r["low"], r["high"]) for r in rows} == {("buyer", 99.0, 100.0), ("seller", 104.0, 105.0)}


def test_a_chart_answer_with_stop_or_invalidation_words_is_not_shown(owner, engine):
    tenant_id, a, _b = owner
    _on("trade_engine", tenant_id)
    bad = _final("The buyer area 99 - 100 holds; the stop sits below it.")
    worse = _final("Below 99 the reading is invalidated.")
    ans = _agent(a, Recording([{"calls": [{"tool": "get_engine_read", "args": {"symbol": "EEESTOCK"}}]}, bad, worse]), "Read the EEESTOCK chart")
    assert ans.source == "summary" and ans.stopped == "not_grounded"
    assert chart_answer.wording_problem("The seller area of interest 104 - 105 is above the price.") is None
    assert "stop" in chart_answer.wording_problem("a stop below 99")
    assert "invalidation" in chart_answer.wording_problem("the invalidation level")
    assert "target" in chart_answer.wording_problem("the target is the seller area")


# --- everyone else: ATP's zone layer, drawn by code ---------------------------------------------------------------------------

STRUCTURE = {"symbol": "EEESTOCK", "instrument": "NSE:EEESTOCK", "tf": "1d", "as_of": "2026-10-20", "bars": 200, "last_close": 101.5,
             "official_close": {"close": 101.5, "date": "2026-10-20"}, "reason": None,
             "chart_state": {"direction": "up", "protected": 98.0, "last_event": "BOS", "last_event_price": 103.0},
             "timeframes": [{"tf": "1d", "direction": "up", "protected": 98.0, "bars": 200, "needed": 30, "short": False,
                             "last_event": "BOS", "last_event_price": 103.0}], "levels": []}
ZONES = {"symbol": "EEESTOCK", "instrument": "NSE:EEESTOCK", "as_of": "2026-10-20", "price": 101.5,
         "bands": [{"role": "demand", "low": 99.0, "high": 100.0, "state": "fresh", "touches": 1, "timeframe": "1d"}]}


def test_without_the_engine_the_zone_layer_answers_with_its_card(owner, monkeypatch):
    tenant_id, a, _b = owner
    _on("trade_engine", tenant_id)
    loader.set_for_tests(None)
    assert engine_read.NAME not in _offered(a)
    reg = tools.registry()

    async def structure(ctx, args):
        return ToolResult(True, dict(STRUCTURE), "2026-10-20", "price_action_layer:lake")

    async def zones(ctx, args):
        return ToolResult(True, dict(ZONES), "2026-10-20", "market_zones")
    monkeypatch.setitem(tools._REGISTRY, "get_chart_structure", reg["get_chart_structure"].__class__(**{**reg["get_chart_structure"].__dict__, "fn": structure, "flag": None}))
    monkeypatch.setitem(tools._REGISTRY, "get_market_zones", reg["get_market_zones"].__class__(**{**reg["get_market_zones"].__dict__, "fn": zones, "flag": None}))
    model = Recording([{"calls": [{"tool": "get_chart_structure", "args": {"symbol": "EEESTOCK", "timeframe": "1d"}},
                                  {"tool": "get_market_zones", "args": {"symbol": "EEESTOCK"}}]},
                       _final("The daily structure is up; the buyer area 99 - 100 is 1.5 points below 101.5.", "EEESTOCK daily: up, buyer area 99 - 100")])
    ans = _agent(a, model, "Read the EEESTOCK chart")
    loader.reset_for_tests()
    assert ans.source == "ai", ans.note
    assert '"name": "get_engine_read"' not in model.sent[0]           # not among the tools offered
    assert ans.chart_headline["area"]["from"] == 99.0 and ans.chart_headline["trend"] == "up"
    card = ans.structure_card
    assert card["areas_below"][0]["from"] == 99.0 and next(t for t in card["timeframes"] if t["tf"] == "1d")["protected"] == 98.0


def test_the_chat_shape_carries_the_code_drawn_chart():
    import inspect
    src = inspect.getsource(chat_routes._agent_answer)
    assert "headline=ans.chart_headline" in src and "structure_card=ans.structure_card" in src
