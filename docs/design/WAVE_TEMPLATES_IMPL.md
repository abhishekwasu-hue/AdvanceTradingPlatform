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
