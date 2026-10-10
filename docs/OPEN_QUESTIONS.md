# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| B-1 | Switch the Postgres image to a TimescaleDB build before the Hostinger PAPER go-live, or after? | After: the go-live runs on plain Postgres 17; the Timescale image and extension come with part B's first schema PR, as a reversible migration on a scheduled evening (PAPER first). Go-live must not wait on the lake. | ADR-0013, part B PR 2 | open |
| B-2 | Which market-data vendor (TrueData / GlobalDataFeed / Kite history)? | Build the vendor seam with a mocked adapter and broker-history backfill first; no paid vendor until the owner picks one (cost > $5/month). | ADR-0016, part B PR 3-4 | open |
