"""Market zones - buyer / seller (demand / supply) zones, order blocks, S/R levels and unfilled gaps.

Ported from the Trade repository @eafc872 (market_zones.py, signals.py, sr_dynamic.py, sr_levels_v3.py, level_memory.py,
mtf_pullback_strategy.find_overnight_gaps) - spec docs/specs/ATP_MARKET_ZONES_PORT.md: Z7a-1 detection, Z7a-2 lifecycle,
level memory and the storage job. Golden parity with Trade: tests/test_zones_parity.py, tests/test_zones_memory_parity.py.

Pure functions (no database, no clock):
  settings.py   ZoneSettings (per-tenant knobs, Trade values as defaults) + resolve_settings
  detect.py     order blocks, demand / supply bases, overnight gaps, role by price, first-touch event, OB retest
  levels.py     classical S/R (clustered swings), major swings, dynamic S/R (Pine Dynamic v2)
  sr_v3.py      scored multi-timeframe S/R levels
  compute.py    all zones as of a time (with their lifecycle), intraday dynamic S/R, the 5m / 15m confluence table
  lifecycle.py  zone states fresh -> tested -> flipped -> invalidated, the transition table and the bar-by-bar tracker
  memory.py     level memory across days (Trade level_memory: tolerance, merge, retire, cap, role by price)
  snapshot.py   one stored snapshot: detection + lifecycle + memory against the previous snapshot

Storage and the job (Z7a-2, flag `market_zones`, off by default):
  store.py      tenant settings (market_zone_settings) and as-of snapshots (market_zone_runs / market_zones)
  job.py        the nightly worker job: shared lake bars closed by the run time -> snapshot -> stored

The chart layer and ScreenQL functions are Z7a-3.
"""
