"""U5: the screen as a funnel - survivors after each top-level ALL stage, the last equal to the screen's matches."""
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
    stages = stage_survivors(ast, v, uni, base_tf="1d")
    assert [s["survivors"] for s in stages] == [2, 2, 2]                                  # UP and UP2 rise; both above 60
    assert sorted(stages[0]["removed"]) == ["DOWN", "FLAT"] and stages[1]["removed"] == stages[2]["removed"] == []
    assert [s["text"] for s in stages] == ["close > SMA(close, 20)", "close > 60", "PctChange(close, 5) > 0"]
    assert stages[-1]["survivors"] == sum(m.matched for m in run_screen(ast, v, uni, base_tf="1d"))
    ast2, v2 = compile_screen("close > 60 AND close > SMA(close, 20)", base_tf="1d")
    assert [s["survivors"] for s in stage_survivors(ast2, v2, uni, base_tf="1d")] == [4, 2]   # all four close above 60; order matters


def test_anything_but_a_top_level_all_is_one_stage():
    for text in ("close > 60 OR close < 0", "NOT close > 60"):
        ast, v = compile_screen(text, base_tf="1d")
        stages = stage_survivors(ast, v, _universe(), base_tf="1d")
        assert len(stages) == 1 and stages[0]["survivors"] == sum(m.matched for m in run_screen(ast, v, _universe(), base_tf="1d"))


def test_the_registry_lists_parameter_defaults_for_inline_editing():
    reg = describe()
    assert reg["RSI"]["defaults"] == {"n": 14} and reg["SwingLow"]["defaults"] == {"degree": 0}
    assert reg["close"]["defaults"] == {}
    assert reg["SMA"]["types"] == {"x": "num", "n": "window"} and reg["Rank"]["cross_sectional"] is True


def test_the_text_the_builder_writes_is_valid_screenql():
    """The frontend model (src/screener/model.ts) writes these shapes; they must parse and validate as written."""
    for text in ("RSI(14)@15m > 60", "CrossAbove(close, EMA(close, 20))", "Supertrend(10, 2.5)@1h <= close",
                 "SwingLow(1) < close AND RSI(14) > 0.3", "close@1d > 100 AND CrossBelow(close, EMA(close, 50)@1d)"):
        assert compile_screen(text, base_tf="5m")[1].ok, text
