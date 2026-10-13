/** U9 TX2: sample library data for stories and tests - the mirrors and watch lists exactly as the server sends them with
 * only `screener_tx2` on (from app.screener.library.mirrors / WATCH_LISTS), plus one confluence and one watch result. */
import type { ConfluenceResponse, MirrorsResponse, WatchListsResponse, WatchResponse } from "./library";

export const SAMPLE_MIRRORS: MirrorsResponse = {
  "mirrors": [
    {
      "id": "starter.momentum.short",
      "mirror_of": "starter.momentum",
      "name": "Momentum (mirror, down side)",
      "method": "indicator",
      "setup": "momentum",
      "direction": "short",
      "base_tf": "1d",
      "source": "starter",
      "text": "RSI(14) < 40 AND close < EMA(close, 20)",
      "reason": null,
      "how": "reflected"
    },
    {
      "id": "starter.oversold.short",
      "mirror_of": "starter.oversold",
      "name": "Oversold (mirror, down side)",
      "method": "indicator",
      "setup": "reversal",
      "direction": "short",
      "base_tf": "1d",
      "source": "starter",
      "text": "RSI(14) >= 70",
      "reason": null,
      "how": "reflected"
    },
    {
      "id": "starter.daily_trend.short",
      "mirror_of": "starter.daily_trend",
      "name": "Trend on the daily (mirror, down side)",
      "method": "indicator",
      "setup": "pullback",
      "direction": "short",
      "base_tf": "1d",
      "source": "starter",
      "text": "close@1d < EMA(close, 50)@1d",
      "reason": null,
      "how": "reflected"
    }
  ],
  "note": "A mirror is the same rules on reflected prices (highs and lows swap, a rise becomes a fall). It is generated from the long screen, never edited by hand."
};

export const SAMPLE_LISTS: WatchListsResponse = {
  "lists": [
    {
      "id": "watch.pullback_ema20",
      "name": "Pullback to the 20 EMA in an uptrend",
      "definition": "Ready: above a rising 50 EMA with the low near the 20 EMA. Trigger: a close above the previous bar's high.",
      "method": "indicator",
      "setup": "pullback",
      "direction": "long",
      "base_tf": "1d",
      "ready": "close > EMA(close, 50) AND EMA(close, 20) > EMA(close, 50) AND low <= EMA(close, 20) + $near_atr * ATR(14)",
      "trigger": "close > high[1]",
      "params": {
        "near_atr": 0.25
      },
      "transition": "Count(1, close > EMA(close, 50) AND EMA(close, 20) > EMA(close, 50) AND low <= EMA(close, 20) + 0.25 * ATR(14))[1] >= 1 AND close > high[1]"
    },
    {
      "id": "watch.near_20_bar_high",
      "name": "Close near the 20-bar high",
      "definition": "Ready: the close within a fraction of an ATR below the prior 20-bar high. Trigger: a close above that high.",
      "method": "price_action",
      "setup": "breakout",
      "direction": "long",
      "base_tf": "1d",
      "ready": "close >= Max(high, 20)[1] - $near_atr * ATR(14) AND close <= Max(high, 20)[1]",
      "trigger": "close > Max(high, 20)[1]",
      "params": {
        "near_atr": 0.5
      },
      "transition": "Count(1, close >= Max(high, 20)[1] - 0.5 * ATR(14) AND close <= Max(high, 20)[1])[1] >= 1 AND close > Max(high, 20)[1]"
    },
    {
      "id": "watch.rsi_oversold_turn",
      "name": "RSI oversold, then back above 30",
      "definition": "Ready: RSI(14) at or below 30. Trigger: RSI(14) crosses back above 30.",
      "method": "indicator",
      "setup": "reversal",
      "direction": "long",
      "base_tf": "1d",
      "ready": "RSI(14) <= 30",
      "trigger": "CrossAbove(RSI(14), 30)",
      "params": {},
      "transition": "Count(1, RSI(14) <= 30)[1] >= 1 AND CrossAbove(RSI(14), 30)"
    }
  ],
  "note": "Ready: the ready condition holds on the last closed bar. Triggered: the trigger holds on the last closed bar and the ready condition held on the bar before. Save the trigger screen to get an alert on the transition."
};

export const SAMPLE_CONFLUENCE: ConfluenceResponse = {
  rows: [
    { symbol: "UP", methods: ["indicator", "my_screens"], method_count: 2, screen_count: 3, screens: [
      { ref: "starter.momentum", name: "Momentum", method: "indicator", setup: "momentum", text: "RSI(14) > 60 AND close > EMA(close, 20)" },
      { ref: "starter.daily_trend", name: "Trend on the daily", method: "indicator", setup: "pullback", text: "close@1d > EMA(close, 50)@1d" },
      { ref: "saved:4", name: "New 10-day high", method: "my_screens", setup: "", text: "close > Max(high, 10)[1]" }] },
    { symbol: "DOWN", methods: ["indicator"], method_count: 1, screen_count: 1, screens: [
      { ref: "starter.momentum.short", name: "Momentum (mirror, down side)", method: "indicator", setup: "momentum", text: "RSI(14) < 40" }] },
  ],
  unflagged: ["FLAT"], not_evaluated: {}, methods_run: ["indicator", "my_screens"], scanned: 3,
  screens: [{ ref: "starter.momentum", name: "Momentum", method: "indicator", setup: "momentum", text: "", base_tf: "1d", matched: 1 },
            { ref: "starter.momentum.short", name: "Momentum (mirror, down side)", method: "indicator", setup: "momentum", text: "", base_tf: "1d", matched: 1 }],
  note: "Agreement is a count of the screens and methods whose filters held on the data shown. It is not a score or a ranking of quality, and it does not say what to trade.",
  disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations.",
};
export const SAMPLE_WATCH: WatchResponse = {
  triggered: ["UP"], ready: ["MID"], neither: ["DOWN"], not_evaluated: {}, note: "Save the trigger screen to get an alert on the transition.",
  name: "Your watch list", base_tf: "1d", ready_text: "close > SMA(close, 5)", trigger_text: "close > high[1]",
  transition_text: "Count(1, close > SMA(close, 5))[1] >= 1 AND close > high[1]", scanned: 3, disclaimer: SAMPLE_CONFLUENCE.disclaimer,
};
