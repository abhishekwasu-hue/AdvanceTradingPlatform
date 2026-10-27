# Groww adapter (terminal master Part 1B)

Owner order: `docs/owner-orders/ATP_SESSION_TERMINAL_MASTER.md` Part 1B. Review: `docs/design/TERMINAL_MASTER_REVIEW.md`
section 3 and findings F9 / F10 / F11 (TM-3, TM-5, TM-6, TM-23, TM-27). Decisions of 23 Oct 2026
(`docs/owner-orders/DECISIONS.md`): build the adapter now; **no LIVE orders and no live prices through Groww until the
lawyer and the broker confirm in writing**; where market orders are not allowed, a marketable limit with a protection
band from the user's settings, exits never blocked; ATP's own trigger engine by default (native GTT / OCO only as a
later per-user opt-in); the same code path for PAPER and LIVE.

Branch `claude/broker-groww`, **stacked on `claude/broker-kotak-neo` (PR #473, not merged when this started)**, so
`app/brokers/gates.py`, `execution/marketable_limit.py` and `brokers/broker_charges.py` are shared, not duplicated.
**No migration** (the day's token lives in the existing encrypted credential payload).

## 1. What works and what is gated

| Area | State | Where |
|---|---|---|
| Connect (API key of the TOTP kind + TOTP secret), daily one-click renew | works | `app/brokers/catalog.py`, `field_specs.py`, `connect.py` (TOTP_AUTO path, unchanged code) |
| The approval login (API key + secret, approved by the user on Groww each day; `sha256(secret + timestamp)`) | works in the adapter; the tile offers the TOTP key (the easiest) | `app/brokers/groww.py` (`token_request`, `approval_checksum`), `field_specs.py` (`api_secret`, Advanced) |
| Token lifecycle: the day's token, ends 06:00 (IST assumed), "is the session alive" check (user details) | works | `groww.py` (`authenticate`, `get_profile`), `token_lifecycle.py` |
| Positions, holdings, margins, order book, trade book, order margin | works (read only) | `groww.py` |
| Instrument list (one CSV for every exchange) and symbol mapping to ATP's instrument master | works | `groww.py` (`parse_instruments`, `resolve`), `contract_symbols.py` (attribute match) |
| Per-day budget by call type (TM-27) | works | `app/brokers/rate_budget.py` (`DAILY_CAPS`, `DailyBudget`, `RateLimitedBroker`), `workers/trading_worker.py` |
| PAPER practising for a Groww account: the same order transform and Groww's stated brokerage | works | `app/execution/paper_adapter.py` (`venue="groww"`, `venue_plan="standard"`) |
| **LIVE orders** (place, modify, cancel, exit, stop) | **refused before anything is sent** | `app/brokers/gates.py` (`GATES["groww"].live_orders = closed`), checked in every order method |
| **Market data** (quotes, LTP, candles, option chain + Greeks, the feed) | **refused before anything is asked**; positions carry no broker price (0) | `gates.py` (`market_data = closed`) + `market_data/broker_data_terms.json` (`pending`); `stream_for` hands out no feed; price picks skip the row (`gates.data_sources`) |
| Groww's native GTT / OCO ("smart orders") | **not used** (TM-23) | no method calls `/order-advance/...`; a test asserts it |

The gates are code, not a runtime switch. Opening one is a reviewed change to `gates.GATES` with the written
confirmation recorded here; TM-6 keeps the data-terms row as the second key.

**Exits (ADR-0004).** While the LIVE-order gate is closed ATP opens no LIVE position at Groww, so there is no ATP exit to
block. When it opens, exits go through the same path as entries; the per-day budget never refuses an exit, a stop, a
change, a cancel or the order-book read (a new entry stops at the exit reserve instead).

**User-facing.** The Groww tile is listed only with `ui_v2` on; it says "Practice only"; the Connect panel shows the
short guide (practice only and why, the paid plan and the static-IP rule, how the secret is kept) with Groww's links.
Screenshots (not committed): session scratchpad `groww/`, 390 and 1440, light and dark.

## 2. The broker's facts: verified vs not

Read on **23 Oct 2026**. groww.in could not be opened from the build machine (DNS refused, as for the review); the
official SDK could: `growwapi` on PyPI (wheel downloaded and read). Everything else is a search extract and is marked so.

| Topic | Fact used | Source | State |
|---|---|---|---|
| Current SDK | `growwapi` 1.5.0, uploaded 6 Dec 2025 (the newest release on 23 Oct 2026) | https://pypi.org/project/growwapi/ | verified |
| Login | `POST https://api.groww.in/v1/token/api/access`, `Authorization: Bearer <API key>`; body `{"key_type": "totp", "totp"}` or `{"key_type": "approval", "checksum": sha256(secret + str(timestamp)), "timestamp": <epoch s>}`; answer `token` | SDK `client.py` (`get_access_token`, `_build_request_data`, `_generate_checksum`) | verified (SDK) |
| Calls | base `https://api.groww.in/v1`; `Authorization: Bearer <token>`, `x-api-version: 1.0`; answers `{status, payload}`, `status: FAILURE` with `error.message` | SDK `_build_headers`, `_parse_response` | verified (SDK) |
| Paths | `/order/create`, `/order/modify`, `/order/cancel`, `/order/list` (segment, page, page_size 25), `/order/trades/{id}`, `/positions/user`, `/holdings/user`, `/margins/detail/user`, `/margins/detail/orders`, `/live-data/quote`, `/live-data/ltp` (`exchange_symbols` NSE_RELIANCE), `/live-data/ohlc`, `/live-data/greeks/...`, `/option-chain/exchange/{ex}/underlying/{u}?expiry_date`, `/historical/candles` (groww_symbol, `1minute`..`1month`), `/historical/expiries`, `/historical/contracts`, `/user/detail` | SDK `client.py` | verified (SDK) |
| Products / types / validity | products CNC, MIS, NRML, MTF, BO, CO, ARB; types MARKET, LIMIT, SL, SL_M; validity DAY, EOS, IOC, GTC, GTD; segments CASH, FNO, COMMODITY, CURRENCY | SDK constants | verified (SDK); ATP sends CNC / MIS / NRML / MTF and DAY / IOC only |
| GTT / OCO | "smart orders" `/order-advance/create` (GTT, OCO) | SDK `create_smart_order` | verified (SDK); **not used** (TM-23) |
| Option chain + Greeks | "the option chain data including Greeks for all strikes"; a per-contract Greeks call | SDK docstrings | verified (SDK); the field names (`strikes`, `CE` / `PE`, `greeks.delta`, `iv`, `underlying_ltp`) **unverified** |
| Instruments | one CSV `https://growwapi-assets.groww.in/instruments/instrument.csv`; columns `exchange`, `trading_symbol`, `groww_symbol`, `exchange_token` | SDK `INSTRUMENT_CSV_URL`, lookups | verified (SDK); `segment`, `instrument_type`, `underlying_symbol`, `expiry_date`, `strike_price`, `lot_size`, `tick_size` **unverified** (read defensively) |
| Feed | NATS over `wss://socket-api.groww.in`; an ed25519 user nkey; `POST https://api.groww.in/v1/api/apex/v1/socket/token/create/` `{"socketKey"}` -> `token` + `subscriptionId`; subjects `/ld/eq/nse/price.<token>`, `/ld/fo/...`, `/ld/indices/...`; protobuf `StocksSocketResponseProtoDto` (4 = live price: 1 tsInMillis, 13 ltp; 6 = index: 1, 2 value) | SDK `feed.py`, `nats_client.py`, `constants.py`, `proto/*_pb2.py` (descriptor decoded) | verified (SDK); never run against the live feed |
| Feed subscription limit | 1,000 instruments (official intro, search extract) vs 3,000 per user (Groww blog, earlier review) | https://groww.in/trade-api/docs/curl (extract); https://groww.in/blog/api-trading-on-groww | **conflicting**; 1,000 used (GW-5) |
| Token end | daily at 06:00 (time zone not stated) | https://groww.in/trade-api/docs/curl (search extract) | **unverified**; 06:00 IST (GW-2) |
| Daily approval | the approval key must be approved on Groww's API Keys page each day | search extracts (data blog, a third-party backend) | **unverified** |
| Rate limits | Orders 10 / s, 250 / min; Live data 10 / s, 300 / min; Non-trading 20 / s, 500 / min; shared within a type; token endpoint 150 a day | official intro (search extract); per-minute figures and "Authentication 30 / min" quoted from the docs by https://github.com/marketcalls/openalgo/pull/2194 (merged 8 Oct 2026) | **unverified**; an older extract said Orders 15 / s, Non-trading 10 / s and 250 / min |
| Daily caps (TM-27) | orders 3,000, live data 5,000, non-trading 3,000 a day | the review's search extract of the official table; a third-party MCP README | **unverified and possibly outdated**: not found in any extract read on 23 Oct 2026 (GW-3). Kept as the conservative budget |
| Static IP | a static IP is required for API order placement; Groww whitelists it (Trade API -> API Keys, a primary and a backup IP; "Groww Cloud" runs strategies on Groww's servers instead) | https://groww.in/blog/static-ip-api-trading-setup (Mar 2026, search extract); third-party setup guide | **unverified** |
| Market orders from an API | not stated on any readable page; Groww's app adds "Market Protection" to market orders; the exchanges' algo rules ask for protection | https://groww.in/help/stocks,-f&o-&-ipo/sx-fno/what-is-market-protection--33 (extract) | **unverified**; treated as not allowed (TM-3, GW-4) |
| History | "historical market data from 2020 onwards, 1-minute to monthly candles" (changelog, 3 Oct 2025); the order said 3 months | https://groww.in/trade-api/docs/curl/changelog (extract) | **conflicting**; per-request range not published (GW-6) |
| Price | Rs 499 a month + taxes (or Rs 4,999 a year); the free trial leaves out the live-data and backtesting APIs | https://groww.in/blog/api-trading-on-groww (Sep 2026, extract); https://groww.in/pricing (extract) | **unverified** |
| Brokerage | stocks: Rs 20 or 0.1% per order, the lower; below Rs 5, the lower of Rs 5 or 2.5% of the value; F&O Rs 20 per order; the normal brokerage applies to API orders | https://groww.in/help/stocks,-f&o,-ipo-&-mtf/searchable/how-much-is-the-brokerage (extract) | **unverified**; auto square-off Rs 50 per position not modelled |
| Response fields | every field read from an answer (`order_list`, `groww_order_id`, `order_status`, `positions`, `realised_pnl`, `holdings`, `clear_cash`, `total_requirement`, `last_price`, `candles`, ...) | Groww's API reference, as quoted in extracts | **unverified**: the SDK passes answers through; every reader is defensive |

What could not be verified at all: every response shape, the daily caps, the token time zone, the market-order rule,
the static-IP page itself, the history range per call, the order-reference length and the LTP batch size. Each is on
the GW list in `docs/OPEN_QUESTIONS.md`; each must be re-read on Groww's own page, with the date, before a gate opens.

## 3. Design

* **Adapter** `app/brokers/groww.py` (`GrowwBroker`, registry name `groww`): TOTP from `totp.py`; the instrument CSV
  once per process through `master_cache` (key `ALL`, split per exchange); the contract-symbol wrapper for F&O
  (attribute match: underlying, expiry, strike, right). ATP exchanges map to Groww's (exchange, segment): NSE / BSE
  CASH, NFO / BFO FNO, MCX COMMODITY.
* **Gates** `GATES["groww"]` = (live_orders closed, market_data closed), the plain note and what opens each.
* **TM-3**: `market_orders_allowed=False`, `stop_market=False`; the shared `to_venue_order` / SL band; the SEBI rule-set
  maps algo MARKET entries to a marketable limit for `groww` (`docs/COMPLIANCE_IN.md` regenerated).
* **TM-23**: no smart-order method at all.
* **TM-27** `rate_budget.py`: `groww` 3 / s, burst 5, 80 / min (a third of the tightest type: orders 250 / min); a
  `DailyBudget` per (tenant, account) in the worker with `DAILY_CAPS["groww"]` (orders, live data, non-trading, logins),
  ATP spending at most 90% of each (`DAILY_SHARE`); of the orders, 10% (`EXIT_RESERVE_SHARE`) is kept for exits: a new
  entry is refused past 80%, an exit / stop / change / cancel never; the order-book and positions reads (an exit's
  follow-up) always pass. The count is the broker's requests, not ATP's calls (the adapter's `http_calls`: the order
  book asks once per segment, a quote once per symbol). A refusal is a 429 `DailyCapReached` with a plain sentence,
  before a token is spent or anything is sent. The counters are
  in-process (the worker); the API's chart reads have their own share (`market_data/api_budget.py`) and are refused by
  the data gate today (GW-7).
* **Charges** `broker_charges.py`: rows may now be a percent with a cap and a minimum for some segments
  (`brokerage_for_order`); Groww `standard` rows (stocks: 0.1% capped at Rs 20, the Rs 5 / 2.5% minimum; F&O Rs 20);
  `PLATFORM_FEES` holds the Rs 499 a month subscription (shown, never added to a trade). The PAPER adapter now asks
  `brokerage_for_order` (Kotak's flat row answers the same as before).
* **Feed** `app/market_data/groww_feed.py` (`GrowwTickStream`): a fresh nkey per connection (`cryptography`, no new
  dependency), the socket token, a minimal NATS client (INFO / CONNECT with the signed nonce / PING / PONG, SUB / UNSUB,
  MSG) and a protobuf reader for the two price messages; at most 1,000 subscriptions; `stream_for` returns it only with
  the data gate open (never today).

## 4. Static IP (for the lawyer and the broker)

Groww (search extract of its blog, March 2026): a static IP is required for API order placement and Groww whitelists
it on the API Keys page (a primary and a backup); "Groww Cloud" (strategies on Groww's own servers) avoids it. ATP runs
on one server with one outgoing IP. Routing many unrelated users' orders through that one IP is the TM-5 question for
the lawyer and Groww. **Nothing in this PR routes any user's LIVE order through ATP's IP** (the gate refuses every
order). The question is in `docs/DATA_SOURCES.md` "For the lawyer" item 4.

## 5. Tests

`backend/tests/test_part1b_groww.py` on recorded fixtures (`tests/fixtures/groww/recorded.json`, made-up values,
tokens built at run time): both logins and the checksum, refused login / inactive token / dead session, the stored
token, the 06:00 expiry, the gates (no request for an order or a price), account reads with the gates closed, the wire
format with the gates opened in the test (limit, TM-3 marketable limit, SL band, refused products / validities, modify /
cancel with the order's segment), TM-23 (no smart-order method; the fake server fails on `/order-advance/`), quotes /
LTP / history / chain + Greeks behind the open gate, instrument parsing and the contract mapping, the per-day budget
(caps, exit reserve, reset at midnight, the wrapper refusing before sending, the worker's budget), charges, PAPER
parity, the catalog entry, the feed (nkey encoding and signature, protobuf decoding, NATS handshake, PING answer, a
split MSG, refusals, the 1,000 cap). `tests/test_p2a_broker_connect.py`: the connect / renew flow through the API and the
ui_v2-only tile. Frontend `src/broker/BrokerScreens.test.tsx`: the tile and the guide.

## 6. Opening a gate (later, not in this PR)

1. The written confirmation (lawyer's opinion for TM-5; Groww's letter for TM-5 and TM-6) filed with the owner.
2. Every "unverified" / "conflicting" row above re-read on Groww's own page, with the date; the daily caps confirmed or
   removed from `DAILY_CAPS`.
3. TM-6: `broker_data_terms.json` and `docs/BROKER_DATA_TERMS.md` row to `confirmed`; then `GATES["groww"].market_data`.
4. TM-5: `GATES["groww"].live_orders`, with the static-IP model the lawyer approves; LIVE stays behind the existing
   switches (`live_trading`, `phase0_paper_only`, the step-up) as for every broker.
