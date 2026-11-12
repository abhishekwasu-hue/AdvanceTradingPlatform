# M99 B0 - audit of existing scanners / templates, and the book-study candidate list (11 Nov)

Owner order M99 B0 (INBOX 11 Nov, M100 / M101 addenda). The owner reviews this table before packs P3-P6 are built.

What is here and what is not:
- **Public (this file):** the ATP inventory (code facts) and a compact candidate list with neutral IDs.
- **Private (trade-data `review/m99_b0/`):** the full table, with codable conditions, every number as a parameter with its reference value, key figures, and the source map. Book text, names and images never come into this repo (M100 R5).

## 1. Summary

**ATP today (main + the unmerged M97 stack)**
- 60 library screens, 57 strategy templates (families A-I), 13 built-in bot strategies, 12 chart overlays.
- Everything written in ScreenQL shares one evaluation function (`runtime._Eval`) for scan, template, overlay and bot.
- These do NOT share it:
  - the 13 built-in strategies;
  - the trend / structure chart layer;
  - the M97 chart layer, which uses reference settings only, so a scanner with edited settings can disagree with the chart.

**Gaps found that M99 must close**
- **Bar classifier and Fibonacci level functions:** do not exist in the backend. These are B1 primitives.
- **Index futures volume:** missing in the bot worker path (`workers/strategy_bars.py`). Every volume-based pack (P3 VSA, P4 Wyckoff) needs it; fixed in B1.
- **BOS / CHoCH:** no per-bar series in ScreenQL.
- **Swing code:** 5 parallel swing implementations, and one of them (`swings.find_swings`) dates a pivot at its own bar. B1 adds one causal module and keeps the old ones.
- **Stored zones:** templates cannot read them.
- **Template overlays:** exist only for families H and I.

**Candidates (from all 13 notes files)**
- 135 rows after merging duplicates: exists 37, partial 62, new 36.
- Priority: H 20, M 49, L 66.
- Missing almost entirely: VSA and Wyckoff.
- **Rules kept:**
  - Every book R:R / size / target rule (CONF-RR) becomes the user's own setting or a projection only.
  - Clock framing (CONF-CLOCK) stays an optional operational setting or an overlay, never a gate.
  - Order wording (CONF-ADVICE) becomes signal text only.

## 2. Owner decisions (defaults apply until the owner answers)
| ID | Question | Default |
|---|---|---|
| M99-Q1 | Pack scope and priorities in section 4 (the review the order asks for before P3-P6) | Build H, then M rows per pack. L rows only when a pack needs them. VAGUE rows wait until the source pages are read (M100 R1). |
| M99-Q2 | 9 indicator rows (IND) belong to no pack. One of them is a second divergence indicator beside RSI (CLAUDE.md §5: RSI divergence is the only indicator in the owner's method) | IND rows go into P6 as optional evidence layers: OFF by default, they never block, generic templates only. They are never used in the owner's own method reading. |
| M99-Q3 | Order of P1: it builds on M97, which waits on the 3 M98-P1 answers | B1 is built now. P1 starts after the M98-P1 answers. |

## 3. ATP inventory

Source: origin/main (2510a03f) plus the unmerged M97 stack: claude/m97-sd-detector, -sd-chart, -sd-presets-copilot, -sd-scanners, -sd-templates. Code facts only.

Common facts (they apply wherever a row says "as common"):
- **Shared evaluation (ScreenQL items):**
  - The scan runs on `runtime._Eval` (backend/app/screener/runtime.py:257), through `run_screen` :1588 and `symbol_match` :1755.
  - Chart study: backend/app/charts/studies.py:306 (`/api/charts/studies`, flag `chart_studies`).
  - M91 overlay: backend/app/screener/watch_screen.py:121 `screen_marks`, route watch_screen_routes.py:309 (flag `watchlist_screen`).
  - Evidence chart: backend/app/screener/evidence.py:725 (flag `evidence_charts`).
  - Bot: only through Scan Then (screener/then/*, flag `scan_then` + `execution_core`, PAPER proposal; `scan_auto_paper` for automatic PAPER).
- **Template path:**
  - Templates compile to ScreenQL parts (strategy_templates/compile.py).
  - `engine.compute` (strategy_templates/engine.py:141) evaluates with `runtime._Eval`.
  - Bot: `adapter.TemplateStrategy` (adapter.py), resolved through custom_strategies/resolver.py; flag `strategy_templates`; PAPER.
  - Parity scan = backtest = options = worker: parity.py (PATHS :39).
  - Template overlay: strategy_templates/routes.py:254 using `pages.overlay_parts` (pages.py:184), then `watch_screen.screen_marks` (routes.py:306). Families H only on main (pages.py:36); H and I on the branch (pages.py:41).
- **Flags:** every flag below is OFF by default (controls.py:653 `DEFAULT_ON_FLAGS` holds none of them).
- **Volume:** a missing or 0 volume on an index counts as missing (`runtime.no_index_volume` :1660). In screener runs a volume condition is then skipped with a note (breakouts/screens.py:89-91). B14 VolumeBreakout reports `volume_required`.

## Table 1: every scanner, template and overlay

### 1A. Starter screens (library source `starter`)
| id | file:line | does | data/TF | shared eval | tests | flag (default) | gaps |
|---|---|---|---|---|---|---|---|
| starter.momentum | backend/app/screener/library.py:46 | RSI(14)>60 and close>EMA20 | spot, 1d | as common (scan + study/M91 overlay); bot via Scan Then only | test_tx2_library (23) | screener_gallery + screener_tx2 (OFF) | literals RSI 14 / 60 / EMA 20 are not params |
| starter.oversold | library.py:47 | RSI(14)<=30 | spot, 1d | as above | as above | as above | literals 14 / 30 |
| starter.daily_trend | library.py:48 | close@1d > EMA50@1d | spot, 1d | as above | as above | as above | literal 50 |

### 1B. Breakout templates (sources `breakouts`, `breakouts_bk3`, `breakouts_bk4`)
- **Definitions:** backend/app/screener/breakouts/templates.py:115-180.
- **Engine:** breakouts/screens.py `evaluate_screen` :654. Rules: _b1 :434, _b2 :484, _b4 :518, _b7 :551, _b13 :579, _b14 :593; intraday.py b16 :91, b17 :101, b18 :124; vcp.py b3 :202; indicator_breaks.py b10 :67, b11 :152, b12 :206, b15 :228; lines.py b5 :228, b6 :321; gaps.py b8 :148; retest.py b9 :209.
- **Causal and settings:** causal by design (screens.py docstring); ATR read on the bar before the signal; one signal per leg. All numbers are settings in breakouts/params.py.
- **Shared eval:** the same engine serves the scanner (ScreenQL `RangeBreakout`, `SwingBreakType`, ...) and the strategy templates B/C. Overlay as common.
- **Tests:** test_z7_bk1_breakouts (44), test_z7_bk2_intraday (39), test_z7_bk3_breakouts (28), test_z7_bk4_breakouts (22), API tests (16 + 7 + 6), test_z7_bk5_fitness (16).
- **Flags:** `screener_breakouts` (+ `_bk3` / `_bk4`), all OFF.
- **Look-ahead:** none found (Z10-a). Some HTF contexts are "engine only" when screenql/1 cannot express them (templates.py:221).
- **Common gaps:** no bot path except Scan Then or the matching B/C template.

| id | line | does | TF | volume |
|---|---|---|---|---|
| breakouts.swing_1h_range | 115 | B1 20-bar range break, D/W trend not against | 1h | optional vol_mult |
| breakouts.daily_swing_break | 116 | B2 swing high/low break typed BOS/CHoCH/BREAK | 1d | opt |
| breakouts.intraday_15m_nr7 | 118 | B4 NR7 setup, next-bar break | 15m | opt |
| breakouts.daily_52w_high | 119 | B7 52-week high | 1d | opt |
| breakouts.weekly_52w_high | 121 | B7 on weekly | 1w | opt |
| breakouts.intraday_15m_expansion | 123 | B13 ATR/range expansion | 15m | opt |
| breakouts.intraday_5m_volume | 125 | B14 volume shock at the 20-bar extreme | 5m | **required** |
| breakouts.intraday_5m_orb15 | 127 | B16 ORB 15 min | 5m | opt (TOD) |
| breakouts.intraday_15m_orb30 | 129 | B16 ORB 30 min | 15m | opt |
| breakouts.intraday_15m_first_hour | 131 | B17 first-hour level | 15m | opt |
| breakouts.intraday_5m_prev_day | 134 | B17 PDH/PDL | 5m | opt |
| breakouts.intraday_5m_day_extreme | 137 | B18 HOD/LOD | 5m | opt (TOD rvol) |
| breakouts_bk3.daily_vcp | 140 | B3 VCP breakout | 1d | dry-up |
| breakouts_bk3.daily_vcp_watchlist | 143 | B3 VCP ready (state) | 1d | dry-up |
| breakouts_bk3.daily_donchian_20 | 147 | B10 Donchian 20 | 1d | - |
| breakouts_bk3.daily_donchian_55 | 149 | B10 Donchian 55 | 1d | - |
| breakouts_bk3.daily_bb_squeeze | 151 | B11 BB squeeze | 1d | opt |
| breakouts_bk3.intraday_15m_ttm_squeeze | 153 | B11 TTM fire | 15m | opt |
| breakouts_bk3.daily_50ma_breakout | 155 | B12 MA breakout | 1d | - |
| breakouts_bk3.daily_rsi_adx_regime | 158 | B15 RSI band exit + ADX rise | 1d | - |
| breakouts_bk4.daily_trendline_break | 162 | B5 trendline | 1d | - |
| breakouts_bk4.swing_1h_channel_break | 165 | B5 channel | 1h | - |
| breakouts_bk4.daily_flag | 168 | B6 flag/pennant | 1d | opt |
| breakouts_bk4.daily_triangle | 171 | B6 triangle | 1d | opt |
| breakouts_bk4.intraday_5m_gap_and_go | 174 | B8 gap beyond PDH/PDL | 5m | - |
| breakouts_bk4.daily_gap_base | 176 | B8 gap beyond 20-day base | 1d | - |
| breakouts_bk4.daily_range_retest | 178 | B9 range retest | 1d | opt |
| breakouts_bk4.swing_1h_range_retest | 180 | B9 range retest | 1h | opt |

### 1C. Legends cards (source `legends`)
- **Location:** backend/app/screener/data/legends.json (line = the `"id"` line); lint in screener/legends.py.
- **Shared eval:** as common. Bot via Scan Then only.
- **Tests:** test_z8_lg1_legends (22), test_n3_lg2_legends (9).
- **Flags:** `screener_legends` (LG1), `_lg2`, `_lg3` (+ `screener_fundamentals`), all OFF.
- **Common gap:** fixed literals inside the texts (SMA 50/150/200, 250 bars, ADX 14, EMA 20, RSI 2, SMA 200 and so on) are not params. Only 1-4 `$` params per card.

| id | line | TF | does / notes |
|---|---|---|---|
| minervini_trend_template | 6 | 1d | MA stack + 52w position + RS percentile; literals 50/150/200/21/250 |
| darvas_box_breakout | 27 | 1d | box break + volume (SMA vol 50 literal) |
| weinstein_stage2_breakout | 49 | 1w | 30-week MA rising + base break |
| turtle_channel_breakout | 69 | 1d | N-day high |
| raschke_adx_pullback | 88 | 1d | ADX(14), EMA20 touch (literals) |
| crabel_nr7 | 107 | 1d | NR-n |
| connors_rsi2_pullback | 126 | 1d | RSI(2) under SMA200 (literals) |
| williams_volatility_breakout | 146 | 1d | open + k x previous range |
| livermore_pivotal_breakout | 165 | 1d | LG2; base + 250-bar high + volume |
| wyckoff_range_spring | 188 | 1d | LG2; range-low undercut and close back |
| raschke_false_breakdown | 209 | 1d | LG2 |
| crabel_opening_range_breakout | 230 | 5m | LG2; ORHigh cross |
| tudor_jones_200_day_regime | 250 | 1d | LG2; close > SMA n |
| graham_net_net | 270 | 1d | LG3; fundamentals |
| graham_number_value | 291 | 1d | LG3 |
| greenblatt_yield_and_return | 312 | 1d | LG3 |
| oneil_growth_breakout | 332 | 1d | LG3; EPS/sales + 250-bar high + volume |

### 1D. M94 Intraday pack scanners (source `intraday`)
- **Location:** backend/app/screener/intraday.py; registered :222. ScreenQL session functions: registry.py:596-609; BandDistancePct :480.
- **Shared eval:** as common. Overlay via study/M91. Bot via Scan Then; templates H cover T1-T10.
- **Tests:** test_m94_pr2_scanners (19), test_m94_pr1_functions (20), test_m94_bands (12).
- **Flags:** `intraday_pack` + `screener_breakouts`, OFF.
- **Data:** the user's own broker or stored intraday bars (`live_per_user`); 1m/5m.

| id | line | does | TF | volume | gaps |
|---|---|---|---|---|---|
| intraday.gappers (S1) | 90 | gap % vs PDC, optional pre-open volume, turnover, ATR% | 5m (+@1d) | PreOpenVolume, turnover | pre-open bars may not exist (M94-Q4); literals SMA 20@1d, ATR(14)@1d; 3 params Not set |
| intraday.new_day_high (S2) | 102 | DayExtremeBreak up + RelVolumeTOD bar/cum + range | 5m | yes | literal RelVolumeTOD(20), ATR(14); range_atr Not set |
| intraday.new_day_low (S3) | 198 | mirror of S2 | 5m | yes | as S2 |
| intraday.top_reversal (S4) | 115 | rise from open in ATRs + bearish pattern near SessionHigh | 5m | - | pattern list literal; move_atr / near_atr Not set |
| intraday.bottom_reversal (S5) | 201 | mirror of S4 | 5m | - | as S4 |
| intraday.vwap_break (S6) | 124 | close crosses SessionVWAP + TOD volume | 5m | yes (index: VWAP has no volume) | literal 20 sessions |
| intraday.turbo_breakup (S7) | 131 | DayExtremeBreak on 1m | 1m | yes | - |
| intraday.turbo_breakdown (S8) | 203 | mirror of S7 | 1m | yes | - |
| intraday.circuit_watch (S9) | 138 | distance to the broker quote's price band | quote | - | LAST_BAR_ONLY: no history, backtest, template or overlay; Upstox/Zerodha only |
| intraday.rsi_extreme (S10) | 146 | RSI@5m extremes + price range + SessionVolume | 1m base, @5m | SessionVolume optional | @5m literal; pmin / pmax / min_vol Not set |
| intraday.volume_surge (S11) | 158 | RelVolumeTOD >= v | 5m | yes | v Not set |
| intraday.key_levels (S12) | 177 | close near PDC/PDH/PDL/pre-open/MAs/52w | 5m (+@1d) | - | literals 250@1d; Lag@1d nuance (M94-Q17) |

### 1E. M97 Supply & Demand pack (branch)
- **Detector:** price_action/sd_zones.py (branch presets-copilot): `detect` :192, `track` :226, `sd_series` :318, `cached_sd_series` :336, settings table :70.
- **ScreenQL functions:** registry.py:867-882 (branch); flag `sd_pack` :279.
- **Shared eval:** scanners and templates share the per-bar SD series (registry, then sd_zones). The chart layer calls `sd_zones` directly through `sd_zones_layer.zone_rows` (:105), not `_Eval`.
- **Tests:** test_m97_sd_zones (17), test_m97_sd_chart (17), test_m97_sd_presets_copilot (8), test_m97_pr2_scanners (14, branch sd-scanners), test_m97_pr3_templates (17, branch sd-templates); frontend sdZones.test (18), sdStyles.test (6).
- **Flag:** `sd_pack` OFF.

| id | file:line | does | TF | gaps |
|---|---|---|---|---|
| supply_demand.demand_enter (Z1) | screener/sd_scanners.py:59 (branch sd-templates) | first touch of a fresh demand zone | `BASE_TF` "1d" (:26) | fixed base TF in the definition; no volume |
| supply_demand.supply_enter (Z2) | :65 | first touch of a fresh supply zone | 1d | as above |
| supply_demand.zone_formed (Z3) | :71 | leg-out closes: DBR/RBR/RBD/DBD/any | 1d | - |
| supply_demand.zone_violated (Z4) | :78 | close beyond the distal line | 1d | - |
| supply_demand.near_fresh_zone (Z5) | :84 | close within % of the nearest fresh zone | 1d | within_pct Not set |
| SD zones chart layer | charts/sd_zones_layer.py:210 (branch) | time-anchored zones per TF; 10m / 2h built by resample | 1m-1M | uses reference settings only, so a scanner with edited settings can disagree with the chart; bars from `price_action_layer.load_frame` (lake or own bars), not the scanner's store-first path |
| SD style presets (Z6) | frontend/src/chart/sdStyles.ts:14 (branch) | investor 1M, positional 1w, swing 1d+2h, short_term 1h+30m, intraday 15m+10m, scalper 5m+3m | - | presets only preselect timeframes |
| Copilot SD facts (Z7) | ai/copilot_chat.py:408, :694, :997 (branch) | tool `get_sd_zones` when the user's overlay pref is on | - | - |

M99 P1 items not present: swing-extreme zone with one-candle confirmation, engulfing zone, swap level, compression, HTF dominance, odds score.

### 1F. Strategy templates library
- **Location:** backend/app/strategy_templates/templates/<id>.json:1; loader library.py:25; schema.py; packs.py (F); archive/ holds 13 older versions (F1, F2, G1-G11 v1).
- **Shared eval:** yes. Scan, backtest, options backtest and worker all read `engine.compute` (parity.py). Bot: adapter. Overlay: families H and I only. The setup can be drawn as a study.
- **Flags:** `strategy_templates` OFF; family G also `screener_waves`; H `intraday_pack`; I `sd_pack` (routes.py:44).
- **Exits:** the user's settings (exits.py). The adapter never forces targets.

| family | ids, with TFs and tests (~n) | what | gaps |
|---|---|---|---|
| A Price action | A1 5m/15m AreaGate pullback to a demand/supply area (32 params; ported level gate, area_gate.py); A2 range edges `ReversalAt(SwingLow/High)` 5m-1d; A5 `RetestEntry` 5m-1d. Tests: test_m55_t5_pullback_area (15), test_tpl3 (33), test_tpl6 (23), test_tpl7 (25) | A1 rebuilds Z7a zones from the frame | A1 ported min_rr 3.0; no overlay (A-G); templates cannot read stored zones (engine.py:147, M97 audit) |
| B Breakout | B1 RangeBreakout; B2 SwingBreakType=="BOS"; B3 channel; B4 trendline; B5 Donchian; B6 PatternBreakout; B7 Squeeze; B8 NRBreakout; B9 VolumeBreakout (volume); B10 GapType GAP_AND_GO (5m/15m/3m); B11 VCP (1d); B12 NewHigh (1d). Others 5m-1d. Tests: test_tpl2 (28), test_tpl4 (55), test_tpl8 (19), test_stratx (24) | breakout engine calls | no overlay; B9 needs volume (index futures volume in the worker not confirmed) |
| C Breakout + retest | C1 range, C2 swing, C3 channel, C4 trendline_projected, C5 Donchian (all `RetestEntry`, 5m-1d); C6 PDH/PDL break + retest (5m-30m). Tests: test_tpl3 (33), test_tpl9 (29), test_tpl5 (31) | | C5 tested only in test_tpl4; no overlay |
| D Trend | D1 SupertrendDir flip (5m-1w); D2 MA cross; D3 MA pullback in trend; D4 MABreakout; D5 ADX/DI; D6 MACD cross; D7 PSAR flip; D9 weekly trend + daily pullback (1d). Tests: test_tpl1_* (15 + 15 + 7 + 26), test_tpl2, test_tpl4 | | D4-D7 tested only in test_tpl4; no overlay |
| E Oscillator / mean reversion | E1 RSI pullback; E2 RsiDivergence (flag screenql_amw_functions); E3 Bollinger reversion; E4 Stochastic cross; E5 SessionVWAP reclaim (5m-30m, volume) | | E4 thin (test_tpl4); E5 uses VWAP, which an index lacks |
| F Option seller packs | F1 = D1 or D3; F2 = A2 or E3 (packs.py:33) | label mapping only | the structure comes from the bot settings (by design) |
| G Waves | G1-G11 WaveState/WaveType/WaveEnd@{wave_tf} state recipes (5m-1d). Tests: test_wave4 (29), test_wave5 (28), test_wave6 (25), test_wave2_recipe (17), test_wave3 (32) | wave count labelled as interpretation | 18-26 params each; no overlay |
| H Intraday (M94) | H1 Fallen angel; H2 ABCD; H3 Reverse ABCD; H4 Bull flag; H5 Bear flag; H6 ORB; H7 VWAP false breakout; H8 VWAP reversal; H9 VWAP + MA trend; H10 previous-day level rejection. All 5m/1m/3m/15m, session-based. Tests: test_m94_pr4 (22), test_m94_pr5 (23), test_m94_pr6_pages (18) | overlay yes (template overlay); how-it-works page; TEMPLATE_LEVEL exit | reference defaults with no source (M94-Q19/Q20/Q24); time window only from Trade settings (Q18); not built: Q25 items |
| I S&D (M97, branch sd-templates) | I1 Demand zone long, I2 Supply zone short, 1d-1m, 13 params. Tests: test_m97_pr3_templates (17) | SDZoneEnter + SDZoneEdge stop/target | overlay yes (PAGE_FAMILIES H, I) |

### 1G. Built-in strategy engine (AI Trading Bot "Library")
- **Registry:** backend/app/strategy_engine/registry.py:15.
- **Shared eval:** no. The bot path is the strategy's own `analyze()`. The chart overlay is the strategy run bar by bar (strategy_engine/chart_routes.py, `POST /api/strategies/{id}/chart-run`). There is no scanner text.
- **Defaults:** base.py `build_signal` default `target_rr=(1.5, 2.5)` (base.py ~:70). Hard-coded EMA/RSI/ADX periods are only params at the class level.
- **Tests:** test_strategies (8), test_phase_ao_chart_strategies (10), test_phase_as_swing (8).
- **Flag:** none (always listed).
- **Ready choices:** the trading-bot form shows 3 of them, `READY` in frontend/src/paperbot/model.ts:22 (orb_15m_5m, vwap_supertrend_5m, swing_ema_pullback_d).

| id | file:line | does | TF |
|---|---|---|---|
| mtf_1m_5m / 1m_15m / 5m_30m / 5m_60m_trend_pullback | mtf_strategy.py:154-161 (class :14) | HTF EMA trend + LTF pullback | 1m/5m with HTF |
| ema_rsi_scalper_1m | indicator_strategies.py:18 | EMA cross + RSI bias | 1m |
| supertrend_adx_scalper_1m | :99 | Supertrend flip + ADX | 1m |
| rsi_adx_momentum_5m | :179 | RSI midline + DI/ADX | 5m |
| macd_ema_trend_5m | combo_strategies.py:76 | MACD + EMA | 5m |
| bb_rsi_reversion_5m | :116 | BB + RSI | 5m |
| vwap_supertrend_5m | :156 | VWAP (average line on an index, :51) + Supertrend | 5m |
| orb_15m_5m | :199 | opening-range breakout | 5m |
| swing_ema_pullback_d | swing_strategies.py:42 | daily EMA pullback | 1d |
| swing_breakout_d | :90 | daily breakout | 1d |

Gaps: no ScreenQL or scanner counterpart; numbers inside are not user settings in the same white-box way.

### 1H. Declarative engine, AI drafts, other templates
| item | file:line | does | shared eval | tests | flag | gaps |
|---|---|---|---|---|---|---|
| Declarative `CustomStrategyConfig` / `DeclarativeStrategy` | strategy_engine/declarative.py:281 / :336 | user-saved no-code rules (operands :98, conditions :207; IST session constants :37-39) | own Operand code (shared with the legacy scanner `Condition`); not ScreenQL | test_declarative_strategy (6), test_custom_strategies_api (8) | - | no built-in templates; IST times hard-coded; LEGACY_MIN_RR 1.2 :265 |
| AI strategy builder (Copilot draft) | ai/strategy_builder.py (EXAMPLE :49, TIMEFRAMES :29) | words to a draft `CustomStrategyConfig`; numbers left empty | as declarative | test_phase_aw_strategist, test_phase_bf_strategist_language | ai_copilot (ON) | no built-in templates |
| Idea library T1-T8 | idea_library/ideas.json:53, 103, 151, 199, 246, 296, 342, 400 | ideas with "try as scan" ScreenQL (15m); T2 adopts A2, T5 adopts A5 | as common | test_idea_library, test_idea_library_channel_swings (19) | idea_library OFF | T1, T3, T4, T6, T7, T8 have no template or bot path |
| Pine snippets (5) | lab/pine/templates.py:9, 25, 40, 56, 71 | ema_cross, rsi_pullback, supertrend_flip, macd_cross, bollinger_reentry, translated to a screen | as common | test_lab_pine_parity (5) | strategy_lab_pine OFF | Pine literals |
| Legacy structure / option scanner | screener/scanner/engine.py (models :12) | trend / structure / S-R / option-chain filters | market_structure + declarative `Condition` | test_scanner, test_scanner_api, test_phase_y_ai_scanner | - | last-bar only; separate code |
| Scan Then template | screener/then/templates.py:11-21 | per-alert-rule level gate (5m/15m confirm) to a notification or PAPER proposal | level_gate port | test_n1_x1_scan_then (30), test_n1_x2_auto_paper (27) | scan_then OFF | - |
| Options builder templates | options_builder/templates.py | payoff structures, not signals | n/a | - | options_builder ON | out of scope |

### 1I. Chart-tool overlays
| overlay | file:line | does | shares eval with scanner? | tests | flag | gaps |
|---|---|---|---|---|---|---|
| Market zones (Z7a) | charts/zones_layer.py:59 | stored zones as of a time, at most 6 bands | yes: `chart.nearest_zone` is the same code as NearZone (screener/zones.py) | test_zones_chart_screenql (25) | market_zones OFF | full-width bands; no formation |
| Price action / structure | charts/price_action_layer.py:685 + price_action_concepts.py | swings, legs, breaks, Dow trend per degree; BOS/CHoCH, liquidity, trendlines, top-down | swings: same `causal_swings` as SwingHigh; structure_tracker is not in ScreenQL | test_ch3 (13), test_ch3b (6 + 6), test_trade_port_price_action (38) | chart_price_action OFF | shared-lake bars only, not the user's broker |
| ScreenQL studies | charts/studies.py:306 | any ScreenQL expression on the chart | yes (runtime) | test_ch6 (23), test_m77_sx2_studies (17) | chart_studies OFF | - |
| M91 watchlist screen overlay (C1) | watch_screen_routes.py:309 | the user's saved screen: hits per bar + condition rows | yes (`screen_marks`) | test_m91 (14) | watchlist_screen OFF | LAST_BAR_ONLY screens are evaluated on cut bars, at most 60 (CUT_BARS) |
| M94 template overlay (F16) | strategy_templates/routes.py:254 | setup / trigger / invalidation marks + level | yes | test_m94_pr6_pages (18) | intraday_pack OFF | families H and I only |
| M97 S&D zones | charts/sd_zones_layer.py:210 (branch) | see 1E | detector yes, settings no | 17 + 24 frontend | sd_pack OFF | reference settings only |
| Evidence charts | screener/evidence.py | match drawn with the screen's own conditions | yes | test_u8_ev3_ev4 and others | evidence_charts OFF | - |
| Read this chart | charts/read_chart.py:296 | areas, state, pullback, commitment candle | existing readers | - | read_chart OFF | - |
| Workspace reading, RSI divergence | charts/workspace_reading.py:140 `divergence_part` | BOS/CHoCH, areas, RSI divergence | yes (`runtime._divergence_swings`) | - | copilot_workspace OFF | regular kind only |
| Strategy chart-run | strategy_engine/chart_routes.py | built-in strategy trades on chart candles | own `analyze` | test_phase_ao (10) | - | - |
| Drawings: Fibonacci / channel | charts/drawings.py:44-101; frontend/src/charting/geometry.ts:48-93 | manual fib retracement / extension, channel | no (manual) | geometry.test.ts | - | default ratios hard-coded (user-editable per drawing) |
| Trades layer | charts/trades_layer.py:152 | the user's own PAPER trades | n/a | - | chart_trades_layer OFF | - |

## Table 2: existing primitives (B1)
| primitive | module:function (file:line) | closed bar / no look-ahead | tests (~n) | verdict |
|---|---|---|---|---|
| Swing pivots + structure | price_action/causal_swings.py `degree_pivots` :188 (zigzag :120, fractal :146); screener runtime `_swings` :999 (SwingHigh/Low/Direction/PriorSwing*, registry :410-423); `_swing_label` :801 (SwingLabel HH/LH/HL/LL, :461, flag AMW); structure_tracker.py `StructureTracker` :89 / `run` :502 (BOS/CHoCH/sweep/range states); market_structure.py `analyze_market_structure` :120; breakouts/screens.py `zigzag_swings` :122 (B2 BOS/CHoCH, `SwingBreakType` :499); legs.py pivots :94/:106; swings.py `find_swings` :27 | yes for causal_swings, structure_tracker, SwingBreakType, SwingLabel (pivot used from its confirm bar); **risk:** swings.find_swings dates the pivot at its own bar (centred 2w+1 window); `Trend` / `StructureEvent` (registry :388-393) are LAST_BAR_ONLY | test_price_action (14), test_s5a_price_action (16), test_ch3b_trade_parity (6), test_trade_port_price_action (38), test_amw3 (30), test_z10a_no_lookahead (19), test_z7_bk1 (44) | **reuse** causal_swings + structure_tracker; **extend:** one module, BOS/CHoCH as a per-bar ScreenQL series (today only via SwingBreakType or the chart layer); 5 parallel swing implementations |
| Relative range / relative volume | runtime `_rel_volume` :1351 (RelVolume :556, flag screener_breakouts; Bars.rvol = mean of the n bars before); breakouts/session.py `time_of_day_rvol` :179 (RelVolumeTOD :587); `_median_range` :1399 (MedianRange :468, this bar excluded); `_atr_percentile` :1356; B13 RangeExpansion screens.py:579; zones/detect.py `average_range` :34; sd_zones `candle_numbers` :180 (branch) | yes | test_z7_bk1 (44), test_z7_bk2 (39), test_m94_pr1 (20) | **extend:** no RelRange (range / average range) series; RelVolume is behind a breakout flag |
| Bar classifier (spread class, close thirds, up/down) | none. Partials: candlestick_patterns.py `_metrics` :9, STRONG_BODY_* :238-240 (hard-coded); sd_zones `_candles` :163 (leg / base tests, branch); legs.py `leg_features` :175; `Pattern` (registry :405) | partials are causal | test_s5a, test_amw3 | **does not exist** (new) |
| Trend-or-range regime tag | runtime `_dow_trend` :1363 (DowTrend :564, UP/DOWN/RANGE from confirmed swings, @tf); structure_tracker states (RANGE, *_WEAK); daily_trend/fold.py (Daily trend for the level gate); ai/regime.py `classify_regime` :43 (indicator, admin diagnostic only) | DowTrend / structure_tracker causal; ai/regime last frame | test_n1_l2_daily_trend (22), test_z7_bk1, test_phase_l_ai | **reuse** DowTrend; extend with a range-width or ADX option if wanted |
| Level / zone store | price_action/zones/: store.py (settings :101), snapshot.py, job.py (nightly), read.py `read_as_of` :94 / `timeline` :194, lifecycle.py `ZoneTracker` :111; screener/zones.py `zone_series` (NearZone/ZoneRole/ZoneState/ZoneStrength, registry :714-720), read at the bar's close; level_strength.py `strength_score` :169; M97 `cached_sd_series` :336 (per run, no store) | yes (snapshot as of the bar's close, tested) | test_zones_* (17, 18, 3, 3, 11, 19), test_zones_chart_screenql (25), test_s5a4 (11) | **extend:** templates cannot read stored zones; no generic level store (PDH, fib, cluster, swap levels) |
| Fibonacci levels | no backend function. frontend charting/geometry.ts `fibLevels` :87 (levels :48-49, drawings only); WaveRetrace / WaveProjection / WaveRatio registry :760-767 (waves/levels.py `wave_levels` :370, flag screener_waves) | wave levels causal | test_wave3_levels_lines (32), geometry.test.ts | **does not exist** generically (retracement / extension / golden section on any swing pair); reuse the wave-level code pattern |
| Price-cluster finder | zones/levels.py `cluster_prices` :56 (chain clustering by tolerance %), `find_support_resistance_levels` :72; zones/chart.py overlap clusters :140 | pure over the inputs (causality depends on the swings fed in) | test_zones_detection (17), test_zones_parity (3) | **extend:** generic multi-source cluster (fib + swings + levels), causal wrapper, ScreenQL access |
| Channel / trend-line | breakouts/lines.py `Lines` :92, `channel_line` :248, `b5` :228 (TrendlineBreakout :644, ChannelLine :649, read from the bars before and projected); price_action/trendlines.py `find_trendline` :58, `detect_trendlines` :92, `channel_of` :121 (chart layer); wave lines WaveLineLevel / WaveLineBreak | yes (lines.py, trendlines on as-of bars) | test_z7_bk4 (22), test_tpl9 (29), test_idea_library_channel_swings (19), test_m76_mp3_structure_lines (15) | **reuse** lines.py; consolidate with trendlines.py |

## Notes
- **Index volume:**
  - market_data/index_volume.py: `near_future` :67 (first FUT expiry on or after the day, the expiry day included); `replace_volume` :87; `screen_frame` :105 (0 becomes NaN for screens, M93-Q3).
  - Used by chart candles (candles_routes.py:223, :553; candles_store.py:234, flag `volume_from_future`), screener runs (screener/routes.py:861 `_future_volume`, :877 `_index_futures`; SCR-FIX A7; index screens skip the store path, routes.py:742) and engine_provider/sources.py:184.
  - Only the current near-month contract: older bars get 0.
  - I found no index_volume call in workers/strategy_bars.py.
- **Store-first (M71 PL-3 / M90):**
  - Flag `market_store_reads` (OFF).
  - Screener: `store_first` screener/routes.py:697, store path :734; daily from the NSE files; intraday from the reader's own stored 1m bars plus the newest bars by REST on his own session. M90 B2-B5: a symbol not stored goes to the reader's own broker, paced; `pending`; batch quotes `quote_batch_max`; 429 pacing (tests test_m90_screener_scale (8)).
  - Also wired in charts (market_data/candles_store.py), the worker (workers/strategy_bars.py, engine_runner.py:197), backtest (backtest/bar_source.py:253), alerts (alerts/barclose.py:243) and templates (strategy_templates/routes.py:287).
  - Daily fallback without a broker login: screener/stored_history.py (flag `backtest_stored_history`).
- **M91 C1/C2 contract (watch_screen.py:1-21):**
  - C2: `screen_marks` (:121) is the ONE evaluation for watchlist rows (`/watchlist-screen/check`) and the chart overlay (C1, `/watchlist-screen/overlay`, :309). It runs `runtime._Eval` on the same bars and params: `matched` = `symbol_match` on the last closed bar; `rows` = evidence condition ids / states; `hits` per bar via causal series (cut bars for LAST_BAR_ONLY, CUT_BARS 60).
  - It adds no rules or defaults.
  - The M94 template overlay reuses it (strategy_templates/routes.py:306).
- **Z10-a harness:** app/quality/causal_cases.py registers every indicator, operand, pattern, swing, structure, zone and ScreenQL function. Coverage is enforced by test_z10a_no_lookahead (19).
## 4. Candidate list (compact; the full conditions are in the private table)

Flags: CONF-RR = R:R / SL / target / size rule in the source (becomes the user setting or a projection); CONF-CLOCK = clock framing (optional operational setting only); CONF-ADVICE = order wording (signal text only); VAGUE = not codable as written; R4 = books silent or disagree (the common definition, as a parameter).

| ID | Candidate | In ATP | Vol | TF | Pack | Pri | Flags |
|---|---|---|---|---|---|---|---|
| SD-01 | Leg-in / base / leg-out zone detector | exists | N | any (S1: monthly..3m by style) | P1 | M |  |
| SD-02 | Formation tag (drop-base-rally, rally-base-rally, rally-base-drop, drop-base-drop) | exists | N | any | P1 | L |  |
| SD-03 | Proximal / distal lines (wick or body edge) | exists | N | any | P1 | L |  |
| SD-04 | Zone state: fresh / tested(n) / violated (plus flipped) | exists | N | any | P1 | L |  |
| SD-05 | Swing-point zone (single swing candle) | partial | N | any | P1 | M |  |
| SD-06 | Zone formed only after a confirming close | partial | N | any | P1 | M |  |
| SD-07 | Zone quality score (departure, base length, time in zone, test decay) | partial | N | any | P1 | H | R4 |
| SD-08 | Retest depth per visit | new | N | any | P1 | M | R4 |
| SD-09 | Broken zone -> swap (role reversal) zone | partial | N | any | P1, P6 | M |  |
| SD-10 | Sweep / false break of a level or zone edge (stop hunt, equal highs/lows) | exists | N | any | P1, P6 | M | R4 |
| SD-11 | Opposing-zone ladder / left history | partial | N | any | P1 | M | CONF-RR |
| SD-12 | Zone contact signals: touch / confirmation candle / RSI divergence at the zone | partial | N | any | P1 | H | CONF-ADVICE |
| SD-13 | Higher-timeframe zone containment | new | N | 2 TFs (htf setting) | P1 | M |  |
| SD-14 | Approach compression into a zone | partial | N | any | P1 | L | R4 VAGUE |
| SD-15 | Long-wick rejection at a zone | exists | N | any | P1, P6 | M | R4 |
| SD-16 | Inside bar at a zone | exists | N | any | P1, P6 | L |  |
| SD-17 | Engulfing / momentum candle after a rejection at the same zone | partial | N | any | P1, P6 | M | R4 |
| SD-18 | Shrinking candles into a zone (momentum loss) | partial | N | any | P1, P6 | M | R4 |
| SD-19 | Touch / test count of a level (strength vs weakening) | exists | N | any | P1, P6 | M | R4 |
| SD-20 | Pattern combo score at a zone | partial | N | any | P1, P6 | L | R4 |
| SD-21 | Follow-through check after a rejection candle | exists | N | any | P1, P6 | L |  |
| SD-22 | Style -> timeframe preset | partial | N | listed | P1 | L |  |
| VSA-01 | Bar classifier: spread, relative volume, close position | partial | Y | any | P3, P4 | H | R4 |
| VSA-02 | No Demand bar | new | Y | any | P3 | H |  |
| VSA-03 | No Supply bar | new | Y | any | P3 | H |  |
| VSA-04 | Test / failed test (and Wyckoff secondary test) | new | Y | any | P3, P4 | H | R4 |
| VSA-05 | Up-thrust (high and low volume variants) | partial | Y | any | P3, P4 | H |  |
| VSA-06 | Reverse up-thrust / professional support | partial | Y | any | P3 | M |  |
| VSA-07 | Stopping volume | new | Y | any | P3, P4 | H | R4 |
| VSA-08 | Selling / buying climax (incl. volume spike with a gap) | new | Y | any | P3, P4 | H | R4 |
| VSA-09 | Shake-out | new | Y | any | P3, P4 | M |  |
| VSA-10 | Falling pressure | new | Y | any | P3 | L |  |
| VSA-11 | Absorption volume | new | Y | any | P3 | M | VAGUE |
| VSA-12 | Effort vs result (up / down) | new | Y | any | P3, P4 | H | R4 |
| VSA-13 | Background strength / weakness score | new | Y | 2 TFs | P3 | M |  |
| VSA-14 | Next-bar confirmation option | partial | Y | any | P3 | M |  |
| VSA-15 | VSA markers with an explain card (spread, volume, close, background) | partial | Y | any | P3 | M |  |
| VSA-16 | Volume trend flags / line break needs effort | partial | Y | any | P3 | L |  |
| VSA-17 | Trend-line clusters + range quarters tool | new | N | any | P3 | L | VAGUE |
| VSA-18 | Templates: test after stopping volume / climax; no demand after climax / up-thrust | new | Y | any | P3 | M |  |
| VSA-19 | Index volume = near-month futures volume | exists | Y | any | P3, P4 | H |  |
| WY-01 | Significant bar (sign-of-strength / weakness bar) | partial | Y | any | P4 | H | R4 |
| WY-02 | Control-of-market state (reversal rule) | new | Y | any | P4 | M |  |
| WY-03 | Phase A chain: preliminary stop, climax, automatic reaction, secondary test | new | Y | any | P4 | H | R4 |
| WY-04 | Range state tracker (creek / ice, age, volume slope, tests per edge) | partial | Y | any | P4 | M |  |
| WY-05 | Spring / UTAD scanner with class #1/#2/#3 and spring test | partial | Y | any | P4 | H |  |
| WY-06 | Sign of strength / jump across the creek + back-up (LPS / BUEC) | partial | Y | any | P4 | H | R4 |
| WY-07 | Templates: spring-test long, back-up long, Phase E continuation (and mirrors) | new | Y | 2 TFs | P4 | M | CONF-RR |
| WY-08 | Higher-timeframe phase / bias context | partial | Y | 2 TFs | P4 | M |  |
| WY-09 | Wave volume (per-swing) and trend health | partial | Y | any | P4, P3 | H |  |
| WY-10 | Liquidity / volume-profile levels (VPOC, naked VPOC, anchored VWAP) | partial | Y | any | P4 | M | CONF-CLOCK R4 |
| WY-11 | Point-and-figure count projection | new | N | any | P4 | L | CONF-RR |
| WY-12 | Warning alerts against an open position | new | Y | any | P4, P7 | M |  |
| WY-13 | Phase / event labeller incl. the trading-opportunities table | new | Y | any | P4 | M |  |
| WY-14 | Re-accumulation / re-distribution | new | Y | any | P4 | L |  |
| FIB-01 | Retracement levels of a swing | exists | N | any | P5 | L |  |
| FIB-02 | Projection / extension levels (incl. 1.272 harmonic) | exists | N | any | P5 | L |  |
| FIB-03 | Golden-section target | new | N | any | P5 | L | CONF-RR |
| FIB-04 | Fibonacci price-cluster detector | partial | N | any | P5 | H | R4 |
| FIB-05 | Wave target box (price x time) per wave type | partial | N | any | P5 | M | CONF-CLOCK |
| FIB-06 | Time projections / time cluster (bars) | new | N | any | P5 | L | CONF-CLOCK |
| FIB-07 | Impulse / correction validity rules and invalidation lines | exists | N | any | P5 | L |  |
| FIB-08 | Extended wave / truncation / alternation tags | partial | N | any | P5 | M |  |
| FIB-09 | Diagonal detector and its line break | exists | N | any | P5 | L |  |
| FIB-10 | Zigzag wave-C rider | exists | N | any | P5 | L | R4 |
| FIB-11 | Expanded flat | exists | N | any | P5 | L |  |
| FIB-12 | Triangle suite (rules, thrust, invalidation) | partial | N | any | P5 | L | CONF-ADVICE |
| FIB-13 | Wave projection panel (w3, w4, w5 targets) | partial | N | any | P5 | M |  |
| FIB-14 | Elliott channel (2-4 base, parallel through wave 3) | partial | N | any | P5 | M | R4 |
| FIB-15 | Second momentum oscillator for wave-5 divergence (EWO) | new | N | any | P5 | L | R4 |
| FIB-16 | Trend-degree guard | partial | N | any | P5 | H |  |
| FIB-17 | Reversal-bar trigger in a projected zone | partial | N | any | P5, P6 | M |  |
| FIB-18 | Inside / outside bar continuation trigger after a missed reversal | partial | N | any | P5, P6 | L | CONF-RR |
| FIB-19 | Missed-reversal do not chase rule | new | N | any | P5 | L | CONF-RR |
| FIB-20 | Answer-key tests before adoption | new | N | any | P5 | M |  |
| FIB-21 | Count-comparison harness | partial | N | any | P5 | L |  |
| FIB-22 | Preferred vs alternate count agreement flag | exists | N | any | P5 | L |  |
| FIB-23 | Confirmation tags (divergence, engulfing, dark cloud, close location) | partial | N | any | P5, P6 | L |  |
| PA-01 | Trend type by contraction reach (fast / normal / slow) | partial | N | any | P6 | M | R4 |
| PA-02 | Range detection by leg symmetry | partial | N | any | P6 | M |  |
| PA-03 | Extreme classifier: fault / double / trap / climax | partial | N | any | P6 | H | R4 |
| PA-04 | Missing-link trend-end scanner | new | N | any | P6 | M | VAGUE |
| PA-05 | Pullback-continuation after an acceleration (baston / pause-candle) | partial | N | any | P6 | H | R4 |
| PA-06 | Entry-type labeller (types 1-4 around a trendline / last-extreme break) | partial | N | any | P6 | M |  |
| PA-07 | Trigger candle at a validated level | exists | N | any | P6 | L | CONF-RR |
| PA-08 | Trendline touches + convergence with a horizontal / polarity line | partial | N | any | P6 | M | CONF-RR |
| PA-09 | Acceleration / consolidation counter (A1 C1 A2 C2 A3) | partial | N | any | P6 | M | R4 |
| PA-10 | Momentum-asymmetry sections scanner | new | N | any | P6 | L | VAGUE |
| PA-11 | Small range vs average range, then strong close outside (session-free kernel) | partial | N | any | P6 | L | CONF-CLOCK |
| PA-12 | Dow trend states, secondary correction, 20 % rule, two-index check | partial | N | any (S13: daily) | P6 | M |  |
| PA-13 | Inside / outside bar flags | exists | N | any | P6 | L |  |
| PA-14 | Hammer / dragonfly doji / gravestone doji | partial | N | any | P6 | L | R4 |
| PA-15 | Elephant trunk (two-bar bearish reversal) and mirror | new | N | any | P6 | L | R4 |
| PA-16 | Two-bar tail reversal | partial | N | any | P6 | L | R4 |
| PA-17 | Gap classifier (breakaway / measuring / exhaustion, island) | partial | Y | daily | P6 | L | CONF-CLOCK |
| PA-18 | Head and shoulders / inverse | partial | N | any | P6 | M | CONF-RR |
| PA-19 | Cup and handle | new | Y | daily / weekly | P6 | L | R4 |
| PA-20 | Market regime tag: trend vs range (structure version) | partial | N | any | P6 | M |  |
| PA-21 | Phase labeller (trend / retracement / consolidation / breakout) | partial | Y | any | P6 | M |  |
| PA-22 | Trendline-reversal pre-signal (momentum loss before the break) | partial | N | any | P6 | M |  |
| PA-23 | Level trade templates: bounce / break-retest / aggressive break | exists | N | any | P6 | L |  |
| PA-24 | Engulfing pattern (two-bar) | exists | N | any | P6 | L |  |
| PA-25 | Dark cloud cover / piercing line | new | N | any | P6 | L |  |
| IND-01 | Bollinger close-outside with expanding width | exists | N | any | - | L |  |
| IND-02 | ADX / DMI trend scanner | exists | N | any | - | L | R4 |
| IND-03 | MACD zero-line filter | exists | N | any | - | L |  |
| IND-04 | Stochastic / RSI exit-from-zone trigger, RSI failing in trend | exists | N | any | - | L |  |
| IND-05 | Rate-of-change momentum bias | exists | N | any | - | L |  |
| IND-06 | Parabolic SAR flip with momentum suppression | partial | N | any | - | L |  |
| IND-07 | EMA stack (10/20, 50/200) | exists | N | any | - | L |  |
| IND-08 | Sentiment panel: India VIX, put-call ratio | partial | N | daily | - | L | CONF-CLOCK |
| IND-09 | Tool agreement checklist | new | N | any | - | L |  |
| INTRA-01 | Fallen angel | exists | Y | 1m / 5m | P2 | L | CONF-CLOCK |
| INTRA-02 | ABCD / reverse ABCD | exists | Y | 1m / 5m | P2 | L | CONF-RR |
| INTRA-03 | Bull / bear flag | exists | Y | 1m / 5m | P2 | L |  |
| INTRA-04 | Opening-range breakout | exists | Y | 5m | P2 | L | CONF-CLOCK |
| INTRA-05 | VWAP false breakout / VWAP reversal / VWAP + MA trend | exists | Y | 1m / 5m | P2 | L | CONF-CLOCK |
| INTRA-06 | Predetermined levels (previous close / high / low, two-days-ago, 52-week) | exists | N | any | P2 | L | CONF-CLOCK |
| INTRA-07 | Extreme-wick level line with recency | partial | N | daily + intraday | P2, P6 | M | R4 |
| INTRA-08 | Structure exit: no new extreme in the trade direction / opposite HH-HL sequence (incl. stagnation) | new | N | 5m (book) | P2, P7 | M | CONF-CLOCK |
| INTRA-09 | Bar-by-bar HH/HL trend (two-candle logic) | partial | N | 5m | P2 | L |  |
| INTRA-10 | Relative volume / gappers watchlist | exists | Y | intraday | P2 | L | CONF-CLOCK |
| INTRA-11 | Order-book imbalance signals | new | N | tick | P2 | L | VAGUE |
| RISK-01 | Loss circuit-breakers (daily cap, monthly cap, giveback of open-session profit) | partial | N | - | P7 | M |  |
| RISK-02 | Size step-down / step-up after streaks | partial | N | - | P7 | M |  |
| RISK-03 | Largest-loss vs largest-win monitor | new | N | - | P7 | L | R4 |
| RISK-04 | Scale-out / partial exits + stop to entry | partial | N | - | P7 | M |  |
| RISK-05 | Trade journal metrics and rule-breach count | partial | N | - | P7 | L |  |
| RISK-06 | Named-setup list check | partial | N | - | P7 | L |  |
| RISK-07 | Position size from risk % and stop distance | exists | N | - | P7 | L |  |
| RISK-08 | Minimum reward:risk check | exists | N | - | P7 | L | CONF-RR |
| RISK-09 | Trailing-stop methods (swing, N-bar with inside-bar skip, Fib sub-leg, 3-phase) | partial | N | any | P7, P5 | M |  |
| RISK-10 | Stop only tightens / never widened | exists | N | - | P7 | L |  |
| RISK-11 | Scale-in only inside a pre-defined stop / add to winners only | new | N | - | P7 | L |  |
| RISK-12 | Options vertical spread from the target box | partial | N | - | P7, P5 | L |  |
