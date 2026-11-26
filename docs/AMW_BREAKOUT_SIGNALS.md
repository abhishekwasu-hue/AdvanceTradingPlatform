# AMW Breakout informational signals

The research worker can run an independent closed-bar scan for NSE equities and indices using ATP's stored candle sources. It consumes raw confirmed B1/B2, structure, RSI divergence and SD facts without changing existing ScreenQL, pullback, risk or order algorithms. A saved setup uses the authenticated `amw_breakout` namespace under the existing `/api/me/prefs` API. The core settings are editable in Settings › Advanced › Breakout signals. The preferences API also accepts advanced rule fields. Up to ten canonical instrument keys and one rule per BO profile are accepted. The server validates the profile, direction, thresholds and timeframe combinations. Both the user preference and the research worker environment switch `AMW_BREAKOUT_SCAN_ENABLED=1` would need separate authorized activation. Both default off.

## Strategy

The previous completed ATR scales the strict buffered close. The same signed calculation serves LONG and SHORT. DIRECT_BREAKOUT can qualify on the trigger close. FOLLOW_THROUGH counts directional closes beyond the frozen level. RETEST_BREAKOUT consumes existing B9 leave/touch evidence and a directional close. AUTO_SELECT is rejected by config. A pending setup expires after five subsequent completed bars by default. A strict reclaim invalidates, while equality does not qualify. The default extension cap is 1.50 ATR.

Raw active opposing zones across selected scopes block inclusive containment or clearance up to 0.5 ATR. Over 0.5 through 2 ATR waits. Clear data with no opposing band passes with its own reason. Invalid or unavailable data cannot satisfy the gate. Each waiting close rechecks extension, opposing zones, divergence and the profile rules.

BO-01 uses direct breakout with divergence disabled. BO-02 requires compatible active divergence and blocks opposite evidence by default. BO-03 waits for a retest and uses divergence for scoring. BO-04 requires the directional candle and blocks opposite divergence. The public strict regular/hidden pairing is preserved. Valid age starts at confirmation. Later closed price beyond the second extreme invalidates. New nondivergent pivots leave existing observations active until their own expiry or breach.

The evaluator uses fixed 100-point profile rubrics. Missing optional facts receive zero. A1 starts at 85, Strong at 70, Watchlist at 55. Directional history requires at least Strong and every mandatory gate. Level/structure/clearance predicates and momentum/volume contributions are candidate rules for research, without statistical calibration.

## Delivery and isolation

The research worker invokes a store-only informational job. The evaluator and replay perform no writes. The publisher stores a history row and owner-scoped dedupe atomically, leaving execution fields unset. The inbox identifies the variant and its quality label. Runtime evidence remains scoped and bounded. No executable strategy ID, approval, intent, or order adapter attaches to these rows. The integration tests check direction, dedupe, ownership and the absence of order-side records. No schema migration is needed.

## Configuration example

The defaults below illustrate a saved, disabled setup. Preserve the account's other existing preferences by writing only this namespace.

```json
{
  "enabled": false,
  "instruments": ["NSE_INDEX:NIFTY50"],
  "rules": [
    {"profile": "BO-01", "direction": "BOTH"},
    {"profile": "BO-02", "direction": "BOTH"}
  ]
}
```

See `Settings` in `backend/app/screener/breakouts/amw_breakout.py` for threshold and timeframe roles. Trigger, structure, RSI, demand, supply and optional context timeframes are independently selectable. Confirmation uses the trigger clock. Historical scans supplied through `analyze` must provide timezone-aware actual close timestamps, preserve source provenance and validate historical availability. The live scheduler checks ATP calendar gaps, and stores no broker credentials. Daily/shortened-session, source corrections and historical data quality require more runtime coverage before activation.

## Verify and roll back

Run the breakout test modules from `backend` using the repository dependencies and an isolated database. Review source coverage and scan latency before authorized activation. To disable future scans, turn the account setting off and leave the worker environment switch unset. Roll back the development changes together if needed. Existing informational journal rows can remain as historical evidence. Future PAPER or LIVE integration requires separate approval and a separate execution design.

## Phase 2 changes and research

The Advanced settings panel adds per-profile presets, an explicit cancel/reset flow and the last observed worker status. A worker heartbeat is reported separately from account opt-in. Each attempted user scan writes a bounded status summary. The Signals Inbox expands stored breakout evidence when present and links to current chart analysis. AMW history rows have no broker or option-chain order shortcut.

- Horizontal levels use past confirmed pivots and ATP price clustering. Zone candidates require a known active raw zone and a confirmed distal violation. Previous-session levels require a verified complete session and its exchange calendar. These sources are opt-in. B1/B2 remain the default.
- Same-direction close events cluster by distance from the first level in each cluster, within the configured prior-ATR tolerance. Separate levels retain separate events.
- A scan-local memo shares compatible immutable snapshot tuples among profiles in the same instrument batch. It holds at most four entries and is dropped after the batch. New data batches do not reuse prior snapshots. The input algorithms still recompute from the closed prefix.

Use the existing ATP historian or an authorized offline export of its entitled candles. From `backend` run:

```bash
python -m app.screener.breakouts.amw_research /path/to/manifest.json --sensitivity --ablation
```

The manifest requires `instrument`, `provenance` and `frames`, a map such as `{"15m":"bars-15m.csv","1h":"bars-1h.csv"}`. Each CSV supplies `start,bar_end,open,high,low,close` and optional volume. Timestamps must carry offsets. The CLI uses the production evaluator in read-only replay, separates LONG/SHORT and measures outcomes over 1/3/5/10/20 subsequent candles. Failure is a strict close back through the broken reference. Continuation is positive directional return at the horizon. Candle-end replay is distinct from a historical reconstruction of exactly what each feed had delivered at that moment. ATP owns revision/ingestion metadata in private candle storage, but the CSV runner does not reconstruct previous revisions from a latest-version export. Previous-session research also requires verified calendar context and is not enabled through this CSV path. Ablation and score-floor overrides stay in offline replay; operational publication retains its 70-point floor.

## Release preparation

Use the repository's [Hostinger runbook](deploy/HOSTINGER_RUNBOOK.md) after review and separate release authorization. Run the isolated CI-equivalent suite, confirm authorized stored candle coverage and scan cost, and review the complete branch diff before any push/merge or rollout. Build backend and frontend using the existing compose release. No migration is introduced. Keep `AMW_BREAKOUT_SCAN_ENABLED=0` during code rollout. A separately authorized operator can later opt in to the research-worker profile and enable the switch, then opt in only the review account.

Use a small opt-in watchlist over 5–10 trading sessions in that separate observation window. Monitor heartbeat, data gaps, both directions, grade/profile counts, scan latency, rejection reasons and duplicate suppression. Validate no order action appears on AMW rows. Pause by disabling account opt-in or setting the worker switch back to `0` and following the approved release runbook to apply configuration. For rollback use the existing `/opt/atp/deploy/atp.sh rollback` procedure only after authorization. Keep published informational rows as history.
