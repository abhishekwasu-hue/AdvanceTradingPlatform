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

## Not in this PR (next PRs of step 1)

- Section 5 "exits as settings" (stop_source / target_rule on the deployment's exit settings): needs a migration and
  the owner's approval. Until then the adapter's Signal carries only the invalidation as its stop.
- R4 rest: the stop_source rule per instrument_kind and the precedence table with tests (comes with section 5).
- R5: the TEMPLATE_EXIT plumbing (both backtest engines and the position monitor read `TemplateStrategy.exit_signal`
  on each bar close of an open trade; reason "Template exit").
- R2: the breakout level companions (BreakoutLevel, BreakoutFailLevel, BreakoutBaseHigh / Low, BreakoutStopRef); they
  go into `engine.SPARSE_SERIES` so they do not hold the warm-up back.
- R6 wiring: the deployment fetch uses `parity.fetch_bars`; swing templates of degree >= 2 carry the "fetch-dependent"
  note.
- R7 (RetestEntry forwarding the base screen's params, `of = "channel"`, projected lines), R10 (NEXT_AREA target).
- Registering templates for deployments / backtest routes, the stored copy with provenance, the UI (step 3).
