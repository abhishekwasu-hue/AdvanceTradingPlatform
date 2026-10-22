From Abhi — WAVE / CORRECTION TEMPLATES (family G of the strategy template library), design + coding spec (English)

Goal: every trader on ATP can trade waves and corrections HIS way. That covers:
- with the higher trend, or the counter-trend C leg, or both;
- entry styles: aggressive, confirmation, breakout, or breakout + retest;
- filters: Fibonacci, RSI, volume, the higher timeframe;
- his own timeframes.
Nothing about style is fixed in code. Every choice is a template parameter with a default and bounds.

Templates ONLY generate signals. Execution is the user's deployment (strike, spread, lots, exits).

This family extends ATP_SESSION_STRATEGY_TEMPLATES.md. Its section 0 (R1–R10), section 3 (S1–S11), section 5 (exits as
settings), section 6 (signal text) and section 8 (tests) apply here unchanged. Where this file and that one differ,
that file's section 0 wins.

Repo rule: this is a GENERIC, public method (classic wave principle + structure). Abhi's own Trade method, its private
settings and charts stay in atp-private. His "model C" is just one configuration of G3–G7 that a user can pick.

STEP 1 — Before code: an independent review of this spec against the real code by a separate agent (CLAUDE.md §6). Fix
the findings, then build. Record this order in docs/owner-orders (INBOX verbatim, a DECISIONS line, README row 14).

READ FIRST (reuse, don't rebuild)
- backend/app/price_action/daily_trend/legs.py:
  - read_counter: the recursive wave reading (impulse 5-3-5-3-5, diagonal, zigzag 5-3-5, flat 3-3-5, triangle,
    combination, 3-wave, single). It uses no ratios.
  - reduce_waves.
  - The impulse / corrective leg kind.
- backend/app/price_action/legs.py:
  - the impulse vs corrective leg classifier;
  - zigzag_pivots;
  - the retracement depth as a continuous feature (no Fibonacci gate).
- backend/app/price_action/causal_swings.py, breaks.py, daily_trend/fold.py: causal swings, BOS / CHoCH, trend phases.
- The ScreenQL registry (backend/app/screener/registry.py): SwingHigh / SwingLow / SwingLabel / SwingBreakType,
  DowTrend, ReversalAt, Pattern, RsiDivergence, RelVolume, NearZone / ZoneState, TrendlineBreakout (line / channel),
  PatternBreakout (triangle / flag), RetestEntry.

1. NEW SERIES FUNCTIONS (R1: true per-bar series; R2: level companions; tests for each)
W1 WaveState(degree, tf):
- A categorical series. At each bar it gives the wave context AS KNOWN AT THAT BAR, from confirmed swings only:
  - IMP_1 … IMP_5 (in an impulse);
  - COR_A / COR_B / COR_C (in a correction);
  - plus a type: ZIGZAG / FLAT / EXPANDED_FLAT / TRIANGLE / DOUBLE (W-X-Y) / UNCLEAR.
- It is built on read_counter. It never rewrites an earlier bar's value. A count that changes later changes only from
  that bar on (S8: no repainting), with a test.
- "UNCLEAR" is a valid state, and templates do not fire on it.
- Alternate count: WaveAlt(degree, tf) gives the second reading. It is evidence only, shown on the chart.
W2 Level companions (NaN when not applicable):
- WaveOrigin: the start of the current impulse, or of A.
- WaveEnd(wave): the end of wave 1 / 2 / 3 / 4 / A / B as known.
- WaveInvalidation: the hard-rule level of the current count:
  - wave 2 never beyond the start of wave 1;
  - wave 3 never the shortest;
  - wave 4 never into wave 1's range, except in a diagonal;
  - B (zigzag) never beyond the start of A;
  - a flat / expanded flat B is allowed beyond it.
  The rule used depends on the count type, with a test per type.
W3 Ratio companions (user parameters only, never hard-coded):
- WaveRetrace(ratio): the price at a retracement ratio of the previous wave.
- WaveProjection(base_wave, ratio): for example C = A × ratio, or 3 = 1 × ratio.
- Used as optional filters / evidence / targets ONLY when the user turns them on.
W4 Break of the correction channel:
- Reuse TrendlineBreakout(mode = channel) / channels on the corrective leg (B, wave 2, wave 4).
- No new channel code, unless the review finds a gap.

2. THE COMMON PARAMETERS (every G template; the settings form generated from the schema)
- Timeframes:
  - signal_tf (the trigger);
  - wave_tf (where the waves are counted, the "minor trend");
  - higher_tf (the main trend).
  - Constraint: higher_tf ≥ wave_tf ≥ signal_tf.
- Trend relation (enum):
  - with_higher_trend (default);
  - counter_trend_allowed (the C leg against the higher trend);
  - any.
- Entry style (enum, the user's choice):
  - aggressive: the first touch of the area / zone at the end of the corrective wave;
  - candle_confirmation: Pattern / ReversalAt at the area;
  - lower_tf_choch: SwingBreakType = CHOCH on signal_tf;
  - breakout: a close beyond the corrective channel / triangle edge / the previous wave's end, by k ATR;
  - breakout_retest: RetestEntry over that break.
  Breakout styles are allowed for ATP users: it is their choice.
- Area source (enum):
  - market zones (NearZone, ZoneState FRESH / TESTED);
  - swing zones;
  - none.
- Optional filters (each on / off, with its own params):
  - a Fibonacci zone (from–to ratios) for the end of the corrective wave;
  - RSI divergence (regular / hidden) at the wave end;
  - volume (corrective wave on lower RelVolume, trigger on higher);
  - higher-timeframe trend agreement;
  - the minimum wave-count clarity (exclude UNCLEAR, exclude when the alternate count disagrees).
- Window (enum): only the first opportunity per wave / every pullback inside the wave.
- Invalidation (enum):
  - WaveInvalidation (default);
  - the corrective wave's extreme;
  - the area's far side;
  - the lower / higher of both.
- exit_signal (enum):
  - none;
  - the opposite CHoCH on wave_tf;
  - WaveState leaves the traded wave;
  - the projection reached.
- Sides: long / short / both.

3. THE CATALOGUE (L = long; the short side is the mirror)
G1 Wave 3 entry — setup: IMP_2 in progress after a clear wave 1 (impulsive). Trigger: the end of wave 2 per the entry
   style. Invalidation: the start of wave 1. The most-used wave trade.
G2 Wave 5 entry — setup: IMP_4 not overlapping wave 1. Trigger: the end of wave 4 per the entry style. Invalidation: the
   wave 4 rule.
G3 C leg after B (counter-trend allowed) — setup: COR_B after an A that broke the wave_tf structure (CHoCH). Trigger:
   the end of B per the entry style. Invalidation: the start of A (zigzag), or the B rule of the counted type.
G4 C leg: B channel break — the corrective channel of B broken in the C direction. Entry style breakout or
   breakout_retest (the user's choice).
G5 C leg: B triangle thrust — B is a triangle. Trigger: the break of the triangle edge (PatternBreakout triangle),
   breakout or retest.
G6 C leg: break of A's end — a close beyond the end of A (C confirmed). Breakout style, or its retest twin (the S/R flip
   retest of A's end).
G7 Expanded-flat sweep — B goes beyond the start of A and comes back. Trigger: the reclaim (a sweep and reclaim at
   WaveOrigin). Invalidation: the B extreme.
G8 End of correction → trend resumes — the correction (zigzag / flat / triangle / double) is complete in the higher-trend
   direction's favour. Trigger:
   - the break of C's channel (breakout or retest);
   - or a CHoCH on signal_tf at the area where C ended.
   Invalidation: the C extreme.
G9 End of impulse (wave 5) reversal — IMP_5 complete, optional regular RSI divergence. Trigger: the break of the wave
   4 / 5 line or a CHoCH. Mostly for counter-trend users; the default sides follow the trend relation param.
G10 Ending diagonal break — a diagonal (read_counter) as wave 5 or C. Trigger: the break of its lower / upper line,
   breakout or retest.
G11 Double correction (W-X-Y) — after a complete first correction, X is treated like B. The same as G3 / G8, with the
   counted type DOUBLE.
G12 Options seller wave pack (family F style) — maps G1–G11 to "bullish / bearish continuation" or "range edge" labels
   for the user's spread settings. No strike, premium or lot.

4. TARGETS (user settings; extends section 5 of the templates spec)
- Add target_rule WAVE_PROJECTION (the user picks base_wave and ratio; NaN ⇒ no target, never a refusal), with tests.
- NEXT_AREA / R_MULTIPLE / TEMPLATE_EXIT / CREDIT_PCT as already specified.
- No template chooses a target by itself.

5. SIGNAL TEXT (policy 11l; language guard test)
Examples:
- "Wave B pullback ended at an area on the 1-hour chart; the minor trend resumed (counter-trend C leg)."
- "Wave 2 ended inside its zone; lower-timeframe structure turned up."
No prices, no buy / sell / target / stop words. The invalidation is in words ("beyond the start of wave A"). The count's
clarity and the alternate count are listed as evidence.

6. UI (proactive; what TradingView / brokers offer, made simpler)
- Chart "Waves" layer (toggle) on the user's own charts:
  - the auto count with labels;
  - the alternate count (dashed);
  - the invalidation line;
  - the optional projection band (when the user turned it on).
  A plain-language box ("Correction in progress: wave B. This view is wrong if price goes beyond …" in words). Phone
  layout; light / dark.
- Wave scans: "in wave 2 / wave 4 pullback", "wave B pullback near an area", "correction complete", "C leg started". Run
  as scan + alerts, through ScreenQL (S11 parity).
- Templates page: a "Waves" group (G1–G12). Each card shows:
  - difficulty;
  - "works best when …";
  - an anonymised example chart older than three months.
- A page tour, "Waves in 2 minutes", for beginners.
- Compare: the same symbol / period with different entry styles side by side (aggressive vs confirmation vs breakout vs
  retest). This lets every user find his own style from the data, with no performance claims.

7. TESTS (section 8 of the templates spec, plus)
- WaveState causality:
  - a truncation test over many t;
  - a later count change never alters earlier values;
  - UNCLEAR blocks signals.
- Hard rules per count type, including flat / expanded flat B and diagonals.
- Each G template: must-fire / must-not-fire fixtures for each entry style.
- Mirror.
- Parity scan = both backtest engines = the worker.
- Ratios only from params: a test that no ratio literal exists in the G recipes.
- Performance: WaveState is computed incrementally (R3), within the perf budget.

8. ORDER
1. The independent review (STEP 1).
2. W1–W4 + the parity harness.
3. G3, G8, G1 (the most used), then G4–G7, G2, G9–G11, then G12.
4. The UI (layer, scans, templates group, tour, compare).
Each step: tests, self-review of every diff, before / after screenshots for UI, PAPER only, the deploy line for Abhi
(say whether a migration is needed).
