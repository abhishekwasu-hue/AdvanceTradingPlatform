"""Market zones - buyer / seller (demand / supply) zones, order blocks, S/R levels and unfilled gaps.

Ported from the Trade repository @eafc872 (market_zones.py, signals.py, sr_dynamic.py, sr_levels_v3.py,
mtf_pullback_strategy.find_overnight_gaps) - spec docs/specs/ATP_MARKET_ZONES_PORT.md, step Z7a-1 (detection only).
Golden parity with Trade: tests/test_zones_parity.py.

  settings.py   ZoneSettings (per-tenant knobs, Trade values as defaults) + resolve_settings
  detect.py     order blocks, demand / supply bases, overnight gaps, role by price, first-touch event, OB retest
  levels.py     classical S/R (clustered swings), major swings, dynamic S/R (Pine Dynamic v2)
  sr_v3.py      scored multi-timeframe S/R levels
  compute.py    all zones as of a time, intraday dynamic S/R, the 5m / 15m confluence table
  lifecycle.py  zone states fresh -> tested -> flipped -> invalidated and the transition function

Pure functions only - storage, the refresh job and the chart layer are later steps (Z7a-2, Z7a-3).
"""
