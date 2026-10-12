/** U9 TX2: sample library data for stories and tests - the mirrors and watch lists exactly as the server sends them with
 * only `screener_tx2` on (from app.screener.library.mirrors / WATCH_LISTS), plus one confluence and one watch result. */
import type { EvidencePack } from "./evidence";
import evidenceSample from "./evidenceSample.json";
import type {
  ConfluenceResponse, DigestState, ExamplesResponse, LearnResponse, MirrorsResponse, RegimeResponse, WatchListsResponse, WatchResponse,
} from "./library";

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

/** U9 TX3: a regime reading (trend + high volatility) with the starters' fits, and a digest state with its messages. */
export const SAMPLE_REGIME: RegimeResponse = {
  as_of: "2026-10-12T11:00:00+00:00", session: "2026-10-09", index: "NIFTY 50",
  trend: { value: "range", reasons: ["NIFTY 50 Dow trend on daily bars (degree 1): RANGE (last swing high lower, last swing low higher)"], numbers: { dow: "RANGE", advancers: 812, decliners: 1104 } },
  volatility: { value: "high_vol", reasons: ["INDIA VIX 18.40: percentile 82 of the last 250 sessions"], numbers: { vix: 18.4, percentile: 82, sessions: 250 } },
  note: "Today's regime is read from closed sessions of NIFTY 50, India VIX and NSE breadth in the shared history. A greyed screen was designed for another regime; it still opens and runs. This is a hint, not advice.",
  fits: {
    "starter.momentum": { fits: false, reason: "Not today's regime: designed for a trending market; today reads range-bound market." },
    "starter.oversold": { fits: true, reason: "Fits today's regime." },
    "starter.daily_trend": { fits: false, reason: "Not today's regime: designed for a trending market; today reads range-bound market." },
  },
};

export const SAMPLE_DIGEST: DigestState = {
  enabled: true, send_time: "16:00", pinned: ["starter.momentum", "starter.oversold"], symbols: ["RELIANCE", "TCS", "INFY"], exchange: "NSE",
  last_sent_on: "2026-10-09", last_status: "Kept in the app: link your Telegram under Settings > Notifications to get it there too.",
  last_digest: null, limits: { pinned: 5, symbols: 50 },
  note: "Matches of your pinned screens on the last closed bar, the first ones in your list order. Screens filter; they do not predict, and this is not advice.",
  messages: ["Setups of the day - Indicator (2026-10-09)\nMomentum: RELIANCE, TCS\nOversold: no match\nMatches of your pinned screens on the last closed bar, the first ones in your list order. Screens filter; they do not predict, and this is not advice."],
};

/** U9 TX4: the Learn panel of a starter and two past examples (one with its evidence pack). */
export const SAMPLE_LEARN: LearnResponse = {
  id: "starter.oversold", name: "Oversold", base_tf: "1d", direction: "long", mirror_of: null,
  method: { id: "indicator", label: "Indicator", definition: "Moving averages, oscillators, bands, channels and volatility measures." },
  setup: { id: "reversal", label: "Reversal", definition: "Exhaustion, divergence, a failed break or a sweep at a level." },
  page: null, page_note: "The method page (P1 §1.6) is linked here once the Learn pages are available.",
  provenance: { id: "starter.oversold", version: 1, changes: [{ version: 1, note: "Filed in the library (U9 TX1)." }],
    source_note: "The U5 funnel builder's own starting point; no external source.", sources: [], text: "RSI(14) <= 30", text_sha256: null },
  note: "Education: past matches of this screen on the shared end-of-day history and what the close did after them. Three examples are illustrations - not a statistic, not a forecast and not advice.",
};

export const SAMPLE_EXAMPLES: ExamplesResponse = {
  id: "starter.oversold", name: "Oversold", base_tf: "1d", text: "RSI(14) <= 30", scanned: 2, after_sessions: [5, 10, 20],
  examples: [{ symbol: "TCS", date: "2026-06-12", close: 3480.5, after: { "5": 2.34, "10": -1.2, "20": 4.05 }, pack: evidenceSample as unknown as EvidencePack },
             { symbol: "INFY", date: "2026-03-03", close: 1500, after: { "5": 0.4, "10": 1.1, "20": -3.33 }, pack: null }],
  problems: [], note: SAMPLE_LEARN.note,
};
