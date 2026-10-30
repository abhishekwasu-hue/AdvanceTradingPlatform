"""Item 5 (owner's handover), job 6: the strategy builder. The trader's own idea, in his words, as a DRAFT paper strategy
made only of ATP's building blocks: what his words state is used as written, what they do not state is asked (never
filled in), parts ATP cannot build are named, and nothing is saved, deployed or started - the draft goes to the Trading bot
page, where the trader reviews, saves and starts it himself."""
import asyncio
import json

import pytest
from sqlalchemy import func, select

from app.ai import copilot_chat as chat
from app.ai import grounding
from app.ai import strategy_builder as sb
from app.ai.output_filter import findings
from app.db.models import AiActionRecord, AiStrategyDraftRecord, CustomStrategyRecord, OrderRecord, StrategyDeploymentRecord
from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy
from evals import runner
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import _flag, recorded_tools  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

FIX = runner.copilot_fixtures()
FULL = ("Turn my idea into a strategy: on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA and RSI 14 is above 50 "
        "on NIFTY. Stop 1.5 x ATR 14, objective 2R.")
OPEN_ENDED = "Make a strategy: go long when the fast EMA crosses the slow EMA"


def _run(coro):
    return asyncio.run(coro)


def _answer(question, fixture="default"):
    calls = []
    out = _run(chat.answer(question, runner.recorded_runner(FIX[fixture], calls)))
    return out, [c["tool"] for c in calls]


def _config(draft, values=None):
    """The config the Trading bot page's review card saves (frontend copilot/strategyDraft.ts): the draft with the trader's
    values; any empty value fails, like the card's disabled Save."""
    values = values or {}
    rules = json.loads(json.dumps(draft["rules"]))
    for key, v in values.items():
        if key.startswith("rules."):
            _, i, part, field = key.split(".")
            rules[int(i)]["condition"][part][field] = v
    for r in rules:
        for part in ("left", "right"):
            op = r["condition"][part]
            op.pop("of", None)
            op.pop("role", None)
            for k, v in list(op.items()):
                assert v is not None or k in ("timeframe", "multiplier", "period") and not _needed(op, k), (k, op)
                if v is None:
                    op.pop(k)
    s = {**draft["settings"], **{k: v for k, v in values.items() if not k.startswith("rules.")}}
    assert None not in (s["stop_loss_atr_mult"], s["atr_period"], s["target_rr"], s["min_rr"]), s
    side = values.get("side")
    cfg = {"name": draft["name"], "timeframe": values.get("timeframe") or draft["timeframe"],
           "long_conditions": [r["condition"] for r in rules if (r["side"] or side) in ("long", "both")],
           "short_conditions": [r["condition"] for r in rules if (r["side"] or side) in ("short", "both")],
           "stop_loss_atr_mult": s["stop_loss_atr_mult"], "atr_period": s["atr_period"], "target_rr": s["target_rr"], "min_rr": s["min_rr"]}
    return CustomStrategyConfig.model_validate(cfg)


def _needed(op, key):
    ind = op.get("indicator")
    return (key == "period" and ind in sb.NEEDS_PERIOD) or (key == "multiplier" and ind in sb.NEEDS_MULTIPLIER) or (
        key == "value" and op.get("type") == "value")


# --- the builder -------------------------------------------------------------------------------------------------------

def test_the_job_is_built_and_routes():
    assert "strategy" in chat.BUILT and chat.BY_ID["strategy"].number == 6
    for q in (FULL, OPEN_ENDED, "Turn my idea into a strategy and test it", "Buy when the 20 EMA crosses above the 50 EMA",
              "sell when RSI 14 crosses below 40, 5 min chart"):
        assert chat.route(q) == "strategy", q
    assert chat.route("What is a backtest?") == "teacher"           # a lesson, not an idea to build
    assert chat.route("Buy RELIANCE now") == "refuse"                # an order, not a rule


def test_a_complete_idea_uses_every_value_as_written_and_saves_as_a_valid_strategy():
    out, called = _answer(FULL)
    assert called == ["get_risk_limits"] and out.job == "strategy" and out.refused is None
    d = out.draft
    assert d["mode"] == "PAPER" and d["complete"] is True and d["asks"] == [] and d["sides"] == ["long"] and d["timeframe"] == "15min"
    assert d["symbol"] == "NIFTY" and d["min_rr_source"] == "your risk limits"
    assert d["settings"] == {"stop_loss_atr_mult": 1.5, "atr_period": 14, "target_rr": [2.0, 2.0], "min_rr": 1.5}
    assert [r["condition"]["operator"] for r in d["rules"]] == ["CROSSES_ABOVE", "GT"]
    cfg = _config(d)
    DeclarativeStrategy("custom:job6", cfg)                          # the engine accepts it as it stands
    assert out.action == {"label": "Review it on the Trading bot page", "to": "/deployments"}
    assert any(u.startswith("I don't know how this idea did in the past") for u in out.unknowns)


def test_unstated_numbers_are_asked_never_invented():
    out, _ = _answer(OPEN_ENDED)
    d = out.draft
    assert d["complete"] is False and d["timeframe"] is None
    assert d["settings"] == {"stop_loss_atr_mult": None, "atr_period": None, "target_rr": None, "min_rr": 1.5}
    ops = d["rules"][0]["condition"]
    assert ops["left"]["period"] is None and ops["right"]["period"] is None
    keys = [a["key"] for a in d["asks"]]
    assert keys == ["timeframe", "rules.0.left.period", "rules.0.right.period", "stop_loss_atr_mult", "atr_period", "target_rr"]
    with pytest.raises(AssertionError):
        _config(d)                                                   # nothing saves until the trader decides
    cfg = _config(d, {"timeframe": "5min", "rules.0.left.period": 9, "rules.0.right.period": 21, "stop_loss_atr_mult": 1.0,
                      "atr_period": 14, "target_rr": [2.0, 3.0]})
    DeclarativeStrategy("custom:job6", cfg)
    text = "\n".join(out.lines())
    assert "Values you decide" in [s["title"] for s in out.sections] and "x ATR (" not in text and "Profit objective:" not in text


def test_without_his_own_risk_limits_the_minimum_reward_to_risk_is_asked():
    out, _ = _answer("Turn my idea into a strategy: go long when the 20 EMA crosses above the 50 EMA on the 5 min chart, "
                     "stop 1 x ATR 14, objective 2R", "no_data")
    assert out.draft["settings"]["min_rr"] is None and out.draft["min_rr_source"] is None
    assert [a["key"] for a in out.draft["asks"]] == ["min_rr"]


def test_his_own_minimum_reward_to_risk_wins_over_the_risk_limits():
    d = sb.build("long when price is above VWAP, minimum rr 2.5", None, 1.5).as_dict()
    assert d["settings"]["min_rr"] == 2.5 and d["min_rr_source"] == "your words"


@pytest.mark.parametrize("idea,expected", [
    ("go long when RSI divergence forms at a demand zone", ["RSI divergence. The Screener can look for it today",
                                                            "price-action areas and structure"]),
    ("go long when MACD crosses its signal line", ["MACD. The Screener can look for it today"]),
    ("buy on a bullish engulfing candle", ["candle patterns"]),
    ("long when price is above VWAP, stop at the swing low", ["a stop at a chart point"]),
    ("long when price is above VWAP, target 100 points", ["an objective in points"]),
    ("long when price is above VWAP after 10 am", ["a time-of-day or day-of-week rule"]),
    ("long when price is above VWAP on the daily chart", ["daily or weekly candles"]),
    ("long when price is above VWAP with 2x average volume", ["volume as a multiple of its average"]),
    ("long when price is above VWAP on the 10 min chart", ["10-minute candles"]),
    ("long when price is above VWAP. exit when RSI crosses 50", ["an exit on a condition"]),
    ("buy RELIANCE when it is above 2900", ["a fixed price level"]),
    ("long when stochastic is oversold", ["Stochastic"]),
])
def test_parts_atp_cannot_build_are_named_not_guessed(idea, expected):
    d = sb.build(idea, None, None)
    for want in expected:
        assert any(x.startswith(want) for x in d.unsupported), (want, d.unsupported)
    for r in d.rules:                                                # what was built is only the buildable part
        assert r.left.get("indicator") not in ("MACD",) and r.right.get("type") in ("indicator", "value")


def test_words_it_cannot_read_are_quoted_back():
    d = sb.build("go long when the moon is full and price is above VWAP", None, None)
    assert len(d.unread) == 1 and d.unread[0].endswith("the moon is full") and len(d.rules) == 1


def test_the_building_blocks_cover_the_strategy_builders_vocabulary():
    cases = {
        "price crosses above the previous day high": ("CLOSE", "CROSSES_ABOVE", "PDH"),
        "close is below the lower bollinger band 20 2": ("CLOSE", "LT", "BB_LOWER"),
        "+di above -di": ("PLUS_DI", "GT", "MINUS_DI"),
        "adx above 25": ("ADX", "GT", None),
        "volume above its average": ("VOLUME", "GT", "VOLUME_SMA"),
        "price reclaims vwap": ("CLOSE", "CROSSES_ABOVE", "VWAP"),
        "price is above the day's open": ("CLOSE", "GT", "DAY_OPEN"),
        "the 9 ema crosses below the 21 ema": ("EMA", "CROSSES_BELOW", "EMA"),
    }
    for words, (left, op, right) in cases.items():
        rules = sb.parse_clause(words, "long")
        assert rules and rules[0].left["indicator"] == left and rules[0].operator == op, words
        assert rules[0].right.get("indicator") == right, words


def test_opening_range_minutes_and_a_higher_timeframe_part():
    d = sb.build("opening range breakout, first 15 minutes, long only", None, None).as_dict()
    assert d["rules"][0]["condition"]["right"] == {"type": "indicator", "indicator": "OR_HIGH", "period": 15, "multiplier": None, "timeframe": None}
    d = sb.build("go long when price is above the 200 sma on the hourly chart, 5 min entries", None, None).as_dict()
    assert d["timeframe"] == "5min" and d["rules"][0]["condition"]["right"]["timeframe"] == "60min"


def test_both_sides_and_a_side_left_open():
    d = sb.build("buy when price is above VWAP, sell when price is below VWAP", None, None).as_dict()
    assert d["sides"] == ["long", "short"] and [r["side"] for r in d["rules"]] == ["long", "short"]
    d = sb.build("when price is above VWAP and RSI 14 is above 60", None, None).as_dict()
    assert d["sides"] == [] and d["asks"][0]["key"] == "side"


def test_the_starter_asks_for_the_idea_and_gives_an_example():
    out, called = _answer("Turn my idea into a strategy and test it")
    assert called == ["get_risk_limits"] and out.draft is None and out.action is None
    assert out.summary[0].startswith("Tell me your idea") and out.starters == [sb.EXAMPLE]
    example, _ = _answer(sb.EXAMPLE)
    assert example.draft["complete"] is True and example.draft["asks"] == []       # the example itself states every value


def test_nothing_buildable_hands_over_no_draft():
    out, _ = _answer("Turn my idea into a strategy: long when RSI divergence forms at a demand zone")
    assert out.draft is None and out.action is None and out.summary[0].startswith("I could not build any part")


def test_every_number_comes_from_his_words_or_his_settings_and_no_advice_words():
    for q in (FULL, OPEN_ENDED, sb.EXAMPLE, "Buy when price is above VWAP and supertrend turns green on 5 min, sell when price is below vwap "
              "and supertrend turns red, 2 lots BANKNIFTY", "Turn my idea into a strategy: opening range breakout on NIFTY, first 15 minutes, "
              "with volume, stop 1 atr"):
        out, _ = _answer(q)
        allowed = grounding.allowed_from_text("\n".join([*out.trusted_text, chat.FIXED_TEXT, q]))
        for line in out.lines():
            assert grounding.check_numbers(line, allowed)[0], line
        assert "dropped_lines" not in out.diagnostics and not findings("\n".join(out.lines())), q
        text = json.dumps(out.as_dict())
        for word in ("CustomStrategyConfig", "CROSSES_ABOVE\"", "Traceback", "flag"):
            assert word not in "\n".join(out.lines()), word
        assert "strategy_blocks" not in out.as_dict()["tool_calls"] and "get_risk_limits" in text


def test_the_ai_wording_only_describes():
    out, _ = _answer(FULL)
    good = runner.ScriptedWriter("Your draft goes long on the 15-minute chart when the 20 EMA crosses above the 50 EMA and RSI (14) is "
                                 "above 50, with your stop at 1.5 x ATR (14) and your objective at 2R.")
    assert _run(chat.narrate(good, out, FULL))[0]
    for bad in ("You should start this strategy now; it will make money.", "Your draft uses a 3 x ATR stop and a 4R objective.",
                "Consider adding a trailing stop to this strategy."):
        assert _run(chat.narrate(runner.ScriptedWriter(bad), out, FULL))[0] is None, bad


def test_the_new_refusals_are_plain_and_call_no_tool():
    for kind, q in (("settings", "Pick a stop-loss for me for this strategy"), ("promise", "Will this strategy make money?"),
                    ("copy", "Copy the strategy of that Telegram channel"), ("order", "Build the strategy and start the trading bot for me")):
        out, called = _answer(q)
        assert out.refused == kind and called == [] and out.summary == [chat.REFUSALS[kind]] and not findings(chat.REFUSALS[kind])
    assert "your own settings" in chat.REFUSALS["settings"] and "can't promise" in chat.REFUSALS["promise"]
    assert "your own idea" in chat.REFUSALS["copy"]
    assert chat.refusal_kind("Copy my idea into a strategy: long when price is above VWAP") is None
    assert chat.refusal_kind("Teach me what a stop-loss is") is None


# --- the route ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def v2_on():
    _run(_flag("ui_v2", True))
    yield
    _run(_flag("ui_v2", False))


def test_the_route_returns_the_draft_and_saves_starts_or_places_nothing(v2_on, recorded_tools):  # noqa: F811 - the fixture
    headers, me = _owner("cop6-route@example.com")

    async def counts():
        async with _session_factory() as session:
            t = me["tenant_id"]
            return tuple([await session.scalar(select(func.count()).select_from(m).where(m.tenant_id == t))
                          for m in (CustomStrategyRecord, StrategyDeploymentRecord, OrderRecord, AiActionRecord, AiStrategyDraftRecord)])
    before = _run(counts())
    body = client.post("/api/ai/chat", headers=headers, json={"message": FULL}).json()
    assert body["job"] == "strategy" and body["job_number"] == 6 and body["draft"]["mode"] == "PAPER" and body["draft"]["complete"] is True
    assert body["tool_calls"] == ["get_risk_limits"] and body["action"]["to"] == "/deployments"
    home = client.get("/api/ai/chat/home", headers=headers).json()
    job = next(j for j in home["jobs"] if j["id"] == "strategy")
    assert job["built"] is True and job["starter"] == "Turn my idea into a strategy and test it"
    refused = client.post("/api/ai/chat", headers=headers, json={"message": "Save it for me and start it"}).json()
    assert refused["refused"] == "order" and refused["draft"] is None
    assert _run(counts()) == before                                  # nothing saved, deployed, ordered or proposed


def test_the_route_is_behind_ui_v2_and_the_copilot_switch():
    headers, _ = _owner("cop6-flags@example.com")
    assert client.post("/api/ai/chat", headers=headers, json={"message": FULL}).status_code == 404
    _run(_flag("ui_v2", True))
    _run(_flag("ai_copilot", False))
    try:
        assert client.post("/api/ai/chat", headers=headers, json={"message": FULL}).status_code == 503
    finally:
        _run(_flag("ai_copilot", True))
        _run(_flag("ui_v2", False))


def test_a_cross_without_a_direction_or_a_side_asks_for_both():
    d = sb.build("when the 20 ema crosses the 50 ema", None, None).as_dict()
    assert d["rules"][0]["condition"]["operator"] is None and d["rules"][0]["side"] is None
    assert {"side", "rules.0.operator"} <= {a["key"] for a in d["asks"]}
    assert "yours to decide" in d["rules"][0]["text"]
