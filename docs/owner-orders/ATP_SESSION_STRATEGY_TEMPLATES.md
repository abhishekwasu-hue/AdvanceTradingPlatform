From Abhi — STRATEGY TEMPLATE LIBRARY (signal recipes), full design + coding spec (English)

Goal: a large library of ready strategy templates (price action, breakout, breakout + retest, indicator-based: Supertrend, moving averages, RSI, MACD, ADX, Bollinger, VWAP …) that any user can open, set up from the dashboard (timeframe, indicator periods, filters, sides), backtest, and run in automation (PAPER now). Templates ONLY generate signals; every execution choice comes from the user's existing deployment settings. Built carefully: the semantics in section 3 are binding, so the logic has no gaps.

Related: ATP_HANDOVER_PACK.md 11k (idea library + compliance), 11l (signal language policy); ATP_SESSION_IDEA_LIBRARY.md (the price-action templates T1–T8 become family A here: same ids, same spec).

READ FIRST (what already exists; reuse it, don't rebuild)
- ScreenQL (app/screener/registry.py + runtime): EMA, SMA, RSI, ADX, PlusDI, MinusDI, ATR, Supertrend, BBUpper / BBMid / BBLower, VWAP, SessionVWAP, CrossAbove / CrossBelow, Lag, Max / Min, SwingHigh / SwingLow / SwingDirection / SwingLabel, DowTrend, ReversalAt, RealBreak, RsiDivergence, Pattern, NearZone / ZoneRole / ZoneState / ZoneStrength, RangeBreakout, SwingBreakout, SwingBreakType, NRBreakout, NewHighBreakout, RangeExpansion, VolumeBreakout, RelVolume, ATRPercentile, DonchianBreakout, SqueezeBreakout, MABreakout, RegimeBreakout, TrendlineBreakout (line / channel), PatternBreakout (flag / pennant / triangle), VCPBreakout, GapBreakout, ORBreakout, RetestEntry, and more. Bind every template to the REAL signatures in the registry (check each argument), and never assume one.
- Breakout family rules (docs/design/SCREENER.md U6 BK1–BK5): closed bars, causal, one signal per leg, mirrored down side, the context ladder, the quality overlay, fitness.
- Deployments (StrategyDeploymentRecord) already hold every execution choice: instrument_kind (underlying / option / future), option_position, expiry_rule, strike_rule / offset / filters, option_strategy (incl. spreads, CUSTOM legs), spread_width, target_credit_pct / stop_credit_pct, premium_stop_pct, max_lots, exit_rules (trailing, break-even, time exits), regime_filter, oi_gates, holding, order_style, entry_mode (AUTO / APPROVAL), routing. Templates never duplicate these.
- The execution core / scan_then path (intents → notification or PAPER proposal → approval).
- Strategy DSL v1 (app/strategy_engine/declarative.py): it embeds stop_loss_atr_mult / target_rr / min_rr. See section 5 for how templates handle exits instead.

0. BINDING CORRECTIONS FROM THE INDEPENDENT CODE REVIEW (where this section and any later text differ, THIS SECTION WINS)
R1 Series functions only. Templates may use only functions that are true per-bar series. Never names in the registry's LAST_BAR_ONLY set (Trend, StructureEvent, PatternBullish / PatternBearish, NearSupport / NearResistance, …): read as a series they copy the last answer backwards (look-ahead) and they take no @timeframe. Use Pattern(name, confirm) or ReversalAt(level, dir) instead of PatternBullish; DowTrend(degree) == "RANGE" instead of Trend; SwingBreakType instead of StructureEvent. The recipe validator refuses LAST_BAR_ONLY names, with a test.
R2 Level companions. Breakout functions return booleans only; their prices stay inside evaluate_screen. Add series companions that read the same columns (NaN off the signal bars), with causal tests: BreakoutLevel(screen, side, …), BreakoutFailLevel, BreakoutBaseHigh, BreakoutBaseLow, BreakoutStopRef. Every breakout / retest template invalidation names one of them. Reuse the existing stop_mode (level_atr / base / midpoint) and stop_atr_mult (breakouts/params.py, quality.py) instead of a new "buffer".
R3 No persisted state. The setup / trigger state machine (S4) is a PURE FUNCTION OF BARS. The recipe computes its whole signal series once, causally, and the adapter's analyze() reads the value at the decision bar. Being in a position never changes the setup state. Compute the series incrementally or once per run, never the full series on every bar (the backtest's WindowCursor passes the whole prefix each call, which would cost the square of the bar count). Parity (S11 / §8) compares SIGNAL SERIES, not trades.
R4 Stops / targets vs the existing engine.
 - On UNDERLYING / FUTURE deployments, stop_source must be TEMPLATE_INVALIDATION (RiskManager refuses a signal without stop_loss and sizes from entry − stop).
 - Make determine_exit_price tolerate target1 = None (today it raises a TypeError), with a test.
 - stop_source is NOT exclusive: TEMPLATE_INVALIDATION is the underlying-level stop. premium_stop_pct / stop_credit_pct stay as independent safety nets, and the first one hit exits (ADR-0004). Trailing / break-even on OPTION trades act on the premium (as today).
 - A precedence table per instrument_kind × option_strategy is written into docs and covered by tests.
R5 Exit-signal plumbing (TEMPLATE_EXIT / S10). Add an exit-signal series to the recipe adapter. BOTH backtest engines (backtest/engine.py and options_engine.py) check it on each bar close of an open trade, and so does the position monitor: exit at the next price, reason "Template exit". This is part of step 1.
R6 History / parity. The deployment fetch = the recipe's min_bars (incl. degree_min_bars and the context frames) × a safety factor; today's intraday fetch (5 days) is too short for swings / RetestEntry. Parity runs the scan, BOTH backtest engines and the worker on the same fixed TRAILING window W (a left-anchored truncation test alone is not enough). Swing templates of degree ≥ 2 carry the existing "fetch-dependent" note.
R7 Retest twins (family C). Extend RetestEntry to forward the base screen's own params (k, n, degree, mode), so that a C template uses its B template's settings; add of = "channel"; trendline / channel retests use the line's value AT the retest bar (projected), not the value frozen at the breakout bar. RetestEntry alone is the setup + trigger; instance = the breakout bar + level id.
R8 Instances. A conflict bar (S5) consumes both sides' instances. Per side only the NEWEST instance is active; a new setup event replaces the old one. Expiry counts signal-timeframe bars from the setup bar. A trigger on the setup bar itself is allowed only when the template says same_bar: true.
R9 Determinism inputs. The params hash includes the pa_settings version (the retest reversal reads global pa_settings and requires reversal_mode = composite). Templates use the ScreenQL runtime's alignment only, never the DSL's own higher-timeframe resample.
R10 NEXT_AREA target: market zones are stored snapshots behind a flag; where no snapshot exists (e.g. older backtest periods) → no target (not a refusal), with a test.

1. WHAT A TEMPLATE IS
A template is a versioned, white-box SIGNAL RECIPE (JSON, stored like custom strategies with immutable versions):
- id, name, family, one-line idea, difficulty (Beginner / Intermediate / Advanced), style (Intraday / Swing / Both);
- allowed_timeframes (the user picks one) + context timeframe(s) (from the existing context_ladder, or a user-chosen HIGHER timeframe);
- params: a typed list {key, label, help, type (int / float / enum / bool / timeframe / ma_type / source), default, bounds, step} + constraints between params (e.g. fast < slow; context timeframe > signal timeframe);
- sides: long / short / both (a user setting, default both);
- context (optional filters on closed context bars), setup (the state or event that ARMS the recipe), trigger (the closed-bar event that EMITS the signal), invalidation (the price rule that defines where the idea is wrong), exit_signal (optional: the event that says the idea has ended), evidence (named items listed on the signal);
- signal_text (structure words per policy 11l, built from tokens; see section 6).
Every part is expressed with registered ScreenQL functions or registered price-action series, so a template can run as a SCREEN (scan), a BACKTEST and a LIVE / PAPER deployment from the SAME definition (parity, section 8).

2. USER FLOW
Ideas / Templates page → pick a template → "Use this template" (a copy in My strategies with provenance) → the settings form, generated from the params schema:
- the timeframe and the context timeframe pickers;
- MA type, periods, multipliers;
- the filters on / off;
- sides.
The constraints are checked live, with plain messages. A live preview chart shows the last signals on a chosen symbol (education: anonymised on public pages; the user's own symbol inside his account). Then: run the backtest → choose the symbols → choose the execution in the deployment form (existing) → approve → automation (PAPER; approval mode as set).

3. EVALUATION SEMANTICS (binding; tests for each)
S1 Closed bars only. A signal at bar t uses bars ≤ t of the signal timeframe, and context values whose bar CLOSED at or before t's close (the runtime's existing alignment; a Daily value is never seen before that day closes).
S2 Events. CrossAbove(a, b) at t ⇔ a[t-1] ≤ b[t-1] and a[t] > b[t] (mirror for below). "Close beyond level by k ATR" uses ATR from bar t-1 (as BK1).
S3 Warm-up. A recipe needs max(lookbacks over all parts) + the indicator warm-up bars (+ the registry's min_bars / degree needs); before that → NO_TRADE("not enough history"). Context timeframes have their own warm-up.
S4 Setup / trigger state machine per (deployment, side):
 IDLE → ARMED when the setup becomes true (for event setups: on the event bar; it carries a setup-instance id = the bar index of the event + the level / line id);
 ARMED → SIGNAL when the trigger fires on a later or the same closed bar (as the template defines), while the setup is still valid and the invalidation is not breached;
 ARMED → IDLE when the setup expires (a template param, e.g. retest_max_bars, using the existing function's own expiry) or its invalidation is breached first.
 One signal per setup instance (re-use BK1's one-per-leg where the function has it). A new instance needs a new setup event.
S5 Both sides on the same bar → no signal for either; log "conflict".
S6 Invalidation sanity: the invalidation price must be on the losing side of the trigger close (long: below the close; short: above). Otherwise → no signal ("invalid geometry"). This replaces today's "non-positive risk" refusal with a clear reason.
S7 Gaps. A trigger evaluates only on the trigger bar's own OHLC; there are no retroactive fills. If a gap takes price through the invalidation before the trigger, the setup is invalid (S4).
S8 Repainting is forbidden. Swings are used only after their confirm bar; lines and patterns only as of each bar (the existing causal implementations); Supertrend / MA on closed bars.
S9 Sessions. Indicator series are continuous across sessions (no reset) unless the template is session-based by design (VWAP, gap, opening-range-type templates), which then use the exchange calendar (existing). Session-based templates are labelled "Intraday (session-based)".
S10 Exit signals are evaluated only for positions opened from the same deployment and template; they never block or delay an exit (ADR-0004).
S11 Determinism. The same bars + the same params give the same signals in the scan, the backtest and live (section 8).

4. SIGNAL OUTPUT
- Internally: direction LONG / SHORT (bullish / bearish setup), the invalidation price (→ Signal.stop_loss), the setup-instance id, the evidence list, the template id + version, the params hash.
- What happens next is the user's deployment: instrument_kind / option construction (e.g. bullish → bull put spread), lots, exits, approval.
- Targets: see section 5.

5. EXITS AND TARGETS ARE THE USER'S SETTINGS (Abhi's rule)
- Templates never choose targets or position size.
- Add to the deployment's exit settings (extend exit_rules / ExitRules, with migration and tests):
 - stop_source: TEMPLATE_INVALIDATION (default for templates) / PREMIUM_PCT (existing) / CREDIT_PCT (existing) / NONE;
 - target_rule: NONE / R_MULTIPLE (target_r, user-set) / NEXT_AREA (the next opposite area from market_zones) / TEMPLATE_EXIT (exit on the template's exit_signal) / CREDIT_PCT (existing target_credit_pct for spreads);
 - the existing trailing / break-even / time exits unchanged.
- The template adapter NEVER calls BaseStrategy.build_signal (it forces targets 1.5 / 2.5 and a min_rr 1.2 refusal: a rule the user never set). It builds Signal directly with risk_reward = None unless the user chose a target_rule. With R_MULTIPLE, the account's existing min_risk_reward in RiskManager still applies, and the settings form says so.
- The DSL v1 fields stop_loss_atr_mult / target_rr / min_rr: keep them for existing custom strategies (backward compatible), but template-based strategies ignore them; their stop / target come from the settings above. Document the precedence and test it.

6. SIGNAL TEXT (policy 11l)
- Structure / indicator words only, from tokens. e.g. "Supertrend turned bullish on the 15-minute chart; the higher-timeframe trend filter agrees."; "Price closed above the channel's upper line and the retest from above was rejected."
- No prices, no indicator values, no buy / sell / target / stop words. The parameter values are shown on the strategy page, not in the signal text.
- On the user's own signals, the area may appear as a range (structure templates only). The invalidation is described in words ("below the pullback low"); its price lives in the order / position record.
- The language guard test covers every template's text tokens.

7. THE CATALOGUE (v1)
Notation: L = long rule; the short side is the mirror unless stated. "Swing" = the registry's causal swings (degree = param). Every number below is a PARAMETER with a default and bounds, never a fixed rule.

FAMILY A — PRICE ACTION / STRUCTURE (the spec is in ATP_SESSION_IDEA_LIBRARY.md: same ids)
A1 Trend pullback to demand / supply · A2 Range edges · A3 RSI divergence at an area · A4 Sweep and reclaim · A5 Structure flip retest · A6 Channel trading · A7 Pattern at an area · A8 Options-seller direction pack.

FAMILY B — BREAKOUT (built on the existing BK detectors; each has a "+ retest" twin in family C)
B1 S/R (range) breakout — setup: a range / base (RangeBreakout with range_mode as a param: fixed (registry default) or auto (the base rule); the template default is auto, stated explicitly); trigger L: close above the range high by k ATR (k param), optional volume expansion (RelVolume ≥ param); invalidation L: back inside: the range high − buffer (param: "range high" / "range mid" / "range low"); exit_signal: a close back inside the range (failed breakout).
B2 Swing-level breakout (BOS) — SwingBreakout / SwingBreakType = BOS in the trend's direction (degree param); invalidation: the last opposite swing; exit: an opposite CHoCH.
B3 Channel breakout — TrendlineBreakout(mode = channel): close beyond the channel's far edge by k ATR. Mirror note: the down side of "above the upper edge of a rising channel" is "below the lower edge of a falling channel"; losing a RISING channel's support is TrendlineBreakout(line, down); invalidation: the broken line's value at the signal bar − buffer; exit: a close back inside the channel.
B4 Trendline breakout — TrendlineBreakout(mode = line) (touches param); invalidation: the line − buffer; exit: a close back across the line.
B5 Donchian breakout — DonchianBreakout(n); invalidation: the opposite Donchian mid or the last swing (param); exit: a close beyond the opposite channel (n_exit param).
B6 Pattern breakout — PatternBreakout(flag / pennant / triangle); invalidation: the pattern's last swing; exit: a close back inside the pattern.
B7 Squeeze breakout — SqueezeBreakout(mode ∈ {bollinger, ttm}, n, lookback, v) as it exists (no bb_k / Keltner args unless added with tests); invalidation: BBMid; exit: closes back inside the bands for at least exit_bars bars (param ≥ 1).
B8 Inside bar / NR7 breakout — NRBreakout; invalidation: the opposite side of the mother bar.
B9 Volume breakout — VolumeBreakout at the range edge; invalidation: the range edge − buffer.
B10 Gap and go (Intraday, session-based) — trigger: GapType(side, level, hold) == "GAP_AND_GO" (GapBreakout alone is true for GO and HOLD); allowed_timeframes ⊆ the registry's GAP_TFS; invalidation: BreakoutFailLevel / the gap level.
B11 VCP breakout (Daily) — VCPBreakout; invalidation: the last contraction low.

FAMILY C — BREAKOUT + RETEST (RetestEntry over the matching B setup; one signal per breakout)
C1 S/R breakout + retest (of = range) · C2 Swing breakout + retest (of = swing) · C3 Channel breakout + retest (of = the channel line: if RetestEntry has no channel option yet, add it, with causal tests) · C4 Trendline breakout + retest (of = trendline) · C5 Donchian breakout + retest (of = donchian).
- Rules for all: setup = the B breakout event (instance = the breakout bar + level id); trigger = RetestEntry's retest bar (by default the reversal at the level, the existing ReversalAt composite; param "touch is enough"); expiry = retest_max_bars (param); failure = a close back inside before the retest → setup void (existing rule); invalidation L = an enum param {retest_low, level_minus_stop_ref, lower_of_both} (default lower_of_both); exit_signal = a close back through the level.

FAMILY D — TREND-FOLLOWING INDICATORS
D1 Supertrend flip — params: period, multiplier; optional context filter: Supertrend direction on the context timeframe agrees. Trigger L: SupertrendDir(period, multiplier) changes from −1 to +1 on the closed bar (ADD SupertrendDir: +1 / −1 series, with causal tests; a plain CrossAbove(close, Supertrend) can fire without a flip when close[t-1] equals the line). Invalidation L: the Supertrend line value at the signal bar. exit_signal: the opposite flip.
D2 Moving-average crossover — params: ma_type (EMA / SMA; WMA / HMA once added), fast, slow (constraint fast < slow), source (close); optional trend filter: close above a third, long MA (on / off, period). Trigger L: CrossAbove(fast MA, slow MA). Invalidation L: SwingLow(degree) (the latest confirmed one), or the slow MA value (param). If it is not below the close, S6 applies (no signal; never walk back to an older swing). exit_signal: the opposite cross.
D3 MA pullback in trend — params: trend MA (slow), pullback MA (fast), ma_type. Setup L: close above the slow MA and the slow MA rising (slope over a lookback param > 0); price pulls back to touch / close below the fast MA. Trigger L: ReversalAt(fast MA, up) (or Pattern(name, confirm) with a bullish pattern) with the close back above the fast MA. Invalidation L: the pullback low = the lowest low from the setup bar to the trigger bar; whether the pullback bar may also be the trigger bar is a param (same_bar, default false). exit_signal: a close below the slow MA (param on / off).
D4 Price vs MA (MABreakout) — the existing MABreakout semantics; invalidation: the MA − buffer.
D5 ADX / DI trend — params: adx_period, adx_threshold, DI cross on / off. Trigger L: CrossAbove(PlusDI, MinusDI) while ADX > threshold and ADX rising (lookback param). Invalidation L: the last swing low. exit_signal: the opposite DI cross, or ADX falling below the threshold (param).
D6 MACD — ADD MACD to ScreenQL first (line, signal, histogram; params fast / slow / signal; the existing app/indicators/trend.py macd; units, causal tests). Trigger L: CrossAbove(MACD line, signal line); optional "only above / below zero" filter. Invalidation L: the last swing low. exit_signal: the opposite cross.
D7 Parabolic SAR flip (ADD PSAR with tests) — trigger L: SAR flips below price; invalidation: the SAR value; exit: the opposite flip.

FAMILY E — MOMENTUM / MEAN REVERSION
E1 RSI pullback in trend — params: rsi_period, oversold / overbought levels, trend filter (MA or Supertrend on the context timeframe). Trigger L: RSI crosses back above oversold while the trend filter is bullish. Invalidation L: the swing low made during the dip. exit_signal: RSI reaching the overbought level (param) or the opposite trigger.
E2 RSI divergence (generic) — setup = the RISING EDGE of RsiDivergence (a state that stays true for up to its lookback; instance = the swing bar), regular divergence only; trigger = SwingBreakType(side, lower degree) == "CHOCH" or a close beyond the confirming bar's extreme (param). RSI period: add an argument to RsiDivergence with tests, or drop the param (today fixed RSI(14)). Invalidation: the divergence's extreme. (A3 is the area-bound version.)
E3 Bollinger mean reversion (range regime) — params: period, k; regime filter: ADX below a threshold or DowTrend(degree) == "RANGE" (param). Trigger L: close back inside after a close below BBLower. Invalidation L: the extreme low of the excursion. exit_signal: a touch of BBMid or BBUpper (param).
E4 Stochastic cross (ADD Stochastic with tests) — the %K / %D cross out of the oversold / overbought zone with a trend filter; invalidation: the swing low / high.
E5 VWAP reclaim / reject (Intraday, session-based) — params: trend filter. Trigger L: close crosses above SessionVWAP after trading below it, with the trend filter bullish. Invalidation L: the session low since the cross-under, or VWAP − buffer (param). exit_signal: a close back below VWAP.

FAMILY F — OPTIONS SELLER PACKS (signals only: the side / regime for the user's spread settings)
F1 Directional seller pack: A1 / D1 / D3 signals mapped to "bullish / bearish continuation" (the user maps them to bull put / bear call in the deployment). F2 Range seller pack: A2 / E3 range edges → "range upper / lower edge". No strike, premium or lot in any template.

Add-indicator work (one PR, before D6 / D7 / E4): MACD, Stochastic, WMA, HMA, Parabolic SAR in app/indicators + ScreenQL registry (units, bounds, causal fixtures, parity with a reference implementation). Optional later: CCI, Ichimoku, Heikin-Ashi source (as a "source" param).

8. PARITY AND TESTS (gate for every template)
- Fixture series per template: synthetic bars where it MUST fire (long and short) and MUST NOT (the near-miss cases), including gaps, a both-sides conflict, expiry, failure-before-retest, warm-up and invalid geometry.
- No look-ahead: a run on bars[0..t] gives exactly the signals at ≤ t that a run on the full series gives (a truncation test over many t).
- Scan = backtest = live: the same recipe evaluated by the screener runtime, the backtest engine and the deployment worker gives identical signal bars and invalidations bar by bar on recorded fixtures (CI).
- Params: bounds, the constraints (fast < slow; the context timeframe higher), every enum; property tests for random valid params (no crash, no signal without a trigger).
- Mirror test: the short side equals the long side on mirrored prices.
- The language guard on the signal text; the provenance and version recorded on each signal.
- Performance: the template scans fit the existing perf budget (the BK perf job extended).
- Exits: stop_source / target_rule precedence; DSL v1 strategies unchanged (regression).

9. UI
- The Templates page, grouped by family, with filters (style, difficulty, timeframe). Each card: the name, a one-line idea, difficulty, style, "works best when …", an anonymised example chart older than 3 months.
- The settings form auto-generated from the params schema, with live validation and the preview chart.
- "Compare templates": backtest two or three on the same symbol / period side by side.
- Every template is also available as a scan ("Run as scan") and in the Copilot / idea → strategy assistant (11e) as a starting point.
- Phone layout; page tour.

10. ORDER
1. Section 0 R1–R10 + section 5 (exits as settings) + the recipe core (schema, state machine, adapter to BaseStrategy, parity harness) + the add-indicator PR.
2. Families D1, D2, B1, C1, B3, C3 (the most requested), then A (with the idea library), then the rest of B / C / D / E, then F.
3. The UI (templates page, settings form, preview, compare).
Each step: tests per section 8; self-review; PAPER only; the deploy line for Abhi after merge.
