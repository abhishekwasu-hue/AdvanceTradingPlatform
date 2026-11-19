# Strategy-first Chart — PLAN (roadmap SFC; PLAN ONLY, no code until the owner writes "APPROVED")

Source: owner task + SPEC, 19 Nov (INBOX, verbatim). Owner remark: much of the SPEC already exists, so reuse first.
Method of this plan: a full inventory of the code (three independent read-only audits: chart / engine / price action, paper, live, data),
then the design. Every "reuse" below points at a file that exists and has tests today.

---------------------------------------------------------------------------------------------------------------------------
## 1. INVENTORY

Verdicts: **REUSE** (as is), **MODIFY** (extend, keep behaviour), **REPLACE** (a new module takes over the role; the old one stays,
CLAUDE.md §8), **NEW** (a true gap).

### 1.1 Chart (frontend; lightweight-charts ^5.2.1)
| Path | What it does | Works? (evidence) | Verdict |
|---|---|---|---|
| `frontend/src/chart/chartCore.ts` | v2 `ChartCore`: one chart with native v5 panes (price, volume, RSI, one shared "own" study pane), 4 fixed overlays, levels (price lines), areas, markers via `createSeriesMarkers` (:528), attribution logo ON (:197-198) | ChartV2 / gestures / viewMemory tests, `lightweightFake.fixture` | MODIFY: one pane PER oscillator (today all "own" studies share one pane, :628-640); study colours from tokens |
| `frontend/src/chart/ChartV2.tsx` | React wrapper; `markers` prop is the ONLY v2 marker entry (:210); legend lists the 4 overlays only | ChartV2.test (asserts `attributionLogo: true`) | MODIFY: strategy legend slot |
| `frontend/src/chart/ChartsPageV2.tsx` | `/charts` page; merges every layer's markers (:202-205) and levels (:375); toolbar `actions` (:276-300) | chartsPageV2.test | MODIFY: host the strategy selection |
| `frontend/src/chart/ChartToolbar.tsx` | the one v2 toolbar (full / phone / compact) | ChartToolbar.test | MODIFY: a "Strategy" picker control (like IndicatorsMenu) |
| `frontend/src/chart/IndicatorsMenu.tsx`, `indicatorsModel.ts` | Indicators menu (panes, 4 overlays, flagged layers) | chartsPageV2.test | REUSE (strategy items are separate) |
| `frontend/src/chart/templateLines.ts` | per-template indicator lines (MA, Supertrend, MACD, RSI, BB, ADX, Stochastic, VWAP, Donchian) + `exitMarkerText` | templateLines.test | REUSE for templates; the new strategy gets its lines from the SERVER (one formula source) |
| `frontend/src/chart/BacktestChartV2.tsx` | backtest chart: template lines, entry / setup / exit markers, per-trade stop segment, setup-area box, legend | backtestChart.test; BacktestPage + StrategyFlowV2 | REUSE pattern; extract the shared trade-layer code |
| `frontend/src/chart/TemplateOverlay.tsx` | `?tpl=` overlay: S / T / W markers + "Wrong beyond" level + legend; same-timeframe rule | TemplateOverlay.test | REUSE (pattern for a server-driven overlay) |
| `frontend/src/chart/ScreenOverlay.tsx` + `screener/evidence.ts:357 evidenceChartLayers` | generic "drawables -> chart" adapter (series, levels, bands, boxes, markers) | watchScreen.test | REUSE: the plot response uses the same drawables shape |
| `frontend/src/components/ChartStrategies.tsx` (`useChartStrategies`) | v1 only: strategy toggles, `/chart-run`, entry/exit markers, entry/SL/T1 lines, stats | no direct tests; ProChart only | MODIFY: port the idea to a v2 hook; v1 untouched |
| `frontend/src/components/ProChart.tsx` | v1 chart (non-`ui_v2` pages) | chartConsistency tests | REUSE as is (no new work, never deleted) |
| `frontend/src/charting/engine.ts`, `lightweight.ts`, `primitives.ts`, `geometry.ts` | `ChartEngine` / `LightweightEngine`: drawings, `setLayer` (bands, segments, labels, marks), `setStudy`; `LayerData.markers` declared but NOT rendered | charting / geometry tests | MODIFY: a position-box layer shape (reuse `geometry.positionStats` + the long/short position drawing painter, `geometry.ts:126-138, 238-252`) |
| `frontend/src/charting/markers.ts` | `ChartMarker` type + `directionMarker` | markLabels.test | REUSE |
| `frontend/src/styles/tokens.css:173-230`, `chart/chartTheme.ts` | chart tokens (`--chart-ov-1..4`, up / down, ...) | chartTheme.test | MODIFY: add `--chart-strat-1..6` (light / dark / cvd) — today strategy colours are raw hex |
| Drawing tools (`charting/tools.ts`, `drawings.ts`, `chart/DrawingRail.tsx`) | user drawings incl. long / short position box; server storage | tools / geometry tests | REUSE; strategy layers never touch user drawings |

### 1.2 Indicators
| Path | What it does | Works? | Verdict |
|---|---|---|---|
| `backend/app/screener/registry.py` | 201 ScreenQL functions with units / limits: SMA EMA WMA HMA TMA, RSI, ADX ±DI, ATR, VWAP, Supertrend, BB, Keltner, MACD (3), Stoch K/D, StochRSI, PSAR, CCI, MFI, Williams %R, OBV, Aroon, Ichimoku, volume, session levels, ... | test_s1a_screenql + test_z10a_no_lookahead (every function registered causal) | REUSE: the block catalogue = this registry |
| `backend/app/indicators/**` + `prefix_cache.py` | indicator math + prefix cache | BENCHMARKS.md, test_realism_benchmark | REUSE |
| `frontend/src/utils/indicators.ts` | frontend copies of EMA / RSI / ADX / ST / VWAP / BB | indicators.parity.test vs backend | REUSE (not used for strategy plots: the server computes) |
| `backend/app/charts/studies.py` | `GET /api/charts/studies` ScreenQL studies on lake bars | studies.test | REUSE (pattern) |

### 1.3 Signal engines
| Path | What it does | Works? | Verdict |
|---|---|---|---|
| `backend/app/screener/{nodes,parser,validator,runtime}.py` | ScreenQL: AND / OR / NOT / ALL / ANY, BETWEEN, IN, `[offset]`, `@tf`; closed bars only; higher TF aligned by close time (runtime.py:405-418); NaN never matches (`_cmp` :1749, `_cross` :1784); lookback per TF (validator.py:50-101) | test_s1*, fuzz, test_z10a | REUSE: the ONE evaluator core |
| `backend/app/strategy_templates/{schema,compile,engine,adapter,parity,exits,management}.py` | `atp.template/1`; `compile_recipe`; `engine.compute(recipe, frames) -> SignalSeries` (pure, whole series once); `TemplateStrategy(BaseStrategy)`; parity harness scan / backtest / options / worker; stop sources / target rules; trailing `management.step` | test_tpl1_parity, test_tpl*, sx tests | REUSE (compile, warm-up, parity, exits, trailing) |
| `backend/app/strategy_engine/declarative.py` | no-code `custom:<id>`: flat AND-only, 24 operands, 6 comparators, per-operand higher TF; last-bar `evaluate` + separate `holds_series` | test_declarative_strategy and 3 more | REPLACE for new strategies (stays as the loader of saved v1 configs; translated to v2 on read) |
| `backend/app/strategy_engine/base.py` | `BaseStrategy.analyze(frames, symbol) -> Signal` — the one signal call of worker (trading_worker.py:2310), runner (runner/loop.py:337), backtest / chart-run | every engine path | REUSE: the new kind plugs in here |
| `backend/app/custom_strategies/{routes,resolver,versioning}.py`; `db/models.py:1029 CustomStrategyRecord` | CRUD, versions, rollback; `resolve_strategy`; `config_json` is free TEXT | test_custom_strategies_api, test_strategy_versioning | MODIFY: accept `atp.strategy/2`; resolver branch; export / import / duplicate. **No migration** |
| `backend/app/strategy_engine/chart_routes.py` | `POST /api/strategies/{id}/chart-run`: run_backtest over CLIENT-sent candles | 1 test | REPLACE by the plot endpoint (server bars, same read as the bot); kept for v1 |
| `backend/app/backtest/{engine,windows,warmup,bar_source,options_engine,repro}.py` | closed-bar backtest, `WindowCursor`, deferred fill at next open, ExitRules, options pricers, fingerprints | test_backtest, test_realism_* | REUSE |
| `frontend/src/strategyFlow/{model.ts,StrategyFlowV2.tsx}` | stepped builder (ui_v2); blocks AND-only; "soon" PA blocks; DEF-0 (blank = off) | model.test | MODIFY: becomes the Builder drawer content |

### 1.4 Chart reader / price action (all causal, ported from the Trade repo)
| Path | What it does | Verdict |
|---|---|---|
| `price_action/causal_swings.py` | confirmed multi-degree pivots (`known_at`) | REUSE |
| `price_action/structure_tracker.py`, `structure_series.py` | HH / HL / LH / LL / EQH / EQL, BOS, CHoCH, range per closed bar | REUSE (PA calculator series API) |
| `price_action/breaks.py`, `legs.py`, `liquidity.py`, `trendlines.py`, `level_strength.py`, `reversal.py` | real vs false break, legs, pools / sweeps, trendlines, zone events, reversal at a level | REUSE |
| `price_action/level_gate/**`, `strategy_templates/area_gate.py` | pullback into demand / supply area -> commitment candle (A1) as a causal series | REUSE ("pullback to level / zone" block) |
| `price_action/zones/**` | S/R, bases, order blocks; STORED nightly snapshot, `read_as_of` never later than `at` | REUSE (zones are as-of reads, never per-bar recompute) |
| `price_action/sd_zones.py`, `pa_pack.py`, `candlestick_patterns.py` via ScreenQL `Pattern(...)` | S&D zones, PA pack, candle patterns | REUSE |
| ScreenQL `RsiDivergence`, `RetestEntry`, `SwingBreakout`, `RangeBreakout`, `NearSupport/Resistance`, `SwingHigh/Low`, `StructureEvent`, `Trend` | the PA functions already exposed to the evaluator | REUSE |
| `charts/price_action_layer.py`, `read_chart.py`, `zones_layer.py` | chart PA layers; read SHARED lake bars only | REUSE the pure parts; the strategy plot reads bars like the bot (see 2) |
| `price_action/market_structure.py`, `swings.py` (older, non-causal labelled) | legacy structure | NOT used by this work |
| `chart_analyze/**`, `chart_reader/**` | AI / vision reading | NOT on the signal path (ADR-0006) |

### 1.5 Paper mode, live orders, data feed
| Path | What it does | Verdict |
|---|---|---|
| `workers/trading_worker.py:2218-2449 _evaluate_deployment` | resolve -> bars -> staleness -> `closed_frames` -> `analyze` (:2310) -> gates -> contract -> `execute_signal_for_user` | MODIFY: extract :2222-2316 into a pure `evaluate()` shared with runner and chart |
| `runner/loop.py:302-415` | the user's runner: same steps (duplicated) | MODIFY: call the same `evaluate()` |
| `execution/signal_execution.py:129 execute_signal_for_user` + `execution/router.py OrderRouter` | the one entry chokepoint; PAPER and LIVE on one `_route` | REUSE as is (no change to order paths) |
| `brokers/rate_budget.py:439-448` + `platform/phase0_lock.py` | live lock on ENTRY orders only; exits always pass (ADR-0004) | REUSE as is |
| `execution/paper_broker.py`, `paper_adapter.py`, `paper/**` | PAPER fills, account, tickets | REUSE |
| `trading/position_monitor.py`, `exit_rules.py`, `square_off.py` | the one close path (paper + live) | REUSE |
| `db/models.py:601 StrategyDeploymentRecord` | bot row: symbol, mode, broker, exit_rules, option fields, template exits | REUSE; strategy-level exits / options become the DEFAULT copy into a new deployment, the row stays the runtime truth |
| `workers/strategy_bars.py` + `market_store/reader.py read_bars(user=, rest=own_rest)` | the bot's bars: own store + own REST top-up (M86) | REUSE: THE bar read for chart, paper and live |
| `market_data/freshness.py closed_frames`, `market_data/universe_feed.py` + `bar_builder.py` | closed-bar cut; ticks -> closed bars (off) | REUSE / finish in P6 |
| `market_lake/bhavcopy*`, `market_data/eod_routes.py` | EOD for users without a broker (M86 D2) | REUSE (EOD strategies) |
| `quality/lookahead.py` + `quality/causal_cases.py` | no-look-ahead harness; coverage test fails on an unregistered function | REUSE; every new block registers here |

**Gaps found (facts):** (1) the chart PA layer and the bot read different bar sources; (2) the declarative engine is AND-only with no
PA blocks; (3) exits / time window / options live on the deployment, not the strategy; (4) no scan = backtest = live harness for
custom strategies; (5) no v2 strategy picker; one shared oscillator pane; no position-box layer; strategy colours are hex;
(6) no static ADR-0006 import-guard test; (7) declarative higher-TF and session operands are recomputed per bar (O(n²)).

---------------------------------------------------------------------------------------------------------------------------
## 2. ARCHITECTURE

```
 broker feed (user's own)        bhavcopy / shared EOD lake
        |  ticks -> closed bars (bar_builder)      |
        v                                          v
 +------------------------------------------------------------------+
 | BAR STORE: market_store.reader.read_bars(user=, rest=own_rest)   |  closed bars only, own rows only (M86)
 +------------------------------------------------------------------+
        | frames {base TF, every @tf the strategy reads}  (+ forming bar, display only)
        v
 +------------------------------------------------------------------+
 | CALCULATORS (pure, causal, whole series once)                     |
 |  ScreenQL runtime _Eval: indicators (registry) + PA functions     |
 |  (causal_swings, structure_series, breaks, area_gate, zones as-of,|
 |   patterns, RsiDivergence ...)                                    |
 +------------------------------------------------------------------+
        | per-leaf series (value + truth)            
        v
 +------------------------------------------------------------------+
 | STRATEGY EVALUATOR  strategy_v2.evaluate(compiled, frames)        |  ONE function
 |  -> EvaluationSeries: entry_long / entry_short per bar, why per   |
 |     leaf (values), exit-condition truth, warm-up reason           |
 |  -> trade walker (backtest engine + exits + management.step)      |
 +------------------------------------------------------------------+
        |                          |                          |
        v                          v                          v
 CHART PLOT API              PAPER engine                LIVE order engine
 POST /api/strategies/       StrategyV2(BaseStrategy)    same BaseStrategy.analyze
  v2/{id}/plot               .analyze -> worker /        -> execute_signal_for_user
 (series, PA levels,          runner -> execute_signal_   -> OrderRouter (venue LIVE)
  markers, why, trades,       for_user (venue PAPER)      locked by phase0 / live_trading
  stats, pending hint)
```

**Same evaluator confirmed:** chart, paper and live all call `strategy_v2.evaluate` on frames from `read_bars(user=...)`. The chart
calls it directly (whole series); the worker / runner call it through `StrategyV2.analyze`, which reads the last closed bar of the
SAME series (the TemplateStrategy pattern, `adapter.py`). A parity test (P0) proves chart markers = backtest entries = worker
signals on the same bars. Order paths are not changed: the evaluator only returns signals (CLAUDE.md §3, ADR-0006).

---------------------------------------------------------------------------------------------------------------------------
## 3. STRATEGY DATA MODEL (`atp.strategy/2`, stored in `CustomStrategyRecord.config_json`; no migration)

Rules: nothing has a default value; a missing / null field means "off". The validator refuses an incomplete rule (e.g. a stop kind
without its value) instead of filling it. Saved v1 configs (`CustomStrategyConfig`) are translated to v2 on read (AND group,
same operands); they still run unchanged through `DeclarativeStrategy` until the owner decides otherwise (OQ-6).

### 3.1 JSON Schema (draft 2020-12, abbreviated `$defs` only where repeated)
```json
{
  "$id": "atp.strategy/2",
  "type": "object",
  "required": ["schema", "version", "name", "timeframe", "entry"],
  "additionalProperties": false,
  "properties": {
    "schema":    {"const": "atp.strategy/2"},
    "version":   {"type": "integer", "minimum": 1},
    "name":      {"type": "string", "minLength": 1, "maxLength": 80},
    "notes":     {"type": "string", "maxLength": 2000},
    "timeframe": {"$ref": "#/$defs/tf"},
    "entry": {
      "type": "object", "required": ["sides"], "additionalProperties": false,
      "properties": {
        "sides": {"enum": ["long", "short", "both"]},
        "long":  {"$ref": "#/$defs/group"},
        "short": {"$ref": "#/$defs/group"},
        "window": {"type": ["object", "null"], "required": ["from", "to"],
                   "properties": {"from": {"$ref": "#/$defs/hhmm"}, "to": {"$ref": "#/$defs/hhmm"}}},
        "max_entries_per_day": {"type": ["integer", "null"], "minimum": 1}
      }
    },
    "exit": {
      "type": ["object", "null"], "additionalProperties": false,
      "properties": {
        "stop":     {"$ref": "#/$defs/level_rule"},
        "target":   {"$ref": "#/$defs/level_rule"},
        "trailing": {"type": ["object", "null"], "required": ["kind"],
                     "properties": {"kind": {"enum": ["points", "pct", "atr", "swing"]},
                                    "value": {"type": "number", "exclusiveMinimum": 0},
                                    "atr_period": {"type": "integer", "minimum": 1},
                                    "swing_degree": {"type": "integer", "minimum": 0, "maximum": 3},
                                    "start_after": {"$ref": "#/$defs/level_rule"}}},
        "time_exit": {"type": ["object", "null"],
                      "properties": {"after_bars": {"type": "integer", "minimum": 1},
                                     "at": {"$ref": "#/$defs/hhmm"}}},
        "long_conditions":  {"$ref": "#/$defs/group"},
        "short_conditions": {"$ref": "#/$defs/group"}
      }
    },
    "options": {
      "type": ["object", "null"], "required": ["legs", "strike", "expiry"],
      "properties": {
        "legs":   {"type": "array", "minItems": 1, "maxItems": 4, "items": {
                     "type": "object", "required": ["action", "right", "ratio"],
                     "properties": {"action": {"enum": ["buy", "sell"]}, "right": {"enum": ["CE", "PE"]},
                                    "ratio": {"type": "integer", "minimum": 1},
                                    "strike_offset": {"type": "integer"}}}},
        "strike": {"type": "object", "required": ["rule"],
                   "properties": {"rule": {"enum": ["atm", "itm", "otm", "delta", "premium"]},
                                  "steps": {"type": "integer", "minimum": 0},
                                  "delta": {"type": "number"}, "premium": {"type": "number"}}},
        "expiry": {"type": "object", "required": ["rule"],
                   "properties": {"rule": {"enum": ["current_week", "next_week", "current_month", "next_month"]}}},
        "side_map": {"type": "object", "properties": {"long": {"type": "string"}, "short": {"type": "string"}}}
      }
    }
  },
  "$defs": {
    "tf":   {"enum": ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "1d", "1w"]},
    "hhmm": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$"},
    "group": {
      "type": ["object", "null"], "required": ["op", "items"],
      "properties": {"id": {"type": "string"},
                     "op": {"enum": ["AND", "OR"]},
                     "items": {"type": "array", "minItems": 1, "maxItems": 20,
                               "items": {"oneOf": [{"$ref": "#/$defs/condition"}, {"$ref": "#/$defs/group"}]}}}
    },
    "condition": {
      "type": "object", "required": ["id", "left", "cmp"],
      "properties": {
        "id": {"type": "string"},
        "timeframe": {"$ref": "#/$defs/tf"},
        "left":  {"$ref": "#/$defs/operand"},
        "cmp":   {"enum": ["crosses_above", "crosses_below", "gt", "lt", "gte", "lte", "between",
                           "rising", "falling", "is_true"]},
        "right": {"$ref": "#/$defs/operand"},
        "low":   {"$ref": "#/$defs/operand"}, "high": {"$ref": "#/$defs/operand"},
        "bars":  {"type": "integer", "minimum": 1},
        "within_bars": {"type": ["integer", "null"], "minimum": 1}
      }
    },
    "operand": {
      "oneOf": [
        {"type": "object", "required": ["kind", "value"], "properties": {"kind": {"const": "value"}, "value": {"type": "number"}}},
        {"type": "object", "required": ["kind", "field"], "properties": {"kind": {"const": "price"},
           "field": {"enum": ["open", "high", "low", "close", "volume", "oi"]}, "offset": {"type": "integer", "minimum": 0}}},
        {"type": "object", "required": ["kind", "block", "params"], "properties": {"kind": {"enum": ["indicator", "pa"]},
           "block": {"type": "string"}, "params": {"type": "object"}, "output": {"type": "string"},
           "offset": {"type": "integer", "minimum": 0}}}
      ]
    },
    "level_rule": {
      "type": ["object", "null"], "required": ["kind"],
      "properties": {"kind": {"enum": ["points", "pct", "atr", "swing", "candle", "premium"]},
                     "value": {"type": "number", "exclusiveMinimum": 0},
                     "atr_period": {"type": "integer", "minimum": 1},
                     "swing_degree": {"type": "integer", "minimum": 0, "maximum": 3},
                     "candle": {"enum": ["entry", "signal", "previous"]},
                     "pct_of_premium": {"type": "number", "exclusiveMinimum": 0}}
    }
  }
}
```
Semantics: AND binds tighter than OR inside the builder's flat chain; the stored form is always an explicit tree (`group`), so the
precedence is fixed at save time and never re-parsed. `within_bars` = "true on the last closed bar or within the last N closed bars".
`timeframe` on a condition = all its operands read that TF (must be ≥ the strategy TF and an exact multiple; validator). `window`,
`max_entries_per_day` and `time_exit.at` are operational settings of the user (time allowed for operational settings, OWNER C.4);
none affects how a condition is read.

### 3.2 Complete example (made-up values, nothing pre-selected by ATP)
```json
{
  "schema": "atp.strategy/2", "version": 1, "name": "EMA trend + pullback to support",
  "timeframe": "15m",
  "entry": {
    "sides": "long",
    "long": {"op": "AND", "items": [
      {"id": "c1", "timeframe": "1h", "left": {"kind": "indicator", "block": "EMA", "params": {"source": "close", "period": 20}},
       "cmp": "gt", "right": {"kind": "indicator", "block": "EMA", "params": {"source": "close", "period": 50}}},
      {"op": "OR", "items": [
        {"id": "c2", "left": {"kind": "pa", "block": "pullback_to_zone", "params": {"zone": "support", "degree": 1}},
         "cmp": "is_true", "within_bars": 3},
        {"id": "c3", "left": {"kind": "pa", "block": "rsi_divergence", "params": {"kind": "regular", "side": "bullish", "period": 14}},
         "cmp": "is_true", "within_bars": 5}
      ]},
      {"id": "c4", "left": {"kind": "pa", "block": "candle_pattern", "params": {"name": "bullish_engulfing"}}, "cmp": "is_true"}
    ]},
    "window": {"from": "09:30", "to": "14:45"},
    "max_entries_per_day": 2
  },
  "exit": {
    "stop": {"kind": "swing", "swing_degree": 1},
    "target": {"kind": "atr", "value": 2.0, "atr_period": 14},
    "trailing": {"kind": "atr", "value": 1.5, "atr_period": 14},
    "time_exit": {"after_bars": 20},
    "long_conditions": {"op": "OR", "items": [
      {"id": "x1", "left": {"kind": "price", "field": "close"}, "cmp": "crosses_below",
       "right": {"kind": "indicator", "block": "EMA", "params": {"source": "close", "period": 20}}}
    ]}
  },
  "options": {
    "legs": [{"action": "buy", "right": "CE", "ratio": 1}],
    "strike": {"rule": "atm"}, "expiry": {"rule": "current_week"},
    "side_map": {"long": "legs"}
  }
}
```
Compiled form (internal, never stored): `c1 = EMA(close,20)@1h > EMA(close,50)@1h`, `c2 = Count(3, AreaGate(support,1)) >= 1`,
`c3 = Count(5, RsiDivergence(14,"regular") == 1) >= 1`, `c4 = Pattern("bullish_engulfing")`, entry = `c1 AND (c2 OR c3) AND c4`.

---------------------------------------------------------------------------------------------------------------------------
## 4. BLOCK CATALOGUE

Every block compiles to ScreenQL functions that are already registered causal (`causal_cases.py`); outputs are per-bar series.
"Pane" is where the chart draws it.

### 4.1 Indicator blocks (settings editable; no preset values in the UI)
| Block | Inputs | Outputs | Module (ScreenQL fn) | Pane |
|---|---|---|---|---|
| SMA / EMA / WMA / HMA / TMA | source, period | value | registry MA family | price |
| VWAP | — | value | `VWAP` / `SessionVWAP` | price |
| Bollinger | period, k | upper, mid, lower | `BBUpper/Mid/Lower` | price |
| Keltner | period, k | upper, mid, lower | `Keltner*` | price |
| Supertrend | period, k | line, direction | `Supertrend`, `SupertrendDir` | price |
| PSAR | step, max | value, direction | `PSAR`, `PSARDir` | price |
| Ichimoku | tenkan, kijun, span | tenkan, kijun, spanA, spanB | `Ichimoku*` | price |
| Donchian / Max / Min | period | high, low | `Max`, `Min` (prior bars) | price |
| RSI | period | value | `RSI` | own (0-100) |
| MACD | fast, slow, signal | macd, signal, hist | `MACD`, `MACDSignal`, `MACDHist` | own |
| Stochastic / StochRSI | k, smooth, d | k, d | `StochK`, `StochD`, `StochRSI` | own |
| ADX / +DI / −DI | period | adx, plus, minus | `ADX`, `PlusDI`, `MinusDI` | own |
| ATR | period | value | `ATR` | own |
| CCI / MFI / Williams %R / OBV / Aroon | period | value(s) | registry | own |
| Volume / Volume SMA / Relative volume | period | value | `volume`, `SMA(volume,n)`, `RelVolume` | volume |
| Session levels (day open, PDH, PDL, PDC, opening range) | minutes for OR | value | `DayOpen`, `PDH`, `PDL`, `PDC`, `ORHigh/ORLow` | price (levels) |
"Every indicator ATP computes" = every registry function with a numeric output (P5 lists them from the registry, not by hand).

### 4.2 Price-action blocks (reuse the existing causal engine)
| Block | Inputs | Outputs | Module | Drawn as |
|---|---|---|---|---|
| Swing high / low | degree 0-3 | last confirmed level, bar | `SwingHigh/SwingLow` (causal_swings) | price lines + small dots |
| Structure HH / HL / LH / LL | degree | label event (bool per label) | `StructureEvent` (structure_series) | labels on swings |
| Trend (structure) | degree | up / down / range | `Trend` / `DowTrend` | legend chip |
| Break of structure / CHoCH | degree | event | `StructureEvent` | marker + broken level segment |
| Support / resistance zone | zone kind, max zones | nearest zone low / high, inside? | Z7a zones as-of (`NearZone`, `ZoneRole`) / `NearSupport`, `NearResistance` | bands |
| Pullback to level / zone | zone kind, degree | event | `AreaGate` (level_gate) | band + marker |
| Breakout / retest | kind (range / swing / trendline / donchian), n | event + level | `RangeBreakout`, `SwingBreakout`, `TrendlineBreakout`, `RetestEntry` + `*Ref` | level segment |
| Candle pattern | name (doji, hammer, shooting star, engulfing, morning / evening star, pin bar, inside / outside bar, rejection), confirm | event | `Pattern(name, confirm)` | marker letter |
| RSI divergence | regular / hidden, bullish / bearish, period | event + the two pivots | `RsiDivergence`, `RsiDivergenceRef` | two-point segment on price and RSI |

### 4.3 Comparators (exact meaning; `[0]` = the last CLOSED bar of the condition's TF)
| Comparator | True on bar t when | NaN / warm-up |
|---|---|---|
| crosses above | `L[1] <= R[1] AND L[0] > R[0]` (`_cross`, same as today) | False |
| crosses below | `L[1] >= R[1] AND L[0] < R[0]` | False |
| > / < / >= / <= | `L[0] op R[0]` (`_cmp`) | False |
| between | `low[0] <= L[0] <= high[0]` (inclusive, `BETWEEN`) | False |
| is rising (bars = n) | `L[0] > L[1] > ... > L[n]` (n consecutive rises; n required, no default) | False |
| is falling (bars = n) | `L[0] < L[1] < ... < L[n]` | False |
| is true (pattern / PA event) | the event series is true on `[0]` | False |
| + within last N bars | `Count(N, cond) >= 1` over the last N closed bars of that TF, including `[0]` | False until N bars exist |
A higher-TF condition holds its value on every base bar until the next higher-TF bar CLOSES (runtime `align` by close time).

---------------------------------------------------------------------------------------------------------------------------
## 5. EVALUATION ENGINE DESIGN (new package `backend/app/strategy_v2/`: schema.py, compile.py, evaluate.py, adapter.py, plot.py)
- **Compile:** `compile(cfg) -> Compiled`: every leaf -> one ScreenQL AST (parser / validator reused), validated for units and TF;
  the tree -> a boolean expression over leaf ids; `lookback` per TF from the validator; `params_hash` for caching / fingerprints.
- **Evaluate (pure):** `evaluate(compiled, frames) -> EvaluationSeries`. Each leaf is evaluated ONCE over the whole closed series
  (`_Eval`, like `engine.compute`), giving a value series per operand and a truth series per leaf; the tree combines truth arrays
  (vectorised). Output per bar: `entry_long`, `entry_short`, `exit_long`, `exit_short`, `warm` and `why[leaf] = (true?, values)`.
- **Closed bars only, no repaint:** frames come through `closed_frames` / `read_bars` (closed bars only). A value at bar t uses bars
  ≤ t only (registry functions are proven causal by `quality/lookahead.py`). New test: truncating the series at any t never changes
  bars ≤ t (the harness's `truncation` check on `evaluate`), so a placed marker never moves.
- **Multi-timeframe rule:** a bar of TF X is visible to a base bar only after it closed: `close_time(X bar) <= close_time(base bar)`
  (`runtime.align`, M93-Q1). Higher TF must be an exact multiple of the base TF; lower-than-base TF is refused.
- **Forming bar ("pending"):** computed separately on `closed + forming` and returned as `pending: {side, leaves}` for display only
  (hollow, dimmed marker). Never stored, never sent to paper / live, never counted in stats; it disappears or becomes a real marker
  when the bar closes.
- **Warm-up per indicator:** validator lookback per TF (e.g. EMA n needs n bars, MACD slow + signal, RSI n + 1, HTF counts closed HTF
  bars); a bar is `warm` only when every leaf it reads has a value. Before that: no signal, reason "warming up" (shown in the legend).
  The plot read fetches `lookback × SAFETY_FACTOR` extra bars (parity.py R6) so the visible window starts warm.
- **Entry walker:** a signal = the bar where the side's tree is true and was false on the previous closed bar (edge; OQ-1).
  Taken when flat, inside `window`, under `max_entries_per_day`; entry fill = the backtest rule (next bar's open, as today).
- **Exits:** reuse the backtest engine: stop / target from `level_rule` (points, %, ATR, swing via `SwingHigh/Low`, candle high / low,
  premium % for options via `options_engine`), trailing via `management.step`, time exit (bars or clock), exit conditions (the same
  evaluator's `exit_*` series, acted on at the next open). Each exit records its rule id (`exit_reason`).
- **Adapter:** `StrategyV2(BaseStrategy)`: `analyze(frames, symbol)` returns the signal of the last closed bar from the same
  `evaluate` output (cached per frames fingerprint), so the worker, runner and backtest use the identical function.
- **Performance target:** 3,000 bars, 10 leaves, 2 TFs: evaluate ≤ 300 ms, whole plot (bars read + evaluate + trades) ≤ 1 s server
  p95; 2 strategies on one chart ≤ 1.5 s; bar-close re-evaluation ≤ 100 ms per deployment (cached prefix). Guard:
  a `test_sfc_perf` benchmark (ATP_PERF=1) + a growth-ratio test like `test_realism_benchmark` (no O(n²)).

---------------------------------------------------------------------------------------------------------------------------
## 6. CHART PLOTTING LAYER
- **API:** `POST /api/strategies/v2/{id}/plot {symbol, exchange, timeframe, bars?}` (own strategy only, 404 otherwise; flag
  `strategy_chart`). Bars are read on the server with `read_bars(user=...)` (same as the bot). Response: `series[]` (id, block,
  output, pane, values), `levels[]` / `bands[]` / `segments[]` (PA), `markers[]` (time, kind entry_long / entry_short / exit /
  signal_skipped / pending, rule id), `why{marker_id: [{leaf, text, values}]}`, `trades[]`, `stats`, `state`, `last_signal`, `gaps`.
- **Add only what it uses:** the frontend draws exactly `series[]`; panes: price overlays on pane 0; every oscillator block gets its
  OWN pane (ChartCore change: pane per study group, not one shared pane); volume blocks join the volume pane.
- **PA levels:** bands / segments through `engine.setLayer("strategy:<id>", ...)` (existing LayerPrimitive).
- **Markers:** one `createSeriesMarkers` plugin per chart (already the case); the strategy hook adds its markers to the page's merged
  list with an id prefix `s<id>:`. Long entry = `arrowUp` / `belowBar`; short entry = `arrowDown` / `aboveBar`; exit = `circle`
  (colour by P&L sign, text = rule short name); skipped signal (already in a trade / outside window) = small square, muted;
  pending = hollow circle, dimmed.
- **Price lines + position box:** open (or last) trade: entry / SL / target price lines (`createPriceLine`, axis labels "Entry",
  "SL", "Target"); position box = a new LayerData shape `position` (filled profit / loss rects from entry time to exit time or the
  right edge, R:R label) reusing `geometry.positionStats` and the position-drawing painter.
- **Remove only what it added:** every series, layer, price line and marker carries the strategy key; deselect calls
  `removeStrategy(id)` which removes exactly those keys (test: the engine state equals the snapshot before select).
- **Multi-strategy colours:** `--chart-strat-1..6` tokens (light / dark / colour-blind sets); a strategy gets the first free colour;
  its lines, markers and box edge use it; up / down meaning colours stay green / red.
- **Legend:** one row per selected strategy: colour swatch, name, state (Warming up / Waiting / In trade / Exited), last signal
  (time + side), × to deselect; its indicator names listed under it (collapsible).
- **Marker hover / tap:** a tooltip listing the conditions that were true on that bar with their values (e.g. "EMA(20) 1h 24,512 >
  EMA(50) 1h 24,380"), the group they satisfied; for an exit: the rule that fired (e.g. "Trailing (ATR 1.5)") and the P&L.
- **Switch symbol / timeframe:** the hook re-requests the plot (abortable fetch); stale markers are removed before the new ones are
  painted; a strategy whose base TF is not the chart TF shows "Built for 15m — switch" in its legend (same rule as TemplateOverlay).

---------------------------------------------------------------------------------------------------------------------------
## 7. UI WIREFRAMES (all in place on /charts; no page navigation)
```
Toolbar (desktop)
[NIFTY ▾] [15m ▾] [Candles ▾] [Indicators] [Strategy ▾ (2)] [Draw] ...                     [⚙]
                                            └ dropdown ───────────────────────┐
                                              ☑ EMA trend + pullback   ● teal  │
                                              ☑ Breakout retest        ● amber │
                                              ☐ RSI divergence swing           │
                                              ─────────────────────────────── │
                                              + New strategy   ✎ Edit   ⧉ Dup  │
                                              ⇩ Import JSON    ⇧ Export        │
                                              └────────────────────────────────┘
Phone: [Symbol] [TF] [Indicators] [Strategy] [More]  -> the dropdown opens as a bottom sheet.

Legend (top-left of the price pane)
● EMA trend + pullback   In trade · Long 10:45            [stats] [×]
   EMA 20 (1h) · EMA 50 (1h) · RSI 14
● Breakout retest        Waiting · last Short yesterday    [stats] [×]

Marker tooltip
┌ Long entry · 10:45 ───────────────────────┐
│ ✓ EMA 20 (1h) 24,512 > EMA 50 (1h) 24,380 │
│ ✓ Pullback to support (within 3 bars)     │
│ ✓ Bullish engulfing                       │
│ ✗ RSI divergence (not needed: OR group)   │
└───────────────────────────────────────────┘
┌ Exit · 13:15 ─────────────────┐
│ Rule: Trailing (ATR 1.5)       │
│ P&L +42.5 pts · +1.3 R         │
└───────────────────────────────┘

Quick-stats drawer (right side; bottom sheet on phone)
Trades 14 · Win 57% · P&L +312 pts · Max DD −96 · Avg R +0.6
| Time        | Side | Entry  | Exit   | P&L  | Exit reason |
| 18 Nov 10:45| Long | 24,520 | 24,563 | +43  | Trailing    |   <- click = scroll chart to the trade
...

Strategy Builder drawer (right side, over the chart; full-height sheet on phone)
Name [______________]   Timeframe [15m ▾]   Sides (•Long ○Short ○Both)
ENTRY — LONG
 [EMA ▾ 20] (1h ▾)  [is above ▾]  [EMA ▾ 50]                    [×]
 AND ▾
 ( [Pullback to zone ▾ support] [is true] within [3] bars   OR ▾
   [RSI divergence ▾ regular bullish] [is true] within [5] )   [group]
 AND ▾
 [Candle pattern ▾ bullish engulfing] [is true]
 [+ condition] [+ group]
Window [09:30]–[14:45]   Max entries per day [2]
EXIT  Stop [Swing ▾ degree 1]  Target [ATR ▾ 2.0 × 14]  Trailing [ATR ▾ 1.5]
      Time exit [20] bars   Exit when [+ condition]
OPTIONS (optional)  Legs [Buy CE ×1]  Strike [ATM ▾]  Expiry [Current week ▾]
[Save] [Save as…] [Run on chart]                (blank fields = off; nothing pre-selected)
```
Explanations sit behind "Details" (prose guard); every always-visible label ≤ 60 characters; colours from tokens only.

---------------------------------------------------------------------------------------------------------------------------
## 8. BUILD PHASES (ordered; each one PR, shippable behind flag `strategy_chart` under `ui_v2`, migration: none in any phase)
| Phase | Scope | SPEC acceptance tests covered | Estimate |
|---|---|---|---|
| P0 | `strategy_v2` schema + validator (DEF-0) + compiler to ScreenQL + `evaluate` + `StrategyV2` adapter + resolver branch + v1→v2 translation; parity test chart = backtest = worker; no-look-ahead truncation test; ADR-0006 import-guard test; perf test | (engine for all) | 2-3 days |
| P1 | plot API (server bars via `read_bars(user)`) + v2 hook + toolbar Strategy dropdown (single strategy) + indicator series in correct panes (pane per oscillator) + entry / exit markers | **T1** (only its indicators + markers on every qualifying closed bar; PA levels part in P2), **T3** deselect = prior state, **T4** change TF = recomputed, no stale markers, **T5** reload = identical markers | 3 days |
| P2 | PA levels / bands drawn, OR groups end to end, several strategies with token colours, legend with state, position box + price lines, pending hint | **T1** (PA levels), **T2** OR count ≥ AND count, **T6** two strategies = two colours, separate legends, deselect one keeps the other | 3 days |
| P3 | explainability: `why` per marker, tooltip (hover desktop, tap phone), exit rule shown | **T7** hover lists true conditions; exit hover shows the rule | 1.5 days |
| P4 | quick-stats drawer: trade list, win rate, total P&L, max drawdown, average R, click scrolls to the trade | **T8** trade count = number of entry markers | 1.5 days |
| P5 | Builder drawer (from StrategyFlowV2 model): every registry indicator, PA blocks, comparators, groups, per-condition TF, entry window / max per day, exits, trailing, time exit, exit conditions, options mapping; save / edit / duplicate / delete / export / import JSON | builder e2e (T1-T8 re-run on strategies BUILT in the drawer) | 4 days |
| P6 | live bar-close re-evaluation from the user's own feed (bar_builder -> store -> evaluate), EOD via bhavcopy, deploy a v2 strategy to PAPER (and LIVE on the same path, switch is the owner's), parity worker = chart on recorded bars | worker / runner parity; chart marker appears at bar close | 3 days |
Each phase: unit + e2e tests, before / after screenshots of each acceptance test (desktop + phone, light + dark) in ONE comparison
page per phase; deploy line with "migration: no". Total ≈ 18-19 working days.

---------------------------------------------------------------------------------------------------------------------------
## 9. RISKS AND OPEN QUESTIONS (each with the recommended answer = the default if no reply)
| # | Question / risk | Recommended answer |
|---|---|---|
| OQ-1 | "Markers on every qualifying closed bar" vs "trade count = entry markers": a condition can stay true for many bars | A **signal** = the bar the rule turns true (edge). Entry arrows = signals taken (flat, in window, under max/day); signals while already in a trade = small muted square, not counted. So T8 holds and nothing is hidden |
| OQ-2 | Data source (M86): a user without a broker has no intraday bars | Intraday strategies plot only on the user's OWN bars (store + own REST); without a broker the dropdown shows "Connect your broker for intraday" and daily / weekly strategies run on bhavcopy |
| OQ-3 | "Every indicator ATP computes" is ~120 numeric functions | Builder lists them all, grouped (Trend / Momentum / Volatility / Volume / Session) with search; the first screen shows the groups, not a long list |
| OQ-4 | Exits / options in the strategy vs on the bot (deployment) row | The strategy holds them; deploying COPIES them into the bot row (which stays the runtime truth and stays editable). Existing bots unchanged |
| OQ-5 | Session window and max entries per day are time-based | Allowed: they are the user's operational settings (OWNER C.4); they never change how a condition reads the chart; blank = off |
| OQ-6 | Existing saved v1 custom strategies | Translated to v2 on read (one AND group); they keep running on DeclarativeStrategy until a parity test shows v2 gives identical signals, then the resolver switches (separate PR, owner told) |
| OQ-7 | The owner's own method (atp-private) | Not part of this public builder; only generic blocks from the public registry. The private engine keeps its own signal path |
| OQ-8 | Oscillator panes: many panes squeeze the price pane on phone | Max 3 lower panes visible; more are listed in the legend with a toggle; phone shows 1 lower pane at a time |
| OQ-9 | Higher-TF / session operands recomputed per bar (O(n²)) in the old engine | v2 evaluates each leaf once over the whole series; perf test guards it |
| OQ-10 | Position box for an open trade that has no target (target off) | Draw only the loss side (entry -> SL); no target line; R:R shown as "—" (never a made-up target) |
| OQ-11 | Signal wording (SEBI) on the chart | Markers / tooltips use "Long entry / Short entry / Exit" structure words on the user's OWN strategy; no advice text; public / shared views never show this layer |
