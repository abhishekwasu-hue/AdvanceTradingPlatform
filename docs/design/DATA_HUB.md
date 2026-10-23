# Data hub in the middle (Terminal master order, Part 1)

Order: `docs/owner-orders/ATP_SESSION_TERMINAL_MASTER.md` Part 1 (README row 20). Review: `docs/design/TERMINAL_MASTER_REVIEW.md`
section 5 and findings F6-F9; owner answers of 23 Oct (TM-12, TM-13, TM-14 defaults accepted; the owner principle: one
code path for paper and live, exits never blocked, AI never places orders). Flag `data_hub` (OFF by default, needs
`ui_v2`). **No migration.**

## 1. What was there

| Piece | Where | Kept as |
|---|---|---|
| `TickStream` (connect, subscribe, backoff 1-2-5-10-30 s, resubscribe on a new socket), Upstox V3 + Kite decoders | `market_data/stream.py` | the stream of every hub connection; gains a `state` and an opt-in stop on a refused token |
| `StreamManager` (one stream per tenant session, env `STREAMING_QUOTES_ENABLED`, off) | `stream.py` | unchanged for tenants without the flag |
| `UserSessionFanIn` + `SessionSymbols` (one session per user credential, priority plan, cap) behind `byob_data_tier` | `market_data/fanin.py` | the hub's per-user side (byob on); the symbol registry and planner of every hub connection |
| Per-credential API budget and quote cache (`api_budget`), shared candles (LD-10) | `api_budget.py`, `shared_candles.py` | unchanged: what is not on a stream still goes through them |
| SSE stream with reload signals | `live/routes.py`, `frontend/src/live/` | carries the new `ticks` / `hub` events (TM-12) |

## 2. The hub

`backend/app/market_data/hub.py`. One broker connection = one live session + one stream, keyed by the connection's
namespace:

- **byob on** (`byob_data_tier`): each user's own credential, `SessionRef.namespace` (`u<user>.c<credential>`); the
  fan-in opens it after the daily login and attaches its stream to the hub.
- **byob off**: the organisation's own session, `org.t<tenant>` (the keys BT1b already gave the organisation's cache);
  the worker's `DataHub.ensure_org` owns it, and the old org-wide `StreamManager` stream for that tenant is dropped
  (no second websocket on the same token).

The stream's symbol set is the connection's `SessionSymbols` plan: open positions and running strategies (the worker,
`Priority.POSITION`), then what the pages registered - charts and the option chain page (`/ltp`, CHART), the watchlist
(`/quotes`, WATCHLIST), the browser's live ticks (`PUT /api/live/ticks`, CHART) - inside `session_cap` (50, or the
broker's recorded cap when lower; TM-14). What does not fit stays on the per-credential REST budget, as before.

**Normalised data through the adapter layer.** Ticks are the existing `Tick` (broker, exchange, platform symbol, LTP,
exchange time); candles stay on `MarketDataService` / shared candles (licensed shared EOD history is a separate store
and is not touched); an option chain is the adapters' `OptionChain`, shared within one connection for
`HUB_CHAIN_SNAPSHOT_SECONDS` (5; `shared_chain`, keyed by the credential row - one user's chain is never read under
another's).

**Where ticks live.** A hub stream writes only into its own connection's `HubTickCache`: in the worker's process (its
decisions read it there) and ONE snapshot per connection at most once a second in Redis (`md:hub:<ns>:ticks`, also
written after the last tick), never the platform-wide `md:tick:*` keys and never the lake recorder. The API reads it
through `HubSnapshotReader` (the per-symbol keys first, as before, then the snapshot), so 500 connections x 50
symbols cost about 500 Redis writes a second instead of 25,000.

## 3. Consumers

| Consumer | Before | With the hub |
|---|---|---|
| Paper / live engine, exits (`MarketDataService.get_ltp`) | a fresh tick of the shared keys, else REST | the connection's hub tick (`tick_namespace`), else REST exactly as before - exits are never blocked by the hub |
| Charts (`GET /api/market-data/ltp`, the chart's LtpHub) | shared keys (org) or the session's keys (byob), then quote cache, then REST | the connection's ticks first (the symbol is registered); in the browser the price also moves with each `ticks` event |
| Watchlist (`POST /api/market-data/quotes`) | as above, then one multi-quote call | the connection's ticks; one batch call for the rows outside the cap; in the browser the rows move with ticks |
| Option chain page, its analyses, the Copilot's chain tool | one broker chain call per request | `shared_chain`: one call per connection, underlying and expiry per 5 s |
| Copilot quote tool | REST quote | REST quote (it needs open / high / low), its last price the connection's tick |
| Scans and alerts | candles (shared, LD-10) | unchanged: candles are not prices |
| Telegram | the alerts' and engine's own reads | unchanged path, reads the same prices |

## 4. States, reconnect and renew

`TickStream.state`: `connecting` -> `live`; a dropped socket -> `reconnecting` (backoff 1, 2, 5, 10, 30 s) -> `live`
with every symbol subscribed again on the new socket. A hub stream (`stop_on_auth_error`) that the broker refuses
(401 / 403 / an authentication error) goes to `renew_needed` and stops: no retry every few seconds. The hub keeps the
connection's symbols; when the login is renewed (a new token fingerprint on the adapter or the credential row) it opens
a new stream and subscribes them all again. A stream outside the hub retries as before.

The status is published per connection (`md:hub:<ns>:status`) and read by the API (`GET /api/live/hub`, `hub` events).
The API also derives `renew_needed` from the row itself (a login that ended reads so before the worker's next cycle)
and `polling` when no stream is known.

**ONE renew prompt.** The app shows one notice above every page (`frontend/src/live/LivePricesBanner.tsx`): "Live
prices from <Broker> are reconnecting ..." or "Your <Broker> login has ended. Log in again to resume live prices." with
one "Log in again" button (the Broker page's renew panel). It hides itself while the page's own broker card already
asks to log in. The hub sends no notification of its own: the worker's existing token check sends the one "session
expired" notification (and the pre-open reminder the morning one) - one ended login never makes two notices.

## 5. To the browser (TM-12)

`PUT /api/live/ticks {sid, items <= 50}` registers the tab's symbols (kept 10 minutes, re-sent every 4); the existing
`GET /api/live/stream?sid=...` then carries `ticks` (only those symbols, only on a change, at most one update per symbol
per second, from the user's own connection) and `hub` (the state, plain words, no ids). `frontend/src/live/liveTicks.ts`
is the tab's store; the watchlist and the charts read it. With the flag off: the stream is as before and both routes
answer 404.

## 6. Rules (data terms)

- The user's own token only: byob on = the user's own credential; byob off = the organisation's own session (the model
  that exists today; `byob_data_tier` keeps deciding who may read - TM-13).
- No cross-user sharing: every key carries the connection's namespace; the browser feed reads only the caller's
  connection; a chain snapshot is keyed by the credential row; a hub stream never feeds platform-wide listeners.
- Nothing here places, changes or blocks an order; the AI tools only read.

## 7. Measurement (`backend/scripts/hub_probe.py`, tests `tests/test_tm1_data_hub_load.py`)

One trader's screen on one Upstox connection for 30 virtual minutes (active chart, chain page, 50-symbol watchlist, a
paper strategy with two open legs, a Copilot question every 5 minutes), modelled with the code's own constants, then two
real runs of the stream code against fake sockets:

| Measure | Before (main, hub off) | After (hub on) |
|---|---|---|
| Broker calls asked per minute (refused included) | 29.6 | 10.3 |
| Broker calls made per minute | 19.9 (API 16.8, worker 3.1) | 10.3 (API 10.3, worker 0.03) |
| Rate-limit refusals (the API's per-credential budget: "budget used") | 292 in 30 min (the 500 / 30 min window runs out after about 19 min) | 0 |
| Broker 429 | 0 | 0 |
| Same price on `/ltp`, `/quotes`, the engine and the Copilot at one instant | 48.6 % of samples (mean spread 1.1, max 9.0 points) | 100 % |
| Dropped socket | - | live -> reconnecting -> live in 1.0 s, 50 of 50 symbols subscribed again |
| Refused token | the old loop retries (3 times in 4 s, then every 30 s) | 1 attempt over 5 cycles, 1 renew; after the renewed login: a new stream, live, 50 symbols |
| Fan-out load (500 connections x 50 symbols x 3 tabs) | - | every tab got every tick of its own connection and nothing else; poll p95 0.06 ms; about 330,000 ticks a second of one CPU into the caches |

What is left on REST with the hub: the watchlist rows outside the cap of 50 (one batch call every 10 s) and the chain
page's chain (one call per 15 s poll, shared with anyone else of the connection inside 5 s). On the host the owner may
run `python backend/scripts/hub_probe.py --load` (no broker, no database); a run against his own token is a later step,
after asking.

## 8. Not in this PR

New brokers' streams (Part 1B: Kotak Neo, Groww; Dhan / Angel One / Fyers / Shoonya have none yet - TM-27), the chain
as a stream, the sector heatmap's batched quotes (Part 3, TM-14) and the chain history recorder (9.4).
