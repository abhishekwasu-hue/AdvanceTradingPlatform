# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| D-1 | OPS limit when a broker publishes none? | 10 orders/s per client per exchange/segment (the spec's default), from `in_sebi.json` `IN-SEBI.ops.throttle.ops_per_second`; a broker-specific value overrides it once known. | part D PR 2 (throttle) | open |
| D-2 | Where does the "generic algo id below the OPS threshold" come from? | Broker-level data in the rule-set: `IN-SEBI.algo_id.registered_above_ops.generic_ids` = {broker: id}, empty until each broker publishes its id (filled by a reviewed rule-set change, no code). Used only while the D2 OPS throttle is on and capped at or below `ops_threshold`; the tenant's registered id always wins. | part D1 | provisional (implemented in D1; owner to confirm per broker) |
| D-3 | How often may the registered static IP change? | Once a week (`max_changes_per_week: 1`), the strictest broker policy seen; per-broker override in config. Verify against each broker's current page before enforcing (flag off until then). | part D PR 4 | open |
| D-4 | Where is a strategy's white-box / black-box class kept? | As platform go-live evidence (`strategy.classification`: the broker filing that lists every LIVE strategy's class), not a column on the strategy version - no broker asks for it per order today. Add a per-version field only if a broker's API starts requiring it. | part D6 | provisional |
