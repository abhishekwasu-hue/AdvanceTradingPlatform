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

Rebuild the CSVs (from `backend/`): `python tests/fixtures/zones/build_fixtures.py`; it reproduces them byte for byte.

Trade's outputs on these fixtures are not in this folder: they are in the private fixtures store (owner decision PR-2,
`docs/PUBLIC_REPO_POLICY.md` section 5), read through `tests/private_fixtures.py` with `ATP_PRIVATE_FIXTURES` set:

- `golden_trade_eafc872.json.gz` - Trade's market zones on these fixtures (`tests/zones_golden.py::trade_outputs`,
  normalised to ATP timeframe labels / ASCII tags / zone states), checked by `tests/test_zones_parity.py`;
- `golden_level_memory_eafc872.json.gz` (Z7a-2) - Trade's `level_memory.py` on the same fixtures
  (`tests/zones_memory_golden.py::trade_outputs`: tolerance, last touch, night-by-night merge walks, remember / nightly
  apply), checked by `tests/test_zones_memory_parity.py`;
- `../price_action/golden_trade_1e6c9ac_ch3b.json.gz` - Trade's price-action engines on the same fixtures
  (`tests/ch3b_golden.py`), checked by `tests/test_ch3b_trade_parity.py`.

Without the store the public CI skips those checks with the reason; with the Trade checkout on disk the tests also
check each file against Trade run live. Fill a store with `python3 scripts/export_private_fixtures.py --to DIR` (from
the repository root). Rebuild a golden only on a deliberate Trade re-sync, into the store:
`ATP_PRIVATE_FIXTURES=<store> TRADE_REPO_PATH=/path/to/trade python -m tests.zones_golden --write` (or
`tests.zones_memory_golden`, `tests.ch3b_golden`), then put the new sha256 into `tests/private_fixtures_manifest.json`
in a reviewed change. They are gzip-compressed JSON, written deterministically by `tests/zones_golden.py::write_golden`
(sorted keys, one layout, gzip header without a name or time), so the same Trade commit gives byte-identical files.
