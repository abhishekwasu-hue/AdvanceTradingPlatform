# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| D-1 | OPS limit when a broker publishes none? | 10 orders/s per client per exchange/segment (the spec's default), from `in_sebi.json` `IN-SEBI.ops.throttle.ops_per_second`; a broker-specific value overrides it once known. | part D PR 2 (throttle) | open |
| D-2 | Where does the "generic algo id below the OPS threshold" come from? | From the broker (each broker issues its own generic id for clients under the threshold) - a per-broker setting next to the tenant's registered `algo_id`; threshold = the same `ops_threshold` param (10). | part D PR 5 | open |
| D-3 | How often may the registered static IP change? | Once a week (`max_changes_per_week: 1`), the strictest broker policy seen; per-broker override in config. Verify against each broker's current page before enforcing (flag off until then). | part D PR 4 | open |
