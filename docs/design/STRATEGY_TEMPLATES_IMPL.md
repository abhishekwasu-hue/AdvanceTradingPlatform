# Strategy templates: implementation status

Owner order: `docs/owner-orders/ATP_SESSION_STRATEGY_TEMPLATES.md` (section 0 R1-R10 is binding). Generic method
only; templates generate SIGNALS, PAPER only; signal text in structure words (policy 11l).

## Step 1, first PR (TPL-1): the recipe core and the add-indicator work

| Part | Where | Status |
|---|---|---|
| Template schema (strict, versioned, params with bounds, constraints, sides, context / setup / trigger / invalidation / exit_signal / evidence / signal_text tokens) | `backend/app/strategy_templates/schema.py` | done |
| Binding to the ScreenQL registry (real signatures via `validate_study`); LAST_BAR_ONLY names refused (R1); settings resolved with plain messages; params hash incl. the pa_settings fingerprint and the engine version (R9) | `compile.py` | done |
| The setup / trigger state machine as a pure function of bars (R3, S1-S7, R8): one signal per instance, newest instance wins, expiry in signal bars, same_bar, arm-level breach, conflict, invalid geometry, warm-up incl. context timeframes | `engine.py` | done |
| Adapter to BaseStrategy: reads the series at the decision bar, one series per backtest run (prefix cache `whole_frame`), never `build_signal`, no targets, risk_reward None | `adapter.py` | done, not wired to deployments / routes |
| Parity harness: scan = backtest (`run_backtest`) = options backtest (`run_option_backtest`) = the worker's call, on one fixed trailing window; trades open on scan signal bars with that bar's invalidation; history to fetch = needs x safety factor (R6) | `parity.py`, `tests/test_tpl1_parity.py` | done |
| `determine_exit_price` with target1 None (R4, part) | `app/trading/exit_logic.py` | done |
| Signal text tokens + guard (no buy / sell / target / stop words, no numbers outside timeframe names) | `language.py` | done |
| Indicators: SupertrendDir, MACD / MACDSignal / MACDHist, WMA, HMA, StochK / StochD, PSAR / PSARDir in `app/indicators` + the ScreenQL registry, runtime and validator (argument order: MACD fast < slow, PSAR start / step <= max) | `app/indicators/*`, `app/screener/*` | done |
| Proof template D1 (Supertrend flip, optional higher-timeframe filter) | `templates/D1.json`, `tests/test_tpl1_d1_supertrend.py` | done |

Tests: `tests/test_tpl1_indicators.py` (hand-written reference implementations, truncation, bounds, the higher
timeframe seen only after its bar closed), `tests/test_tpl1_recipe_core.py` (every state-machine rule, R1 refusals,
strict schema, constraints, params hash, language guard), `tests/test_tpl1_d1_supertrend.py` (must fire / must not,
mirror, warm-up, truncation over many t, property tests on random valid settings, the adapter), `tests/test_tpl1_parity.py`.
The new series are in `app/quality/causal_cases.py`, so the no-look-ahead harness covers them too.

## Step 2 (TPL-2, draft PR stacked on the wave step 2 PR): wiring, D2 / B1 / B3, adopt as a paper strategy

Behind `ui_v2` and the new flag `strategy_templates` (OFF). No migration. Owner questions TPL-Q8..TPL-Q17.

| Part | Where | Status |
|---|---|---|
| A template with the user's settings as ONE strategy id (`template:<id>:<version>:<tf>:<sides>:<values>`, defaults empty, canonical, <= 80 characters, floats to 6 decimals): the deployment / order / trade records carry it, so it is the stored provenance (TPL-Q9) | `ids.py` | done |
| The adapter registered: `resolve_strategy` builds `TemplateStrategy` for a template id, behind `strategy_templates` and the flags of the functions the recipe reads (off: "unknown strategy"); the deployment and backtest routes and the worker run it unchanged | `custom_strategies/resolver.py` | done |
| R6 wiring: the worker fetches `history_days` of candles for a template (`parity.fetch_bars` -> sessions -> calendar days, capped at 400; TPL-Q11) instead of the 5-day intraday default | `adapter.py`, `workers/trading_worker.py` | done |
| TPL-Q2: the provenance (template, version, settings hash, setup instance) is on `Signal.provenance` - never serialised, never in the reasons a user reads; the worker logs it | `core/models.py`, `adapter.py`, `parity.py` | done |
| Template deployments are PAPER only ("Strategy templates run on paper only for now") | `deployments/routes.py` | done |
| R2 level companions: `RangeBreakoutRef` / `LineBreakoutRef` (`what` = level, fail_level, base_high, base_low, held_level, stop_level_atr, stop_base, stop_midpoint; prices on signal bars, `held_level` forward); the stop references are `quality.stop_reference`, shared with the breakout overlay; in `engine.SPARSE_SERIES` (TPL-Q13) | `screener/registry.py`, `runtime.py`, `breakouts/quality.py` | done (range, trendline / channel) |
| `MA(x, n, type)` (EMA / SMA / WMA / HMA, the same series as those functions) so D2's average type is a setting | `screener/registry.py`, `runtime.py` | done |
| `ParamSpec.choice_labels`: the words the settings form shows for each choice | `schema.py` | done |
| D2 moving-average crossover (average type, fast < slow, optional trend average, invalidation the latest confirmed swing or the slow average, exit the opposite cross) | `templates/D2.json` | done |
| B1 range breakout (range fixed / auto - auto by default, break distance, volume condition, invalidation just beyond the edge / the far side / the middle with the edge buffer, exit a close back inside) | `templates/B1.json` | done |
| B3 channel breakout (touches, break distance, invalidation just beyond the broken edge / the other line / the middle, exit a close back inside; TPL-Q12) | `templates/B3.json` | done |
| C1 / C3 (breakout + retest) | - | waited for R7 (TPL-Q8): done in step 3 below |
| Adopt as a paper strategy: `GET /api/strategy-templates` (cards, settings in words, `available` by the organisation's flags), `POST /api/strategy-templates/{id}/adopt` (settings checked in plain words -> the paper bot form's pre-fill: strategy id, candles, holding, PAPER; creates nothing) | `routes.py` | done |
| The v2 Paper bot form: "Strategy templates" in "Pick a strategy", a chart timeframe, the template's usual settings, `?template=<id>`; Start on paper sends the same PAPER request; a bot on a template shows the template's name (TPL-Q16) | `frontend/src/paperbot/PaperBotV2.tsx`, `src/strategyTemplates/` | done |

Tests (section 8 gate per template): `tests/test_tpl2_templates.py` - must fire long and short / must not (no cross, a wick, a
near miss below k ATR, the volume asked for), invalidation per choice, S6, exits, warm-up, the truncation test over many t,
the mirror test, scan = backtest = options backtest = worker on one trailing window (the TPL-1 harness), property tests on
random valid settings, the language guard, "no number written into a rule"; plus the ids, the provenance, the resolver
behind the flags and the worker's history. `tests/test_tpl2_routes.py` - the API (flag off: 404), adopt as a pre-fill only,
a template through the deployment (PAPER) and backtest routes. The companions and `MA` are in the runtime fixtures, the
no-look-ahead cases (lookahead job) and the history sweep. Frontend: `src/strategyTemplates/templates.test.tsx`, e2e
`strategy-templates-v2.e2e.ts` (baselines 1440 / 390 / 360, light / dark).

## Step 3 (TPL-3): R7, C1 / C3, family A (A2, A5)

Same flag `strategy_templates` (OFF), PAPER only, no migration. Owner questions TPL-Q18..TPL-Q25.

| Part | Where | Status |
|---|---|---|
| R7: RetestEntry forwards the base breakout's own settings as NAMED arguments (`k=`, `n=`, `v=`, `mode=`, `touches=`; left out = the breakout parameter's default, so every older call is byte-identical); the validator checks named arguments like positional ones (TPL-Q24) | `screener/registry.py`, `validator.py`, `runtime.py` | done |
| R7: `of = "channel"` - the B5 channel breakout's retest, the broken far edge read along its line at each bar after the breakout (projected), for RetestEntry, the B9 screen and the companion; `trendline` keeps its frozen level (TPL-Q18) | `screener/breakouts/retest.py` (`retest_events`, `Retest.level_at`), `params.py` | done |
| R7 / R2 companion `RetestEntryRef(what, side, of, bars, reversal, stop_mult, k=, n=, v=, mode=, touches=)`: level (projected for a channel), retest_low (the pullback's extreme, TPL-Q19), base_high / base_low, held_level (projected until the next retest), stop_level_atr / stop_base / stop_midpoint (quality.stop_reference); read from the same `retest_events` as the setup | `screener/runtime.py`, `registry.py` | done |
| A sparse series inside a number function (`Least(RetestEntryRef(..), RetestEntryRef(..))`) does not hold the warm-up back | `engine.py` (`_reads_sparse`) | done |
| A yes / no setting may fill a yes / no argument ("Wait for a reversal at the level", TPL-Q25) | `compile.py` | done |
| C1 range breakout and retest: B1's own breakout settings (the same keys, labels, defaults, bounds), retest within, wait for a reversal (or a touch), invalidation {the pullback, the breakout's own reference (B1's choices + edge buffer), whichever is farther - default}, exit a close back through the level | `templates/C1.json` | done |
| C3 channel breakout and retest: B3's settings, the edge projected, the same retest / invalidation / exit choices | `templates/C3.json` | done |
| A2 range edges (idea library T2): a reversal at the latest swing low / high inside `DowTrend == "RANGE"`, invalidation the edge (TPL-Q21) | `templates/A2.json` | done |
| A5 structure flip retest (idea library T5): "what broke" (swing / range / trendline / channel), a rejection retest, invalidation the level plus a buffer setting (0) (TPL-Q22) | `templates/A5.json` | done |
| A1, A3, A4, A8 (areas), A6 (channel lines), A7 (patterns) | - | not built: no zone data in the template engine / no area-edge price, no "at a channel line" or swing-pattern function (TPL-Q23) |

Tests: `tests/test_tpl3_templates.py` - R7 (the user's breakout settings reach the retest - an index without volume
signals only with them; named arguments left out = the older calls; named arguments validated; the channel retest's level
projected to the retest bar and held along the line; the B9 screen's channel choice), C1 must fire long and short on the
BK4 retest fixture, every invalidation choice by value, must not (a close back inside first, a weak retest unless a touch
is enough, too late, the breakout's own distance / volume), S6, the exit, the warm-up, "exactly the retests" with the
user's settings on random data; C3 the same against the channel retests, its exit along the projected edge, touches
forwarded; A2 only inside a range, never on a rejection outside one, the edge as invalidation; A5 per "what broke"; for
all four: the mirror test, the truncation test over many t, scan = backtest = options backtest = worker on one trailing
window (the TPL-1 harness), property tests on random valid settings, the language guard, "no number written into a rule",
the ids round trip and fit, the adapter's signal. The companion and the forwarded calls are in the runtime fixtures
(`test_s1b_screen_runtime.py`), the no-look-ahead cases (`app/quality/causal_cases.py`, lookahead job) and the history
sweep. No frontend change: the Paper bot form lists the templates from the API.

## Step 4 (TPL-4): R2 for the other breakout families, and the next B / C / D / E templates

Same flag `strategy_templates` (OFF), PAPER only, signals only, no migration. Owner questions TPL-Q26..TPL-Q40.

| Part | Where | Status |
|---|---|---|
| R2 for the other breakout families (TPL-Q13, TPL-Q26): `SwingBreakoutRef`, `NRBreakoutRef`, `VolumeBreakoutRef`, `DonchianBreakoutRef`, `MABreakoutRef`, `PatternBreakoutRef`, `VCPBreakoutRef` (Daily) - the breakout screens B2, B4, B14, B10, B12, B6, B3 of `screener/breakouts` - one function per family with the breakout's own arguments, defaults, bounds and flags, the same `_breakout_ref` (the screen's own columns on its signal bars, `held_level` forward, the stop references of `quality.stop_reference`); sparse in the engine | `screener/registry.py`, `runtime.py`, `strategy_templates/engine.py` | done |
| B2 swing-level breakout: a break of structure (`SwingBreakType == "BOS"`), invalidation the latest confirmed opposite swing (or just beyond the broken swing), exit the opposite CHoCH (TPL-Q27) | `templates/B2.json` | done |
| B4 trendline breakout: `TrendlineBreakout(line)`, invalidation just beyond the line / the far end of the move along it, exit a close back across the line as of the breakout (TPL-Q28) | `templates/B4.json` | done |
| B5 Donchian breakout: invalidation the channel's middle or the latest confirmed opposite swing, exit a close beyond the opposite side of a shorter channel (TPL-Q29) | `templates/B5.json` | done |
| B6 pattern breakout (flag / pennant / triangle): invalidation the pattern's far side (or the edge / middle), exit a close back inside (TPL-Q30) | `templates/B6.json` | done |
| B7 squeeze breakout (Bollinger / TTM, `SqueezeBreakout` as it exists): invalidation the bands' middle, exit `exit_bars` closes in a row back inside the squeeze's own bands (TPL-Q31) | `templates/B7.json` | done |
| B8 inside bar / NR breakout: invalidation the other side of the mother bar (the screen's fail level), no exit (TPL-Q32) | `templates/B8.json` | done |
| B9 volume breakout: invalidation just beyond the level that must hold (the range edge, or the surge bar's own extreme), no exit (TPL-Q32) | `templates/B9.json` | done |
| B11 volatility contraction breakout (Daily): the platform's VCP rules as settings, invalidation the last contraction (or just beyond the pivot) (TPL-Q33) | `templates/B11.json` | done |
| C5 Donchian breakout and retest: B5's channel settings forwarded to `RetestEntry(of = "donchian")` (R7), the retest / invalidation / exit choices of C1 (TPL-Q34) | `templates/C5.json` | done |
| D4 moving-average breakout (`MABreakout`), invalidation the average less a buffer (TPL-Q35) | `templates/D4.json` | done |
| D5 ADX / DI trend: the DI cross with ADX above its threshold and rising, or ADX rising through it with the leading line; exit the opposite cross or ADX below the threshold (TPL-Q36) | `templates/D5.json` | done |
| D6 MACD crossover, optional zero-line filter (TPL-Q37); D7 parabolic SAR flip, invalidation the SAR (TPL-Q38); E4 stochastic cross out of the oversold / overbought zone, optional higher-timeframe trend average (TPL-Q39) | `templates/D6.json`, `D7.json`, `E4.json` | done |
| Signal text: structure / indicator words per template (no prices, no values, no buy / sell / target / stop words) | `language.py` | done |

Tests: `tests/test_tpl4_templates.py` - the companions against each breakout screen's own columns and the stop reference
(both sides, `held_level` forward), their arguments equal to the breakout's; per template: "fires exactly on" its breakout /
indicator event on random data, long and short, with the invalidation by value, S5 conflicts and S6 geometry checked on
every event bar; the must-not near misses (a change of character or a plain break for B2, a larger break distance, the
volume asked for on an index without volume, ADX weak or not rising, a stochastic cross in the middle, a steady rise with
no SAR flip, every VCP rule on the BK3 fixture); every invalidation choice; the exits by value; the warm-up; the shared
gate for all fourteen - the mirror test (whole-tick prices so a tie stays a tie; around zero for the two templates that
read a percentage of the price), the truncation test over many t, scan = backtest = options backtest = worker on one
trailing window (the TPL-1 harness; B11 on the Daily VCP fixture), property tests on random valid settings (no signal
without its trigger, S6), the language guard, "no number written into a rule" (only a direction value, the zero line
and the bar before), the ids round trip and fit, the adapter's signal, provenance and flags. The companions are in the
runtime fixtures (`test_s1b_screen_runtime.py`), the no-look-ahead cases (`app/quality/causal_cases.py`, lookahead job),
the history sweep and the breakout families' registry tests. No frontend change: the Paper bot form lists the templates
from the API. No idea-library card gains an honest match (the T ideas read areas, rejections, divergences and sweeps;
none of these templates does), so no `adopt` entry is added.

Not built in step 4 (TPL-Q40): B10 gap and go (a gap companion on the session clock), C2 swing breakout + retest
(RetestEntry's `degree` is the wave degree: the swing degree is not forwarded yet), C4 trendline breakout + retest (the
projected trendline retest, TPL-Q18, waits for the owner), D3 MA pullback, E1 RSI pullback, E3 Bollinger mean reversion and
E5 VWAP reclaim (each invalidation is "the extreme since the setup": an engine piece the state machine does not have yet;
E5 also needs "the same session" for the cross-under), E2 RSI divergence (RsiDivergence reads a fixed RSI 14 and its
extreme has no companion), and family F.

## Step 5 (TPL-5): the extreme since the setup bar, a session-bound setup, the swing degree for the retest; C2, D3, E1, E3, E5

Same flag `strategy_templates` (OFF), PAPER only, signals only, no migration. Owner questions TPL-Q41..TPL-Q49.

| Part | Where | Status |
|---|---|---|
| The engine piece "the extreme since the setup bar" (TPL-Q41): an invalidation `{"extreme": "since_setup"}` (alone or as one choice of an invalidation switch) is the lowest low (long) / highest high (short) from the setup instance's bar to the trigger bar, kept while the state machine walks the bars and restarted by a newer setup; for an invalidation only; every earlier template unchanged (engine version 1, the same params hash) | `strategy_templates/schema.py` (`SetupExtreme`, `InvalidationSwitch`), `compile.py`, `engine.py` | done |
| A setup bound to its session (TPL-Q42, E5): `session_bound` voids the instance on the first bar of another session; the session is the exchange calendar's for each bar's own timestamp (the runtime's session map, as SessionVWAP) - no time-of-day rule; intraday timeframes only | `schema.py`, `engine.py` | done |
| R7 for C2 (TPL-Q43): RetestEntry / RetestEntryRef take `swing=` - the swing breakout's degree (`degree` stays the wave degree); left out = the default, so older calls are byte-identical; validated like the other named arguments, and a larger degree asks for its history (`Spec.kwarg_degree_bars`) | `screener/registry.py`, `runtime.py`, `validator.py` | done |
| C2 swing breakout and retest: B2's swing size and break distance forwarded, retest within, wait for a reversal (or a touch), invalidation {the pullback, just beyond the broken swing, whichever is farther - default, the latest confirmed opposite swing}, exit a close back through the level | `templates/C2.json` | done |
| D3 moving-average pullback in trend (TPL-Q44): the first bar of the pullback to the fast average in a rising (falling) slow average, the reversal at the fast average with the close back beyond it within `pullback_max_bars`, invalidation the pullback's extreme, same bar a choice, exit a close back across the slow average (or none) | `templates/D3.json` | done |
| E1 RSI pullback in trend (TPL-Q45): the RSI back out of the oversold (overbought) zone after a dip into it, with the higher-timeframe trend filter (an average or the Supertrend), invalidation the dip's extreme, exit the opposite zone or the opposite signal | `templates/E1.json` | done |
| E3 Bollinger mean reversion in a range (TPL-Q46): the first close back inside after a close beyond the lower (upper) band, in a range regime (ADX below its threshold, or the swing structure a range), invalidation the excursion's extreme, exit a touch of the middle or the opposite band | `templates/E3.json` | done |
| E5 session VWAP reclaim / reject (TPL-Q47, "Intraday (session-based)"): the close back above (below) the session VWAP after a cross the other way in the same session, optional higher-timeframe trend average (on by default), invalidation the session's extreme since the cross or VWAP with an ATR buffer, exit a close back across VWAP | `templates/E5.json` | done |
| Signal text: structure / indicator words per template | `language.py` | done |

Tests: `tests/test_tpl5_templates.py` - the engine piece on hand-made bars (the setup bar and the trigger bar included,
an earlier bar and a later bar not, a newer setup restarts it, same bar, S6, a conflict), its definition and no look-ahead
on random marks (property test), the schema allows it for an invalidation only and every earlier template still compiles
without it; the session-bound setup across two sessions and refused on Daily; `swing=` against the B9 screen's own retest
events per degree, left out = the older call, ignored by another base, validated, the history by degree; per template
"fires exactly" against an independent walk of S4 / S5 / S6 / R8 over series written in the test (long and short, the
invalidation and the setup bar by value), the must-not cases (late, against the trend, a trending market, a cross from
the previous session, an index without volume, a stricter retest), the exits by value, the settings' constraints; the
shared gate for all five - the mirror test (whole ticks; the Supertrend filter from the bar its seeded direction mirrors;
the swing-structure regime left out: its labels break ties one way), the truncation test over many t, scan = backtest =
options backtest = worker on one trailing window (the TPL-1 harness), property tests on random valid settings, the
language guard, "no number written into a rule", the ids round trip and fit, the adapter's signal, provenance and flags.
`swing=` is in the runtime fixtures (`test_s1b_screen_runtime.py`) and the no-look-ahead cases (`app/quality/causal_cases.py`,
lookahead job). No frontend change: the Paper bot form lists the templates from the API. No idea-library card gains an
honest match (TPL-Q48).

Not built in step 5 (TPL-Q49): E2 RSI divergence (an RSI period on RsiDivergence, an extreme companion and the
lower-degree trigger: its own PR - done in TPL-7 below), B10 gap and go, C4 trendline breakout + retest (TPL-Q18), family F
(done in TPL-6 below).

## Family F (TPL-6): the option seller packs F1, F2

Same flag `strategy_templates` (OFF), PAPER only, signals only, no migration. Owner questions TPL-F1..TPL-F8.

A pack is not a new recipe: it is an ordinary `atp.template/1` template whose setting `source` (a `variants` switch, as
family G) picks a MEMBER template, and every part of each variant is the member's own expression with the member's own
settings (same keys, labels, defaults, bounds, constraints and timeframes). So a pack signals on exactly the member's bars,
with the member's invalidation and exit; only the words differ - the signal text adds the pack's reading of the side for
the option structure the user set up. The mapping is `app/strategy_templates/packs.py` (`MEMBERS`); the tests hold every
variant equal to its member part by part and series by series, so editing one without the other fails. The option
structure (bull put spread on the bullish side, bear call spread on the bearish one, an iron condor on either), its
strikes, width, expiry, lots and exits stay the deployment's own settings (DECISIONS: options construction stays in the
user's settings); a pack has no field for any of them and the adopt pre-fill carries none (a note says where they come from).

| Part | Where | Status |
|---|---|---|
| F1 directional seller pack: `source` = Supertrend flip (D1: its period, multiplier and higher-timeframe filter), read as "a bullish / bearish continuation for the option structure in your own settings"; invalidation the Supertrend line, exit the opposite flip (inert until R5) | `templates/F1.json` | done (member D1) |
| F2 range seller pack: `source` = range edges (A2: its swing size), read as "the lower / upper edge of the range ..."; invalidation the edge, no exit (A2 has none) | `templates/F2.json` | done (member A2) |
| The pack mapping (pack -> source -> member), the adopt note | `packs.py`; `routes.py` (`_notes`, one line) | done |
| Signal text: four tokens (structure words; no buy / sell / target / stop words, no numbers) | `language.py` | done |
| F1's other members A1 (areas, TPL-Q23) and D3 (the "extreme since the setup" piece), F2's member E3 (the same piece) | - | D3 and E3: done in TPL-7 below (pack version 2); A1 waits (TPL-Q50) |
| G12 (the wave seller pack, labels over G1-G11) | - | not built: G1-G11 already switch their parts on their own settings (trigger style, window) and share setting keys with different defaults / choices, so one pack template cannot hold them with today's one-level `variants`; needs a pack composition (TPL-F7, WAVE-10) |

Tests: `tests/test_tpl6_family_f.py` - every pack variant compiles to its member's own parts (every allowed timeframe, the
filter on), the pack's settings are the member's, the pack's series equals the member's (direction, invalidation, exits,
reasons, warm-up, instances) on random data and in property tests on random valid settings; F1 must fire on the turn on
both sides with the line and the opposite flip, must not in a steady trend or on noise, the higher-timeframe filter, the
warm-up and the refusals; F2 only at a range edge inside a range, never outside one, no exit, S6; for both: the mirror
test (F1 from the bar the Supertrend directions mirror each other, as D1; F2 on mirror-exact fixtures, TPL-F6), the
truncation test over many t, scan = backtest = options backtest (bull put, bear call and iron condor configurations: the
same signals for each) = worker on one trailing window; the user's structure decides the legs (bull put spreads only on
bullish signals, bear call spreads only on bearish ones, condors on either with four legs), the width and the lots come
from the configuration; no pack field, setting or rule names a strike, premium, expiry, lot, spread or order; the language
guard, "no number written into a rule", the ids, the adapter's signal (no target, risk_reward None), provenance and flags;
the API: the cards ("Option seller packs"), adopt as a pre-fill with the note and no option field, an OPTION bull put
deployment on PAPER from the pre-fill keeping the user's width, LIVE refused. No frontend change: the Paper bot form lists
the templates from the API.

## TPL-7: E2 RSI divergence, and the packs version 2 (F1 + D3, F2 + E3)

Same flag `strategy_templates` (OFF), PAPER only, signals only, no migration. Owner questions TPL-Q50..TPL-Q57.

| Part | Where | Status |
|---|---|---|
| `RsiDivergence(side, lookback, degree, period=)`: the RSI's period as a named argument; left out = RSI 14, so every older call (screens, alerts, the idea library) is byte-identical - tested against main's implementation; validated like the other named arguments, a long period asks for its history (TPL-Q54) | `screener/registry.py`, `runtime.py` | done |
| The divergence's prices `RsiDivergenceRef(what, side, degree, period=)`: `extreme` (the divergent swing's own low / high) and `confirm_bar` (the far side of the bar that confirmed it), held from that confirmation bar until the next divergence of the side, missing before the first one; the same swings and RSI as RsiDivergence (one shared reading); flag `screenql_amw_functions`; sparse in the engine; drawn as a line in the evidence (TPL-Q51) | `screener/registry.py`, `runtime.py`, `evidence.py`, `amw_functions.py`, `strategy_templates/engine.py` | done |
| E2 RSI divergence (generic): the setup the rising edge of a regular divergence (instance: the bar that confirmed the swing), the trigger a setting - a change of character on smaller swings (`SwingBreakType`, the smaller size below the divergence's) or a close beyond the confirming bar - within `confirm_max_bars`, the confirming bar itself allowed; invalidation and arm level the divergence's extreme (a close beyond it first voids the setup); RSI period, swing size, divergence age, smaller swing size, break distance all settings; no exit (the order names none) (TPL-Q51..Q53). A3 stays the area-bound version (TPL-Q23) | `templates/E2.json` | done |
| Schema (still `atp.template/1`; every earlier template unchanged): a variant may itself be a switch (nested `variants`, opened one level after the other; a nested switch never re-chooses by an outer setting; every leaf of an invalidation has an expression); `signal_text` may be chosen by an enum setting (`SignalTextSwitch`); `timeframes_by` limits the signal timeframes per choice (a plain message names them in words); `validate_template` compiles every path of nested choices | `schema.py`, `compile.py`, `library.py` | done |
| Packs version 2: F1 `source` = Supertrend flip (D1) or moving-average pullback in trend (D3, not on Weekly); F2 `source` = range edges (A2) or Bollinger mean reversion in a range (E3; the swing size shared with A2 is one setting with the same default and bounds). Each member's parts, settings, switches, timeframes and words are its own (generated from the member JSON; tests hold them equal part by part and series by series) | `templates/F1.json`, `F2.json`, `packs.py` | done |
| Version 1 kept: `templates/archive/F1.v1.json`, `F2.v1.json` are main's version 1 files word for word (their hashes pinned in a test); `library.get_version`, and `ids.decode` resolves an id adopted on version 1 to version 1 - the same signals, words and params hash; the library lists and adopts the newest only (TPL-Q55) | `library.py`, `ids.py` | done |
| A1 as an F1 member | - | not built: A1 is not in the library (areas, TPL-Q23, TPL-Q50) |

Tests: `tests/test_tpl7_e2_packs_v2.py` - RsiDivergence without `period=` equal to main's implementation (seeds, sides, lookbacks,
degrees) and to `period=14`; each period against a reference with that period; the companion against the swings by value
(held, a non-divergent swing leaves it), its validation and history, both read no later bar; E2 "fires exactly" against an
independent walk of S4 / S5 / S6 / R8 with the arm-level breach (both triggers, several settings, swing size 2), both sides,
the must-not cases (no rising edge, late, the extreme closed through first, a flat market), the constraints and the
warm-up by swing size, the mirror test (whole ticks), the truncation test over many t, scan = backtest = options backtest
= worker on one trailing window, property tests on random valid settings, the language guard, "no number written into a
rule", the ids, the adapter's signal, provenance and flags; the nested switch, the signal-text switch and `timeframes_by`
(accepted and refused), every earlier template without them; the version 1 files word for word, version 1 ids resolving
to exactly their behaviour (equal to the member and to version 2's first source; the hash records the version), through
the strategy resolver, unknown versions refused; the new members equal to D3 / E3 on their own; no option field; the API
(cards with two sources, E2 listed, adopt of the new source as a PAPER pre-fill). `tests/test_tpl6_family_f.py` reads
version 2 (its version 1 cases are the default source). The new series are in the runtime fixtures, the no-look-ahead
cases (lookahead job) and the history sweep. No frontend change: the Paper bot form lists the templates from the API.

## Not in this PR (next PRs)

- Family A's area templates A1, A8, A3, A4 (an area companion + zone timelines in the template engine, TPL-Q23), A6 / A7
  (their functions); then the rest of B / C / D / E (TPL-Q40: an engine "extreme since the setup" invalidation for D3, E1,
  E3, E5 and the swing degree for C2: done in step 5; the gap companion for B10; C4 after TPL-Q18; E2 after an RSI period
  and an extreme companion, TPL-Q49: done in TPL-7), then F. Family F: F1 / F2 built (TPL-6 above); their further
  members D3 and E3 joined in pack version 2 (TPL-7); A1 waits for areas (TPL-Q50); G12 next (TPL-F7).
- Section 5 "exits as settings" (stop_source / target_rule on the deployment's exit settings): needs a migration and
  the owner's approval. Until then the adapter's Signal carries only the invalidation as its stop.
- R4 rest: the stop_source rule per instrument_kind and the precedence table with tests (comes with section 5).
- R5: the TEMPLATE_EXIT plumbing (both backtest engines and the position monitor read `TemplateStrategy.exit_signal`
  on each bar close of an open trade; reason "Template exit").
- R2 for the gap family (B10), with its template.
- R7 rest: the projected trendline retest (TPL-Q18); R10 (NEXT_AREA target). (Forwarding the swing degree for C2: done
  in step 5.)
- The stored copy in "My strategies" with provenance (a table: migration), the settings form, Templates page, compare (step 3).
