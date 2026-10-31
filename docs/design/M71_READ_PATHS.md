# M71 read paths - inventory and migration onto `app.market_store` (PL-3 + PL-1(a))

Owner order M71 (PL-1, PL-3). Line numbers are origin/main at d420c2a5. "Calls / request" = broker REST history calls
one request makes for one symbol unless stated. Wave 2 migrates the rows marked "wave 2", each in its own files.

## The service (read side only, `backend/app/market_store/`)

- `read_bars(session, symbol, interval, start, end, *, user=None, purpose="feature", exchange="NSE", rest=None, now=None, dh3=True) -> BarsResult`
- `read_many(session, symbols, interval, start, end, *, user=None, purpose="scan", exchange="NSE", rest=None, now=None, release=None, dh3=True) -> dict[str, BarsResult]`
  (one store pass for all symbols, then `release`, then at most one paced top-up per symbol on ONE budgeted adapter).
- `BarsResult`: `frame` (closed bars, START-stamped UTC index, open/high/low/close/volume), `gaps` (`Gap(start, end, kind,
  reason, queued)`, kind `older` / `newest` / `cold`), `source` (`lake:daily`, `lake:intraday`, `lake:intraday+broker:<name>`,
  "" when nothing is stored), `rest_calls`, `stored_bars`, `topped_up_bars`; `bars()` gives `OHLCVBar`s.
- `RestSource(adapter, record_id, broker, wait_seconds)`: the reader's OWN session (`market_store.sources.from_record` /
  `own_rest`, M71Q-2). `reader.paced_adapter(rest)` is the ONE place a top-up is paced (`api_budget.BudgetedAdapter`); it
  becomes PL-2's data-adapter factory with one line.
- Flag `market_store_reads` (OFF; in the trader pack). OFF = every feature reads exactly as before.

Read order:
1. Daily / Weekly: the NSE bhavcopy in the shared lake (licensed sources only, adjusted), every NSE symbol, zero broker
   calls. Weekly built from it (`bar_source.weekly`); the week still trading is dropped. Other exchanges: cold.
2. Intraday: the reader's own 1-minute rows (`md_tenant_candles`, `broker:<name>:u<id>`; the tenant's other rows for the
   OWNER only; never the shared index scope, never another member) -> END labels become START labels at the boundary
   (`reader._row_bars`, END - 1 minute) -> ONE resampler: `price_action.zones.job.resample_closed` (the chart layer's).
   DH3 flags applied (`dh3=True`, charts / screener) or rows as stored (`dh3=False`, the engine's mode).
3. Newest bars by REST only when the store's last bar END < the last CLOSED bar END of the interval: 1-minute bars after
   it, at most `TOPUP_MAX_DAYS` (7) back, one history call for past sessions + one intraday call for the running session,
   through the reader's own session only; kept per credential until the interval's next bar closes (second run = zero
   calls). Budget refused / no own session -> a `newest` gap with a plain line, never a failure.
4. Older missing history -> an `older` gap, handed to `request_gap_fill(symbol, interval, start, end)` once PL-1c puts it
   in `app.market_lake` (looked up at call time, `reader.GAP_FILL_MODULES`), else recorded in the result only.
5. Never zeros: a missing minute stays missing; a bucket whose later minutes were not read is not shown as closed; a
   missing volume stays NaN (index volume from futures, `index_volume`, is NOT used by the service).

## Inventory

| # | Feature | file:line | Interval | Calls / request today | State |
| --- | --- | --- | --- | --- | --- |
| 1 | Screener run + batches (`/api/screener/run`) | `screener/routes.py` `fetch_frames` :469-612 (bars_of :549, today bar :613, futures :623, daily attach :715) | any | 2-4 per symbol (history chunks, today, Daily attach, future x2), <= 50 symbols, unbudgeted unless batch | **switched** (flag on): `_store_frames` - Daily/Weekly zero calls; intraday store + newest top-up; cold symbols / indices with future volume / other exchanges keep the old path |
| 2 | Backtests (`/api/backtest/bars`, server candles, warm-up, Copilot backtest tools, AI backtest routes) | `backtest/bar_source.py` `resolve` :245-292 (via `evidence.server_frame` `ai/evidence.py:38-68`) | any | 1-2 per run (history chunks) | **switched** (flag on): `bar_source.from_store` first; nothing stored -> the old order |
| 3 | Charts / multi-chart / Terminal / dashboard sparks (`POST /api/market-data/candles`) | `market_data/candles_routes.py:284-340,448-504`, `service.py:162+` (`get_candles` / `get_history` / `get_daily_range` / `get_today_bar`), `shared_candles.py`, `lake_fill.py` | any | history (<= 30-day chunks) + today + 2 for an index's future, per pane, re-polled every 60 s | wave 2 (W2-A) |
| 4 | Copilot / AI candles, evidence, regime, market memory | `ai/tools/market.py:46-60,134,206`, `ai/routes.py:1061-1069`, `ai/evidence.py:38-68`, `ai/market_memory.py:144,155`, `ai/worker_bridge.py:44` | 1min, day, any | 2-4 per symbol per question | wave 2 (W2-B) |
| 5 | Copilot screener tool | `ai/tools/screener.py:346-359` (`load_universe`, store off) | any | as row 1 | wave 2 (W2-G): pass `store=await store_first(...)` |
| 6 | Breakouts / library / ranking screens | `screener/breakouts/routes.py:199-213`, `screener/library_routes.py:146`, `screener/ranking_routes.py:200` (`fetch_frames`, store off) | any + 1d | as row 1, up to 3 `fetch_frames` per run (breakouts) | wave 2 (W2-G): pass `store=True` under the flag |
| 7 | Bar-close alerts | `alerts/barclose.py:479-490` (`fetch_frames` x2, intrabar `include_forming`) | rule tf + 15m | 2 per rule per symbol, every closed bar | **switched** (W2-D, flag on): `server_fetch(store=True)` -> `fetch_frames(store=True)` as the rule creator; Daily from the NSE files, no 15m rebuild of a session they hold; every broker call left on the DATA budget (`budget_out`, `api_budget.no_wait`); intrabar keeps the forming bar on the DATA budget |
| 8 | Worker strategies | `workers/trading_worker.py:1882` (`_history_source(...).get_frames`) | strategy tf | history + today per symbol every cycle | **switched** (W2-D, flag on): `workers/strategy_bars.frames` - the store over the broker path's window, newest bars via the member's own session on the DATA budget (`paced_adapter`, no wait); cold -> `get_frames` on that same paced session; a refusal = a plain reason (waiting for data) |
| 9 | M66 engine | `engine_provider/sources.py:95-129` (`broker_bars`, futures), `workers/engine_runner.py` | 1m, 1d | 1m history + intraday + Daily if not stored + future 1m | wave 2 (W2-C): `read_bars(..., dh3=False)` |
| 10 | VIX gate | `risk_engine/vix_gate.py:165` | day | 1 per cycle | **switched** (W2-D, flag on): previous close from the store's Daily when it holds the last session; the live price on the DATA budget (`vix_gate._paced`, no wait) |
| 11 | Option chain spot / probes | `option_chain/probe.py:103`, `market_data/probe.py:88,101` | 1min | 1-2 per probe | **switched** (W2-E, flag on): chain pages (analyses / charts / ask) paced on the credential's data budget, one chain call per credential per snapshot window (`option_chain/chain_reads.py`), a busy broker = the last chain with its age; the spot's last close and the probe's own close via `read_bars`; the chain recorder on the data pool. `market_data/probe.py` not in this slice |
| 12 | Backfills (writers, not readers) | `market_lake/intraday_history.py:558-561`, `index_history.py:434-436,695`, `ingest.py:213` | 1m, day | 1 per window, off-hours, paced | unchanged (PL-1c owns gap fill) |

## What wave 2 must keep

- Each consumer's DH3 mode (charts / screener `dh3=True`, engine `dh3=False`).
- Index volume from the near-month future (SCR-FIX A7, `index_volume`) is read by the consumer, never by the service
  (row 1 keeps indices with a future on the old path until the future's bars are in the store).
- The data terms (M71Q-2): one user's broker bars never reach another user; a user without his own broker gets Daily /
  Weekly + the plain "Connect your broker for intraday bars." gap.

## Known limits (PL-3 slice)

- Holes inside the window (a missing session between stored ones) are not listed as gaps; only the leading (older) and
  trailing (newest) ones.
- Daily after the close: today's bar appears once NSE's file is stored (a `newest` gap until then; no broker call).
- A long intraday window reads every stored minute (a 60-day 1H scan of 50 symbols ~ 1.2 M rows): stored resampled
  timeframes (PL-1b) will shorten it.
