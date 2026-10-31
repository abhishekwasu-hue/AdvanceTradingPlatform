# M71 PL-2 - broker API limits (published figures, pools, exit reserve)

Code: `backend/app/brokers/broker_limits.py` (the one table, `PUBLISHED`), used by `rate_budget.RateLimitedBroker`
(trading worker) and `market_data/api_budget.py` (API). Read on 31 Oct 2026. The build session could not open
upstox.com, kite.trade or dhanhq.co directly (network policy), so figures come from the owner's research R33, the
brokers' community / support pages and search extracts, as marked. **Status "to confirm"** = check the broker's own
page; the figure in force is the lower one.

## Published limits (as the broker states them; ATP counts to `floor(limit x (1 - margin))`, margin 10 %)

| Broker | Pool / bucket | Per second | Per minute | Per 30 min / hour | Per day | Source | Status |
|---|---|---|---|---|---|---|---|
| Upstox | data `standard` (history, quotes, account) | 25 | 250 | 1,000 / 30 min | - | community.upstox.com t/rate-limits-on-api-usage/3384, t/how-to-rate-limit/4726 | to confirm (default, lower set) |
| Upstox | orders | 10 | 250 | 1,000 / 30 min | - | lower of R33 and the community figures | to confirm |
| Upstox (`BROKER_UPSTOX_LIMIT_SET=r33`) | data / orders | 50 / 10 | 500 / 500 | 2,000 / 2,000 per 30 min | - | owner research R33 (upstox.com/developer/api-documentation/rate-limiting) | to confirm |
| Zerodha (Kite) | data `quote` | 1 | - | - | - | kite.trade/docs/connect/v3/exceptions/ (rate limits), R33 | published |
| Zerodha (Kite) | data `history` | 3 | - | - | - | same | published |
| Zerodha (Kite) | data `other` (account reads) | 10 | - | - | - | same | published |
| Zerodha (Kite) | orders | 10 | 200 | - | 3,000 | R33; a newer forum reply says 400 / min, 5,000 / day (lower used) | published |
| Dhan | data `data` (history) | 5 | - | - | 100,000 | dhanhq.co/docs/v2/ (Rate limit); dhan.co support "what are the api rate limits" | published |
| Dhan | data `quote` | 1 | - | - | - | same | published |
| Dhan | data `non_trading` | 20 | - | - | - | same | published |
| Dhan | orders | 10 | 250 | 1,000 / hour | 7,000 | same | published |
| Angel One | data `history` (getCandleData) | 3 | 180 | 5,000 / hour | - | smartapi.angelone.in forum topic 4387 "Changes in API rate limit" | published (forum table) |
| Angel One | data `quote` (getLtpData) | 10 | 500 | 5,000 / hour | - | same | published (forum table) |
| Angel One | data `account` | 1 | - | - | - | not found: conservative | conservative |
| Angel One | orders (place / modify / cancel) | 20 | 500 | 1,000 / hour | - | same forum table | published (forum table) |
| Fyers | one allowance for all REST | 10 | 200 | - | 100,000 | fyers.in API v3 "Plans & Rate Limits" as quoted on fyers.in/community | published |
| Fyers | ATP split: data / orders | 7 / 3 | 140 / 60 | - | 70,000 / 30,000 | 70 / 30 split of the one allowance | ATP split |
| Groww | data `live_data` | 10 | 300 | - | 5,000 | docs/design/BROKER_GROWW.md (search extracts) | to confirm |
| Groww | data `non_trading` | 20 | 500 | - | 3,000 | same | to confirm |
| Groww | orders | 10 | 250 | - | 3,000 | same | to confirm |
| Kotak Neo | data `quote` (and history) | 25 | - | - | - | SDK docs quotes.md (docs/design/BROKER_KOTAK_NEO.md) | published |
| Kotak Neo | data `account` | 3 | 120 | - | - | none published: conservative | conservative |
| Kotak Neo | orders | 10 | - | - | - | Kotak FAQ (search extract) | to confirm |
| Shoonya | data / orders | 3 / 3 | 120 / 120 | - | - | nothing published found: ATP's earlier conservative figure | conservative |
| CoinDCX | data / orders | 6 / 4 | - | - | - | no official figure; coindcx Ruby client README: ~4 orders/s, 10 calls/s in all | conservative |
| Any other | data / orders | 2 / 2 | 100 / 100 | - | - | none recorded | conservative |

## Design

- Every window is a sliding Redis sorted set per credential (`bl:{<credential id>}:<bucket>:<seconds>`), checked and
  recorded in one Lua script (atomic; a refused call is recorded nowhere). The day window is a rolling 24 hours.
- Pools: orders, exits, stops, changes and cancels draw only the ORDER buckets; history, quotes and account reads the
  DATA buckets. In-process, `RateBudget.acquire_order` has its own buckets and lock; no lock is held while sleeping.
- Exit reserve: entries leave `BROKER_ORDER_EXIT_RESERVE_SHARE` (20 %) of every order window free; ordinary data calls
  leave `BROKER_DATA_PRIORITY_RESERVE_SHARE` (10 %) for an exit's reads (price under `exit_reads()`, positions, order
  book). A priority call waits at most `BROKER_EXIT_MAX_WAIT_SECONDS` (2 s) for the next slot, then is sent and counted.
- API data reads (BudgetedAdapter: charts, screens, the store reader) wait for the next slot, a full minute or 30-minute window included, at most `BROKER_DATA_CALL_WAIT_SECONDS` (20 s) per call within the request's allowance, then a plain "Data is busy; try again in a minute." gap. Worker data calls wait for a slot at most `BROKER_DATA_MAX_WAIT_SECONDS` (30 s) in all; a slot further away (a full 30-minute
  or day window) is refused at once with "The broker is busy right now; please try again in a minute."
- The API (`api_budget`) adds the same published windows to its own script, so worker and API share one count.
- HTTP 429 / "too many requests": retried after `base x 2^n` (0.5 s, capped at 8 s) with equal jitter, each retry taking
  a slot again; in all at most 20 s (data) / 60 s (orders), then the plain message above. Data 429s are kept out of the
  circuit breaker (metric only), so they cannot open the breaker that holds new entries.
- Redis down: the worker counts the same windows in-process (orders and exits flow); the API stays at api_budget's own
  per-process share. Redis `maxmemory-policy` other than `noeviction` logs a warning (TTL'd window keys could be
  evicted and under-count); the admin payload reports it.
- Calls per day: `bhttp:<YYYYMMDD>` (every HTTP request an adapter's client sent, hooked in `build_adapter`) and
  `bcalls:<YYYYMMDD>` (calls that took a budget slot), by broker / pool / kind, 429s and caller purpose; admin
  `GET /api/admin/console/system` -> `broker_calls`.
- `api_budget.budgeted_data_adapter(record, purpose)` is the one factory every data reader should build its adapter with
  (wave 2 switches the callers: Screener, alerts, Copilot, AI evidence).

## Settings (operational, not trading values)

`BROKER_BUDGET_SAFETY_MARGIN` 0.10 · `BROKER_ORDER_EXIT_RESERVE_SHARE` 0.2 · `BROKER_DATA_PRIORITY_RESERVE_SHARE` 0.1 ·
`BROKER_DATA_MAX_WAIT_SECONDS` 30 · `BROKER_EXIT_MAX_WAIT_SECONDS` 2 · `BROKER_429_BASE_SECONDS` 0.5 ·
`BROKER_429_CAP_SECONDS` 8 · `BROKER_429_DATA_MAX_WAIT_SECONDS` 20 · `BROKER_429_ORDER_MAX_WAIT_SECONDS` 60 ·
`BROKER_UPSTOX_LIMIT_SET` conservative | r33.
