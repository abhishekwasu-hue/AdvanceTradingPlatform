# Live 1-minute data from your own FYERS account

AMW can collect **completed 1-minute candles** from a user's **own FYERS account** in the background, store them for that
user, stream them live to that user's dashboard and API clients, and (optionally) use the account as the data source for
that user's scans. AMW never places, modifies or cancels orders.

> **Data licensing.** The data comes from each user's own FYERS account under FYERS' API terms. AMW stores it per user and
> shows it **only to that user**: it is never shared with, or redistributed to, other users. Serving one operator-paid
> feed to all users requires a separate redistribution licence (see [Data vendor options](#data-vendor-options)).

## 1. Setup (each user)

1. You need a FYERS trading account. In the FYERS API dashboard (<https://myapi.fyers.in/dashboard/>) create an app for
   your own account.
2. Set the app's **Redirect URL** to the Live Data page of your AMW installation. It must match exactly:
   * desktop: `http://localhost:8501/Live_Data_FYERS`
   * server: `https://<your-domain>/Live_Data_FYERS`
3. In AMW open **Live Data (FYERS)** → *Connection* → enter the **App ID** and **Secret** → *Save connection*.
   They are stored encrypted and never shown again (the App ID is displayed masked).
4. **Every trading day:** *Generate FYERS login link* → *Log in to FYERS*. FYERS redirects back to the page and AMW saves the
   session. If the redirect cannot reach AMW, paste the full redirected URL into the fallback field.
   FYERS sessions expire daily; AMW treats a session as valid until **06:00 IST the next morning** (conservative).
5. *Feed* tab → enter up to **100 symbols** (`RELIANCE.NS`, `TCS`, `^NSEI`, `^NSEBANK`, `^CNXIT`) and the poll interval →
   *Start background feed*.
6. Optional: *Data source for scans* → **My FYERS account**. Scans then use your FYERS daily/intraday history. If you have
   not logged in today, scans fall back to the default provider and the Screener shows a warning; every signal record
   stamps the provider that was actually used.

## 2. How the collector works

* About **5 seconds after each minute closes** (09:16 … 15:35 IST, trading days only, holidays from Settings), the
  collector requests **today's 1-minute candles** for each symbol from FYERS `GET /data/history`.
* Only **completed** candles are kept (the forming minute is never stored or shown) and each is validated: finite
  OHLC, `high ≥ max(open, close)`, `low ≤ min(open, close)`, positive prices, non-negative volume, inside the session.
  Rejected candles are counted, not stored.
* Bars are upserted per user: new bars are stored; a later different value for an existing bar updates it and increments
  `revisions`.
* **No forward-filling.** Because the whole day is requested on every poll, minutes missed during an outage are filled
  with FYERS' own candles on the next successful poll.
* Bars older than 30 days are purged daily.
* Errors are isolated per symbol.

| Status | Meaning |
|---|---|
| `WAITING` | Started; waiting for the next completed minute (or just logged in again) |
| `RUNNING` | Last poll succeeded for all symbols |
| `DEGRADED` | Last poll had errors for some symbols (see *last error*) |
| `MARKET_CLOSED` | Outside 09:15–15:35 IST or not a trading day |
| `TOKEN_EXPIRED` | FYERS session missing/expired. No FYERS calls are made. Resumes automatically after the user logs in — in the dashboard **or** through the API |
| `RATE_LIMIT_GUARD` | FYERS returned HTTP 429 twice today. All FYERS calls for this account stop until the next day (protects the account from a daily block) |
| `ERROR` / `STOPPED` | Not connected / stopped by the user or from another AMW process |

### One feed per account, across processes

The Streamlit dashboard and the API server are separate processes sharing one database. To avoid two collectors polling
the same FYERS account (which could exceed FYERS' limits):

* A **database lease** allows exactly **one collector per user** across all processes. Starting a second one is refused
  with *"already running in another AMW process"*.
* The holder renews the lease every 10 s and publishes its status there, so every process shows the same status
  (`runner`: `this process` / `another AMW process`).
* **Stop** works from any process: it releases the lease and the holder stops itself at its next heartbeat (≤ 10 s).
* A holder that stops renewing for 90 s (crash) is considered dead and can be replaced.
* **Auto-resume** (`AMW_FEED_AUTORESUME=1`, set by `api_server.py` and `start_screener.bat`): on start-up, feeds the user
  left running are restarted; after a crash the restart is retried once after the old lease goes stale.
* The daily **rate-limit guard** is stored in the database and honoured by every process.

## 3. Streaming and API

WebSocket `ws://<api-host>:8000/api/v1/stream/bars` (use `wss://` behind TLS). The first message, within 10 s, must be

```json
{"token": "<your AMW API token>", "symbols": ["RELIANCE.NS", "^NSEI"]}
```

(`token` is omitted in `AMW_AUTH_MODE=local`; an empty `symbols` list streams all of your symbols). The token travels
in the message body, not in the URL, so it does not appear in access logs.

| Message | Content |
|---|---|
| `{"type": "subscribed", ...}` | Accepted; current collector status |
| `{"type": "bar", ...}` | A new completed bar: `symbol, ts (IST ISO-8601, bar start), open, high, low, close, volume, source, revisions` |
| `{"type": "revision", ...}` | The full corrected bar. A revision can be the first message for a bar if it was corrected before the stream picked it up — **clients should upsert by `(symbol, ts)`** |
| `{"type": "status", ...}` | Collector state changes (`status`, `detail`, `runner`) |
| `{"type": "error", "message": "unauthorized"}` | Bad token; the socket closes with code 4401 |

The stream reads bars from the database (the single source of truth), so it works no matter which process runs the
collector; delivery latency is ≤ 2 s after the bar is stored (immediate when the collector runs in the API process).
Only bars stored **after** the connection opened are streamed; fetch earlier bars with `GET /api/v1/feed/bars`.

Minimal Python client:

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8000/api/v1/stream/bars") as ws:
        await ws.send(json.dumps({"token": "<API token>", "symbols": ["RELIANCE.NS"]}))
        async for raw in ws:
            print(json.loads(raw))

asyncio.run(main())
```

| REST endpoint | Purpose |
|---|---|
| `PUT /api/v1/broker/fyers` | Save App ID, Secret, Redirect URL (encrypted; never returned) |
| `GET /api/v1/broker/fyers/login-url` | Daily login link (one-time `state`, valid 15 min) |
| `POST /api/v1/broker/fyers/callback` | Complete login with `auth_code` + `state` |
| `DELETE /api/v1/broker/fyers` | Stop the feed and delete stored credentials |
| `POST /api/v1/feed/start` · `POST /api/v1/feed/stop` · `GET /api/v1/feed/status` | Control and status (one feed per account across processes) |
| `GET /api/v1/feed/bars?symbol=&since=&limit=` | Stored bars of the caller |
| `GET /api/v1/data-source` · `PUT /api/v1/data-source?source=default|fyers` | Selected vs. actually active scan data source |

## 4. Limits and rate budget

FYERS publishes per-app limits of **10 requests/s, 200/min and 100,000/day**, and warns that repeatedly exceeding the
per-minute limit can block the account for the rest of the day (verify the current values in the FYERS API docs).

* AMW's local budget per user and process: **4/s and 150/min** (collector and scans share it).
* **100 symbols** per feed → at most 100 requests per poll. A full day at a 1-minute interval is about
  100 × 375 ≈ 37,500 requests (below the daily limit). Use a longer poll interval to save requests; bars stay 1-minute.
* When the collector runs in another process, scans in this process get `150 − (symbols ÷ interval)` requests/min.
* Two HTTP 429 responses in one day stop all FYERS calls for the account until the next day.
* Known limitation: two FYERS-sourced scans started at the same time **in two different processes** each use their own
  budget. Run FYERS-sourced scans from one place at a time.

## 5. Security

* App Secret and access token: AES-256-GCM at rest (same master key as BYOK AI keys), bound to the user and purpose;
  never returned by the API; App ID shown masked.
* OAuth `state`: random, stored hashed, one-time, bound to the user, expires after 15 minutes (prevents completing a
  login another user started).
* Token, secret and auth code are redacted from errors and logs; the dashboard clears the Secret from server-side widget
  state after saving.
* Tenant isolation: bars, status and stream are filtered by the owner's user id (tests cover cross-user access).
* No trading: the FYERS client calls exactly three URLs — `generate-authcode`, `validate-authcode`, `data/history`
  (enforced by a test), and the API has no order/position/funds routes (also enforced by a test).

## 6. What was verified — and what was not

Verified with a **mocked FYERS API** (no account or network needed; `tests/test_feed.py`, 26 tests, one of them PostgreSQL-only): bar validation and
completion, no forward-fill, symbol mapping, `appIdHash`, login and one-time state, redaction, encrypted credentials,
completed-bar polling, revisions, catch-up after missed minutes, token expiry, rate-limit guard, poll schedule
(session/weekends/holidays), per-user hub and WebSocket isolation, scans on FYERS data and fallback reporting, scan abort
on an expired session, and the multi-process behaviour (one collector, shared status, remote stop, login in the other
process resumes the collector, shared rate guard, auto-resume after a crash) with two service instances on one database.
The lease, status and stream queries are also tested on PostgreSQL 16. The WebSocket stream was additionally exercised
against a real `uvicorn` server with bars written by a separate process.

**Not verified against the live FYERS API** (no FYERS account in the build environment). Before production, do one live
login and one full trading day, and confirm: response fields (`s`, `candles`, error codes), the actual token expiry
time, index symbol names (`NSE:NIFTY50-INDEX`, `NSE:NIFTYBANK-INDEX`, `NSE:NIFTYIT-INDEX`), current rate limits and
intraday history range, and whether refresh tokens are available for your app (AMW assumes a daily login).

## Data vendor options

For one operator-provided feed shown to many users, a **redistribution / display licence** is required. Public prices
below were checked in October 2026 and are indicative only; confirm with each vendor.

| Option | What it offers | Indicative price | Use in AMW |
|---|---|---|---|
| FYERS API (implemented) | 1-minute history, live data for the account holder | Free with a FYERS account | **Per user only** — API terms prohibit redistribution |
| Dhan Data API | Market-data API with WebSocket | about ₹499 + tax per month, per account | Per user only (same model as FYERS) |
| Accelpix (authorised vendor) | Tick-by-tick real-time data for charting platforms (Pix Connect), Pix APIs, 5-day free trial | Pix Connect from ₹1,599 per month + 18 % GST, one segment; API pricing not published | Retail plans are for the subscriber's own platforms; ask for a commercial redistribution quote |
| Global Datafeeds (authorised L1 vendor since 2010) | Real-time, historical and a **Realtime Exchange Snapshot API** (whole-exchange snapshots at 1/2/5/10/15/30 min, hourly, daily); per-key request-per-hour limits | plan-based (on the vendor's pricing page / sales) | Published terms: data is for the charting/analysis use of a single subscriber; redistribution needs a separate commercial licence |
| NSE (direct) | Real-time and snapshot data products | licence fees per display medium | Redistribution of exchange data requires an agreement with the exchange |

Within a ₹5–10k/month budget only personal-use plans were found. To serve all users from one feed, request a
**display/redistribution licence quote** from an authorised vendor; until then the per-user broker connection is the
compliant, low-cost path.
