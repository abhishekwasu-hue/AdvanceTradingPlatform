"""Five common Pine snippets that translate cleanly (handover 11c-1). Signal conditions only: white box, no performance
claim, no sizing, stops or targets (those are the user's own settings). Every template is translated in the tests."""
from __future__ import annotations

from typing import Dict, List

TEMPLATES: List[Dict[str, str]] = [
    {
        "id": "ema_cross",
        "name": "EMA cross",
        "summary": "The fast EMA crosses above the slow EMA; the exit condition is the cross back below.",
        "source": """//@version=5
strategy("EMA cross", overlay=true)
fastLen = input.int(9, "Fast EMA", minval=1, maxval=200)
slowLen = input.int(21, "Slow EMA", minval=2, maxval=500)
fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
""",
    },
    {
        "id": "rsi_pullback",
        "name": "RSI pullback in an uptrend",
        "summary": "Above the trend EMA, RSI turns back up through the pullback level.",
        "source": """//@version=5
indicator("RSI pullback", overlay=true)
rsiLen = input.int(14, "RSI length", minval=2, maxval=100)
trendLen = input.int(200, "Trend EMA", minval=10, maxval=500)
level = input.float(40, "Pullback level", minval=5, maxval=60)
trendUp = close > ta.ema(close, trendLen)
rsiValue = ta.rsi(close, rsiLen)
pullbackTurn = trendUp and ta.crossover(rsiValue, level)
alertcondition(pullbackTurn, title="RSI turns up from the pullback level in an uptrend")
""",
    },
    {
        "id": "supertrend_flip",
        "name": "Supertrend flip",
        "summary": "The Supertrend direction turns up (or down) on the bar.",
        "source": """//@version=5
indicator("Supertrend flip", overlay=true)
atrLen = input.int(10, "ATR length", minval=1, maxval=100)
factor = input.float(3.0, "Factor", minval=0.5, maxval=10, step=0.1)
[stLine, dir] = ta.supertrend(factor, atrLen)
turnsUp = ta.change(dir) < 0
turnsDown = ta.change(dir) > 0
plot(stLine, "Supertrend", color=dir < 0 ? color.green : color.red)
alertcondition(turnsUp, title="Supertrend turns up")
alertcondition(turnsDown, title="Supertrend turns down")
""",
    },
    {
        "id": "macd_cross",
        "name": "MACD cross",
        "summary": "The MACD line crosses above its signal line while both are below zero.",
        "source": """//@version=5
indicator("MACD cross")
fastLen = input.int(12, "Fast length", minval=1, maxval=100)
slowLen = input.int(26, "Slow length", minval=2, maxval=200)
sigLen = input.int(9, "Signal length", minval=1, maxval=50)
[macdLine, signalLine, hist] = ta.macd(close, fastLen, slowLen, sigLen)
crossUp = ta.crossover(macdLine, signalLine) and macdLine < 0
plot(hist, "Histogram", style=plot.style_columns)
plotshape(crossUp, title="MACD crosses up below zero", style=shape.triangleup, location=location.bottom)
""",
    },
    {
        "id": "bollinger_reentry",
        "name": "Bollinger re-entry",
        "summary": "The close comes back inside the lower Bollinger band after closing below it.",
        "source": """//@version=5
indicator("Bollinger re-entry", overlay=true)
length = input.int(20, "Length", minval=5, maxval=200)
mult = input.float(2.0, "Deviations", minval=0.5, maxval=4)
[middle, upper, lower] = ta.bb(close, length, mult)
reentry = close[1] < lower[1] and close > lower
plot(middle)
plot(upper)
plot(lower)
alertcondition(reentry, title="Close back inside the lower band")
""",
    },
]

__all__ = ["TEMPLATES"]
