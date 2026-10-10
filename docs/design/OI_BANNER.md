# OI Banner and option-chain OI regime (design note)

Spec: `docs/specs/ATP_OI_BANNER_SPEC.md` (from Abhi). The logic is ported from the Trade repo: `oi_analysis.py`, the
banner block in `page_dashboard.py`, and `oi_snapshot_collector.py`. Strings are English only, and there are no
symbol, strike-step or date constants.

## Layers

| Item | Where | What |
|------|-------|------|
| O1 | `backend/app/option_chain/oi_regime.py` | Pure functions plus `OIRegimeSettings`. No DB, broker or clock. |
| O2 | `option_chain/snapshots.py` + migrations | 5-minute collector per enabled underlying: `oi_snapshots`, `strike_oi_snapshots`, day-baseline OI, stale detection, history API |
| O3 | banner API + frontend | `GET /api/option-chain/{underlying}/banner`, `/history`, `/strikes`, `/settings`; Banner component; history table (5/10/15); per-strike OI chart |
| O4 | alerts | Banner alert types on the existing `alerts/dispatcher`. They fire on a state change only, with cooldowns and dedupe keys. |
| O5 | strategy engine | Opt-in gates (`oi_diff_entry_gate`, `pcr_gate`, `iv_change_gate`, `swing_oi_gate`, `oi_wall_confirmation`). They fail closed on stale data and never touch exits (ADR-0004). |

## O1: the functions

All thresholds are parameters, and their defaults live in `OIRegimeSettings`. `resolve_settings(platform, tenant,
underlying)` layers the overrides and refuses unknown keys. Ported unchanged:
- `classify_oi_price_action` (the OI × premium matrix);
- `generate_oi_price_signal`;
- `is_genuine_rotation`, `rotation_confirmed_for_2_snapshots`;
- `compute_oi_signal_with_hysteresis`;
- `reconcile_with_diff_level`;
- `compute_pcr_signal`, `compute_pcr_zone_label` (five bands; the edges are settings);
- `compute_oi_price_matrix`, `compute_rollover_proxy`;
- `swing_oi_gate`, `check_oi_diff_entry_gate`;
- `check_oi_wall_confirmation` (the caller passes the day-baseline OI);
- `find_psychological_level`;
- `check_pcr_gate`, `check_iv_change_gate` (with `iv_change_from_average` and the marubozu sideways test);
- `compute_dte`, `aggregate_history`.

The pure part of `fetch_and_save_oi_snapshot` is `summarise_chain` (the ATM window), `slot_start` and
`evaluate_snapshot`, which builds the banner state.

**Max pain** is ATP's existing `analysis.compute_max_pain`, not a copy. `oi_regime.max_pain` sorts the strikes first,
so a tie resolves to the lowest strike as in the source.

**Strike step:** taken from the `strike_step` setting, or else inferred from the chain's own strike spacing (the
smallest gap). The psychological round number is the `psychological_round_to` setting, or else
`strike_step × psychological_step_multiple` (default 10, which gives 500 for a 50-point step, as in the source).

## Deliberate differences from the source

1. **`check_oi_confirmation` fails closed on missing data.** Trade skipped the gate in that case. The spec's gate rule
   (fail closed) wins.
2. **Strictness "B" is "own direction, Strong".** Trade compared the signal with `"🟢 BULLISH"`, a label format that no
   longer exists, so its strict mode could never pass. The docstring's intent ("full match, not even Weakening") is
   what is implemented here.
3. **A PCR without a timestamp is stale** (`is_stale`). Trade used a value whose time it could not parse.
4. **The ATM comes from `chain.underlying_ltp`.** Trade read `underlying_spot_price` from the middle row of the chain.
5. **Banner messages are English and name the underlying**, e.g. "Put writing rising + Call short covering → avoid
   shorting calls; {underlying} bias bullish". The source's NIFTY-specific Marathi text is not used.
6. **The first snapshot of the day reads "Insufficient data — history builds through the session"**
   (`first_of_day=True`).

Kept as in the source, and flagged for review:
- `swing_oi_gate` counts a SIDEWAYS PCR band as *opposing* either direction (Trade passes `compute_pcr_signal`'s bias
  straight in).
- The ATM rounds with Python's `round` (banker's rounding on an exact half).

## O2: collector, tables, history API

- **Tables** (migration `e2b4d6f8a0c1`, reversible; checked on Postgres 16 with `upgrade` → `check` → `downgrade` → `upgrade`):
  - `oi_snapshots`: one row per (underlying, slot), platform-wide reference data like `option_chain_snapshots`;
  - `strike_oi_snapshots`: call/put OI, premium and IV per strike;
  - `oi_day_baselines`: the first OI seen per strike per day, never updated;
  - `oi_banner_settings`: per tenant and underlying, `*` = the tenant default, with `enabled`, `exchange` and JSON
    `overrides`.
- **No verdict is stored.** The collector keeps `OI_BANNER_COLLECT_SPAN` strikes either side of the money, wider than
  any banner window. Every reading (diff, stable signal, classes, PCR, max pain) is recomputed by replaying the day's
  stored strikes through `evaluate_snapshot` with the reader's settings. Two tenants with different thresholds or ATM
  ranges therefore see their own banner from the same data. Persisted per-tenant `banner_states` arrive with the
  alert rules (O4), which need change detection.
- **Worker** (`TradingWorker._oi_banner`):
  - collects every underlying that some tenant enabled, once per slot (`OI_BANNER_SLOT_MINUTES`), and only while that
    underlying's venue is open;
  - reads through the first of those tenants with a usable broker session;
  - skips a slot that is already stored (unique key plus savepoint);
  - logs a failure and tries the next tenant; it never blocks the cycle and never places an order.
- **API:** `GET /api/option-chain/{u}/banner`, `/history?date=&interval=5|10|15` (newest first, last value per
  bucket), `/strikes?date=` (per-strike series with the day baselines), and `GET`/`PUT /settings` (PUT is owner only;
  overrides are validated before they are stored). Every row carries `slot` and `data_as_of`. The response carries
  `market_open`, `stale` (only while the venue is open; no data counts as stale) and `age_minutes`.
- **Retention:** `RETENTION_OI_SNAPSHOTS_DAYS`, default 400.

## Golden fixtures

`backend/tests/fixtures/oi_regime/golden.json` holds 1,300+ cases produced by running Trade's own functions on
seeded random inputs (`capture_from_trade.py`, run by hand, never in CI). It covers:
- classification;
- rotation;
- 150 hysteresis sequences replayed step by step;
- PCR band edges;
- the OI-price matrix;
- psychological levels;
- the gates and the swing gate;
- rollover;
- max pain against ATP's function.

`tests/test_o1_oi_regime.py` replays them all and adds Trade's named test cases in English. Mutating the hysteresis
rotation condition, a PCR edge, the strength baseline, the max-pain tie order or the swing-gate comparison makes the
suite fail.

## Open questions (provisional defaults in force)

- **OI-1:** should a SIDEWAYS PCR count against both directions in `swing_oi_gate`? Default: yes, as in Trade.
- **OI-2:** default psychological level = 10 × strike step (500 for a 50-point step). Override per underlying.
- **OI-3:** the banner's "avoid shorting calls/puts" wording comes from the spec. Should it go through the SCREENER §6
  compliance filter, which bans buy/sell/target? Default: the spec's wording.
- **OI-5:** the collector reads through the first enabling tenant's broker session (a platform-wide fact, read once).
  Default: yes, like `option_chain_snapshots`. The alternative is a platform vendor feed, a later seam.
- **OI-4:** should the PCR gate limits (`pcr_bullish_min` / `pcr_bearish_max`) and the IV gate limit default to "not
  configured"? Default: yes, so a strategy that opts in (O5) must set them.
