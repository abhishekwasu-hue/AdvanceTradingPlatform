"""OC3 (owner's order, PART C2): Ask AI on the Option chain - a safe analysis spec.

- the spec validator refuses anything outside the whitelist (unknown columns, functions or keys, too deep or too large,
  a window over 20, injection-looking strings, advice words in labels);
- the evaluator is exact on hand-made fixtures (the owner's two examples, division by zero, history refs, window
  functions, sort / limit, ATM ties) and every number comes from the data;
- a saved analysis (the `oc_analyses` prefs namespace) re-evaluates on a new snapshot with no AI;
- the AI path with a fake model: a spec, an unsupported request, an invalid answer repaired once, an answer that stays
  invalid, quota and cost cap exhaustion (plain messages), the rate limit, BYOK keys never used, the disclaimer, no
  secret or question text in the logs; the ui_v2 flag-off 404 and per-user storage.
"""
import asyncio
import json
import logging
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.ai import output_filter
from app.brokers.models import OptionChain, OptionChainRow
from app.me import prefs as P
from app.option_chain import ask_ai, ask_routes
from app.option_chain import ask_spec as A
from app.option_chain.ask_spec import AnalysisSpec, HistoryPoint
from app.platform.controls import DEFAULT_OFF_FLAGS
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)          # 11:30 IST
STRIKES = [21900.0, 21950.0, 22000.0, 22050.0, 22100.0, 22150.0, 22200.0]
CE_OI = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0]
PE_OI = [900.0, 700.0, 650.0, 300.0, 100.0, 50.0, 0.0]
OPEN_CE = [100.0, 150.0, 300.0, 400.0, 400.0, 500.0, 700.0]
OPEN_PE = [900.0, 700.0, 500.0, 300.0, 100.0, 40.0, 0.0]


def _run(coro):
    return asyncio.run(coro)


def _rows(ce=CE_OI, pe=PE_OI, chg=None):
    out = []
    for i, s in enumerate(STRIKES):
        row = {"strike": s, "ce_oi": ce[i], "pe_oi": pe[i], "ce_ltp": 10.0 + i, "pe_ltp": 20.0 - i}
        if chg is not None:
            row["ce_oi_chg"], row["pe_oi_chg"] = chg[0][i], chg[1][i]
        out.append(row)
    return out


def _open_point(at=NOW - timedelta(hours=2)):
    return HistoryPoint(at=at, rows={s: {"ce_oi": OPEN_CE[i], "pe_oi": OPEN_PE[i]} for i, s in enumerate(STRIKES)})


def _spec(**kw):
    base = {"title": "Test", "window": 2, "columns": [{"label": "CE OI", "expr": "ce_oi"}]}
    base.update(kw)
    return AnalysisSpec.model_validate(base)


def _refused(**kw):
    with pytest.raises(ValidationError):
        _spec(**kw)


OWNER_ONE = {"title": "CE-PE OI near ATM", "window": 2,
             "columns": [{"label": "CE OI", "expr": "ce_oi"}, {"label": "PE OI", "expr": "pe_oi"}, {"label": "CE-PE OI", "expr": "ce_oi - pe_oi"}],
             "chart": {"type": "bar", "x": "strike", "y": ["CE-PE OI"]}}
OWNER_TWO = {"title": "OI up more than 20% today", "window": 20,
             "columns": [{"label": "CE OI chg %", "expr": "pct_change(ce_oi, ce_oi@open)"}, {"label": "PE OI chg %", "expr": "pct_change(pe_oi, pe_oi@open)"}],
             "filters": ["pct_change(ce_oi, ce_oi@open) > 20 or pct_change(pe_oi, pe_oi@open) > 20"]}


# --- the validator -----------------------------------------------------------------------------------------------------

def test_the_owner_examples_are_valid_specs():
    for spec in (OWNER_ONE, OWNER_TWO):
        assert AnalysisSpec.model_validate(spec).title == spec["title"]


@pytest.mark.parametrize("expr", [
    "ce_open_interest", "nifty", "price", "close", "x", "os", "self", "spec",                       # unknown columns
    "sqrt(ce_oi)", "exp(1)", "log(pe_oi)", "eval(1)", "exec(1)", "open(1)", "len(ce_oi)", "ce_oi(1)",  # unknown / bad calls
    "__import__('os')", "__import__", "ce_oi.__class__", "ce_oi.real", "().__class__", "[1, 2]", "{1: 2}",  # injection
    "ce_oi; 1", "ce_oi # 1", "'ce_oi'", "\"ce_oi\"", "`ce_oi`", "${ce_oi}", "{{7*7}}", "ce_oi\\n", "lambda: 1",
    "ce_oi ** 2", "ce_oi % 2", "ce_oi // 2", "ce_oi = 1", "!ce_oi", "not ce_oi > 1", "ce_oi if 1 else 2",
    "1e5", "0x10", "1_000", "ce_oi@close", "ce_oi@-0m", "ce_oi@-376m", "ce_volume@open", "ce_oi@open@open",
    "\u0441e_oi", "ce_oi\u200b", "\uff43e_oi",                                                                  # look-alikes, hidden chars
    "", "   ", "(ce_oi", "ce_oi)", "abs()", "abs(ce_oi, pe_oi)", "pct_change(ce_oi)", "ce_oi pe_oi",
    "ce_oi > 1",                                                                                       # a condition as a column
    "rank(sum(ce_oi))", "avg(rank(ce_oi))",                                                            # nested window functions
    "(" * 13 + "1" + ")" * 13,                                                                         # too deep
    " + ".join(["ce_oi"] * 21),                                                                        # too many nodes (41)
    "ce_oi + " * 20 + "1",                                                                             # longer than 160 characters
])
def test_the_validator_refuses_anything_outside_the_whitelist(expr):
    _refused(columns=[{"label": "X", "expr": expr}])


@pytest.mark.parametrize("bad", [
    {"window": 21}, {"window": -1}, {"window": "5"}, {"window": 5.0}, {"window": True}, {"window": None},
    {"limit": 0}, {"limit": 42}, {"limit": "3"}, {"version": 2}, {"version": True}, {"title": ""}, {"title": "x" * 61},
    {"columns": []}, {"columns": [{"label": f"C{i}", "expr": "ce_oi"} for i in range(7)]},
    {"columns": [{"label": "A", "expr": "ce_oi"}, {"label": "a", "expr": "pe_oi"}]},                # duplicate labels
    {"columns": [{"label": "Strike", "expr": "ce_oi"}]},
    {"columns": [{"label": "A", "expr": "ce_oi", "code": "1"}]},                                    # unknown nested key
    {"columns": [{"label": "<script>", "expr": "ce_oi"}]}, {"columns": [{"label": "A;B", "expr": "ce_oi"}]},
    {"columns": [{"label": "Buy zone", "expr": "ce_oi"}]}, {"columns": [{"label": "Sell above", "expr": "ce_oi"}]},
    {"title": "Best strikes to sell"}, {"title": "Target levels"}, {"title": "Recommended strikes"}, {"title": "Guaranteed OI"},
    {"title": "Strikes \u0915"},                                                                        # not plain English
    {"filters": ["ce_oi"]}, {"filters": ["ce_oi > 1"] * 5}, {"filters": [1]},
    {"sort": {"by": "ce_oi > 1"}}, {"sort": {"by": "ce_oi", "order": "up"}}, {"sort": {"by": "ce_oi", "extra": 1}},
    {"chart": {"type": "pie", "x": "strike", "y": ["CE OI"]}}, {"chart": {"type": "bar", "x": "strike", "y": ["Nope"]}},
    {"chart": {"type": "bar", "x": "Nope", "y": ["CE OI"]}}, {"chart": {"type": "bar", "x": "strike", "y": []}},
    {"chart": {"type": "bar", "x": "strike", "y": ["CE OI", "CE OI"]}},
    {"code": "import os"}, {"eval": "1"}, {"python": "x"},                                           # unknown top-level keys
    {"columns": [{"label": f"C{i}", "expr": "ce_oi + pe_oi + ce_ltp + pe_ltp + ce_iv + pe_iv + ce_bid + pe_bid + ce_ask + pe_ask + ce_volume + pe_volume"}
                 for i in range(6)]},                                                                # over the spec's expression budget
])
def test_the_validator_refuses_bad_keys_types_and_limits(bad):
    _refused(**bad)


def test_the_validator_caps_the_spec_node_count():
    expr = " + ".join(["ce_oi"] * 20)                           # 39 nodes, within one expression's cap
    columns = [{"label": f"C{i}", "expr": expr} for i in range(6)]
    with pytest.raises(ValidationError):
        AnalysisSpec.model_validate({"title": "Big", "window": 1, "columns": columns})


def test_errors_never_repeat_the_rejected_text():
    with pytest.raises(ValidationError) as caught:
        _spec(columns=[{"label": "X", "expr": "__import__ + evilname"}])
    assert "evilname" not in str(caught.value).split("input_value")[0]


def test_the_expression_grammar_reads_what_it_should():
    assert A.parse_expression("CE_OI - PE_OI") == A.parse_expression("ce_oi-pe_oi")
    node = A.parse_expression("abs(ce_oi@-15m) / 2 >= -1 and (pe_oi > 0 or rank(pe_oi) <= 3)")
    assert isinstance(node, A.Logic) and node.op == "and"
    assert A.history_refs(_spec(columns=[{"label": "A", "expr": "ce_oi@open + pe_oi@-15m + ce_iv@open"}])) == ["open", "-15m"]


# --- the evaluator ------------------------------------------------------------------------------------------------------

def test_owner_example_one_ce_minus_pe_oi_near_atm():
    out = A.evaluate(AnalysisSpec.model_validate(OWNER_ONE), _rows(), spot=22040.0, now=NOW)
    assert out["state"] == "ok" and out["atm_strike"] == 22050.0 and out["rows_in_window"] == 5
    assert [c["label"] for c in out["columns"]] == ["Strike", "CE OI", "PE OI", "CE-PE OI"]
    assert [(r["strike"], r["atm_offset"], r["cells"][2]) for r in out["rows"]] == [
        (21950.0, -2, -500.0), (22000.0, -1, -350.0), (22050.0, 0, 100.0), (22100.0, 1, 400.0), (22150.0, 2, 550.0)]
    assert out["chart"] == {"type": "bar", "x_label": "Strike", "series": [{"label": "CE-PE OI", "points": [
        {"x": 21950.0, "y": -500.0}, {"x": 22000.0, "y": -350.0}, {"x": 22050.0, "y": 100.0}, {"x": 22100.0, "y": 400.0},
        {"x": 22150.0, "y": 550.0}]}]}


def test_owner_example_two_oi_up_more_than_twenty_percent_today():
    out = A.evaluate(AnalysisSpec.model_validate(OWNER_TWO), _rows(), spot=22040.0, now=NOW, history=[_open_point()])
    kept = {r["strike"]: r["cells"] for r in out["rows"]}
    assert list(kept) == [21950.0, 22000.0, 22100.0, 22150.0]
    assert kept[21950.0][0] == pytest.approx(100 / 3, abs=1e-6) and kept[22000.0][1] == 30.0
    assert kept[22100.0][0] == 25.0 and kept[22150.0] == [20.0, 25.0]           # exactly 20 % is not "more than 20 %"
    assert out["history"] == [{"ref": "open", "at": (NOW - timedelta(hours=2)).isoformat()}]
    # the same question on the broker's day change: then = now - change
    chg = ([0.0, 50.0, 0.0, 0.0, 100.0, 100.0, 0.0], [0.0, 0.0, 150.0, 0.0, 0.0, 10.0, 0.0])
    spec = _spec(window=20, columns=[{"label": "CE chg %", "expr": "pct_change(ce_oi, ce_oi - ce_oi_chg)"}],
                 filters=["pct_change(ce_oi, ce_oi - ce_oi_chg) > 20 or pct_change(pe_oi, pe_oi - pe_oi_chg) > 20"])
    assert [r["strike"] for r in A.evaluate(spec, _rows(chg=chg), spot=22040.0, now=NOW)["rows"]] == [21950.0, 22000.0, 22100.0, 22150.0]


def test_without_history_the_values_are_missing_and_the_rows_left_out():
    out = A.evaluate(AnalysisSpec.model_validate(OWNER_TWO), _rows(), spot=22040.0, now=NOW, history=[])
    assert out["rows"] == [] and out["history"] == [{"ref": "open", "at": None}] and "No strike" in out["message"]
    # a snapshot read after `now` is never used
    late = A.evaluate(AnalysisSpec.model_validate(OWNER_TWO), _rows(), spot=22040.0, now=NOW, history=[_open_point(NOW + timedelta(minutes=1))])
    assert late["rows"] == []


def test_division_by_zero_gives_a_missing_value():
    spec = _spec(window=20, columns=[{"label": "Ratio", "expr": "ce_oi / pe_oi"}, {"label": "Pct", "expr": "pct_change(ce_oi, pe_oi)"}])
    rows = {r["strike"]: r["cells"] for r in A.evaluate(spec, _rows(), spot=22040.0, now=NOW)["rows"]}
    assert rows[22200.0] == [None, None] and rows[22100.0] == [5.0, 400.0]
    assert A.evaluate(_spec(columns=[{"label": "Z", "expr": "1 / 0"}]), _rows(), spot=22040.0, now=NOW)["rows"][0]["cells"] == [None]


def test_minutes_back_history_takes_the_latest_snapshot_at_or_before_within_tolerance():
    def point(minutes, value):
        return HistoryPoint(at=NOW - timedelta(minutes=minutes), rows={s: {"ce_oi": value} for s in STRIKES})
    spec = _spec(window=0, columns=[{"label": "Then", "expr": "ce_oi@-15m"}, {"label": "Diff", "expr": "ce_oi - ce_oi@-15m"}])
    out = A.evaluate(spec, _rows(), spot=22040.0, now=NOW, history=[point(30, 1.0), point(16, 2.0), point(2, 3.0)])
    assert out["rows"][0]["cells"] == [2.0, 398.0] and out["history"][0]["at"] == (NOW - timedelta(minutes=16)).isoformat()
    stale = A.evaluate(spec, _rows(), spot=22040.0, now=NOW, history=[point(30, 1.0), point(2, 3.0)])      # 15 min past target
    assert stale["rows"][0]["cells"] == [None, None] and stale["history"][0]["at"] is None


def test_window_functions_sort_limit_and_missing_values():
    spec = _spec(window=20, columns=[{"label": "CE OI", "expr": "ce_oi"}, {"label": "Rank", "expr": "rank(pe_oi)"},
                                     {"label": "Share %", "expr": "ce_oi / sum(ce_oi) * 100"}, {"label": "Vs avg", "expr": "pe_oi - avg(pe_oi)"}],
                 filters=["rank(pe_oi) <= 3"], sort={"by": "pe_oi", "order": "desc"}, limit=2)
    out = A.evaluate(spec, _rows(), spot=22040.0, now=NOW)
    assert [(r["strike"], r["cells"][1]) for r in out["rows"]] == [(21900.0, 1.0), (21950.0, 2.0)]
    assert out["rows"][0]["cells"][2] == pytest.approx(100 / 2800 * 100, abs=1e-6)
    assert out["rows"][0]["cells"][3] == pytest.approx(900 - 2700 / 7, abs=1e-6)
    # equal values share a rank; a missing value has none and sorts last
    rows = _rows()
    rows[1]["ce_oi"] = 700.0
    rows[3]["ce_oi"] = None
    out = A.evaluate(_spec(window=20, columns=[{"label": "R", "expr": "rank(ce_oi)"}], sort={"by": "ce_oi", "order": "asc"}),
                     rows, spot=22040.0, now=NOW)
    assert [(r["strike"], r["cells"][0]) for r in out["rows"]][-3:] == [(21950.0, 1.0), (22200.0, 1.0), (22050.0, None)]


def test_atm_ties_take_the_lower_strike_and_the_window_stops_at_the_chain_ends():
    assert A.evaluate(_spec(window=0), _rows(), spot=22025.0, now=NOW)["atm_strike"] == 22000.0
    out = A.evaluate(_spec(window=3), _rows(), spot=21000.0, now=NOW)
    assert out["atm_strike"] == 21900.0 and [r["atm_offset"] for r in out["rows"]] == [0, 1, 2, 3]
    assert A.evaluate(_spec(), _rows(), spot=None, now=NOW)["state"] == "no_spot"
    assert A.evaluate(_spec(), [], spot=22000.0, now=NOW)["state"] == "no_rows"


def test_evaluation_is_deterministic_and_reads_only_the_data():
    spec = AnalysisSpec.model_validate(OWNER_TWO)
    first = A.evaluate(spec, _rows(), spot=22040.0, now=NOW, history=[_open_point()])
    assert first == A.evaluate(spec, _rows(), spot=22040.0, now=NOW, history=[_open_point()])
    numbers = {round(v, 6) for r in first["rows"] for v in r["cells"] if v is not None}
    expected = {round((c - o) / o * 100, 6) for c, o in zip(CE_OI + PE_OI, OPEN_CE + OPEN_PE) if o}
    assert numbers <= expected


def test_describe_says_the_spec_in_plain_words():
    lines = A.describe(AnalysisSpec.model_validate(OWNER_TWO))
    text = " ".join(lines)
    assert lines[0].startswith("Strikes: the ATM strike") and "20 listed strikes on each side" in lines[0]
    assert "Column \"CE OI chg %\": the percent change from call OI at the session's first stored snapshot to call OI." in lines
    assert "Kept only where" in text and "is greater than 20" in text and "left out" in text and "snapshots stored" in text
    assert "A division by zero, or a percent change from zero, gives a missing value." in lines
    chart = " ".join(A.describe(AnalysisSpec.model_validate(OWNER_ONE)))
    assert "Chart: a bar chart of \"CE-PE OI\" by strike." in chart and "call OI minus put OI" in chart
    for line in A.describe(AnalysisSpec.model_validate(OWNER_ONE)) + lines:
        assert not output_filter.findings(line), line


# --- the AI path (a fake model) ----------------------------------------------------------------------------------------

class _LLM:
    name, model = "fake", "fake-model"

    def __init__(self, answers):
        self.answers, self.calls, self.prompt_version = list(answers), [], None

    async def complete(self, system, user, *, max_tokens=2000):
        self.calls.append((system, user))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def _provider(monkeypatch, fake):
    from app.ai import settings as ai_settings

    async def provider_for(session, tenant, **kw):
        assert kw.get("task") == ask_ai.TASK
        return fake
    monkeypatch.setattr(ai_settings, "provider_for", provider_for)


class _Chains:
    """The broker: returns `chain` for every read and counts them."""

    def __init__(self, chain):
        self.chain, self.reads = chain, 0

    async def get_option_chain(self, underlying, expiry=None):
        self.reads += 1
        return self.chain.model_copy(update={"underlying": underlying})


def _chain(ce=CE_OI, pe=PE_OI, spot=22040.0, expiry="2026-10-27"):
    return OptionChain(underlying="X", expiry=expiry, underlying_ltp=spot,
                       rows=[OptionChainRow(strike=s, call_oi=ce[i], put_oi=pe[i], call_ltp=10.0 + i, put_ltp=20.0 - i, call_iv=0.12)
                             for i, s in enumerate(STRIKES)])


def _broker(monkeypatch, chain):
    from app.market_data import candles_routes
    fake = _Chains(chain)

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake", account_label="primary")
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: fake)
    return fake


def _org(tag, flags=("ui_v2",)):
    t = _tenant(f"oc3-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in flags:
        _flag(name, t["tenant_id"])
    return t


def _ask(t, question="show the CE-PE OI difference for ATM +- 2 strikes as a table", symbol=None, **kw):
    body = {"symbol": symbol or f"OCT{uuid.uuid4().hex[:6].upper()}", "question": question, **kw}
    return client.post("/api/option-chain/ask", headers=t["headers"], json=body)


def test_the_flag_is_off_by_default_and_both_routes_are_404_while_off(monkeypatch):
    assert "ui_v2" in DEFAULT_OFF_FLAGS
    t = _org("off", flags=())
    _broker(monkeypatch, _chain())
    _provider(monkeypatch, _LLM([{"spec": OWNER_ONE}]))
    r = _ask(t)
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "ui_v2"
    r = client.post("/api/option-chain/eval", headers=t["headers"], json={"symbol": "NIFTY", "spec": OWNER_ONE})
    assert r.status_code == 404
    assert client.post("/api/option-chain/ask", json={"symbol": "NIFTY", "question": "anything"}).status_code == 401
    assert client.post("/api/option-chain/eval", json={"symbol": "NIFTY", "spec": OWNER_ONE}).status_code == 401


def test_a_question_becomes_a_validated_spec_evaluated_on_the_chain(monkeypatch):
    t = _org("spec")
    chains = _broker(monkeypatch, _chain())
    fake = _LLM([{"spec": OWNER_ONE}])
    _provider(monkeypatch, fake)
    r = _ask(t)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["state"] == "ok" and out["message"] is None and len(fake.calls) == 1 and chains.reads == 1
    assert [row["cells"][2] for row in out["result"]["rows"]] == [-500.0, -350.0, 100.0, 400.0, 550.0]
    assert out["spec"]["title"] == "CE-PE OI near ATM" and out["description"] == A.describe(AnalysisSpec.model_validate(OWNER_ONE))
    assert out["disclaimer"] == output_filter.framing() and "Educational" in out["disclaimer"] and "not investment advice" in out["disclaimer"]
    assert out["prompt_version"] == ask_ai.PROMPT_VERSION == fake.prompt_version
    assert out["data"]["spot"] == 22040.0 and out["expiry"] == "2026-10-27"
    system, user = fake.calls[0]
    assert "ce_oi_chg" in system and "@open" in system and "<untrusted_data" in user and "CE-PE OI difference" in user


def test_an_unsupported_request_says_so_in_one_sentence_and_offers_the_closest_analysis(monkeypatch):
    t = _org("unsup")
    _broker(monkeypatch, _chain())
    closest = {"title": "Call OI near ATM", "window": 2, "columns": [{"label": "CE OI", "expr": "ce_oi"}]}
    _provider(monkeypatch, _LLM([{"unsupported": True, "reason": "News sentiment is not part of the option chain. Try OI.", "closest": closest}]))
    out = _ask(t, question="which strikes will news move tomorrow").json()
    assert out["state"] == "unsupported" and out["message"] == "News sentiment is not part of the option chain."
    assert out["result"] is None and out["closest"]["spec"]["title"] == "Call OI near ATM"
    assert [r["cells"] for r in out["closest"]["result"]["rows"]] == [[200.0], [300.0], [400.0], [500.0], [600.0]]
    assert out["closest"]["note"] == ask_ai.CLOSEST_NOTE and out["disclaimer"]
    # a reason with advice words, a number the question does not carry, or another script is replaced by the fixed sentence
    for reason in ("Buy the 22000 CE now, it is a sure-shot.", "Sentiment moves strikes by 37 points.", "\u0928\u093e\u0939\u0940."):
        _provider(monkeypatch, _LLM([{"unsupported": True, "reason": reason, "closest": None}]))
        out = _ask(t, question="which strikes will news move tomorrow").json()
        assert out["state"] == "unsupported" and out["message"] == ask_ai.CANNOT, reason
        assert out["closest"]["spec"]["title"] == ask_ai.CLOSEST_FALLBACK["title"]          # always a closest analysis


def test_an_invalid_answer_gets_one_repair_turn(monkeypatch):
    t = _org("retry")
    _broker(monkeypatch, _chain())
    bad = {"spec": {**OWNER_ONE, "columns": [{"label": "Buy zone", "expr": "__import__('os')"}]}}
    fake = _LLM([bad, {"spec": OWNER_ONE}])
    _provider(monkeypatch, fake)
    out = _ask(t).json()
    assert out["state"] == "ok" and len(fake.calls) == 2
    assert "refused by the validator" in fake.calls[1][1] and "columns.0" in fake.calls[1][1]
    assert "Buy zone" not in json.dumps(out) and "__import__" not in json.dumps(out)


def test_an_answer_that_stays_invalid_gives_the_plain_cannot_sentence(monkeypatch):
    t = _org("cannot")
    _broker(monkeypatch, _chain())
    fake = _LLM(["no json at all", {"spec": {**OWNER_ONE, "window": 50}, "note": "x"}])
    _provider(monkeypatch, fake)
    r = _ask(t)
    out = r.json()
    assert r.status_code == 200 and out["state"] == "cannot" and out["message"] == ask_ai.CANNOT and len(fake.calls) == 2
    assert out["spec"] is None and out["result"] is None and out["closest"]["spec"]["title"] == ask_ai.CLOSEST_FALLBACK["title"]
    assert "no json at all" not in r.text


def test_quota_exhaustion_shows_a_plain_message_and_spends_nothing(monkeypatch):
    from app.ai import metering
    from app.ai.providers import RuleBasedProvider
    t = _org("quota")
    chains = _broker(monkeypatch, _chain())
    monkeypatch.setattr(metering, "budget_exhausted", _async(True))
    _provider(monkeypatch, RuleBasedProvider(reason="the plan's monthly AI budget (1,500 INR) is spent; the rules answer until the 1st"))
    r = _ask(t)
    out = r.json()
    assert r.status_code == 200 and out["state"] == "quota" and out["message"] == ask_routes.QUOTA and chains.reads == 0
    assert "used up" in out["message"] and "Saved analyses still update" in out["message"] and out["result"] is None
    # no provider at all: its own plain sentence
    monkeypatch.setattr(metering, "budget_exhausted", _async(False))
    _provider(monkeypatch, RuleBasedProvider())
    out = _ask(t).json()
    assert out["state"] == "ai_off" and out["message"] == ask_routes.NO_PROVIDER


def _async(value):
    async def fn(*_a, **_k):
        return value
    return fn


def test_the_cost_cap_meters_each_call_and_stops_ask_ai_once_the_budget_is_spent(monkeypatch):
    """The real provider path: the organisation's provider (Settings > AI) wrapped by the meter; once the plan's monthly
    budget is spent the next question is a plain "quota" state and the model is not called."""
    from app.ai import metering, pricing
    from app.ai import providers as prov
    from app.ai import settings as ai_settings
    from app.db.models import Tenant
    t = _org("cap")
    _broker(monkeypatch, _chain())
    r = client.put("/api/ai/provider", headers=t["headers"], json={"data_consent": True, "provider": "anthropic", "api_key": "placeholder-for-tests"})
    assert r.status_code == 200, r.text

    class Inner:
        name, model = "anthropic", "fake-fast"

        def __init__(self):
            self.calls = 0

        async def complete_full(self, system, user, *, max_tokens=2000):
            self.calls += 1
            return prov.Completion(text=json.dumps({"spec": OWNER_ONE}), input_tokens=1200, output_tokens=300, model=self.model, provider=self.name)
    inner = Inner()
    seen = {}

    def build(name, key, model, client=None, *, task="general"):
        seen.update(task=task, key_given=bool(key))
        return inner
    monkeypatch.setattr(ai_settings, "build_provider", build)
    out = _ask(t).json()
    assert out["state"] == "ok" and inner.calls == 1 and seen == {"task": ask_ai.TASK, "key_given": True}
    assert out["usage"]["calls"] == 1 and out["usage"]["tokens_input"] == 1200 and out["provider"] == "anthropic"

    async def spend():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            budget = metering.budget_inr(tenant)
            assert budget > 0
            await metering.record(session, tenant.id, ask_ai.TASK, "anthropic", "fake-fast",
                                  prov.Completion(text="x", input_tokens=1, output_tokens=1, cost_usd_override=pricing.inr_to_usd(budget)))
            await session.commit()
            return await metering.month_usage(session, tenant.id)
    usage = _run(spend())
    assert usage["by_feature"].get(ask_ai.TASK, 0) > 0
    out = _ask(t).json()
    assert out["state"] == "quota" and out["message"] == ask_routes.QUOTA and inner.calls == 1


def test_the_ai_rate_limit_answers_with_a_plain_sentence(monkeypatch):
    from app.ai import rate_limit
    from app.core import rate_limit as core_rl
    from app.main import app
    t = _org("rate")
    _broker(monkeypatch, _chain())
    _provider(monkeypatch, _LLM([{"spec": OWNER_ONE}, {"spec": OWNER_ONE}]))
    monkeypatch.delitem(app.dependency_overrides, rate_limit.ai_rate_limit, raising=False)   # the suite turns it off
    monkeypatch.setattr(rate_limit, "limits_for", lambda plan: {"user": 1, "tenant": 0})
    core_rl.reset("ai_u")
    core_rl.reset("ai_t")
    assert _ask(t).status_code == 200
    r = _ask(t)
    assert r.status_code == 429 and "Too many AI requests" in r.json()["detail"] and r.headers.get("Retry-After")


def test_a_users_own_vision_key_is_never_used(monkeypatch):
    """BYOK in ATP is the Vision chart review only (docs/BYOK.md): Ask AI runs on the organisation's provider, never on
    a member's personal key."""
    from app.ai import byok
    from app.ai import providers as prov

    def never(*_a, **_k):
        raise AssertionError("a personal BYOK key must not be used for Ask AI")
    monkeypatch.setattr(byok, "open_key", never)
    monkeypatch.setattr(prov, "build_byok_provider", never)
    t = _org("byok", flags=("ui_v2", "vision_byok"))
    chains = _broker(monkeypatch, _chain())
    out = _ask(t).json()                                   # no organisation provider: the plain sentence, no call anywhere
    assert out["state"] == "ai_off" and out["message"] == ask_routes.NO_PROVIDER and chains.reads == 0


def test_a_provider_error_is_a_plain_state_and_nothing_secret_reaches_the_logs(monkeypatch, caplog):
    from app.ai.providers import ProviderError
    t = _org("logs")
    _broker(monkeypatch, _chain())
    leak_marker = "LEAKMARKER-oc3-upstream-text"
    question = "QUESTIONMARKER show call OI near the money"
    _provider(monkeypatch, _LLM([ProviderError(f"401 from upstream: {leak_marker}")]))
    caplog.set_level(logging.DEBUG)
    r = _ask(t, question=question)
    out = r.json()
    assert out["state"] == "ai_error" and out["message"] == ask_routes.AI_ERROR
    assert leak_marker not in r.text and leak_marker not in caplog.text
    assert "QUESTIONMARKER" not in caplog.text and "Authorization" not in caplog.text and "Bearer" not in caplog.text


def test_no_broker_answers_plainly_before_any_ai_call(monkeypatch):
    t = _org("nobroker")
    fake = _LLM([{"spec": OWNER_ONE}])
    _provider(monkeypatch, fake)
    out = _ask(t).json()
    assert out["state"] == "no_broker" and "Connect your broker" in out["message"] and fake.calls == []


# --- saved analyses: per user, no AI, live with the chain ---------------------------------------------------------------

def _put_saved(t, value, version=None):
    params = {"version": version} if version is not None else None
    return client.put("/api/me/prefs/oc_analyses", headers={**t["headers"], "Content-Type": "application/json"},
                      content=json.dumps(value), params=params)


def test_a_saved_analysis_re_evaluates_on_a_new_snapshot_without_ai(monkeypatch):
    from app.ai import settings as ai_settings
    from app.option_chain import snapshots
    t = _org("saved")
    symbol = f"OCS{uuid.uuid4().hex[:6].upper()}"
    r = _put_saved(t, {"items": {"abcd1234": {"name": "OI jump", "spec": OWNER_TWO}}}, version=0)
    assert r.status_code == 200, r.text
    saved = client.get("/api/me/prefs", headers=t["headers"]).json()
    spec = saved["prefs"]["oc_analyses"]["items"]["abcd1234"]["spec"]
    assert saved["versions"]["oc_analyses"] == 1 and spec["filters"] == OWNER_TWO["filters"]

    async def store_open():
        # the session's first stored snapshot (the OI collector's slot), early today (IST)
        now = datetime.now(timezone.utc)
        today = now.astimezone(snapshots.IST).replace(hour=0, minute=0, second=1, microsecond=0)
        at = min(now - timedelta(seconds=1), today.astimezone(timezone.utc) + timedelta(minutes=1))
        async with _session_factory() as session:
            res = await snapshots.collect(session, symbol, _chain(ce=OPEN_CE, pe=OPEN_PE), at, span=10)
            await session.commit()
            return res.status
    assert _run(store_open()) == "OK"

    async def no_ai(*_a, **_k):
        raise AssertionError("evaluating a saved analysis must not reach the AI")
    monkeypatch.setattr(ai_settings, "provider_for", no_ai)
    chains = _broker(monkeypatch, _chain())
    first = client.post("/api/option-chain/eval", headers=t["headers"], json={"symbol": symbol, "spec": spec})
    assert first.status_code == 200, first.text
    assert [r["strike"] for r in first.json()["result"]["rows"]] == [21950.0, 22000.0, 22100.0, 22150.0]
    assert first.json()["data"]["snapshots"] == 1 and first.json()["disclaimer"] == output_filter.framing()
    # a new reading of the chain: the same saved spec, new numbers
    chains.chain = _chain(ce=[100.0, 150.0, 300.0, 400.0, 400.0, 500.0, 1400.0], pe=OPEN_PE)
    second = client.post("/api/option-chain/eval", headers=t["headers"], json={"symbol": symbol, "spec": spec}).json()
    assert [(r["strike"], r["cells"][0]) for r in second["result"]["rows"]] == [(22200.0, 100.0)]
    assert chains.reads == 2


def test_eval_refuses_a_spec_outside_the_whitelist(monkeypatch):
    t = _org("evalbad")
    chains = _broker(monkeypatch, _chain())
    bad = {**OWNER_ONE, "columns": [{"label": "X", "expr": "__import__('os').system('id')"}]}
    r = client.post("/api/option-chain/eval", headers=t["headers"], json={"symbol": "NIFTY", "spec": bad})
    assert r.status_code == 422 and "system" not in r.text and chains.reads == 0


def test_saved_analyses_are_validated_capped_versioned_and_per_user():
    a, b = _org("prefsa"), _org("prefsb")
    assert _put_saved(a, {"items": {"abcd1234": {"name": "X", "spec": {**OWNER_ONE, "window": 21}}}}).status_code == 422
    assert _put_saved(a, {"items": {"abcd1234": {"name": "X", "spec": {**OWNER_ONE, "code": "x"}}}}).status_code == 422
    assert _put_saved(a, {"items": {"BAD ID!": {"name": "X", "spec": OWNER_ONE}}}).status_code == 422
    assert _put_saved(a, {"items": {"abcd1234": {"name": "<b>x</b>", "spec": OWNER_ONE}}}).status_code == 422
    nine = {f"id{i:04d}": {"name": f"A{i}", "spec": OWNER_ONE} for i in range(P.MAX_SAVED_ANALYSES + 1)}
    assert _put_saved(a, {"items": nine}).status_code == 422
    assert _put_saved(a, {"items": {"abcd1234": {"name": "Mine", "spec": OWNER_ONE}}}, version=0).status_code == 200
    # rename (version 1 -> 2), then a stale save is a 409 with the current value
    assert _put_saved(a, {"items": {"abcd1234": {"name": "Renamed", "spec": OWNER_ONE}}}, version=1).status_code == 200
    stale = _put_saved(a, {"items": {}}, version=1)
    assert stale.status_code == 409 and stale.json()["detail"]["current"]["items"]["abcd1234"]["name"] == "Renamed"
    assert _put_saved(a, {"items": {}}, version=2).status_code == 200                       # delete
    assert _put_saved(a, {"items": {"efgh5678": {"name": "Only mine", "spec": OWNER_ONE}}}, version=3).status_code == 200
    assert client.get("/api/me/prefs", headers=b["headers"]).json()["prefs"]["oc_analyses"] == {"items": {}}
    assert list(client.get("/api/me/prefs", headers=a["headers"]).json()["prefs"]["oc_analyses"]["items"]) == ["efgh5678"]


def test_the_most_a_user_can_save_fits_the_prefs_size_cap():
    """Why a prefs namespace and no table: eight analyses at every length cap still fit MAX_BODY_BYTES."""
    terms = ["ce_oi", "pe_oi", "ce_ltp", "pe_ltp", "ce_iv", "pe_iv", "ce_bid", "pe_bid", "ce_ask", "pe_ask", "ce_volume",
             "pe_volume", "ce_delta", "pe_delta", "ce_gamma", "pe_gamma", "ce_theta", "pe_theta", "ce_vega", "pe_vega"]
    budget, columns = A.MAX_SPEC_EXPR_CHARS - len("ce_oi"), []                 # the sort key below takes 5
    for i in range(A.MAX_COLUMNS):
        expr = ""
        for term in terms:
            longer = f"{expr} + {term}" if expr else term
            if len(longer) > min(A.MAX_EXPR_CHARS, budget - 5 * (A.MAX_COLUMNS - 1 - i)):
                break
            expr = longer
        columns.append({"label": f"C{i}" + "x" * 30, "expr": expr})
        budget -= len(expr)
    assert A.MAX_SPEC_EXPR_CHARS - 10 <= sum(len(c["expr"]) for c in columns) + len("ce_oi") <= A.MAX_SPEC_EXPR_CHARS
    spec = {"title": "T" * 60, "window": 20, "columns": columns, "limit": 41,
            "sort": {"by": "ce_oi", "order": "desc"}, "chart": {"type": "line", "x": columns[0]["label"], "y": [c["label"] for c in columns[1:3]]}}
    AnalysisSpec.model_validate(spec)
    item = {"name": "N" * 40, "spec": spec, "saved_at": "2026-10-21T10:00:00+00:00"}
    value = {"items": {f"{i:016d}": item for i in range(P.MAX_SAVED_ANALYSES)}}
    P.OcAnalysesPrefs.model_validate_json(json.dumps(value))
    assert len(json.dumps(value).encode()) < P.MAX_BODY_BYTES


def test_a_stored_analysis_that_no_longer_validates_is_dropped_alone():
    good = {"name": "Good", "spec": OWNER_ONE}
    bad = {"name": "Old", "spec": {**OWNER_ONE, "columns": [{"label": "X", "expr": "retired_column"}]}}
    out = P.stored_value("oc_analyses", json.dumps({"items": {"good0001": good, "bad00001": bad}}))
    assert list(out["items"]) == ["good0001"] and out["items"]["good0001"]["name"] == "Good"
