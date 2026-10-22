# Wave / correction templates (family G): implementation status

Owner order: `docs/owner-orders/ATP_SESSION_WAVE_TEMPLATES.md`. Plan of record: the independent review
`docs/design/WAVE_TEMPLATES_REVIEW.md` (branch `claude/wave1-review`, PR #361; where it differs from the order, the review
wins). This file records what is built per build step; when #361 is merged, its section 7 points here.

Binding: generic method only (the classic wave principle and structure; nothing from any one trader's private method),
signals only, PAPER only, structure words in the signal text, no fixed numbers in reading rules (the only numbers - the
B/A ratios - are settings with today's values).

## Build steps 2 + 3 (PR #367): the shared pieces - recipe extensions and the W1 causal wave counter

No G template is built yet. No migration.

| Finding | What | Where | Status |
|---|---|---|---|
| WB3 | Schema, still `atp.template/1`: family `G` and ids `G1`..`G99`; `variants` per part (`Switch` for trigger / invalidation / exit signal, `SetupSwitch`, `ContextSwitch`) keyed by an enum setting, every choice covered, null allowed except for the invalidation; only the chosen variant is compiled and counts for the warm-up; `validate_template` compiles every variant; `@{timeframe_setting}` placeholders filled before parsing and validated like a written `@1h` (a finer timeframe is refused by the validator) | `app/strategy_templates/schema.py`, `compile.py` (`chosen`, `fill_timeframes`, `Recipe.chosen_setup` / `has_trigger`), `engine.py` | done |
| WB3 | D1 unchanged: the JSON file's bytes (SHA-256 pinned), its params hash formula (family G alone adds the wave settings' fingerprint, R9), its parts and its validation | `tests/test_wave2_recipe.py::test_d1_stays_byte_identical` | done |
| WB4 | `engine.SPARSE_SERIES` = the wave series that are missing off their waves (`WaveAlt` now; `WaveOrigin`, `WaveEnd`, `WaveInvalidation`, `WaveCap`, `WaveRetrace`, `WaveProjection`, `WaveLineLevel` when W2-W4 register them): they never hold the warm-up | `engine.WAVE_SPARSE_SERIES` | done |
| WB7 | Signal text: wave labels, types and clarity in words (`WAVE_WORDS`, `TYPE_WORDS`, `CLARITY_WORDS`), placeholders `{wave_timeframe}` (setting `wave_tf`) and `{higher_timeframe}` (setting `higher_tf`), wave tokens incl. the invalidation in words; the guard passes every token; `validate_template` refuses a token whose timeframe setting the template lacks | `app/strategy_templates/language.py`, `library.signal_text` | done |
| WB1 | The causal counter: degree-d swings (each one wave) plus the running end; up to five waves back as candidate origins; per origin the readings whose rules hold with the last wave running (`done=False`): IMPULSE, DIAGONAL (wave four overlapping wave one), ZIGZAG, FLAT / EXPANDED_FLAT (B/A settings), TRIANGLE (D / E), DOUBLE (once X is finished; while X runs only an alternate); every FINISHED wave's inner structure (degree d - 1, confirmed by the bar) read by `read_counter` must fit its role | `app/price_action/waves/counter.py` | done |
| WB1 / WAVE-1 | Main count: the degree d + 1 swing direction decides impulse vs correction, then the oldest valid origin, then the simpler type; a tie on the implied larger direction is UNCLEAR. Alternate: the competing reading from the same origin, else the best other reading of more than one wave, else another type from the same origin | `counter.count`, `counter._alternate` | done |
| WAVE-5 | Clarity: CLEAR / ALT_AGREES / ALT_DISAGREES / UNCLEAR (a tie, or a finished wave with no inner swings) | `counter.count` | done |
| WAVE-6 | The four B/A ratios are settings with today's values (equal to the generic rules' defaults, a test keeps them equal), with bounds; `read_counter` takes them as an optional argument (default: the Daily trend's values, unchanged; the memo keys them) | `app/price_action/waves/settings.py`, `daily_trend/legs.py read_counter(prices, s=None)` | done |
| WB1 / R3 / S8 | Per-bar series, recomputed only on a bar that confirmed a new degree-d swing; other bars carry the count; a past bar is never rewritten; memoised by the frame's content for the four functions | `app/price_action/waves/series.py` | done |
| WB2 | Imports only the generic rules, `read_counter` / `leg_points` (and the reductions if needed), the causal swings and the price-action settings; never the Daily trend fold / context or the level gate; no owner configuration named | `tests/test_wave2_counter.py` (AST allow-list, a subprocess import check, a name check) | done |
| WB12 | Degrees 1-3 only (degree 0 has no inner structure); min bars = the swing degree's history; degrees 2 / 3 are fetch-dependent like the swings | registry `_WDEGREE`, `degree_min_bars` | done |
| WB13 | ScreenQL `WaveState(degree)`, `WaveType(degree)`, `WaveAlt(degree)`, `WaveClarity(degree)` (categorical) behind the new flag `screener_waves` (OFF; `controls.py`, `flag_catalog.py`): hidden from the registry endpoint and the Copilot's registry while off; refused in screens (validate / run / Copilot / library / history through `routes._gate`), chart studies and alert rules; an active rule pauses with the reason; wave alerts fire on bar close only | `app/screener/registry.py`, `runtime.py`, `waves.py`, `routes.py`, `charts/studies.py`, `alerts/rules_routes.py`, `alerts/barclose.py` | done |
| WB14 | Public synthetic tests: the generic rules and `read_counter` per count type on hand-made sequences (impulse, extended impulse, diagonal contracting / expanding, zigzag, flat regular / running / expanded, triangle contracting / expanding, combination); the counter per count type must label / must not label; mirror | `tests/test_wave2_counter.py` | done |
| - | No look-ahead: the four functions in `app/quality/causal_cases.py` (the Z10-a harness: truncation, future perturbation, immutability); truncation over many t; a later change never alters earlier values; recompute only on a new swing; left-truncation agreement (degree 1); the performance budget | `tests/test_wave2_counter.py`, `tests/test_z10a_no_lookahead.py` | done |

## W2 / W3 levels and ratios, W4 anchored wave lines and R7 (the review's build steps 4 + 5; stacked on #367)

No G template is built yet. No migration. Everything below is behind `screener_waves` (OFF).

| Finding | What | Where | Status |
|---|---|---|---|
| WB5 / W2 | Per-bar levels of the MAIN count (the alternate is evidence only, WB8), from the count known at the bar, its running end (the extreme since the last confirmed swing, bar by bar) and the bars up to it; NaN where they do not apply: `WaveOrigin`, `WaveEnd(degree, wave)` (1-4 / A-D finished; `current` = the running end), `WaveInvalidation` per type - wave two: start of one; wave three: start of one, then end of two once the running end passed the end of one (WAVE-21); wave four: end of one (impulse) / end of two (diagonal); the fifth wave: end of four; zigzag B: start of A; flat / expanded flat B: none (NaN); C: end of B; triangle D / E: end of B / C | `app/price_action/waves/levels.py` | done |
| WB5 | "Wave three is never the shortest" is a CAP on the fifth wave (`WaveCap`, evidence on the winning side), never an invalidation level (S6); the zigzag's limit is the B/A ratio SETTING (`zz_max_ba`, reached with `WaveRetrace`), not a hard level | `levels.WaveLevels.cap`, tests | done |
| WB5 / WAVE-8 | EXPANDED_FLAT is decided only once C passes A's end, so the G7 arm "FLAT with B beyond A's start" is its own series: `WaveBeyondOrigin` (a wave against the first one beyond the count's start, any amount; WAVE-26); the type stays FLAT during B (test) | `levels.WaveLevels.beyond_origin` | done |
| W3 | `WaveRetrace(degree, ratio)`, `WaveProjection(degree, base_wave, ratio)` (from the running wave's start, in its direction; base `previous`, 1-4, A-D, finished only) and `WaveRatio(degree, base_wave)` (the running wave's length so far / the base wave's: the retracement or the extension as a value). The ratio is always the caller's (a literal or a `$setting`, read once); the modules hold no ratio (the grep test, extended) | `levels.py`, registry | done |
| WB4 | The level / ratio / line number series are in `engine.SPARSE_SERIES` (`WaveRatio` added); the yes / no ones are never missing | `strategy_templates/engine.py` | done |
| WB6 / W4 | `WaveLineLevel(degree, line)` anchored to the count's own pivots, projected with the trendlines' line math: `zero_two` (0-2; a zigzag's 0-B base), `one_three` (a triangle's A-C side, a diagonal's 1-3 line), `two_four` (2-4; a triangle's B-D side), `channel_<wave>` / `channel_current` (the wave's start-side line through its outermost lower-degree swing of the same kind: the corrective channel, WAVE-22). Flat-sided triangles are just lines of no slope; no volume requirement | `levels.WaveLevels.line` | done |
| WB6 | `WaveLineBreak(degree, line, side, k)`: the first close beyond the line by k x ATR (ATR of the bars before, the BK1 rule), once per line; a line first known beyond the close waits for a close back (WAVE-23) | `app/screener/breakouts/wave_breaks.py` | done |
| R7 | `RetestEntry(side, of, bars, reversal, degree, line, wave, k)`: `of = "wave_line"` (the retest of the broken line, its value at each retest bar, WAVE-24) / `"wave_end"` (the retest of a finished wave's end after a close beyond it, G6's twin). The B9 rule (leave, come back within `bars`, a reversal or a touch, close at or beyond; a close back inside ends the watch). The older choices take the unchanged B9 path: their signals are byte-identical (an output hash pinned from the code before this step) and so are their validation and the registry entries of every other function | `runtime.py`, `wave_breaks.retests`, tests | done |
| WB13 | Every new function carries `screener_waves`; RetestEntry's wave choices are gated too (a `$parameter` choice resolved from the screen's parameters, unknown = gated): while off the registry endpoint and the Copilot's registry show RetestEntry as before, and screens, studies and alert rules using a wave choice are refused; alerts on bar close only. No-look-ahead cases for every new function and the wave retest | `app/screener/waves.py`, `routes.py`, `nl.py`, `alerts/*`, `quality/causal_cases.py` | done |
| WB7 | Lines in words (`LINE_WORDS`), new tokens (the void levels in words, the cap, flat B beyond A's start, the channel / 2-4 / triangle side break, the close beyond the end of wave A, the retests); the guard passes every phrase | `strategy_templates/language.py` | done |
| WB11 / WAVE-3 | The wave templates' area source defaults to swing zones (`AREA_SOURCE_DEFAULT`); market zones only when chosen (no snapshot = no area, never a refusal) | `waves/settings.py` | done (used by the G templates) |
| WB2 | The allow-list now admits `bisect`, `app.price_action.trendlines` (the line math) and `waves.levels`; still never the Daily trend fold | `tests/test_wave2_counter.py` | done |

Tests: `tests/test_wave3_levels_lines.py` - the invalidation per count type (impulse two / three / four / five, diagonal
four, zigzag / flat / flat-beyond-A B, regular / expanded flat C, triangle D / E, double three Y, wave A) and its mirror;
wave three's level moving with the running end; the cap (must fire / must not, mirror); flat B beyond A's start with the
type FLAT; origin / ends; ratio levels and values; point lines, triangle sides with a flat side, the corrective channel
(outermost swing, confirmed swings only, mirror); per-bar series on a hand-made path through waves two to five; truncation
over many t at degrees 1-3 and on the fifth-wave path (values and anchors); a later change never alters earlier values;
mirror on bars; the break (once, arming, k, no volume) and the retest (the broken line's value, failure); ScreenQL equals
the levels; the wave retest through ScreenQL by truncation; RetestEntry byte-identity; the flag (hidden, refused, alert
rules, the Copilot registry); sparse series; words; area default.

Not here (the review's later build steps): the volume companion (WaveVolumeLower), the zone-edge companion and
RsiDivergence `kind` (with the G templates that read them); the G parity harness; the templates G1-G11 and G12; the UI
(`chart_waves_layer`). The wave ratio settings reach the ScreenQL functions with the G templates (their template
parameters; the params hash already covers them).

Owner questions: WAVE-17..WAVE-20 (step 2), WAVE-21..WAVE-26 (this step) in `docs/OPEN_QUESTIONS.md`.

## The G parity harness and the templates G1, G3, G8 (the review's build steps 6 + 7, first part)

No migration. The templates are JSON in the repository (`backend/app/strategy_templates/templates/G1.json`, `G3.json`,
`G8.json`). They are listed and can be adopted (as a PAPER strategy pre-fill, nothing starts by itself) only while BOTH
`strategy_templates` and `screener_waves` are on for the organisation; a G strategy id runs only with the flags of the
functions it reads (`screener_waves`, `screener_breakouts`, and `screener_breakouts_bk4` for the retest style).
G4-G7, G2, G9-G11 and G12 are not built yet.

| Item | What | Where | Status |
|---|---|---|---|
| Step 6 | The G parity harness on the TPL-1 harness: the wave / higher timeframe frames built from the SAME trailing window W (`context_frames`, the runtime's resample); a frame with other history is refused (`check_frames`: a count reads its swings from its first bar, WB12); scan with the frames handed in = scan that resamples = backtest = options backtest = worker; `wave_history_ok` (the window holds the wave degree's history) | `app/strategy_templates/wave_parity.py` | done |
| G1 | Wave three after wave two. Window: wave two of an impulse in the trade's direction, or wave three before it passed the end of wave one (`IMP_2`, or `IMP_3` with its running end not beyond wave one's end), the count not unclear (WAVE-5), the Fibonacci zone on wave two's depth when on; arm level the start of wave one (wick). Triggers: touch of the area / reversal candle at the running end of wave two at the area (both only while wave two runs), the smaller structure's CHoCH on the signal chart, the close beyond the channel of wave two by k ATR, its retest (R7). Invalidation: the start of wave one (`WaveInvalidation`, default) or the last smaller swing | `templates/G1.json` | done |
| G3 | Wave C after wave B. Window: wave B (not a double three's X - that is G11), or wave C before it passed the end of wave A; wave A changed the swing structure on the wave timeframe (the Dow trend at the wave degree no longer points against wave C; a setting allows any A, WAVE-31); counter-trend allowed by default (WAVE-2). Triggers as G1 on the channel of wave B. Invalidation: the start of wave A, or the end of wave B if B went past it (`Least` / `Greatest` of the origin, the running end and the last wave-degree swing: exact in wave B and wave C, also for a flat B with no hard level) or the last smaller swing | `templates/G3.json` | done |
| G8 | Correction complete, trend resumes. "May be complete": wave C of a zigzag / flat / expanded flat / double three whose running end reached the end of wave A, or a triangle's wave E, against the larger direction; the window holds `hold_bars` signal bars after it was last seen (the count is recomputed only on a confirmed swing, so the confirmation of C's end changes the state). Triggers: touch / candle while the last wave runs, CHoCH on the signal chart, the close beyond the channel of wave C (or E), its retest; never on an unclear count. Invalidation: the extreme of the last wave (`Least` / `Greatest` of the running end and the last wave-degree swing; for a contracting triangle the end of its wave C) or the last smaller swing | `templates/G8.json` | done |
| Common | Every number a setting with bounds and the review's defaults (wave / higher timeframe 1 hour / Daily, wave size 1, smaller swing 0, trend relation per WAVE-2, area swing zones per WAVE-3, clarity "not unclear" per WAVE-5, first opportunity per WAVE-12, Fibonacci zone off, no exit signal per WAVE-11); a choice reaches an expression only as a compared word (`$area_source == "none" OR ...`), so no function is switched by a hidden number; the only number nodes are the one-bar offset of "a new smaller swing" and the zero of "seen at least once" (a test) | the three JSON files, `tests/test_wave4_templates.py` | done |
| WB9 | Exit signals per template: none (default), the wave structure's CHoCH against the idea, and (G1 / G3) the count leaving the traded waves; inert until R5 (the form says it is not acted on yet); never true on the signal bar | the three JSON files | done |
| WB7 | One sentence per template in structure words ("The wave two pullback on the 1-hour chart may be over; an upward wave three may be starting; the count is void beyond the start of wave one."): the text cannot vary by trigger style (tokens switch on yes / no settings only), so it says what holds for every style | `strategy_templates/language.py` | done |
| ids | Family G choices are written as their position in the strategy id (`ids.COMPACT_FAMILIES`), so some twenty settings stay within the 80 characters; every other family's id is unchanged (a test) | `strategy_templates/ids.py` | done |
| Flags | A family listed only with its own flag (`routes.FAMILY_FLAGS`: G -> `screener_waves`): hidden and 404 on adopt while off | `strategy_templates/routes.py` | done |

Not built here (owner questions WAVE-27..WAVE-37): market zones as the area and the area's far side as an invalidation
(no zone-edge series yet), the four wave ratio settings as template parameters (the ScreenQL wave functions do not take
them yet), the RSI divergence and volume filters (RsiDivergence `kind` and WaveVolumeLower are not built), the
projection target / exit (section 5: a migration and the owner's approval), the evidence lines (clarity, the alternate)
in the signal text, a touch-only retest. Tests: `tests/test_wave4_templates.py` - must-fire per trigger style on pinned
synthetic wave paths (G3's retest checked as its expression: the fixture's channel of wave B is too steep for a retest
in the window), must-not (unclear count, disagreeing alternate, the higher trend, wave A's structure, the Fibonacci zone),
mirror, truncation over many t (fixtures and random-walk bars), parity (fixtures; the 1-hour wave chart with the
options backtest), property tests on random valid settings, the language guard, the flags, the id length.

## The templates G4-G7, G2, G9-G11 (the review's build step 7, second part)

No migration. Eight more JSON templates in `backend/app/strategy_templates/templates/` (`G2`, `G4`-`G7`, `G9`-`G11`), with
the same flags and gates as G1 / G3 / G8: listed and adoptable (a PAPER pre-fill, nothing starts by itself) only while
`strategy_templates` and `screener_waves` are on; a strategy id runs only with the flags of the functions it reads. Signals
only; exits per WB9 (computed, not acted on until R5); no projection target (section 5 needs a migration and the owner's
approval). G12 waits for family F (WAVE-10).

| Template | Setup (the window) | Trigger styles | Invalidation (default first) | Status |
|---|---|---|---|---|
| G4 Wave C: the break of wave B's channel | G3's window: wave B, or wave C before it passed the end of wave A; not a double three's X; Fibonacci zone on wave B's depth (off) | the close beyond the channel of wave B by k ATR (default), its retest (R7) | the start of wave A, or wave B's end beyond it; the last smaller swing | done |
| G5 Thrust out of a triangle | a triangle's wave E (type TRIANGLE) against its first wave, held `hold_bars` signal bars after the count last showed it (as G8) | the close beyond the triangle side through the ends of waves B and D (`two_four`), its retest | the far end of the triangle on wave E's side (the end of C or E, whichever is further); the last smaller swing | done |
| G6 Wave C: the close beyond the end of wave A | wave C (not a double three's Y) | the wave chart's close beyond the end of wave A by k x ATR(`atr_n`) of the bars before (default), the retest of that level (R7 `wave_end`) | the end of wave B (`WaveInvalidation`); the last smaller swing | done |
| G7 Expanded flat: the sweep beyond the start of wave A | wave B of a FLAT beyond the start of wave A (`WaveBeyondOrigin`, any amount: WB5 / WAVE-8; the type stays FLAT during B) | the close back on the near side of the start of wave A by k ATR (default), a reversal candle at it, the smaller structure's CHoCH | the extreme of wave B (the sweep); the last smaller swing | done |
| G2 Wave five after wave four | wave four of an IMPULSE (wave four not into wave one), or the fifth wave before it passed the end of wave three; arm level the end of wave one (wick); window first / every pullback; Fibonacci zone on wave four's depth of wave three (off) | G1's five: touch / candle at the area (while wave four runs), CHoCH, the channel of wave four's break, its retest | the end of wave one; the last smaller swing | done |
| G9 End of the fifth wave: a reversal | the fifth wave (impulse or diagonal) beyond the end of wave three, held `hold_bars` bars | the smaller structure's CHoCH (default), the 2-4 line's break (WAVE-7), its retest | the extreme of the fifth wave; the last smaller swing | done (no RSI filter yet, WAVE-29) |
| G10 Ending diagonal: the break of its line | the fifth wave of a DIAGONAL beyond the end of wave three, held `hold_bars` bars | the diagonal's 2-4 line break (default), its retest | the extreme of the fifth wave; the last smaller swing | done |
| G11 Double three: wave Y after wave X | wave Y of a DOUBLE before it passed the end of wave W (X is the main count only once finished, WB8); Fibonacci zone on X's depth of W (off) | CHoCH (default), the channel of wave X's break, its retest | the end of wave X; the last smaller swing | done |

Common: every number a setting with bounds (trend relation per WAVE-2: with for G2, counter-trend allowed for G4-G7 / G11,
any for G9 / G10); the only number nodes in any expression are `Lag(x, 1)` (a new smaller swing since the bar before; the
ATR of the bars before) and the zero of `Count(...) > 0` (a test). The hold-window templates (G5, G9, G10) never signal on
an unclear count (`WaveState != "UNCLEAR"` in each trigger, as G8). One sentence per template in structure words (twenty
new tokens), e.g. G7: "Wave B of a flat on the 1-hour chart went beyond the start of wave A and came back; a downward wave
C may be starting; the count is void beyond the extreme of wave B." Shared files touched: `strategy_templates/language.py`
(the tokens) only.

Tests: `tests/test_wave5_templates.py` - must-fire per trigger style on pinned synthetic wave paths (an impulse, a
contracting diagonal, a zigzag, a flat with B beyond A's start, a contracting triangle, a double three) with the count's own
invalidation and S6; must-not on another count type (G5 without a triangle, G10 on an impulse, G2 on a diagonal, G11 on a
zigzag, G7 without the sweep, G4 / G6 on a double three, G9 without a fifth wave), a disagreeing alternate, the higher
trend, the counter-trend C leg, the Fibonacci zone, the window; mirror per case; truncation over many t on the fixtures and
on random-walk bars; property tests on random valid settings; parity (scan = backtest = options backtest = worker) on the
fixtures and with the 1-hour wave / Daily higher frames; exits never on the signal bar; the words; the flags; the id length.

Owner questions WAVE-38..WAVE-45 in `docs/OPEN_QUESTIONS.md`.

## The companions: the volume, zone-edge and divergence confirmations of G1-G11 (version 2)

No migration, no new flag. Branch `claude/waves-companions`. The same gates as before: a G template is listed and
adoptable (a PAPER pre-fill, nothing starts by itself) only while `strategy_templates` and `screener_waves` are on; a
strategy id runs only with the flags of the functions it reads (the divergence choice reads `RsiDivergence`, which keeps
its own flag `screenql_amw_functions`, as E2 does). G12 is not built (it needs the pack composition, TPL-F7).

| Item | What | Where | Status |
|---|---|---|---|
| Versioning | Every G template is now version 2; version 1 is kept word for word in `templates/archive/G<n>.v1.json` (SHA-256 pinned against main) and a strategy id adopted on version 1 resolves to exactly it (the #402 pattern). The cards and "Adopt" offer version 2 only | `templates/G1.json`..`G11.json`, `templates/archive/` | done |
| Parity | With the companions off (their defaults), version 2 compiles to the same parts expression for expression as version 1, for every variant of every version 1 switch, with the same flags, lookback and watch; the signals, invalidation, exits, reasons, warm-up and instances are identical on every pinned fixture and on random-walk bars; only the params hash differs (the version is in the provenance) | `tests/test_wave6_companions.py` | done |
| Schema | `confirmations`: optional confirmations of the trigger, each a switch on its own enum setting whose default choice is null ("off by default", refused otherwise; one setting per confirmation; never nested; needs a trigger). A chosen one is AND-ed to the chosen trigger at compile time (`(trigger) AND (confirmation)`); with all off the trigger is its own expression. `validate_template` compiles every choice | `strategy_templates/schema.py` (`Confirmation`), `compile.py` | done |
| Strategy id | A template with confirmations leaves out the trailing default values (`template:G1:2:15m:both:` for the defaults); a missing value at the end reads as the default; canonical (a written trailing default is refused). Every template without confirmations, and every version 1 id, is written exactly as before | `strategy_templates/ids.py` | done |
| Divergence (WAVE-14, WAVE-29, WAVE-42) | `RsiDivergence(..., kind=)` and `RsiDivergenceRef(..., kind=)`: `regular` (left out: byte-identical to before) or `hidden` (bullish: a higher low with a lower RSI; bearish: a lower high with a higher RSI), checked against an independent reading of the swings. Settings `divergence` (off / regular / hidden), `div_degree` (the swings compared: 0 for the swings inside a wave; G9 / G10 default 1, the fifth wave against the third), `div_bars` (15), `rsi_period` (14); the divergence of the signal's side on the wave chart must hold on the trigger bar | registry, `runtime.py`, every G template | done |
| Volume (WAVE-15) | `WaveVolumeLower(degree, wave, base_wave)`: the mean volume of `wave` (finished, or the running one up to the bar before - a trigger bar's own volume never counts in the wave it may end) below the finished base wave's; a comparison, no number; false where either wave is not in the reading or a bar has no volume. Settings `volume` (off / the pullback wave lower / the trigger bar higher / both) and `vol_mult` (1.0: `RelVolume() >= vol_mult`, the breakout screens' average length). Per template: G1 two vs one, G2 four vs three, G3 / G4 / G6 / G7 B vs A, G11 X vs W; the hold-window templates read it while the count showed the wave (`Count(hold_bars, ...) > 0`): G5 E vs A, G8 the correction's last wave vs A, G9 / G10 the fifth wave vs the third | `waves/levels.py volume_lower`, registry, `runtime.py`, every G template | done |
| Zone edge (WAVE-27, WAVE-33) | `SwingZoneEdge(side, degree, width)`: the far side of the band the area reads (`SwingZoneDistance` within +/- width): the zone mid less / plus width median ranges (the same median range). New invalidation choices on the templates that read an area (G1, G2, G3, G8): `area` (the far side of the area) and `further` (whichever is further: the count's limit / the last wave's extreme, or the area's far side). Swing zones only (market zones are still not an area source, WAVE-3) | registry (flag `screener_waves`), `runtime._swing_zone`, `engine.ZONE_EDGE_SPARSE_SERIES`, G1 / G2 / G3 / G8 | done |
| No look-ahead | Causal cases for `WaveVolumeLower`, `SwingZoneEdge` and the hidden divergence (the Z10-a harness); truncation of every companion on random-walk bars | `app/quality/causal_cases.py`, tests | done |

Tests: `tests/test_wave6_companions.py` - version 1 kept (digests), version 2 = version 1 with the companions off (parts,
flags, lookback; signals on every fixture of the earlier G tests and on random walks), a version 1 id's behaviour, the id
within 80 characters (trailing defaults left out, canonical), the schema rules, the AND of a chosen confirmation, no
number literal in any companion expression; the divergence kinds (regular byte-identical, hidden against an oracle);
WaveVolumeLower (lower / not lower / equal, the running wave without this bar, no volume, truncation); SwingZoneEdge
against SwingZoneDistance; must-fire / must-not per companion on the pinned wave paths (volume: falling / equal volume,
the trigger bar's volume, both; divergence: regular and hidden cases that fire and the other kind that does not, fixtures
without a divergence; zone edge: `area` / `further` levels and S6), mirror, truncation and parity (scan = backtest =
options backtest = worker) with the companions on; the flags. Updated: `tests/test_wave4_templates.py`,
`tests/test_wave5_templates.py` (version 2, the adopt id prefix), `tests/test_tpl7_e2_packs_v2.py` (the archive also
holds the G versions), `tests/test_wave2_recipe.py` (the wave functions), `tests/test_s1b_screen_runtime.py` (runtime
fixtures).

Not built here (owner questions WAVE-46..WAVE-52): market zones as the area and their edge (still not an area source),
the companions in the signal text (the text switches on yes / no settings only), the volume filter refused with a message
on a symbol without volume (it never confirms instead), G12 (TPL-F7), the projection target / exit (section 5), the UI.

