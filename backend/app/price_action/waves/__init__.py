"""Wave / correction reading (family G of the strategy templates; docs/design/WAVE_TEMPLATES_REVIEW.md W1).

Generic wave principle only - the classic rules and structure, nothing from any one trader's private method (review
finding WB2: the modules here import the generic rule functions, the reductions / `read_counter` / `leg_points` and the
causal swings; never the Daily trend fold; tests/test_wave2_counter.py enforces the list).

* `settings` - the B/A ratios that decide the corrective type, as settings with today's values (WAVE-6)
* `counter`  - the count as known at one bar from the confirmed swings: main reading, alternate, clarity (WB1, WAVE-1)
* `series`   - the count on every bar, recomputed only on a newly confirmed swing (R3), never rewriting a past bar (S8)

Signals only: nothing here places, sizes or exits an order. Wave labels are shown in words (`strategy_templates.language`).
"""
from app.price_action.waves.counter import CLARITY, STATES, TYPES, Reading, Swing, WaveCount, count, readings
from app.price_action.waves.series import WAVE_DEGREES, WaveSeries, cached_wave_series, wave_series
from app.price_action.waves.settings import WAVE_DEFAULTS

__all__ = ["CLARITY", "STATES", "TYPES", "Reading", "Swing", "WaveCount", "count", "readings", "WAVE_DEGREES", "WaveSeries",
           "cached_wave_series", "wave_series", "WAVE_DEFAULTS"]
