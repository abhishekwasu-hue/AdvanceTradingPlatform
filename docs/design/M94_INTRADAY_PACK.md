# M94 Intraday pack - spec review against the code and build plan

Owner order M94 (INBOX 10 Nov, DECISIONS 10 Nov "M94"; roadmap row M94). This file is the review CLAUDE.md §6 asks for
before a big spec is built: every item checked against the code on `main` (b5e17888), the findings, the PR plan, the
open questions (each with a default; also in docs/OPEN_QUESTIONS.md as M94-Q1...) and the proof plan. Docs only: no
code is changed by this review.

Source of the definitions: a private study of a day-trading text (read-only, kept outside this repository). This file
carries only generic definitions written in our own words: no text from the source, no author, title, product or brand
name and no example symbols. Numbers quoted from the study are marked **ref** and are "reference values - edit before
use": nothing is enforced, every number is a setting (I2).

Words used here: *setup* = the condition that arms a template; *trigger* = the bar that gives the signal; *invalidation*
= where the idea is wrong (the template's "stop rule"; the UI says "The idea is wrong beyond", see F1); *exit options*
= the deployment's own exit settings (app/strategy_templates/exits.py). *Session* = the exchange session on the
exchange calendar (NSE 09:15-15:30 IST), never a clock rule inside a reading condition (F9).

Every draft ScreenQL text below was parsed and validated against the registry (`app.screener.validator.validate`) on
the named timeframe, and five draft templates (T6, T7, T8, T9, T10) were compiled with `compile_recipe`, on b5e17888.
Texts that call a NEW function (marked NEW) cannot be validated until PR-1 adds it.

---

## 1. Summary of the 25 items

Status: **exists** = runs today without new code (only filing / text); **partial** = most parts exist, one new piece;
**new** = needs a new runtime function, data field or engine part.

| # | Item | Status | Reuses | New piece | PR |
|---|---|---|---|---|---|
| S1 | Intraday Gappers | partial | DayOpen, PDC, ATR@1d, SMA(volume)@1d | PreOpenVolume (pre-open bars) | 1, 2 |
| S2 | New Day High | exists | DayExtremeBreak, RelVolumeTOD, Max / Min | - | 2 |
| S3 | New Day Low | exists | the S2 mirror | - | 2 |
| S4 | Top Reversal | partial | DayOpen, Pattern, ATR | SessionHigh | 1, 2 |
| S5 | Bottom Reversal | partial | the S4 mirror | SessionLow | 1, 2 |
| S6 | VWAP Break | exists | CrossAbove / CrossBelow, SessionVWAP, RelVolumeTOD | - | 2 |
| S7 | Turbo Breakup | exists | DayExtremeBreak on 1m, RelVolumeTOD | - | 2 |
| S8 | Turbo Breakdown | exists | the S7 mirror | - | 2 |
| S9 | Circuit Watch | new | the broker quote path (M90 batch quotes) | band fields in Quote + BandDistancePct | 3 |
| S10 | RSI Extreme | partial | RSI@5m, close range | SessionVolume | 1, 2 |
| S11 | Volume Surge | exists | RelVolumeTOD (bar / cumulative) | - | 2 |
| S12 | Key Levels | partial | PDC, PDH, PDL, Lag(high/low,1)@1d, SMA/EMA@1d, Max/Min(…,250)@1d | PreOpenHigh / PreOpenLow | 1, 2 |
| T1 | Fallen Angel (long) | partial | DayOpen, PDC, Max / Min, ATR, evidence | SessionHigh | 1, 5 |
| T2 | ABCD (long) | partial | DayExtremeBreak, SwingLow, session_bound | SessionLow | 1, 5 |
| T3 | Reverse ABCD (short) | partial | the T2 mirror | SessionHigh | 1, 5 |
| T4 | Bull Flag | partial | PatternBreakout("flag"), PatternBreakoutRef | named pole / flag arguments, SessionCount | 1, 5 |
| T5 | Bear Flag | partial | the T4 mirror | as T4 | 1, 5 |
| T6 | Opening Range Breakout | exists | ORBreakout, ORHigh / ORLow, SessionVWAP | - (draft compiled) | 4 |
| T7 | VWAP False Breakout | exists | Count, CrossAbove / CrossBelow, SessionVWAP, since-setup extreme | - (draft compiled) | 4 |
| T8 | VWAP Reversal | exists | SessionVWAP, Min / Max, EMA, since-setup extreme | - (draft compiled) | 4 |
| T9 | VWAP MA Trend | exists | SessionVWAP, EMA, Count, exit signal | - (draft compiled) | 4 |
| T10 | Level Rejection | exists | PDH / PDL / PDC, @1d levels, Count, EMA | - (draft compiled; SessionCount optional) | 4 |
| R1 | Position-size calculator | partial | risk-based sizing (RiskManager, position_size.py) | the calculator screen | 7 |
| R2 | Daily max-loss lock | exists | Risk limits "Daily loss limit" (entries only) | an explicit "never an exit" test on the M94 path | 7 |
| R3 | Profit give-back stop | new | Risk limits checks, TradingDayState | a day-peak record (migration) + the check | 7 |

Totals: exists 12 (S2, S3, S6, S7, S8, S11, T6-T10, R2), partial 11 (S1, S4, S5, S10, S12, T1-T5, R1), new 2
(S9, R3).

Every template additionally needs the shared, template-wide pieces of PR-4 / PR-6: family "Intraday" (F2), the
"reference values - edit before use" label (F8), the one-screen "how it works" with a schematic (F15), the template
overlay on the chart (F16), level exits (F10) and the footer (F14).

---

## 2. New runtime functions (PR-1)

All causal: a value at bar j reads bars 0..j only; the session comes from the exchange calendar's session map
(`app/screener/breakouts/session.py` `map_sessions`), so a half day or a special session needs no special case. Each
gets the cut-bar test (the series read bar by bar equals the function run on bars cut at each bar, Z10-a) and a
positive / negative fixture. Behind the new flag `intraday_pack` (off by default) and `screener_breakouts` (they reuse
its session code).

| Function | Kind / unit | Inputs | Definition | Built on |
|---|---|---|---|---|
| `SessionHigh()` | factor, price | - | the highest high of the session's regular bars up to and including this bar; missing on a pre-open bar | `session.session_extremes_before` + this bar |
| `SessionLow()` | factor, price | - | the lowest low, same rule | as above |
| `SessionVolume()` | factor, volume | - | the sum of the volume of the session's regular bars up to and including this bar | `SessionMap.session` ids |
| `SessionCount(cond)` | factor, count | a condition | the bars of this session so far (this bar included) where `cond` held | as `Count`, reset at each session |
| `PreOpenHigh()` / `PreOpenLow()` | factor, price | - | the high / low of the pre-open bars of this bar's session date; known from the session's first regular bar; missing when the data has no pre-open bars | `session.premarket_levels` (already used by DayLevelBreak "premarket") |
| `PreOpenVolume()` | factor, volume | - | the volume of those pre-open bars; missing as above | `SessionMap.pre_session` |
| `BandDistancePct(side)` | factor, pct; LAST BAR ONLY | "upper" / "lower" | (upper band - last price) / last price x 100, or (last price - lower band) / last price x 100, from the user's broker quote; missing when the quote has no band | new Quote fields (PR-3) |
| PatternBreakout / PatternBreakoutRef named arguments | - | `pole=`, `pole_bars=`, `flag_min=`, `flag_max=`, `retrace=` | the existing B6 parameters (`pole_min_atr`, `pole_max_bars`, `flag_min_bars`, `flag_max_bars`, `flag_max_retrace`) as call arguments; left out = today's values, so every existing call is byte-identical | `_bk(...)` named-argument pattern (as RetestEntry's `k=`) |

`BandDistancePct` is answered for the last bar only (registry LAST_BAR_ONLY): no stored band history exists, so it is a
scanner input only, never a template or backtest input (F5).

---

## 3. Scanners (screener library, pack "Intraday")

Common to all twelve: data tier "Your broker" (`live_per_user`): the user's own broker session or the stored intraday
the M71 / M86 / M90 rules allow (HB2 stored 1-minute bars are the owner's only); never shared live data (I4). Base
timeframe 1m or 5m (I4); 3m / 15m allowed where the function allows. Closed bars only. Each has a long screen and its
short counterpart filed as its own id where the order names both (S2 / S3, S4 / S5, S7 / S8); the library's `.short`
mirror rule (`library.mirror_of`) is used to generate the short text and is tested equal. Universe default: the user's
list, or F&O stocks (F12). Footer on every result: I5.

A **0** in a volume parameter means "no volume condition", as in the existing breakout parameters
(`tod_vol_mult`: "0 = no volume condition"). **Not set** means the run asks for the value before it runs.

### S1 Intraday Gappers
- (a) The session opened away from the previous session's close by at least a set percent, with optional checks on
  pre-open volume, the stock's normal daily volume (or turnover) and its daily range (ATR as % of price).
- (b) `DayOpen()`, `PDC()` (registry.py), `ATR(n)@1d`, `SMA(volume, n)@1d`; the gap engine `breakouts/gaps.py` (B8)
  is a different idea (the open beyond the previous HIGH / LOW, held for a window) and stays as it is.
- (c) Today, without pre-open volume:
  `Abs(DayOpen() - PDC()) / PDC() * 100 >= $gap_pct AND SMA(close * volume, 20)@1d >= $min_turnover AND ATR(14)@1d / close * 100 >= $min_atr_pct`
  (validated on 5m). With PR-1: `... AND PreOpenVolume() >= $pre_vol`. Up / down: `(DayOpen() - PDC()) ... >= / <= -`.
- (d) Previous daily bars (store or broker) and the session's first bar. Pre-open bars: not shown to exist in any
  broker feed by the code (F4); without them the pre-open volume condition is "not available", the gap uses the
  session's opening price.
- (e) `gap_pct` **ref 2** (%); `pre_vol` **ref** a share count in another market: Not set (F8); average volume **ref**
  a share count: replaced by turnover, Not set; ATR **ref** an amount in another currency: Not set as `min_atr_pct`.

### S2 New Day High / S3 New Day Low
- (a) A close beyond the session's high (low) so far, with volume high against the same time of day, the session's
  volume so far above normal, and a recent range wide enough to move.
- (b) `DayExtremeBreak(side, v, vwap, skip, k)` (B18, breakouts/intraday.py `b18`), `RelVolumeTOD(sessions, mode)`
  (session.py `time_of_day_rvol`), `Max` / `Min`. Existing screen `breakouts.intraday_5m_day_extreme` stays.
- (c) Today (validated on 5m):
  `DayExtremeBreak("up", $v, $vwap, $skip, $k) AND RelVolumeTOD(20, "bar") <= $v_max AND RelVolumeTOD(20, "cumulative") >= $cum_v AND (Max(high, $range_bars) - Min(low, $range_bars)) >= $range_atr * ATR(14)[1]`;
  S3: `"down"`.
- (d) The intraday bars of the symbol and 21 prior sessions of the same timeframe (RelVolumeTOD needs `tod_sessions`
  + 1 sessions, F12).
- (e) `v` **ref 4** (the last 5 minutes at 400 % of normal) and `v_max` **ref 20** (2,000 %) on 5m; `cum_v` **ref 2**;
  `range_bars` **ref** 15 minutes (3 bars on 5m); `range_atr`: the study gives an amount, Not set; price and float
  limits of the study: not carried (no float data, F18). `vwap`, `skip`, `k`: the existing breakout defaults.

### S4 Top Reversal / S5 Bottom Reversal
- (a) A large rise from the session's open, then a bearish reversal candle at or near the session's high (S5: a large
  fall, a bullish reversal candle at or near the session's low).
- (b) `DayOpen()`, `Pattern(name)` (series form; `PatternBearish` is last-bar only, F23), `ATR`.
- (c) PR-1 (NEW `SessionHigh`):
  `(SessionHigh() - DayOpen()) / DayOpen() * 100 >= $move_pct AND SessionHigh() - high <= $near_atr * ATR(14)[1] AND (Pattern("shooting_star") OR Pattern("bearish_engulfing") OR Pattern("evening_star"))`;
  S5 with `SessionLow()`, `hammer` / `bullish_engulfing` / `morning_star`. (The pattern part validated today.)
- (d) The session's intraday bars.
- (e) The study names these scanners without numbers: `move_pct`, `near_atr` Not set (Q3); the pattern list editable.

### S6 VWAP Break
- (a) The close crosses the session VWAP (up or down) on volume high against the same time of day.
- (b) `SessionVWAP()` (session.py `session_vwap`), `CrossAbove` / `CrossBelow`, `RelVolumeTOD`.
- (c) Today (validated): `CrossAbove(close, SessionVWAP()) AND RelVolumeTOD(20, "bar") >= $v`; down with `CrossBelow`.
- (d) Intraday bars with volume (an index has none: M93-Q3, F13).
- (e) `v`: the study says "heavy", no number: the existing default `tod_vol_mult` 1.5 (Q3).

### S7 Turbo Breakup / S8 Turbo Breakdown
- (a) A new session high (low) on 1-minute bars with unusual 1-minute volume against the same time of day.
- (b) `DayExtremeBreak` on 1m (its timeframes include 1m).
- (c) Today (validated on 1m): `DayExtremeBreak("up", $v, $vwap, $skip, $k)`; S8 `"down"`.
- (d) 1-minute bars and 21 prior sessions of 1-minute bars (F12).
- (e) `v`: "unusual", no number: existing default 1.5 (Q3).

### S9 Circuit Watch
- (a) The last price is at or within a set percent of the stock's upper (or lower) price band of the day, as the
  exchange sets it and the broker's quote reports it (the Indian counterpart of a trading halt).
- (b) The quote path (`brokers/*.get_quote`, M90 one call per 500 keys). Nothing models a band today: `Quote`
  (brokers/models.py) has no band field, `InstrumentRecord` neither (F5).
- (c) NEW: `BandDistancePct("upper") <= $near_pct` / `"lower"`.
- (d) The user's broker quote (live, per user). No history: last bar only, not backtestable.
- (e) `near_pct` Not set (the study's halt rule is another market's, not carried).

### S10 RSI Extreme
- (a) 5-minute RSI above a high level or below a low level, with the session's volume so far and the price within a
  set range.
- (b) `RSI(n)` (registry), `@5m` on a 1m base.
- (c) Today (validated on 1m): `(RSI($n)@5m >= $hi OR RSI($n)@5m <= $lo) AND close >= $pmin AND close <= $pmax`;
  PR-1 adds `AND SessionVolume() >= $min_vol` (or turnover `SessionVolume() * close`).
- (d) Intraday bars.
- (e) `hi` **ref 90**, `lo` **ref 10**; `n`: the study gives no period: RSI's registry default 14; the volume and price
  limits are another market's units: Not set.

### S11 Volume Surge
- (a) Volume against the average of the same time of day in previous sessions: this bar, or the session so far.
- (b) `RelVolumeTOD(sessions, mode)`.
- (c) Today (validated): `RelVolumeTOD($sessions, $mode) >= $v`.
- (d) As S2.
- (e) `v`: no number of its own in the study (its 2x belongs to the new-high scanner; shown as a hint only), Not set;
  `sessions` default 20, `mode` bar / cumulative.

### S12 Key Levels
- (a) The close is within a set distance of any chosen level: previous close, previous two sessions' high / low,
  pre-open high / low, the user's moving averages (daily or intraday), the 52-week high / low. The legend names the
  level that is near (M91 rows: one row per condition).
- (b) `PDC()`, `PDH()`, `PDL()`; `high@1d` / `Lag(high, 1)@1d` (closed daily bars: the previous session and the one
  before); `SMA` / `EMA` `@1d`; `Max(high, 250)@1d` / `Min(low, 250)@1d`.
- (c) Today (validated on 5m), one OR branch per chosen level:
  `Abs(close - PDC()) / close * 100 <= $tol_pct OR Abs(close - Lag(high, 1)@1d) / close * 100 <= $tol_pct OR Abs(close - Max(high, 250)@1d) / close * 100 <= $tol_pct ...`;
  PR-1 adds `PreOpenHigh()` / `PreOpenLow()`. The exact @1d semantics (that `Lag(high, 1)@1d` is the session before
  the previous one on an intraday base) is pinned by a fixture in PR-1 (F24).
- (d) Daily bars (250+ for the 52-week levels) and intraday bars.
- (e) `tol_pct`: the study's tolerance is an amount in another currency: Not set; averages **ref** daily 50 and 200
  simple, 9 and 20 exponential (any user choice); 52-week window: the existing 52-week screen's own window.

---

## 4. Strategy templates (Strategies > templates, family "Intraday")

Common to all ten: `style` "Intraday (session-based)", setups `session_bound: true` (an instance never outlives its
session, schema.py `Setup.session_bound`), intraday timeframes only (1m / 3m / 5m / 15m). Signals only: no size, no
order, no price in the signal text (language.py tokens; F1). Stop and target of a deployment come from the user's exit
settings: the invalidation is used as the stop only when he keeps "Where the idea is wrong" (exits.py
`TEMPLATE_INVALIDATION`). Exit options: today NONE / R_MULTIPLE / TEMPLATE_EXIT (the template's exit signal) /
break-even at R (ExitRules); PR-6 adds level exits (F10). An optional "only between" window per template is Not set
(Q7). Runs on PAPER through Adopt (routes.py) and the AI Trading Bot (frontend `paperBotPathFor`), LIVE only as any
strategy, by the user's own broker and settings; AI never places or changes an order (ADR-0006).

### T1 Fallen Angel (long)
- (a) A stock that opened far above the previous close spikes to a new session high, falls sharply, then holds a
  tight base at a support; the signal is the first bar that closes above the base on volume clearly above the base's
  bars. Wrong below the base.
- (b) `DayOpen`, `PDC`, `Max` / `Min` with offsets, `ATR`, template `evidence` (support confluence: near
  `PreOpenLow()`, `SMA(close, 50)`, `SMA(close, 200)@1m`); state setup + trigger like E1 / E3.
- (c) PR-1 (NEW `SessionHigh`). Setup (state): `(DayOpen() - PDC()) / PDC() * 100 >= $gap_pct AND (SessionHigh() - Min(low, $base_n)) >= $drop_atr * ATR(14)[1] AND (Max(high, $base_n) - Min(low, $base_n)) <= $base_atr * ATR(14)[1]`.
  Trigger: `close > Max(high, $base_n)[1] AND volume >= $vol_x * Max(volume, $base_n)[1]`.
  Invalidation: `Min(low, $base_n)[1] - $buf * ATR(14)[1]`. Exit signal: `close < Min(low, $exit_n)[1]`.
- (d) Intraday bars, previous close; pre-open low where the data has it.
- (e) All Not set except: trigger timeframe **ref** 1m or 5m; levels for exits **ref** VWAP, the session high, the
  pre-open high, the previous session's high / low (F10). The study's gap and volume figures are single examples or
  float-based: not carried (F20).

### T2 ABCD (long) / T3 Reverse ABCD (short)
- (a) A first leg makes a new session high (B); the pullback holds a higher low (C) above the leg's start (A); the
  signal is the turn back up from C. Wrong below C. T3 is the mirror (new session low, lower high, turn down).
- (b) `DayExtremeBreak` (B, event), `SwingLow(degree)` / `SwingHigh(degree)` (confirmed swings), event setup with
  `expiry_bars`, `session_bound`.
- (c) PR-1 (NEW `SessionLow` / `SessionHigh`). Setup (event, expiry `$expiry` bars): `DayExtremeBreak("up", $v, true, $skip, $k)`.
  Trigger: `SwingLow($deg) > SessionLow() AND close > SwingLow($deg) AND CrossAbove(close, Max(high, $turn_n)[1])`.
  Invalidation: `SwingLow($deg) - $buf * ATR(14)[1]`. Exit signal: `close < Min(low, $exit_n)[1]` (a new N-bar low).
  C is known only once the swing is confirmed (the degree's threshold): causal by construction, the signal comes
  after C is confirmed, never at C's bar.
- (d) Intraday bars, 21 prior sessions for the volume check.
- (e) Support at C **ref** VWAP or the 9 / 20 EMA (as optional evidence); exit **ref** a new 5-minute low against the
  trade; the study's "half at D, rest at break-even" needs partial exits, which deployments do not have (Q6).

### T4 Bull Flag / T5 Bear Flag
- (a) A strong pole to a new session high, then a tight, shallow consolidation; the signal is the first close above
  the consolidation with volume above the consolidation's. Wrong below the consolidation's low. Only the first N flags
  of a session. T5 is the mirror (short: MIS only, F17).
- (b) `PatternBreakout(side, "flag", v, k)` and `PatternBreakoutRef("base_low", ...)` (breakouts/lines.py, B6;
  template B6 uses them on any timeframe), `DayExtremeBreak`.
- (c) PR-1 (named pole / flag arguments, NEW `SessionCount`). Setup (event, same bar):
  `PatternBreakout("up", "flag", $v, $k, pole=$pole_atr, flag_max=$flag_max, retrace=$retrace) AND high >= SessionHigh() AND SessionCount(PatternBreakout("up", "flag", $v, $k, pole=$pole_atr, flag_max=$flag_max, retrace=$retrace)) <= $max_flags`.
  Invalidation: `PatternBreakoutRef("base_low", "up", "flag", $v, $k, 0)`. Exit signal: `close < Min(low, $exit_n)[1]`.
  (`PatternBreakout(...) AND DayExtremeBreak(...)` validated today on 5m.)
- (d) Intraday bars; B6 needs `LINE_MIN_BARS` (125) bars of warm-up.
- (e) `max_flags` **ref 2**; trigger **ref** a new 1-minute high together with a new 5-minute high; the B6 defaults
  (`pole_min_atr` 3, flag 5-15 bars, retrace 0.5) are ATP's existing defaults, shown as editable.

### T6 Opening Range Breakout
- (a) The first close beyond the high (low) of the session's first N minutes, with volume against the same time of day
  and the close on the same side of the session VWAP; optionally only when the range is small against the daily ATR.
  Wrong back through the other side of the range, its middle, or VWAP (the user chooses).
- (b) `ORBreakout(side, minutes, v, vwap, skip, k)` (B16), `ORHigh` / `ORLow`, `SessionVWAP`; existing screens
  `breakouts.intraday_5m_orb15` / `intraday_15m_orb30` and the Legends ORB card stay as they are.
- (c) Today; compiled as a draft. Setup (event, same bar): `ORBreakout("up", $minutes, $v, true, 0, 0) AND (ORHigh($minutes) - ORLow($minutes)) <= $max_or_atr * ATR(14)@1d`.
  Invalidation variants: `ORLow($minutes)` / `(ORHigh($minutes) + ORLow($minutes)) / 2` / `SessionVWAP()`.
  Constraint `minutes` whole bars of the signal timeframe (as B10).
- (d) Intraday bars, daily ATR.
- (e) `minutes` **ref 5 / 15 / 30** (the order: chosen by the user; the study also names 60); `max_or_atr` **ref 1.0**
  (the range clearly smaller than the daily ATR); exits **ref** the next level, or a new 5-minute low / high against.

### T7 VWAP False Breakout
- (a) Short side: price traded above the session VWAP for a while, then closes back below it; the buying failed. Long
  side (mirror): traded below, then closes back above. Wrong beyond the session extreme since the cross, or VWAP with
  a buffer.
- (b) `Count`, `CrossAbove` / `CrossBelow`, `SessionVWAP`, invalidation `{"extreme": "since_setup"}` (schema
  `SetupExtreme`). Template E5 (reclaim / reject) is the near relative: T7 adds the minimum time on the other side.
- (c) Today; compiled as a draft. Setup (event, same bar), short:
  `Count($bars_n, close > SessionVWAP())[1] >= $bars_min AND CrossBelow(close, SessionVWAP())`; long mirrored.
  Exit signal: `close > SessionVWAP()` (short). Optional context: a rejection at a level (evidence, as T10's levels).
- (d) Intraday bars.
- (e) Time above VWAP **ref** 30-45 minutes (e.g. `bars_n` 9, `bars_min` 6 on 5m); exits **ref** the session low, then
  the next level.

### T8 VWAP Reversal
- (a) Price stretched far from the session VWAP stops making new extremes; the signal is the first turn on the signal
  timeframe (close above a short EMA and above the previous bar's high). Wrong beyond the session extreme. Level exit
  **ref** VWAP first.
- (b) `SessionVWAP`, `Min` / `Max` with offsets, `EMA`, `{"extreme": "since_setup"}`.
- (c) Today; compiled as a draft. Setup (event, expiry `$expiry`), long:
  `(SessionVWAP() - close) >= $ext * ATR(14)[1] AND low > Min(low, $no_new)[1]`. Trigger: `close > EMA(close, $ema_n) AND close > high[1]`.
  Invalidation variants: since-setup extreme / `low[1]` (the previous bar's low). Short mirrored.
- (d) Intraday bars.
- (e) `ema_n` **ref 9** (on 1m); `ext` Not set; exits **ref** VWAP, then the 9 / 20 EMA, then slower averages.

### T9 VWAP MA Trend
- (a) Price holds one side of the session VWAP and a chosen EMA acts as support (resistance); the signal is a pullback
  to the EMA that closes back on the trend side after the EMA has held for N bars. Wrong a buffer beyond the EMA; exit
  on a close beyond the EMA.
- (b) `SessionVWAP`, `EMA`, `Count`, exit signal (TEMPLATE_EXIT).
- (c) Today; compiled as a draft. Setup (event, same bar), long:
  `close > SessionVWAP() AND low <= EMA(close, $ma_n) AND close > EMA(close, $ma_n) AND Count($held_n, close > EMA(close, $ma_n)) >= $held_n`.
  Invalidation: `EMA(close, $ma_n) - $buf * ATR(14)[1]`. Exit signal: `close < EMA(close, $ma_n)`.
- (d) Intraday bars.
- (e) `ma_n` **ref 9 or 20** (20 stronger); stop buffer **ref** an amount in another currency: Not set as `buf` (ATRs).

### T10 Level Rejection
- (a) Price is turned away from a daily level more than once (short: from below; long: support holds from above); the
  signal is the confirmation on the signal timeframe (a close beyond the previous bar). Wrong beyond the level or the
  nearest short EMA, the user chooses.
- (b) Levels: `PDH()`, `PDL()`, `PDC()`, `Lag(high, 1)@1d`, `Max(high, 250)@1d`, `SMA(close, n)@1d`; `Count`; `EMA`;
  variants per level (`SetupSwitch`).
- (c) Today; compiled as a draft. Setup (event, expiry `$expiry`), short at the previous high:
  `Count($look, high >= PDH() - $tol * ATR(14)[1] AND close < PDH())[1] >= $touches`. Trigger: `close < low[1]`.
  Invalidation variants: `PDH() + $buf * ATR(14)[1]` / `EMA(close, $ema_n) + $buf * ATR(14)[1]`. With PR-1 the touch
  count can also be `SessionCount(...)` (this session) instead of the last `look` bars - a choice in the settings.
- (d) Daily and intraday bars.
- (e) `touches` **ref 2**; `ema_n` **ref 9** (on 1m); tolerance Not set (another currency's amount).

---

## 5. Risk tools (user switches on; OFF by default)

### R1 Position-size calculator
- (a) Account x risk % (or a rupee amount) / stop distance = quantity, in whole lots (a cash share's lot is one share);
  less than one lot places nothing and says so (never rounded up).
- (b) EXISTS as the engine rule: Risk limits "Risk-based sizing" (`risk_engine/risk_limits.py` CHECKS `risk_sizing`:
  `risk_per_trade_pct` / `risk_per_trade_rupees`), applied by `RiskManager.evaluate` (risk_manager.py) and chosen in
  Settings > Trade settings > Position size (`execution/position_size.py` `quantity_for`, mode Risk-based). Not set
  until he saves it (DEF-0 / M68).
- (c) NEW: a calculator screen (Trade settings > Position size, and on the template page) that calls the same sizing
  function with his entry / stop / account and shows the quantity - it changes nothing until he saves.
- (d) His own capital and risk settings; the instrument's lot size.
- (e) Risk per trade **ref** 1 % (the study: at most 2 %); shown as a hint, not pre-filled (Q9).

### R2 Daily max-loss lock
- (a) Once today's loss reaches his own limit, no new entry today; exits always work.
- (b) EXISTS: Risk limits "Daily loss limit" (`daily_loss`: `max_daily_loss_pct` / `daily_loss_rupees`), checked in
  `RiskManager.evaluate` on entries only (`daily_loss_block`); today's P&L = closed today + open unrealised
  (`trading/persistence.py`, the Indian trading day). Exits never pass through it (ADR-0004).
- (c) No new engine code. M94 adds a test on the template PAPER path: the lock refuses a template entry and the open
  template position still exits.
- (d) His own trades (per organisation, F7).
- (e) No reference value (the study's loss rule is monthly, not part of the order).

### R3 Profit give-back stop
- (a) After the day's P&L has reached a peak, once it falls back by his own % of that peak, no new entry for the rest
  of the trading day; exits always work.
- (b) The Risk limits framework (a new `Check` in CHECKS with its field, the switch, `plan_save`, the block text, the
  log), `TradingDayState`, the drawdown ladder's peak idea (guardian.py: the peak never moves down).
- (c) NEW: `profit_giveback` check (field `giveback_pct`), and a stored day peak (realised + open P&L, the higher of
  the stored peak and the current value, per organisation and mode, per trading day) - a migration. Entries only.
- (d) His own trades.
- (e) `giveback_pct` **ref 30** (the study: do not give back more than 30 % of the profit made earlier in the day);
  Not set until saved.

---

## 6. India rules I1-I5 against the code

| Rule | Today | M94 work |
|---|---|---|
| I1 session 09:15-15:30 IST | the exchange calendar (`market_data/calendar.EXCHANGE_SESSIONS`), session.py; no constants | none |
| I1 pre-open 09:00-09:08 | pre-open bars are read "where the data has them" (SessionMap `pre`, DayLevelBreak "premarket"); availability per broker not shown by the code (F4) | PreOpen* functions; Q4 |
| I1 intraday product (MIS) for shorts | holding is the user's (M68); the worker skips a SWING short in cash (trading_worker.py "cash equity cannot be held short overnight") | none; a template short on cash needs his INTRADAY holding (F17) |
| I1 price bands | none (F5) | PR-3 |
| I1 5-level depth | adapters read only the best bid / ask (`depth.buy[0]`) | none in M94 (F6) |
| I1 liquidity filter | `IsFnO()`; turnover via `SMA(close * volume, 20)@1d` | used in S1 / templates as optional settings |
| I2 every number editable | template `params`, screen `$params`; TS-4: template values are suggestions | the "reference values - edit before use" label (F8) |
| I3 how it works / scanner / overlay / PAPER | Adopt + AI Trading Bot exist; no how-it-works page, no template overlay | PR-6 |
| I4 own data | `live_per_user` tier, M90 store-first + paced broker queue | none |
| I5 footer | a similar constant exists (F14) | one constant for M94 surfaces |

---

## 7. Findings

- **F1 Wording (CLAUDE.md §3).** Templates already avoid buy / sell / stop / target in what they say: parts are
  `setup` / `trigger` / `invalidation` / `exit_signal`; the settings say "The idea is wrong beyond"; signal text is fixed
  tokens (language.py) and `language.guard` refuses those words, digits and prices; the screener taxonomy lint refuses
  buy / sell / target / best in names and definitions (taxonomy.py `SCREENER_WORDS`). "Long" / "Short" / "Long and
  short" are already UI words (StrategyFlowV2 result step; template `sides_default`). The words "stop" and "target"
  appear only in the user's OWN exit settings (frontend strategyTemplates/exits.ts: "Where the idea is wrong", "No fixed
  target"). M94 follows that: the order's "entry trigger" is shown as "Trigger", "stop rule" as "The idea is wrong
  beyond", "exit options" as the existing Exits settings. New tokens describe structure only ("the close broke above
  the opening range", "price came back below VWAP after holding above it").
- **F2 "Group Intraday" does not exist yet.** The screen library has two axes (method x setup) plus tags; "intraday"
  is a `timeframe_family` tag and 10 pre-built screens already carry it. Templates are grouped by family A-G; the schema
  pins `family: Literal["A".."G"]` and the id pattern `^[A-G][0-9]{1,2}$` (schema.py), routes.py `FAMILY_NAMES` /
  `FAMILY_FLAGS`. A family "H" = "Intraday" is a schema change (not a database change). Q1.
- **F3 Overlap with what exists.** ORB, first-hour, previous-day, new-high / low and gap screens exist
  (`breakouts.intraday_*`, `breakouts_bk4.intraday_5m_gap_and_go`, the Legends ORB card), and templates B6 (flag), B10
  (gap and go), C6 (previous-day break and retest), E5 (VWAP reclaim / reject). M94 adds new ids and reuses their
  functions; nothing existing is changed or removed (CLAUDE.md §8). The 48 screens are under audit (SCR-AUDIT, M93):
  M94 does not touch them.
- **F4 Pre-open data is not shown to exist.** The code handles pre-open bars when they are in the data, but nothing in
  the repository shows that a user's broker candles include 09:00-09:08 bars; this is not known (OWNER_RULES A.3). The
  gap is computed from the session's opening price (for NSE stocks in the pre-open session, the opening price is the one the pre-open session discovers) and the
  pre-open volume / high / low conditions say "not available" when the data has no pre-open bars. Q4.
- **F5 Price bands: no data in ATP.** No band field in `Quote` or `InstrumentRecord`; the quote parsers keep only
  OHLC, volume, OI and the best bid / ask. Circuit Watch needs band fields from each broker's quote (Q5), is answered
  for the last bar only, and cannot be backtested or used by a template (no band history is stored). ATP never
  computes a band itself.
- **F6 Market depth is not used by any of the 25 items.** The adapters receive the depth arrays and keep level 1. The
  order's "5-level depth instead of Level 2" has no consumer among the scanners, templates or risk tools; showing 5
  levels (e.g. in the Terminal) is a separate item, not built here (Q11).
- **F7 Risk tools: two of three exist.** R1 sizing and R2 daily loss are built, off until set, entries only. Today's
  P&L and the trades count are per organisation (`tenant_id`), so in a multi-user organisation the lock is
  organisation-wide; in a one-user organisation it is his own. R3 is new and needs a stored day peak (migration).
- **F8 Reference values vs M68 / DEF-0.** Template parameter defaults are allowed as the strategy's own suggestion (M68
  TS-4; scripts/check_trading_literals.py allow-list concept): they need the visible label "reference values - edit
  before use". Risk limits stay Not set (DEF-0): the risk tools show reference values as hints only. Study values in
  another market's units (share counts, a currency amount, float) do not carry to NSE and are Not set (Q2).
- **F9 Clock framing (CLAUDE.md §5, OWNER_RULES C.4).** Those rules bind Abhi's own method (the Trade reader port);
  generic templates may use indicator parameters as settings. M94 I1 / I2 make session and time windows operational
  settings. The functions are anchored to the exchange session (as B16-B18, E5 already are), not to a clock; the study's
  time-of-day map becomes only an optional "only between" window per template, Not set (Q7). `TimeOfDay` is behind
  `screenql_amw_functions`.
- **F10 Exit options.** Deployments have `target_rule` NONE / R_MULTIPLE / TEMPLATE_EXIT / WAVE_PROJECTION and
  ExitRules (trailing %, break-even at R, time exit). The study's exits are mostly levels (VWAP, the session high /
  low, the previous close, the pre-open levels) and "a new N-bar low / high against" (expressible today as the
  template's exit signal). Level exits need a new `target_rule` (TEMPLATE_LEVEL: one level the template offers, read on
  the signal bar) and a column on the deployment row (migration). Partial exits on a deployment do not exist (Q6).
- **F11 Flags.** `strategy_templates` is "unfinished" in flag_catalog.py; its production value is not known from the
  code (A.2). M94 templates also read `screener_breakouts`, `screener_breakouts_bk4` (PatternBreakout, GapType) and
  `screenql_amw_functions` (SwingLabel, TimeOfDay); a template whose function is off shows "available: false" (routes.py
  `_card`). New flag `intraday_pack` (off) gates the new functions, the library source and family H.
- **F12 Data volume.** `RelVolumeTOD` needs `tod_sessions` + 1 = 21 sessions of the same timeframe; on 1m that is about
  21 x 375 bars per symbol. From a user's broker this is heavy for a long list (M90's paced queue applies). Default
  universe: the user's list or F&O stocks; the result says how many symbols were checked.
- **F13 Index volume.** Volume-reading screens on an index are "Not checked: volume not available" (M93-Q3a); the M94
  scanners are for stocks.
- **F14 Footer.** ATP already has "Information and education only, not advice. Every trading decision is yours."
  (study/snapshot.py, ai/copilot_chat.py). The order gives "Information and education only, not investment advice." -
  used exactly, as one constant, on M94 surfaces; the older line stays where it is (Q10).
- **F15 "How it works" + schematic.** No template detail page or schematic exists; the card shows `idea` (300
  characters). New: one screen per template (bullets: setup, trigger, wrong beyond, exit options; details folded) with
  an inline SVG drawn from the template's own synthetic test series, evaluated by the engine - so the drawing is our
  own and matches the tests; no third-party chart.
- **F16 Overlay (M91 C1 / C2).** `watch_screen.screen_marks` is the one shared evaluation, but its routes take the
  user's own SAVED screen id. The scanners' overlay works once he saves the library screen; the template overlay (setup
  met / not met, trigger markers, the invalidation line) needs a route that takes a template + settings and calls the
  same `screen_marks` on each compiled part, with the test that list and chart agree.
- **F17 Shorts.** A short template on cash shares only trades with his INTRADAY holding; with SWING the worker skips it
  with its reason. Nothing new.
- **F18 Liquidity instead of float.** No float data exists; `IsFnO()` and a turnover setting replace it (I1).
- **F19 Order of work.** The owner's line: finish the current work first; the roadmap row M94 sits on branch
  `claude/inbox-10nov-m94` (not merged yet). R2: at most 5 build items in progress.
- **F20 Single examples are not rules.** Numbers that appear once as an example in the study (a gap size, a volume on
  one day) are not reference values; only stated criteria are.
- **F21 Bear Flag.** The study describes but does not use it itself; built as the mirror because the order lists it.
- **F22 Top / Bottom Reversal.** The study names these scanners without criteria; the order's own definition is used,
  parameters Not set.
- **F23 Last-bar-only patterns.** `PatternBullish` / `PatternBearish` are last-bar only (`timeframed=False`); series
  use (templates, chart markers) needs `Pattern(name)`.
- **F24 Validation evidence.** The drafts were checked by the parser + validator and five templates compiled; the
  meaning of `@1d` reads on an intraday base (closed daily bars only) is pinned by fixtures in PR-1, not assumed.

---

## 8. PR plan (build order; one `claude/m94-*` branch and draft PR each; merge on green CI)

| PR | Content | Tests | Migration |
|---|---|---|---|
| PR-1 `m94-functions` | SessionHigh, SessionLow, SessionVolume, SessionCount, PreOpenHigh / Low / Volume; PatternBreakout(Ref) named pole / flag arguments; flag `intraday_pack` | positive / negative fixture each; cut-bar causality; existing B6 calls byte-identical; `@1d` level fixtures (F24) | no |
| PR-2 `m94-scanners` | S1-S8, S10-S12 as library source `intraday` (taxonomy filing, `register_source(..., flag="intraday_pack")`), the gallery "Intraday" filter, footer constant | positive / negative fixture per scanner; taxonomy coverage + advice-word lint; mirror equality for S3 / S5 / S8 | no |
| PR-3 `m94-circuit` | Quote band fields (first brokers per Q5), BandDistancePct (last bar only), S9 | adapter parsing on synthetic payloads; positive / negative; "not available" path | no |
| PR-4 `m94-templates-a` | family H "Intraday" (schema, FAMILY_NAMES, FAMILY_FLAGS), T6-T10, new tokens, the reference-values label | trigger / invalidation on synthetic bar series per template; parity scan = backtest = live (parity.py); language guard | no |
| PR-5 `m94-templates-b` | T1-T5 (need PR-1) | as PR-4 | no |
| PR-6 `m94-pages` | "how it works" page + schematic, template overlay route on `screen_marks`, level exits (TEMPLATE_LEVEL) | list = chart agreement test; level exit parity; e2e Adopt -> PAPER | yes (deployment `target_level` column) |
| PR-7 `m94-risk` | R1 calculator screen, R2 template-path test, R3 give-back check + day peak | lock refuses new entries and never an exit (R2, R3); sizing equals RiskManager's | yes (day peak) |

All PRs: no change to existing screens / templates; English only; check_english_only, check_public_repo,
check_trading_literals. Deploy line with each PR; migrations only in PR-6 / PR-7 (any time until practice starts,
then after 15:30 IST - DECISIONS 25 Oct).

---

## 9. Open questions (defaults; also docs/OPEN_QUESTIONS.md M94-Q1...)

| ID | Question | Default |
|---|---|---|
| M94-Q1 | Where is the "Intraday" group? | Library: a new source `intraday` and an "Intraday" filter chip in the gallery; each screen still filed on its real method / setup. Templates: a new family H "Intraday". Both behind `intraday_pack` |
| M94-Q2 | Study values in another market's units (share counts, a currency amount, float) | Not set; the run asks; the help shows only the unit-free study hint |
| M94-Q3 | A parameter the study gives no number for | The existing ATP function default where one exists (e.g. `tod_vol_mult` 1.5), shown as editable; else Not set |
| M94-Q4 | Pre-open data | Gap from the session's opening price vs the previous close; pre-open volume / high / low only where the data has pre-open bars, else "not available" - never a false match |
| M94-Q5 | Circuit Watch brokers | Upstox and Zerodha quote band fields first; other brokers say "not available from your broker" until added |
| M94-Q6 | Level exits / partial exits | Build TEMPLATE_LEVEL (one level per deployment) in PR-6; partial exits on deployments not built in M94 |
| M94-Q7 | Time windows | An optional "only between" window per template, Not set (the whole session); the study's segment is a hint |
| M94-Q8 | Give-back peak | The day's peak of closed + open P&L as ATP sees it on each worker cycle and entry check; no minimum peak (not stated); resets next trading day |
| M94-Q9 | Risk calculator reference | Not pre-filled; "reference 1 % (study: at most 2 %) - edit before use" as a hint he can tap; nothing saved without Save |
| M94-Q10 | Footer text | The order's exact sentence on M94 surfaces only; the existing line stays elsewhere |
| M94-Q11 | 5-level depth | Not in M94 (no item consumes it); a separate roadmap item if wanted |
| M94-Q12 | Short counterparts in the library | Filed as their own ids (S3, S5, S8) so the list shows them by name; text generated by the mirror rule and tested equal |

---

## 10. Proof plan (matches the order)

ONE comparison page for the milestone (CLAUDE.md §6), dark theme, phone 390 + desktop:
1. The library list: the "Intraday" filter with the 12 scanners (390 + desktop) and Strategies > templates with the
   10 "Intraday" templates.
2. One template page: "how it works" with its schematic, the settings with the reference-values label, the footer.
3. One scanner run on real data (the user's own session or stored intraday) with one matched stock's chart and the
   overlay legend (met / not met per condition, M91 C1).
4. One PAPER trade of one template: signal -> paper order -> position -> exit, on the real site.
Tests (CI): each scanner positive / negative on fixtures; each template's trigger and invalidation on a synthetic bar
series; risk tools: the lock refuses new entries and never an exit. Report to the owner in Marathi, at most 15 lines.

---

## 11. M99 P2 upgrade (flag `intraday_pack`, OFF)

- **Key-level grade** - ScreenQL `LevelGrade(level, what, near, look, touches, recent, tier, step)`
  (app/screener/level_grade.py): information only, 0-5 points - touches of the level's band, a recent visit, the level's
  kind (1 previous close, 2 previous high / low, 3 pre-open and session levels), a round number (the user's step, or
  `level_strength.round_distance`'s steps of the symbol), another key level in the band. `what` returns one part.
  Scanner S13 "Graded Key Levels" (`intraday.graded_levels`): band and points Not set (the study's tolerance is another
  currency's amount).
- **Trade-management options** for the H templates (app/strategy_templates/management.py; `TemplateExits.management`,
  migration b4d6f8a0c2e3): trail by swing / ATR from the best price / a moving average, exit after N bars with no new
  extreme, exit after N bars in the trade. Every option OFF, every number the user's, stops only tighten, no exit is ever
  blocked; one `management_step` for the backtest engine and the worker (read on a bar close, acted on at the next price).
  A partial exit is the ExitRules scale-out (M99 P7), not built twice. OPEN_QUESTIONS M99-P2-Q1 / Q2.
- **Futures / options bots (follow-up PR)**: the options are read on the bars the bot READS (the underlying); the result
  acts on the traded instrument through the existing paths - a future's stop = the new underlying stop plus the entry's
  transplant offset (`contract_execution.underlying_stop_view` / `underlying_stop_fields`), a single option's underlying
  stop moves and the option exits when the underlying crosses it (`check_contract_exit`; no premium stop made up); the
  options backtest engine calls the same `management_step`. Refused in plain words: a multi-leg structure (its exits are
  its credit levels), and the trail on a single option with the premium stop only (no underlying stop to move).

