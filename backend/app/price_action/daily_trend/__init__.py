"""N1 L2: the Daily trend the level gate starts from (step 1, "trend first"), ported from the Trade repo with golden
parity (docs/design/N1_EXECUTION_CORE.md §2, §5).

* `fold`     - Trade `decision3/daily.py` (`fold_impulse`, degree-aware Dow, ABC_FIX / Q49 corrections), `state_at`,
               `prepare` (Trade `daily_swings.prepare`) and `prepare_daily` (Trade `level_reaction.prepare_daily`).
               `DState` carries the fields the gate's `context_from_state` reads: trend, band, phase, counter,
               protected (.price), origin_risk, mature.
* `legs`     - Trade `decision3/daily_legs.py` with the `legs2/measure.py` parts it uses: is a Daily leg impulse or corrective.
* `rules`    - Trade `patterns2/rules.py` (impulse, wedge, triangle) and `rules2.py` (zigzag, flat).
* `settings` - the defaults at the pinned Trade revision.

Pure: no I/O, orders, broker or database; no flag and no caller yet (the scan wiring is U4 X1). Golden parity:
tests/test_n1_l2_daily_trend.py, goldens made by Trade's own code (tests/fixtures/daily_trend/make_golden.py), drift
check tests/fixtures/daily_trend/drift_check.py. Trade's free-text reasons (`why`) are English here and not compared.
"""
