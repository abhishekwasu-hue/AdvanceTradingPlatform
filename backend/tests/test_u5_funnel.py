"""U5: the screen as a funnel - survivors after each top-level ALL stage, the last equal to the screen's matches."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.screener import compile_screen
from app.screener.registry import describe
from app.screener.runtime import SymbolData, run_screen, stage_survivors


def _sym(name, closes):
    idx = pd.date_range("2026-03-02", periods=len(closes), freq="D", tz="UTC")
    c = np.asarray(closes, float)
    return SymbolData(name, {"1d": pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 100.0}, index=idx)})


def _universe():
    return [_sym("UP", np.linspace(100, 160, 60)), _sym("DOWN", np.linspace(160, 100, 60)),
            _sym("FLAT", [100.0] * 60), _sym("UP2", np.linspace(50, 70, 60))]


def test_each_stage_counts_the_symbols_still_in_and_the_last_is_the_screen():
    ast, v = compile_screen("close > SMA(close, 20) AND close > 60 AND PctChange(close, 5) > 0", base_tf="1d")
    uni = _universe()
    funnel = stage_survivors(ast, v, uni, base_tf="1d")
    stages = funnel["stages"]
    assert funnel["with_data"] == 4 and [s["survivors"] for s in stages] == [2, 2, 2]                                  # UP and UP2 rise; both above 60
    assert sorted(stages[0]["removed"]) == ["DOWN", "FLAT"] and stages[1]["removed"] == stages[2]["removed"] == []
    assert [s["text"] for s in stages] == ["close > SMA(close, 20)", "close > 60", "PctChange(close, 5) > 0"]
    assert stages[-1]["survivors"] == sum(m.matched for m in run_screen(ast, v, uni, base_tf="1d"))
    ast2, v2 = compile_screen("close > 60 AND close > SMA(close, 20)", base_tf="1d")
    assert [s["survivors"] for s in stage_survivors(ast2, v2, uni, base_tf="1d")["stages"]] == [4, 2]   # all four close above 60; order matters


def test_anything_but_a_top_level_all_is_one_stage():
    for text in ("close > 60 OR close < 0", "NOT close > 60"):
        ast, v = compile_screen(text, base_tf="1d")
        stages = stage_survivors(ast, v, _universe(), base_tf="1d")["stages"]
        assert len(stages) == 1 and stages[0]["survivors"] == sum(m.matched for m in run_screen(ast, v, _universe(), base_tf="1d"))


def test_the_registry_lists_parameter_defaults_for_inline_editing():
    reg = describe()
    assert reg["RSI"]["defaults"] == {"n": 14} and reg["SwingLow"]["defaults"] == {"degree": 0}
    assert reg["close"]["defaults"] == {}
    assert reg["SMA"]["types"] == {"x": "num", "n": "window"} and reg["Rank"]["cross_sectional"] is True
    # U5 D2 review: functions that need more than bars say so (the builder hides them until the run loads that data)
    assert reg["IsFnO"]["needs"] == ["reference"] and reg["ChainBias"]["needs"] == ["option_chain"] and reg["RSI"]["needs"] == []


BUILDER_TEXTS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener" / "builderTexts.json"


def test_the_text_the_builder_writes_is_valid_screenql():
    """The contract with the frontend model (src/screener/model.ts): its test writes each `text` from `stage` with
    `registry`; here every text (alone and all joined by AND, as the canvas sends them) must validate, and the registry
    the builder was tested against must still be what the server describes."""
    fixture = json.loads(BUILDER_TEXTS.read_text())
    reg = describe()
    for name, entry in fixture["registry"].items():
        assert reg[name] == entry, f"{name} changed on the server - update builderTexts.json and re-run the frontend test"
    texts = [c["text"] for c in fixture["cases"]]
    assert len(texts) >= 10
    for text in texts:
        assert compile_screen(text, base_tf="5m")[1].ok, text
    assert compile_screen(" AND ".join(texts), base_tf="5m")[1].ok


def test_a_cross_sectional_stage_ranks_over_the_whole_universe_as_in_the_screen():
    """U5 review: re-running a stage on the survivors only would rank Rank() over a shrunken set."""
    ast, v = compile_screen("close < 150 AND Rank(close) <= 1", base_tf="1d")
    uni = _universe()
    funnel = stage_survivors(ast, v, uni, base_tf="1d")
    assert funnel["stages"][-1]["survivors"] == sum(m.matched for m in run_screen(ast, v, uni, base_tf="1d"))


def test_a_symbol_without_enough_history_is_left_out_not_removed_by_stage_one():
    ast, v = compile_screen("close > 0 AND close > SMA(close, 50)", base_tf="1d")
    uni = _universe() + [_sym("NEW", [100.0] * 10)]                                     # listed recently
    funnel = stage_survivors(ast, v, uni, base_tf="1d")
    assert funnel["with_data"] == 4 and all("NEW" not in s["removed"] for s in funnel["stages"])


def test_each_symbol_shows_which_stages_it_passed_on_its_own():
    """U5 D2 why-matched chips: a stage's pass is the symbol's own result, even after an earlier stage removed it."""
    ast, v = compile_screen("close > 100 AND close > SMA(close, 20)", base_tf="1d")
    funnel = stage_survivors(ast, v, _universe(), base_tf="1d")
    # UP2 (last close 70) fails stage one but passes stage two on its own; FLAT equals its SMA (not above)
    assert funnel["passes"] == {"UP": [True, True], "DOWN": [False, False], "FLAT": [False, False], "UP2": [False, True]}
    matched = {m.symbol for m in run_screen(ast, v, _universe(), base_tf="1d") if m.matched}
    assert {s for s, row in funnel["passes"].items() if all(row)} == matched
