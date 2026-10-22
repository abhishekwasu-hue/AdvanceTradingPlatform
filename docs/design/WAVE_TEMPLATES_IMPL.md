# Wave / correction templates (family G): implementation status

Owner order: `docs/owner-orders/ATP_SESSION_WAVE_TEMPLATES.md`. Plan of record: the independent review
`docs/design/WAVE_TEMPLATES_REVIEW.md` (branch `claude/wave1-review`, PR #361; where it differs from the order, the review
wins). This file records what is built per build step; when #361 is merged, its section 7 points here.

Binding: generic method only (the classic wave principle and structure; nothing from any one trader's private method),
signals only, PAPER only, structure words in the signal text, no fixed numbers in reading rules (the only numbers - the
B/A ratios - are settings with today's values).

## Build steps 2 + 3 (this PR): the shared pieces - recipe extensions and the W1 causal wave counter

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

Not here (the review's later build steps): W2 / W3 companions (WaveOrigin, WaveEnd, WaveInvalidation per type, WaveCap,
WaveRetrace, WaveProjection, WaveVolumeLower, the zone-edge companion, RsiDivergence `kind`); W4 anchored wave lines and
R7 RetestEntry; the G parity harness; the templates G1-G11 and G12; the UI (`chart_waves_layer`). The wave ratio settings
reach the ScreenQL functions with the G templates (their template parameters; the params hash already covers them).

Owner questions from this step: WAVE-17..WAVE-20 in `docs/OPEN_QUESTIONS.md`.
