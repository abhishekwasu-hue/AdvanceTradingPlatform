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

## Not in this PR (next PRs)

- Family A's area templates A1, A8, A3, A4 (an area companion + zone timelines in the template engine, TPL-Q23), A6 / A7
  (their functions); then the rest of B / C / D / E in section 10 order (B2 / C2 swing, B4 / C4 trendline with the projected
  retest TPL-Q18, B5 / C5 Donchian, D3-D7, E1-E5), then F.
- Section 5 "exits as settings" (stop_source / target_rule on the deployment's exit settings): needs a migration and
  the owner's approval. Until then the adapter's Signal carries only the invalidation as its stop.
- R4 rest: the stop_source rule per instrument_kind and the precedence table with tests (comes with section 5).
- R5: the TEMPLATE_EXIT plumbing (both backtest engines and the position monitor read `TemplateStrategy.exit_signal`
  on each bar close of an open trade; reason "Template exit").
- R2 for the other breakout families (swing, Donchian, pattern, ...), with their templates.
- R7 rest: the projected trendline retest (TPL-Q18) and forwarding the swing degree (C2); R10 (NEXT_AREA target).
- The stored copy in "My strategies" with provenance (a table: migration), the settings form, Templates page, compare (step 3).
