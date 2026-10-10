# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| B-1 | Switch the Postgres image to a TimescaleDB build before the Hostinger PAPER go-live, or after? | After: the go-live runs on plain Postgres 17; the Timescale image and extension come with part B's first schema PR, as a reversible migration on a scheduled evening (PAPER first). Go-live must not wait on the lake. | ADR-0013, part B PR 2 | open |
| B-2 | Which market-data vendor (TrueData / GlobalDataFeed / Kite history)? | Build the vendor seam with a mocked adapter and broker-history backfill first; no paid vendor until the owner picks one (cost > $5/month). | ADR-0016, part B PR 3-4 | open |
| IB-1 | IBKR Web API vendor registration: which legal entity registers with IBKR as a third-party vendor, who is the compliance contact, and when? | Build only the `WebApiTransport` interface and a stub that refuses with the reason; tenants see "IBKR is operator-only for now". Checklist in `docs/BROKERS_IBKR.md` §5. Owner item (entity/compliance). | ADR-0018, PR I7 | open |
| IB-2 | Which IB Gateway Docker image: a community image or our own build? | Review one image, pin it by digest, run it under compose profile `ibkr` only (off by default), paper mode. An own build only if the review finds a problem. | ADR-0018, PR I3 | open |
| IB-3 | US market-data subscriptions for the paper account (monthly fees). | None until the owner decides (spend > $5); the smoke test and paper runs use delayed data and say so. | BROKERS_IBKR.md §1, PR I3 | open |
| IB-4 | India-resident rules for a foreign broker: cash equity/ETF allowed with an LRS note; foreign derivatives and margin blocked. Correct, and which official source should the rule file cite? | Ship exactly that rule-set as data with its source field marked "to be confirmed by the owner / a CA"; blocks name the rule; exits are never blocked. | ADR-0018 §8, PR I5 | open |
| IB-5 | IBKR commission plan for cost models: fixed or tiered, per account? | Per account, chosen when the account is added; the backtest default is the plan of the operator's paper account. Fee values live in data files with source and checked-on date. | ADR-0018 §7, PR I4 | open |
| CR-1 | Who refreshes the crypto exchange registry's `fiu_registered` flags (source: FIU-IND list), and how often? | A data file with source + checked-on date per row; a warning when a row is older than 90 days; changes go through a reviewed PR. | ADR-0017 §8, PROVIDERS.md | open |
