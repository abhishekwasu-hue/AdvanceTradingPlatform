# Market-zones parity fixtures (Z7a-1)

Source: the Trade repository at commit **eafc872** (read-only). All bars are **synthetic and seeded** - copied from the
generators in Trade's own tests and frozen as CSV (rounded to 2 decimals). No real market data is used, and in
particular nothing from the sealed NIFTY holdout (Trade `data/nifty50_*.parquet`).

| File | Generator (Trade@eafc872) | Shape |
|---|---|---|
| `bn_daily.csv` | `tests/test_banknifty_independent_test.py::_csv` (BANKNIFTY-style daily random walk, seed 3), first 400 days | daily |
| `market_5m.csv` | `tests/test_sr_levels_v3.py::_market(days=10, seed=3)` (session-shaped 5m sine market) | 750 x 5m, 75 a day |
| `walk_1m.csv` | `tests/test_sr_dynamic.py::_walk(seed=1)` + open = previous close, 1-minute stamps | 900 x 1m |
| `session_15m.csv` | `tests/test_major_levels.py::_nse` session layout (25 x 15m from 09:15) on a BANKNIFTY-like price with tight bases before impulsive candles and overnight gaps planted (seed 7) | 750 x 15m, 30 days |

The 15m / 30m / 1h frames used by the tests are resampled from these (label / closed left, as Trade's tests do), and
Trade's `tests/test_market_zones.py::_fake_ohlc` series is generated in code (`tests/zones_golden.py::fake_ohlc`).

`golden_trade_eafc872.json` is Trade's output on these fixtures (`tests/zones_golden.py::trade_outputs`, normalised to
ATP timeframe labels / ASCII tags / zone states). `tests/test_zones_parity.py` checks ATP against it on every run and,
when the Trade checkout is on disk, checks the file against Trade run live.

Rebuild (from `backend/`): `python tests/fixtures/zones/build_fixtures.py` (CSV) and
`TRADE_REPO_PATH=/path/to/trade python -m tests.zones_golden --write` (golden JSON - only on a deliberate Trade re-sync).

`golden_level_memory_eafc872.json` (Z7a-2) is Trade's `level_memory.py` on the same fixtures
(`tests/zones_memory_golden.py::trade_outputs`: tolerance, last touch, night-by-night merge walks, remember / nightly
apply); `tests/test_zones_memory_parity.py` checks ATP's `memory.py` against it. Rebuild only on a deliberate Trade
re-sync: `TRADE_REPO_PATH=/path/to/trade python -m tests.zones_memory_golden --write`.
