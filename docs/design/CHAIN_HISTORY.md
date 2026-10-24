# Chain history (Terminal master, Part 9.4) and the OI collector's data terms (TM-7)

Status: built (branch `claude/terminal-chain-history`). Order: `docs/owner-orders/ATP_SESSION_TERMINAL_MASTER.md` Part 9.4
("record option-chain snapshots ... START NOW"). Review: `docs/design/TERMINAL_MASTER_REVIEW.md` section 4. Owner
decisions 23 Oct: TM-7 (chain history only through the owner's own broker session into his tenant-private store; no
cross-user sharing, RDY-6 data terms), TM-8 (no deletion until the owner sets a limit).

## 1. What it does

Every `CHAIN_HISTORY_SLOT_MINUTES` (default 1) while the venue is open, the trading worker records the full option chain
of NIFTY, BANKNIFTY and SENSEX (and, behind `CHAIN_HISTORY_STOCKS_ENABLED`, the F&O stocks in `CHAIN_HISTORY_STOCKS`),
the nearest `CHAIN_HISTORY_EXPIRIES` (default 2) expiries each. Per strike and side: LTP, bid, ask, volume, OI, OI change,
IV and the broker's Greeks (delta, gamma, theta, vega); per chain: spot, the slot and the read time. This builds ATP's own
IV / OI history for Part 9.3, Part 10 (replay) and backtests - no paid vendor needed.

Nothing is computed or guessed: a value the broker did not send is stored as missing. Greeks are the broker's own; a
reader that needs Greeks the broker did not send computes them on read and labels them as computed.

## 2. Whose session, whose store (TM-7)

- **The owner** is HB2's owner, one rule for every owner-only data path: `HISTORY_OWNER_TENANT_ID`, confirmed by
  `HISTORY_OWNER_TENANT_NAME` (a mistyped id never reaches a customer's tenant). Why not "the SUPER_ADMIN's
  organisation": a platform admin is a role, not a data holder - an admin account may sit in an operations
  organisation without a broker, and there may be more than one. The confirmed tenant id is explicit, already set on
  the host for HB0 / HB2, and cannot change by a role edit.
- **The session** is the one `HISTORY_OWNER_USER_ID` (else the lowest-id active OWNER of that tenant) linked themselves
  at a broker whose data terms are confirmed (Tier A: Upstox), with a usable token now
  (`intraday_history.owner_session`; the Upstox Analytics Token keeps it usable without the daily login). A
  teammate's, a co-owner's or another organisation's session is never tried: no owner session = a gap, shown on the
  admin card.
- **The store** is `md_tenant_chain_snapshots`, a separate table no shared read path touches. Every read
  (`app/market_lake/chain_store.py`) takes the reader's tenant id and refuses None. Within the tenant the rows carry
  their holder (`source = broker:<name>:u<id>`) and only that member reads them (BT1b, `tenant_store.holder_visible`).

## 3. Schedule, calendar, rate budget

- `chain_recorder.due` gives one slot per period; the worker starts one slot as a background task
  (`TradingWorker._maybe_start_chain_history`), never two at once (a slot still running makes the next one a gap).
- The venue's open state comes from the exchange calendar (`market_session_status`, holidays included); SENSEX on the
  BSE family. A closed venue reads nothing.
- Each read takes one token from the trading worker's own `RateBudget` of that session through `acquire_spare`:
  `CHAIN_HISTORY_RESERVE` (0.5) of both buckets always stays free for exits, and the budget's lock is never held while
  waiting. No token within `CHAIN_HISTORY_TOKEN_WAIT_SECONDS` = the slot's remaining reads are skipped (a gap), never
  queued. A 429 or a refused login ends the slot. At most `CHAIN_HISTORY_MAX_READS_PER_SLOT` (12) reads per slot, the
  indices first. Default load: 3 x 2 = 6 chain calls a minute, 180 per 30 minutes (Upstox allows 2,000).
- Read-only: only `get_option_chain` is called; no order path is reachable. Flag `chain_history`, off by default.

## 4. Storage and size

One row per (tenant, underlying, expiry, slot), unique. The strikes are one zlib-compressed JSON document
(`codec = zlib-json-v1`): `{"v":1,"fields":[...],"rows":[[strike, 11 call fields, 11 put fields], ...]}`. A 61-strike
test chain compresses to under a third of its JSON. Estimate: about 200 strikes x 2 expiries x 3 underlyings x 375
slots a day = 2,250 chains a day, roughly 20-30 KB each compressed, so tens of MB a day; measure on the first day from
the admin card ("Today" shows the stored bytes) and report.

Retention: `RETENTION_CHAIN_HISTORY_DAYS`, 0 = kept (TM-8, the default). A set value is floored at 7 days. A nightly
Parquet roll-up to the off-site store comes later, once R2 exists (ADR-0013).

## 5. Read API (tenant-scoped) and the admin card

- `GET /api/chain-history/coverage` - days with chains per underlying (counts, first / last slot).
- `GET /api/chain-history/day?underlying=&day=&expiry=` - one IST day, oldest first.
- `GET /api/chain-history/range?underlying=&start=&end=&expiry=` - at most `CHAIN_HISTORY_READ_MAX_DAYS` days and
  `CHAIN_HISTORY_READ_MAX_CHAINS` chains (counted first; 422 above).
  Behind the caller's `chain_history` flag (404 while off). Another organisation gets an empty answer.
- `GET /api/admin/chain-history/status` (SUPER_ADMIN + two-factor) feeds the **Chain history** card in Admin > System:
  state (Recording / Off / Not set up / Waiting for the owner's login), interval, today's chains and bytes, last
  snapshot, stored total, and per underlying the slots recorded vs expected and today's gaps. Counts and times only.

## 6. The OI collector fix (TM-7, same rule)

Before: `_oi_banner` read one chain per underlying per 5-minute slot through the FIRST organisation (by id) that had
enabled the banner and had a usable broker session, and stored it as platform-wide rows that every organisation's
banner, OI charts, analyses, Ask AI history, alerts and OI gates read.

After: every organisation that enabled an underlying is read through ITS OWN first usable broker session and its slot is
stored under its tenant (`oi_snapshots.tenant_id`, `oi_day_baselines.tenant_id`; uniqueness per tenant). Every reader
(`snapshots.day_chains`, `latest_capture`, `recent_session_closes`, `baselines`, the OI gates) takes the reader's tenant.

Who sees what:
- An organisation with its own usable broker session: unchanged - its banner, charts, alerts and gates work as before,
  now from its own reads (one chain call per enabled underlying per 5-minute slot on its own rate budget).
- An organisation without one: no stored slots any more (the banner says history is building, alerts can report the
  collector as stale, an OI gate on a deployment blocks NEW entries until data exists - exits are never blocked).
  Linking its own broker brings it back from the next slot.
- Rows stored before the fix (no tenant) are read only by the confirmed owner (`HISTORY_OWNER_TENANT_ID` +
  `HISTORY_OWNER_TENANT_NAME`); with no confirmed owner nobody reads them. Retention deletes them after 400 days as before.
- `byob_data_tier` on: unchanged (the banner stays off for that organisation).

## 7. Migration

`250d796123b7` (after `e8a0c2d4f6b9`): the new table, the two nullable tenant columns with their keys, and the per-tenant
unique constraints. Rollback-safe (the previous release writes NULL tenants and keeps its own check-before-insert).
Deploy after 15:30 IST.

## 8. Tests

`backend/tests/test_tm94_chain_history.py` (no network; fake chains and adapters): compression round-trip, idempotent
slot, tenant and holder isolation, the recorder never uses another user's session, the interval scheduler and worker
hook, closed venue, the rate budget (spare tokens only; no token = gap; 429), retention, the read API, the admin card,
the OI collector fix and the pre-fix rows. The OI tests (`test_o2/o4/o5`, `test_oc2/oc3/oc5`) now store per tenant.
