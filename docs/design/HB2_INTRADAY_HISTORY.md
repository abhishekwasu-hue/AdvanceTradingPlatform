# HB2 - Owner intraday history on the server (design note)

Roadmap: `docs/ROADMAP_STATUS.md` (HB2, first N-item after Phase 0). Background: `docs/design/HB_HISTORY_BACKFILL.md`
(HB0 private store, HB1 daily backfill). Code: `backend/app/market_lake/intraday_history.py`. Flag: **`history_intraday`**
(off by default, in `DEFAULT_OFF_FLAGS`). Migration `f3b5d7a9c1e2` (one new table, `ROLLBACK_SAFE = True`).

## Why
The Trade-repo collector that was to fill HB0's R2 store was cancelled, so HB0's importer stays idle, and HB1 covers
only daily bars. HB2 is now the source of the owner's 1-minute history: a capture of each session after the close plus
a resumable backfill, both read through the owner's own broker session and written into HB0's tenant-private store.

## What is stored, and where
- Table `md_tenant_candles` (HB0), tenant = `HISTORY_OWNER_TENANT_ID`, confirmed by `HISTORY_OWNER_TENANT_NAME` (HB0's
  rule: a mistyped id refuses instead of loading another customer's tenant).
- Same lake format as HB0's 1-minute import: timeframe `1m`, key `EXCHANGE:SYMBOL`, bar END in UTC (the broker labels by
  START; END = START + 1 minute), unadjusted prices (B4 adjusts on read), B1 versions (an identical bar is skipped, a
  changed one becomes the next version), source `broker:<name>` (e.g. `broker:upstox`).
- `ingested_at` = the time the bar was written. Only bars whose END is at or before the fetch time are stored; a forming
  bar is dropped and counted (`dropped_forming`). So an as-of replay never sees a bar before it was really known.
- Read path: `tenant_store.candles` and `GET /api/market-data/history?store=tenant` (the caller's own tenant only),
  3/5/15/30/60-minute bars resampled on read. Nothing is written to the shared lake (`md_candles`).
- Who may read them (BT1b, review M3): the bars came through ONE member's own broker session, so `store=tenant` serves
  `broker:*` rows only to that member (the `user_id` recorded on the tenant's `history_intraday_days`). Every other
  member - a teammate, a VIEWER, another OWNER - gets the private store without them (`tenant_store`'s
  `exclude_prefixes`), and the quality-event count leaves them out too. Open question HB2-11.
- Checks per day (HB0's): labels must be whole minutes inside 09:15-15:30 IST (a START-labelled batch is refused, never
  shifted), two different bars with one END refuse the day, a price that is not a positive number or a volume that is
  not a count refuses the batch. Quality events (B3): duplicates, bar problems and per-session gaps, recorded with the
  owner's `tenant_id` (counted only for that tenant). Gaps are reported, never filled.
- Retention: as for the rest of the lake, rows are kept (neither lake table is in `run_retention`).

## Whose session (BYOK / BT1b)
Only ONE pinned user's own session (review M2): `HISTORY_OWNER_USER_ID` when set (refused unless it is an active user
of the owner tenant), else the lowest-id active OWNER of that tenant. If that user has no usable session the pass does
nothing - no co-owner (a teammate promoted to OWNER), teammate or other tenant is tried. The credential must be one that
user linked themselves in the owner's tenant (`entitlement.own_records`), at a broker whose data terms allow Tier A
(`broker_data_terms.json`) and whose adapter serves both 1-minute endpoints (Upstox for now), with a usable token now
(`pick_session`). A teammate's credential, another tenant's credential and execution-only brokers are never used; with
no such session the pass does nothing (`no_session`) and is tried again later that evening. Only the adapter's candle
endpoints are called (`get_historical_data` for past days, `get_intraday_candles` for today); no order path is touched.

## Rate budget and the trading worker
- In the worker, the pass receives the worker's own `RateBudget` for that session (`TradingWorker._budget_for`, the
  same object `RateLimitedBroker` draws from for exits). Each request takes a token through the new
  `RateBudget.acquire_spare(reserve)`: only while both buckets still hold `HISTORY_INTRADAY_RESERVE` (50%) of their
  capacity on top of it, and without holding the budget's lock while it waits, so an exit's `acquire()` is never
  queued behind a history read. If the time budget would run out while waiting, the pass stops.
- Request volume (review M1): at most `HISTORY_INTRADAY_MAX_REQUESTS` (600) broker requests per pass; a capped pass
  ends `request_cap` and the worker starts the next one 30 minutes later at the earliest. Upstox caps a user at 2,000
  standard API requests per 30 minutes, which the per-second / per-minute buckets do not model, so history takes under
  a third of that window and leaves the rest to the worker and the owner's own apps.
- MCX (review M1): while the owner tenant has a non-stopped MCX deployment and the MCX session is open (to 23:30 IST),
  no pass starts (`mcx_session`) and a running pass stops before its next request.
- After waiting for a spare token, the hours, blackout dates, flag and MCX are checked again before the request goes
  out (review L1); if one says stop, the claimed days go back to `pending`.
- A 429 ends the pass (claimed days go back to `pending`); an auth error (401 / 403 / expired token) also ends it.
  Other errors fail that window and skip the instrument's other windows until the next pass. History reads do not
  report to the trading circuit breaker, so a history error can never block a LIVE entry.
- The CLI (a separate process) cannot share the worker's in-memory budget; it uses its own `RateBudget` at the broker's
  conservative limits and still leaves the reserve unused, and it refuses to run during NSE hours. Run it when the
  worker is quiet (it is off-hours by construction).

## When (decision)
Never while the NSE session could need the API for live reads: HB2 calls the broker only on a day without an NSE
session, or on a trading day from `HISTORY_INTRADAY_HOUR_IST` (16:00; never before 15:45, when every bar of the day is
final) until `HISTORY_INTRADAY_STOP_HOUR_IST` (08:00; never after 09:00) the next morning. The check runs before every
request, so a long pass stops itself before the open. The worker starts a pass once the day's bars are final (16:00 IST,
NSE closed), in its own task with its own DB sessions and a time budget (`HISTORY_INTRADAY_BUDGET_SECONDS`, 30 min); an
unfinished pass (time budget, no session yet, rate limited) is started again at most every
`HISTORY_INTRADAY_RETRY_MINUTES` (15) until one completes; then the day is done. The worker keeps one adapter per
(credential, its last update, IST day) and reuses it across that evening's passes (review L3), so the instrument master
is not reloaded every 15 minutes; a re-login or a new day builds a new one. `HISTORY_INTRADAY_BLACKOUT_DATES` lists IST
dates on which HB2 never calls the broker: special sessions the holiday calendar does not know (Muhurat evening, a
budget-day Saturday), which would otherwise look like closed days (review L2). The MCX evening session may still be
trading on the same broker account: that is what the 50% reserve is for. Stopping the worker cancels the task.

## Capture, backfill, resume
- Instruments: `HISTORY_INTRADAY_SYMBOLS` (`NSE:RELIANCE,NSE:NIFTY 50`; a bare symbol is NSE) plus the owner tenant's
  non-stopped deployments (`HISTORY_INTRADAY_INCLUDE_DEPLOYMENTS`). NSE / BSE (cash and indices) only.
- Days: trading days from `HISTORY_INTRADAY_START` (2022-01-03) to the last day whose bars are final. Weekends and listed
  holidays are not asked for; in a year without a holiday calendar every weekday is asked (a holiday comes back empty).
- Order: today's capture first (the intraday endpoint, one request per instrument), then the backfill newest first,
  in windows of `HISTORY_INTRADAY_WINDOW_DAYS` (7) calendar days per historical request.
- State: `history_intraday_days`, one row per (tenant, instrument, trade date): `pending | running | done | no_data |
  refused | failed`, with bars / inserted / corrected / skipped / dropped_forming / quality events, first and last bar,
  the credential and endpoint used, attempts, note and error. Days of a window are claimed `running` before the request;
  each day is written and marked `done` in one transaction. An interrupted pass leaves claimed days `running` with
  nothing written; the next pass asks for them again. `done` days are never asked for again (CLI `--redo` re-asks:
  identical bars are skipped, changed ones become versions). `no_data` is asked again while recent (3 days);
  `refused` / `failed` are asked again by the next pass. One pass at a time per database (advisory lock `0x48420003`).
- The flag is checked before every request: turning `history_intraday` off stops a pass.
- A window's bars are split by IST date before they are checked, so one bad bar refuses only its own day; the window's
  other days are stored (review L5). A bar whose time cannot be read still refuses the whole window.

## Admin and CLI
- `GET /api/admin/history-intraday/progress` (SUPER_ADMIN with an MFA session; classified `admin` in
  `tests/tenancy_route_table.py`): per instrument the days by status, first / last day done and bars stored, and the
  days that need attention with `started_at`; a `running` day claimed more than 2 hours ago is flagged `stale` (its pass
  died; the next pass asks for it again) and counted in `stale_running` (review L4). Counts and names only, never bars.
- `python -m app.market_lake.intraday_history run [--from D] [--to D] [--symbols ...] [--redo] [--budget-seconds N]`,
  `progress`, `purge --tenant ID --confirm-name NAME` (removes HB2's `broker:*` rows, their quality events and day rows
  of a wrongly configured tenant). Exit codes: 0 complete, 1 something left, 2 refused (flag off, owner not confirmed,
  NSE hours).

## Operator steps (on the server; this branch never ran them)
1. Deploy, `alembic upgrade head` (migration `f3b5d7a9c1e2`).
2. Set `HISTORY_OWNER_TENANT_ID` / `HISTORY_OWNER_TENANT_NAME` (already used by HB0) and `HISTORY_INTRADAY_SYMBOLS`.
3. The owner links Upstox under Settings > Brokers and logs in that day.
4. Admin > Controls: turn `history_intraday` on (or allow-list the owner's tenant).
5. Start small, off-hours: `docker compose exec backend python -m app.market_lake.intraday_history run --from 2026-09-01`;
   check `progress`; then leave the worker to capture each evening and work back to `HISTORY_INTRADAY_START`.

## Not changed
Z7a-2 market zones, the screener and every other shared reader still read the shared lake only; HB2 adds nothing they
can see. HB0's importer and HB1's job are unchanged.

## Tests (`backend/tests/test_hb2_intraday_history.py`, no network)
Flag off = no work (job and worker hook; tenant allow-list works); closed bars only (a forming bar is dropped), END
labels, ingest stamp and as-of hiding; windows newest first with today from the intraday endpoint; gaps recorded for the
owner's tenant only; resume after a cancelled pass and after the time budget; idempotent re-run (no request) and
`--redo` (one changed bar -> one new version); refused and empty days; `acquire_spare` keeps the reserve, gives up at a
deadline and never holds the lock for exits; the pass draws from the worker's budget and a 429 ends it; tenant isolation
(no session from a teammate, another tenant or an execution-only broker; bars only in the owner's private store; other
tenants and the shared lake see nothing, also through the history API); never during NSE hours and a pass stops before
the morning; the worker hook shares the worker's RateBudget and is cancelled on stop; admin progress is SUPER_ADMIN only.
Review follow-ups: one pinned session user (a co-owner's credential is never used; `HISTORY_OWNER_USER_ID` checked
against the tenant); `broker:*` rows readable only by the session holder (a VIEWER and a co-owner of the same
organisation get none through the history API); the request cap; MCX in session stops the pass; blackout dates; hours
and flag re-checked after the token wait; one adapter per IST day; a bad day refuses only itself; stale `running` days
in progress.

## Open questions
HB2-1 .. HB2-11 in `docs/OPEN_QUESTIONS.md`.
