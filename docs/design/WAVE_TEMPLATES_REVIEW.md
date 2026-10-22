# Wave / correction templates (family G): the independent review (22 Oct)

The order `docs/owner-orders/ATP_SESSION_WAVE_TEMPLATES.md` (family G, G1-G12), reviewed against main 89fa1490 (#360)
by a separate agent that only read the spec and the code. This file is the plan of record for building the order.
Where it differs from the order, the finding wins until the owner says otherwise (owner questions WAVE-1..WAVE-16 in
`docs/OPEN_QUESTIONS.md`, each with a provisional default the build uses).

Binding inputs: CLAUDE.md (generic method only in this public repository; signals only; PAPER only; structure words;
no fixed numbers in reading rules - numbers live only in settings), `ATP_SESSION_STRATEGY_TEMPLATES.md` section 0
(R1-R10, binding where it differs) and sections 3 / 5 / 6 / 8, and `docs/design/STRATEGY_TEMPLATES_IMPL.md` (TPL-1, #343).

**Verdict.** The goal, the common parameters and the catalogue are sound, and most of the building blocks exist. Four
assumptions are wrong or incomplete and must be fixed before code: (1) `read_counter` classifies a given finished move;
it does not produce a per-bar wave position, wave ends or an alternate count, so W1 needs a new causal counter on top
of it; (2) the template schema cannot express family G (the id / family pattern, the enum-driven entry styles and the
three timeframes); (3) W4's "no new channel code" does not hold: the existing line breakouts are not anchored to the
corrective wave; (4) the signal-text guard refuses "wave 2" (a digit). Each has a resolution below (WB1-WB14).

**Migrations: none** for the series, the recipe extensions and the templates G1-G11 (all computed from bars; templates
are JSON in the repository; flags are registered in code). Section 4 (target_rule WAVE_PROJECTION) and the exit
settings ride on section 5 of the templates spec ("exits as settings"), which needs a migration and the owner's
approval; it is not part of this family's first steps.

## 1. What exists today (the code the order says to reuse)

| Piece | Where | What it really does |
|---|---|---|
| Wave rules (generic) | `backend/app/price_action/daily_trend/rules.py:33` `triangle`, `:54` `wedge`, `:89` `impulse`, `:110` `zigzag`, `:122` `flat` | Pure functions of the wave-end prices x0..xn; `done=False` checks a move whose last wave is still running. Impulse: 2 not beyond 1's start, 3 beyond 1's end, 4 strictly beyond 1's end, 3 not the shortest (checked once five waves exist), 5 beyond 3's end. Wedge (diagonal): 4 not beyond 2's end, contracting / expanding length relations. Zigzag / flat: by the B/A ratio (settings). |
| Pattern ratios | `daily_trend/settings.py:36` `PATTERN_DEFAULTS` | `zz_max_ba` 0.90, `flat_min_ba` 0.618, `flat_b_max` 2.0, `flat_expanded_min` 1.05: the zigzag / flat / expanded-flat type depends on these ratios. |
| `reduce_waves` / `reduce_counted` | `daily_trend/legs.py:114`, `:137` | Alternating extremes reduced to 5 / 3 / 1 waves (removes the smallest inner pair); `reduce_counted` also says how many original legs each wave holds. |
| `read_counter` | `daily_trend/legs.py:177` | One WHOLE move (extreme to extreme, one degree lower pivots inside) -> `{"family", "n"}`: impulse / diagonal / triangle / combination / zigzag / flat / 3-wave / single. Recursive, memoised per thread by the prices (`:181`). Uses `PATTERN_DEFAULTS` hard-wired (`:192`). Returns no labels, no wave ends, no in-progress position, no alternate. |
| `motive_segs`, `leg_points`, `structure`, `classify` | `daily_trend/legs.py:316`, `:288`, `:256`, `:362` | The five wave segments of a motive reading; a leg's inner points from pivots confirmed by a bar (no look-ahead); the leg kind (structure plus the "nature C" percentile measure, `LEGS_DEFAULTS`). `leg_points` reads `p.bar` / `p.confirm_bar` (the Daily `DPivot` fields). |
| Daily trend fold | `daily_trend/fold.py:201` `fold_impulse` | The ported Daily trend state machine with the owner's decisions (Q22, Q28, Q49, A1, ABC_FIX: `corr_label`, `wave_a`, `alt`, `unfolded_123`). |
| Leg classifier | `backend/app/price_action/legs.py:106` `zigzag_pivots`, `:175` `leg_features`, `:273` `classify` | Impulse vs pullback labels (STRONG_IMPULSE ... RANGE) on its own median-range ZigZag; the retracement depth is a continuous feature (no Fibonacci gate). |
| Causal swings | `backend/app/price_action/causal_swings.py:120` `zigzag_pivots`, `:188` `degree_pivots`, `:200` `tentative_pivot` | The ScreenQL swings: ATR-multiple thresholds per degree D0-D3 (`pa_settings.py:60`), a pivot known only from its confirm bar, the running extreme as a tentative pivot, `known_at` truncation invariance. Fields `bar_idx` / `confirmed_idx`. |
| Breaks | `price_action/breaks.py:59` `first_real_break` | Real break vs false break of a level. |
| ScreenQL swings / structure | `backend/app/screener/registry.py:258-262` SwingHigh / SwingLow / SwingDirection, `:287` SwingLabel, `:311` SwingBreakout, `:314` SwingBreakType (BOS / CHOCH / BREAK), `:339` DowTrend; runtime `screener/runtime.py:488` `_swing_pivots`, `:566` `_swings`; Dow in `screener/breakouts/screens.py:292` | All true per-bar series from confirmed pivots; usable with `@timeframe` (R1). |
| Reversal, patterns, divergence, volume | `registry.py:271` ReversalAt(level, direction), `:253` Pattern(name, confirm), `:283` RsiDivergence(side, lookback, degree), `:331` RelVolume | RsiDivergence is REGULAR only with a fixed RSI(14) (`runtime.py:519`); it and SwingLabel are close-only in alerts (`screener/amw_functions.py:28`). |
| Areas | `registry.py:435-441` NearZone / ZoneRole / ZoneState / ZoneStrength; `:264-269` SwingZoneStrength / SwingZoneDistance | Market zones are stored snapshots behind the `market_zones` flag (OFF), tenant-scoped (`screener/zones.py:1`); swing zones are computed from the bars but only for the LAST confirmed swing of a side. Neither gives a zone-edge price series. |
| Line / pattern / retest breakouts | `registry.py:400` TrendlineBreakout(side, mode, touches, k), `:404` PatternBreakout(side, pattern, v, k), `:416` RetestEntry(side, of, bars, reversal); `screener/breakouts/lines.py:1`, `retest.py:102`; params `breakouts/params.py:250-299` | Lines come from their own fractal swings (`tl_order`) over the last `tl_max_bars`, the newest anchors; mode channel = beyond the FAR edge of a channel sloping the same way; triangle = two sloped lines converging, with volume (`vol_mult`), flat-sided triangles not detected (BK4-2). RetestEntry `of` is range / swing / donchian / trendline only (R7 not built). |
| Recipe core (TPL-1) | `backend/app/strategy_templates/schema.py`, `compile.py`, `engine.py`, `adapter.py`, `parity.py`, `language.py`, `templates/D1.json` | Strict schema (`schema.py:145` id `^[A-F][0-9]{1,2}$`, `:148` family A-F); one Context with one `timeframe_param` (`:102`); timeframe and yes / no settings cannot be read inside an expression (`compile.py:172`); state machine with event / state setups, arm level, expiry, same_bar, conflict, geometry, warm-up (`engine.py`); `SPARSE_SERIES` empty (`engine.py:39`); signal-text guard refuses any digit outside timeframe names (`language.py:34-46`), placeholders `timeframe` / `context_timeframe` only (`language.py:22`); adapter not wired to deployments. |
| Chart layers | `frontend/src/chart/indicatorsModel.ts:6`, `useTradesLayer.ts`; flag catalog `backend/app/platform/flag_catalog.py:124` | The "Layers" group shows a layer only while its flag is on; flags are registered in code (no migration). |

Tests that exist: `backend/tests/test_n1_l2_daily_trend.py` checks `read_counter` and the rules against a Trade golden
file from the PRIVATE fixtures store; the public CI skips those tests. There is no public synthetic test of
`read_counter` or of the rules per count type.

## 2. The shared rules, item by item

### Repo rule (generic method only)

- Exists: the generic rule functions (`rules.py`), the reductions and `read_counter` are the classic wave principle.
  `fold_impulse` and its fields (`corr_label`, `wave_a`, `alt`, `unfolded_123`, the Q22 / Q28 / Q49 / A1 / ABC_FIX
  decisions) are the owner's Daily trend method, ported from Trade.
- Finding: building WaveState on `fold_impulse` would publish the owner's method as a product feature, and "model C"
  as a named preset would point at it.
- Resolution (WB2): the new wave code imports only `rules.py`, `reduce_counted`, `read_counter`, `motive_segs`,
  `leg_points` and `causal_swings`; never `daily_trend.fold`, `daily_trend.context`, `level_gate`, nor the nature-C
  measure (`classify`, `LEGS_DEFAULTS`) as a gate. An import test enforces it. No preset, label, comment or doc names
  the owner's configuration; a user builds any configuration from the public parameters.

### W1 WaveState(degree, tf)

- Exists: the rules and `read_counter` (above). Nothing produces a per-bar wave position.
- Spec assumption "built on read_counter" is only half right: `read_counter` answers "what family is this finished
  move" for points someone else chose. It does not choose the origin, does not label the running wave (IMP_2, COR_B),
  does not return wave ends and has no alternate.
- Findings:
  - The origin choice IS the count. A five-wave leg followed by a pullback reads both IMP_2 and COR_B (a five-wave A).
    G1 and G3 would then fire on the same bars in the same direction (WB8).
  - Causal latency: a wave end is known only when its pivot is confirmed (price came back by the degree threshold), so
    IMP_2 starts on wave 1's confirm bar, not at its extreme; the running wave's end is the tentative extreme
    (`causal_swings.py:200`), which moves bar by bar but never rewrites an earlier bar.
  - Degree: the inner structure is read one degree lower on the same timeframe. At degree 0 there is no lower degree,
    so every wave reads "single" (fits both) and nothing is clear.
  - The spec's states have no labels for a triangle's D / E legs, and W-X-Y has no labels of its own.
  - Zigzag pivots are path-dependent on where the history starts (`registry.py:91`); the counter's origin search reaches
    back several pivots, so it is more fetch-sensitive than SwingHigh / SwingLow.
  - Forming bars: a forming bar can confirm a swing and take it back, as for RsiDivergence / SwingLabel.
  - `leg_points` reads Daily `DPivot` fields; causal swings use other field names.
- Resolution (WB1): a new module `backend/app/price_action/waves/` with a causal counter:
  1. Pivots: `causal_swings.degree_pivots` at the wave degree d (1-3) and at d - 1 for the inner structure, plus the
     tentative extreme; a small pivot view adapts them to `leg_points`.
  2. At each new confirmed pivot (and when the tentative extreme crosses a rule level), candidate origins are the last
     `wave_lookback_pivots` confirmed pivots that are the extreme of everything since them. For each, the points from
     the origin to the tentative extreme are reduced (`reduce_counted`) and checked with the PARTIAL rules
     (`impulse` / `wedge` / `zigzag` / `flat` / `triangle` with `done=False`); finished waves are checked with
     `read_counter` (wave 1, 3, 5, A, C motive; 2, 4, B corrective).
  3. Primary vs alternate: the larger-degree context decides impulse vs correction (a first leg with the degree d + 1
     swing direction is wave 1, against it is A); then the oldest valid origin; a tie on the running wave's direction
     is UNCLEAR. WAVE-1.
  4. Values: `IMP_1`..`IMP_5`, `COR_A`..`COR_E` (D / E only in a triangle), `UNCLEAR`; type `IMPULSE`, `DIAGONAL`,
     `ZIGZAG`, `FLAT`, `EXPANDED_FLAT`, `TRIANGLE`, `DOUBLE` (W / X / Y read as A / B / C with type DOUBLE, so G11 is
     G3 / G8 with type DOUBLE as the spec says), `UNCLEAR`. Registry: `WaveState(degree)`, `WaveType(degree)`,
     `WaveAlt(degree)`, `WaveClarity(degree)` (categorical: CLEAR / ALT_AGREES / ALT_DISAGREES / UNCLEAR - no score).
  5. Incremental (R3): the count is recomputed only on a pivot confirmation or a rule-level crossing; other bars carry
     it forward and update the tentative end. `read_counter`'s memo bounds the recursion.
  6. Degree choices 1-3 only; min_bars = `SWING_DEGREE_MIN_BARS[d]` plus the lookback; degree >= 2 carries the
     "fetch-dependent" note (R6); the functions join `INTRABAR_FUNCTIONS` (close-only alerts) and a new flag
     `screener_waves` (OFF) (WB13).
- Look-ahead / repaint: WaveState[t] is a function of bars <= t only; a later change shows from its bar on. Tests:
  truncation over many t, "a later count change never alters earlier values", left-truncation agreement (as S5-A4
  measured for swings), mirror.

### W2 level companions

- Exists: wave ends are the pivots; nothing exposes them as series.
- Findings on the hard rules (WB5), checked against `rules.py`:
  - "wave 2 never beyond the start of wave 1": matches `impulse` (`rules.py:96`); equal is allowed.
  - "wave 3 never the shortest": the code checks it only once five waves exist (`rules.py:103`). It yields a CAP on
    wave 5 (on the winning side of a wave-5 idea), not a level on the losing side. As an invalidation it would fail S6
    (invalid geometry) every time. It belongs in the count (the count changes when wave 5 passes the cap) and in the
    evidence, not in WaveInvalidation.
  - "wave 4 never into wave 1's range, except in a diagonal": matches `impulse` (`rules.py:100`, strictly beyond);
    the diagonal's own level is wave 2's END (`wedge`, `rules.py:73`), which the spec does not state.
  - "B (zigzag) never beyond the start of A": the code's zigzag already ends at B/A >= `zz_max_ba` (0.90, a setting),
    before A's start; B/A between `flat_min_ba` and `zz_max_ba` is both a zigzag and a flat (primary / alternate).
  - "a flat / expanded flat B is allowed beyond it": matches `flat` (no upper limit; B > `flat_b_max` x A is only
    "strong evidence"). So a flat B has NO hard invalidation level: WaveInvalidation is NaN there.
  - EXPANDED_FLAT is a sub-type the code decides only once C goes beyond A's end (`rules.py:136-140`); during B the type
    is FLAT with "B beyond the origin".
- Resolution: `WaveOrigin(degree)`, `WaveEnd(degree, wave)` (wave in 1-4 / A-D / `current` = the tentative end as
  known), `WaveInvalidation(degree)` per type: IMP_2 -> wave 1's start; IMP_3 -> wave 1's start until 3 passes 1's
  end, then wave 2's end; IMP_4 -> wave 1's end (impulse) / wave 2's end (diagonal); IMP_5 -> wave 4's end; COR_B
  zigzag -> A's start; COR_B flat -> NaN; COR_C -> B's end; triangle D / E -> the end of B / C; DOUBLE X -> W's start.
  `WaveCap(degree)` for the wave-5 cap (evidence). All are price series, NaN when not applicable, and go into
  `engine.SPARSE_SERIES` so they do not hold the warm-up back (WB4). A test per type, flat and expanded flat B
  included, and diagonals.

### W3 ratio companions

- Spec says `read_counter` "uses no ratios": wrong. The zigzag / flat / expanded-flat type depends on the B/A ratios
  in `PATTERN_DEFAULTS`, and `read_counter` hard-wires them (`legs.py:192`) with a memo keyed by the prices only
  (`legs.py:181`).
- Resolution: the four type ratios become ADVANCED wave settings with today's values as defaults (WAVE-6). `read_counter`
  gains an optional settings argument (default `PATTERN_DEFAULTS`, so the Daily trend and its golden parity stay
  unchanged) and the memo key includes them; they are template parameters, so the params hash covers them (R9).
  `WaveRetrace(degree, ratio)` and `WaveProjection(degree, base_wave, ratio)` take the ratio only from a `$setting`.
  The "no ratio literal" test scans the G recipe JSON and the waves module for literals other than the declared
  defaults.

### W4 the corrective channel

- Spec says "reuse TrendlineBreakout(mode = channel), no new channel code". It does not fit (WB6):
  - TrendlineBreakout finds its own line from fractal swings over the last `tl_max_bars` (`lines.py:1-20`), the newest
    anchors, unrelated to the corrective wave's pivots.
  - A C-direction break of B's channel is a break of the near edge AGAINST the channel's slope; mode channel is the far
    edge of a channel sloping the same way (`params.py:250`).
  - PatternBreakout(triangle) needs two sloped lines and volume; it misses flat-sided triangles (BK4-2) and may
    disagree with the counted triangle.
  - RetestEntry has no `of` for a channel or a wave level (R7 not built).
- Resolution: one anchored series pair from the counter's own pivots, reusing the line math of
  `price_action/trendlines.py` and the BK1 break rule (close beyond by k ATR, ATR from bar t-1):
  `WaveLineLevel(degree, line)` (price, projected to each bar) and `WaveLineBreak(degree, line, side, k)` with line in
  `b_channel` (the corrective wave's line on the C side, through its lower-degree pivots), `c_channel`, `triangle_edge`
  (the b-d line), `two_four` (the 2-4 line, also a diagonal's lower / upper line). R7 adds `of = "wave_line"` and
  `of = "wave_end"` to RetestEntry, reading the line's value at the retest bar. TrendlineBreakout / PatternBreakout stay
  available as optional evidence.

### Section 2 common parameters

| Parameter | Finding | Resolution |
|---|---|---|
| signal_tf / wave_tf / higher_tf, constraint higher >= wave >= signal | The constraint form exists (`schema.py` Constraint, timeframe compare). But a timeframe setting cannot reach an expression (`compile.py:172`) and there is one Context timeframe only (WB3). | Compile-time placeholders `@{wave_tf}` / `@{higher_tf}` in expressions, filled from timeframe settings and then validated like a literal `@1h`. |
| Trend relation | `DowTrend(degree)@{higher_tf}` exists; RANGE blocks both sides under with_higher_trend. The default per template is not stated (G3 is "counter-trend allowed"). | with_higher_trend = the signal's direction equals the higher trend; counter_trend_allowed = also the opposite direction, only for a C / Y leg; any = no filter. Defaults per template: WAVE-2. |
| Entry style | One template must switch its trigger by an enum; the schema has no conditional parts (WB3). aggressive needs an area; candle_confirmation's ReversalAt needs a price level and there is no zone-edge series. | Schema `variants`: per part, one expression per enum value, chosen at compile time (only the chosen one is compiled and counts for the warm-up). aggressive requires area_source != none or the Fibonacci zone on (a constraint). ReversalAt reads `WaveEnd(degree, "current")`. |
| Area source | Market zones are snapshots behind an OFF flag, absent for older backtest periods (R10), so a default of market zones gives no signals in backtests and breaks parity (WB11). Swing zones cover only the last confirmed swing of a side. | Default swing zones at degree d - 1 (computed from bars, parity-safe); market zones optional, "no snapshot -> no area" is no signal, never a refusal. WAVE-3. |
| Fibonacci zone filter | Expressible with WaveRetrace on the running end. | `WaveEnd(current)` between `WaveRetrace($fib_from)` and `WaveRetrace($fib_to)`, off by default. |
| RSI divergence regular / hidden | RsiDivergence is regular only, RSI(14) fixed. | Add a `kind` argument (regular default, hidden) with causal tests; RSI stays 14 unless the period argument of E2 lands. WAVE-14. |
| Volume | "Corrective wave on lower volume" needs an average over a variable span; no such function. Indices have no volume. | `WaveVolumeLower(degree)` (the corrective wave's mean volume below the previous impulse wave's: a comparison, no number) and RelVolume on the trigger; the filter is refused with a plain message on a symbol without volume. WAVE-15. |
| Clarity | No measure exists. | `WaveClarity` (categorical) and the param min_clarity (exclude UNCLEAR always; optionally exclude ALT_DISAGREES). WAVE-5. |
| Window | first per wave = a state setup (one signal per instance); every pullback = an event setup. Setup kind is fixed per template. | `variants` covers the whole setup; "every pullback" = each new confirmed lower-degree pivot inside the corrective wave starts an instance (R8: newest wins). WAVE-12. |
| Invalidation enum | "the area's far side" has no price series. | Add a zone-edge price companion (swing zones now, market zones when the flag is on, NaN otherwise); a NaN invalidation gives "no_invalidation" (S6), so the default is "WaveInvalidation, else the corrective wave's extreme". |
| exit_signal | R5 plumbing is not built, so an exit series is computed but nothing acts on it yet. "WaveState leaves the traded wave": at the trigger the state still shows the corrective wave (its end is not confirmed), so a plain "state != traded wave" fires at once (WB9). "projection reached" is a target in disguise. | Per template, "leaves" = the count is invalidated or moves past the expected wave (G1: not IMP_2 / IMP_3). The form says exits act once R5 lands. "projection reached" waits for section 5 with WAVE_PROJECTION; default none. WAVE-11. |
| Sides | Exists. | - |

### Sections 4-8

- Section 4 targets: WAVE_PROJECTION needs `target_rule` on the deployment's exit settings (templates spec section 5:
  migration + owner approval, not built). NaN -> no target, never a refusal: keep. Not before section 5.
- Section 5 signal text (WB7): the guard refuses any digit outside timeframe names (`language.py:46`), so "Wave 2
  ended ..." is refused. Wave labels are written in words ("wave two", "wave B", "the fifth wave"); two placeholders
  `{wave_timeframe}` / `{higher_timeframe}`; invalidation in words; clarity and the alternate count as evidence tokens.
  WAVE-4.
- Section 6 UI: the "Layers" group and flags exist; the templates page, the settings form and Compare are the templates
  spec's step 3 (not built), so G's UI follows it. Example charts: no real-data chart is committed (public repository);
  synthetic examples in the repository, anonymised examples older than three months rendered on the server. WAVE-9.
  Wave scans run through ScreenQL (S11) with close-only alerts.
- Section 7 tests: all kept; added: public synthetic rule tests per count type (the existing ones need the private
  golden, WB14), left-truncation agreement, the import test of WB2, the mirror note (the ZigZag checks a high before a
  low when both qualify on the first pivot, so mirror fixtures start with an unambiguous first move).
- Section 8 order: kept, with the shared prerequisites first (section 5 of this file).

## 3. The catalogue, template by template

Long = the traded leg goes up; the short side is the mirror. "d" = the wave degree on wave_tf.

| # | Setup / trigger in today's terms | Spec assumption | Findings | Resolution |
|---|---|---|---|---|
| G1 Wave 3 entry | setup state `WaveState(d)@{wave_tf} == "IMP_2"` and type IMPULSE / DIAGONAL; arm level WaveOrigin (wick); trigger by entry style; invalidation WaveInvalidation | Right, with W1 | IMP_2 is known only from wave 1's confirm bar; "a clear wave 1" is undefined; overlaps G3 (WB8); exit "leaves the wave" fires at once (WB9) | Clear = wave 1's inner structure reads motive (WAVE-5); primary count decides IMP vs COR; exit per WB9 |
| G2 Wave 5 entry | `IMP_4`, type IMPULSE (no overlap); invalidation wave 1's end | Right | "the wave 4 rule" = wave 1's end for an impulse, wave 2's end for a diagonal; the 3-not-shortest cap limits wave 5 | WaveInvalidation per type; WaveCap as evidence ("the fifth wave is limited") |
| G3 C leg after B | `COR_B` after an A that made a CHoCH at wave_tf; invalidation A's start (zigzag) or the B rule | Partly | Flat B has no hard level (NaN); the code's zigzag ends at `zz_max_ba` before A's start; overlaps G1 | Default invalidation "WaveInvalidation, else B's extreme"; primary count rule (WAVE-1); default trend relation counter_trend_allowed (WAVE-2) |
| G4 B channel break | `COR_B`; trigger `WaveLineBreak(d, "b_channel", side, $k)`; retest twin via RetestEntry `of = "wave_line"` | Wrong (W4) | Existing TrendlineBreakout is not anchored and mode channel is the wrong edge; RetestEntry lacks the option | WB6 |
| G5 B triangle thrust | `COR_B` with type TRIANGLE complete (E confirmed); trigger `WaveLineBreak(d, "triangle_edge", ...)` | Wrong (PatternBreakout) | PatternBreakout needs volume, misses flat sides, is unanchored; WaveState lacks D / E labels | WB6; COR_D / COR_E labels; PatternBreakout as evidence only |
| G6 Break of A's end | `COR_C` started; trigger `CrossAbove(close, WaveEnd(d, "A") + $k * Lag(ATR($atr_n), 1))` (mirror below); retest twin `of = "wave_end"` | Right for the break; retest needs R7 | SwingBreakout(degree) may target a B-internal swing, not A's end | Use WaveEnd, not SwingBreakout; R7 extension |
| G7 Expanded-flat sweep | `COR_B`, type FLAT and `WaveEnd(d, "current")` beyond WaveOrigin; trigger the reclaim (`CrossAbove(close, WaveOrigin(d))` or ReversalAt at it); invalidation B's extreme | Wrong label | EXPANDED_FLAT is decided only when C passes A's end, so a setup on it never fires during B | Arm on FLAT + B beyond the origin by any amount (WAVE-8); "expanded" shows as evidence later |
| G8 Correction complete | the last corrective leg (C, E or Y) with its inner structure motive (C) and, for a zigzag, C reached A's end (`rules.reached`); trigger `WaveLineBreak(d, "c_channel")` or `SwingBreakType(...) == "CHOCH"` on signal_tf at the area; invalidation the C extreme | Partly | "Complete" is not knowable before the trigger; the spec has no E / Y labels; overlaps G1 / G2 one degree up | Setup = "C may be complete" (minimum structure met), the trigger confirms; labels as in W1; documented overlap across degrees |
| G9 End of impulse | `IMP_5` with 5 beyond 3's end; optional RsiDivergence (regular); trigger the 2-4 line break or a CHoCH; invalidation wave 5's extreme | Partly | "the wave 4 / 5 line" is ambiguous; counter-trend by nature | The classic 2-4 line (WAVE-7); sides follow the trend relation (spec) |
| G10 Ending diagonal | the current wave 5 or C whose inner structure reads DIAGONAL (partial `wedge`, `done=False`); trigger the 2-4 line of the diagonal; invalidation its extreme | Partly | "as wave 5 or C" needs the position in the count: available only through W1 | WaveType of the inner structure of the running wave; WB6 lines |
| G11 Double W-X-Y | `COR_B` / `COR_C` with type DOUBLE | Partly | X in progress is indistinguishable from a new impulse until X reads corrective; at W's end it overlaps G8 in the opposite direction | DOUBLE is primary only once X reads corrective (three waves) and has not gone beyond W's start; otherwise the alternate (evidence only) |
| G12 Seller wave pack | labels over G1-G11 signals | Depends | Family F's pack format does not exist; the schema has no composition; no label for counter-trend signals | Built after F as a label mapping, not a recipe; WAVE-10 |

## 4. Blocking findings and their resolution

| # | Finding | Resolution |
|---|---|---|
| WB1 | `read_counter` classifies a given finished move; W1 needs a per-bar position, the origin choice, wave ends and an alternate. | New causal counter in `app/price_action/waves/` on the generic rules (partial checks with `done=False`) and `read_counter` for finished waves; recomputed on pivot confirmations only (R3). |
| WB2 | `daily_trend/fold.py` is the owner's Daily trend method; using it (or a "model C" preset) would publish it. | Import only the generic rules / reductions / `read_counter` / `leg_points` / causal swings; an import test; no owner preset or name. |
| WB3 | The schema refuses family G (`schema.py:145`, `:148`), cannot switch parts by an enum, and timeframe settings cannot reach expressions. | Additive schema extension (still `atp.template/1`; D1 unchanged): family G, `variants` per part keyed by an enum setting, `@{tf_setting}` placeholders; tests that unchosen variants are not compiled and do not hold the warm-up. |
| WB4 | Level companions are NaN off their waves; today every value series must "start" before the warm-up ends. | Add the wave level companions to `engine.SPARSE_SERIES`. |
| WB5 | Hard rules: the 3-not-shortest rule is a cap, not an invalidation (S6); a flat B has no hard level; the zigzag limit is a ratio setting; the diagonal level is 2's end; EXPANDED_FLAT is known only after C passes A's end. | WaveInvalidation per type as listed in W2; WaveCap as evidence; G7 arms on FLAT + B beyond the origin. |
| WB6 | W4: existing line breakouts are not anchored to the corrective wave, mode channel is the wrong edge, PatternBreakout differs from the counted triangle, RetestEntry has no channel / level option. | `WaveLineLevel` / `WaveLineBreak` from the counter's pivots with the trendlines line math and the BK1 rule; R7 RetestEntry `of = "wave_line"` / `"wave_end"` with the projected value. |
| WB7 | The signal-text guard refuses digits, so "wave 2" fails; only two placeholders. | Wave labels in words; `{wave_timeframe}` / `{higher_timeframe}`; guard tests over every G token. |
| WB8 | One structure reads IMP_2 and COR_B (G1 / G3) or "correction complete" and "W of a double" (G8 / G11). | The primary count decides by the larger-degree context; the alternate is evidence only; G templates read the primary only, so they are disjoint by construction. |
| WB9 | "WaveState leaves the traded wave" fires at the trigger; exits have no plumbing yet (R5). | Defined per template as "invalidated or past the expected wave"; inert until R5, said in the form. |
| WB10 | Prerequisites from the templates spec are open: R5 (exit plumbing), R6 wiring (deployment fetch of 5 days is too short for degree >= 1 swings), R7 (RetestEntry extension), adapter not wired to deployments / routes. | Build order (section 7): R7 before the retest styles; R6 wiring before any G deployment; G signals run as scans and backtests first. |
| WB11 | Area source "market zones" has no data in older backtests and is behind an OFF flag: no aggressive / candle signals there, parity breaks. | Default swing zones; market zones optional with "no snapshot -> no area". |
| WB12 | Degree 0 has no inner structure; fetch sensitivity grows with the origin search. | Degree 1-3 only; min_bars = degree bars + lookback; fetch-dependent note for degree >= 2; left-truncation agreement test. |
| WB13 | The wave functions read swings a forming bar can confirm; new functions need a gate. | Add them to `INTRABAR_FUNCTIONS` (close-only alerts) and behind a new flag `screener_waves` (OFF); templates carry the flags they need (breakouts, BK4, zones). |
| WB14 | The rule / `read_counter` tests in public CI are skipped (private golden). | Public synthetic tests per count type (impulse, diagonal contracting / expanding, zigzag, flat regular / running / expanded, triangle contracting / expanding, combination) on hand-made point sequences. |

## 5. Migrations

- None for W1-W4, the schema extension, the language tokens, the flags and G1-G11 (all code and JSON).
- Section 4 (WAVE_PROJECTION, and the exit "projection reached") needs the templates spec's section 5 "exits as
  settings" (`stop_source` / `target_rule` on ExitRules): a migration and the owner's explicit approval. Not in this
  family's first PRs. When it comes: deploy after 15:30 IST, or `MIGRATION_FORCE=1` only on the owner's word.
- The stored, user-adopted template copies (provenance) are the templates spec's step 3 store, not wave-specific.

## 6. Owner questions

WAVE-1 .. WAVE-16 in `docs/OPEN_QUESTIONS.md` (section "Wave / correction templates (family G): the review's owner
questions"), each with the provisional default the build uses.

## 7. Build plan (the spec's section 8 order, with the shared pieces first)

Each step is one draft PR: tests, ruff + mypy, self-review, PAPER only, no migration, the deploy line after merge.

1. **Done: this review** (docs only).
2. **Recipe extensions** (WB3, WB4, WB7): family G, `variants`, `@{tf_setting}` placeholders, SPARSE_SERIES entries,
   wave text tokens and placeholders. Tests: schema refusals, unchosen variants not compiled and not in the warm-up,
   placeholders validated like literals, D1 unchanged (params hash and signals), guard over every token.
3. **W1 the causal counter** (WB1, WB2, WB5, WB12, WB13, WB14): `app/price_action/waves/` + registry `WaveState`,
   `WaveType`, `WaveAlt`, `WaveClarity` behind `screener_waves`; `read_counter` settings argument (Daily trend
   unchanged). Tests: public rule tests per count type; must-label / must-not-label fixtures; truncation over many t;
   a later change never alters earlier values; UNCLEAR on ambiguity; left-truncation agreement; mirror; the import
   test; the perf budget (incremental, the BK perf job extended); `causal_cases` entries so the no-look-ahead harness
   covers them.
4. **W2 / W3 companions**: WaveOrigin, WaveEnd, WaveInvalidation (a test per type, flat / expanded flat B, diagonal),
   WaveCap, WaveRetrace, WaveProjection (ratio only from a setting), WaveVolumeLower, the zone-edge companion,
   RsiDivergence `kind`. Tests: causal, NaN off-wave, the "no ratio literal" test.
5. **W4 anchored lines + R7** (WB6): WaveLineLevel / WaveLineBreak (b_channel, c_channel, triangle_edge, two_four) on
   the trendlines line math and the BK1 rule; RetestEntry `of = "wave_line"` / `"wave_end"` with the projected value
   (shared with family C). Tests: causal, projected value at the retest bar, once per break, mirror.
6. **Parity harness for G**: scan = backtest = options backtest = worker on one trailing window W (R6), with wave_tf
   and higher_tf frames.
7. **Templates G3, G8, G1** (the most used), then **G4-G7**, **G2**, **G9-G11**. Per template and per entry style:
   must-fire / must-not-fire fixtures (gaps, conflict, expiry, failure before the retest, warm-up, invalid geometry,
   UNCLEAR, alternate disagrees), mirror, truncation, parity, language guard, property tests on random valid settings.
8. **G12** after family F's pack format exists (WAVE-10).
9. **UI** after the templates spec's step 3: the "Waves" chart layer (flag `chart_waves_layer`, OFF; count, dashed
   alternate, invalidation line in words, optional projection band; phone, light / dark), wave scans and alerts, the
   "Waves" group on the Templates page, the "Waves in 2 minutes" tour, Compare by entry style (no performance claims).
   Before / after screenshots, desktop + phone, light + dark.

Outside this family but needed before G deployments: R5 (exit plumbing), R6 wiring (deployment fetch), and section 5
(exits as settings, migration + owner approval) for any target rule.
