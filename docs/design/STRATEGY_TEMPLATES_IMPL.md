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
| C1 / C3 (breakout + retest) | - | waits for R7 (TPL-Q8) |
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

## Not in this PR (next PRs)

- C1 / C3 on R7 (TPL-Q8); the rest of the catalogue in section 8 order (A with the idea library, then B / C / D / E, F).
- Section 5 "exits as settings" (stop_source / target_rule on the deployment's exit settings): needs a migration and
  the owner's approval. Until then the adapter's Signal carries only the invalidation as its stop.
- R4 rest: the stop_source rule per instrument_kind and the precedence table with tests (comes with section 5).
- R5: the TEMPLATE_EXIT plumbing (both backtest engines and the position monitor read `TemplateStrategy.exit_signal`
  on each bar close of an open trade; reason "Template exit").
- R2 for the other breakout families (swing, Donchian, pattern, ...), with their templates.
- R7 (RetestEntry forwarding the base screen's params, `of = "channel"`, projected lines - on `claude/wave3-levels-lines`),
  R10 (NEXT_AREA target).
- The stored copy in "My strategies" with provenance (a table: migration), the settings form, Templates page, compare (step 3).
