# Kotak Neo adapter (terminal master Part 1B)

Owner order: `docs/owner-orders/ATP_SESSION_TERMINAL_MASTER.md` Part 1B. Review: `docs/design/TERMINAL_MASTER_REVIEW.md`
section 3 and findings F4 / F10 / F11 / F12 (TM-3, TM-5, TM-6, TM-26, TM-27). Decisions of 23 Oct 2026
(`docs/owner-orders/DECISIONS.md`): build the adapter now; **no LIVE orders and no live prices through Kotak Neo until
the lawyer and the broker confirm in writing**; where market orders are not allowed, a marketable limit with a protection
band from the user's settings, exits never blocked; the same code path for PAPER and LIVE.

Branch `claude/broker-kotak-neo`. **No migration** (the session lives in the existing encrypted credential payload).

## 1. What works and what is gated

| Area | State | Where |
|---|---|---|
| Connect (consumer key, mobile number, UCC, MPIN, TOTP secret), daily one-click renew | works | `app/brokers/catalog.py`, `field_specs.py`, `connect.py` (TOTP_AUTO path, unchanged code) |
| Token lifecycle: TOTP login -> view token -> MPIN -> trade token + session id + the account's base URL, stored as one encrypted string; daily expiry; "is the session alive" check | works | `app/brokers/kotak_neo.py` (`encode_session`, `authenticate`, `get_profile`), `token_lifecycle.py` |
| Positions, holdings, funds (limits), order book, trade book, order margin | works (read only) | `kotak_neo.py` |
| Instrument list and symbol mapping to ATP's instrument master | works | `kotak_neo.py` (`parse_scrip_master`, `resolve`), `contract_symbols.py` (attribute match) |
| PAPER practising for a Kotak Neo account: Kotak's own order transform and stated brokerage | works | `app/execution/paper_adapter.py` (`venue="kotak_neo"`) |
| **LIVE orders** (place, modify, cancel, exit, stop) | **refused before anything is sent** | `app/brokers/gates.py` (`live_orders = closed`), checked in every order method of the adapter |
| **Market data** (quotes, LTP, candles, option chain, the streaming feed) | **refused before anything is asked**; prices inside positions / holdings are left out (0) | `gates.py` (`market_data = closed`) plus `market_data/broker_data_terms.json` (`pending`); `stream_for` hands out no feed; price-source picks skip the row (`gates.data_sources`) |

The gates are code, not a runtime switch (no flag, no environment variable, no admin button). Opening one is a reviewed
change to `gates.GATES` together with the written confirmation recorded here. TM-6 keeps the data-terms row as the
second key: `market_data_open` needs both the gate and the `confirmed` status.

**Exits (ADR-0004).** While the LIVE-order gate is closed ATP can open no LIVE position at Kotak Neo, so there is no
ATP exit to block; a position the user opened himself in the Kotak app is shown (read only) and closed in the Kotak app.
When the gate opens, exits go through the same path as entries, MARKET exits as marketable limits re-priced until filled
(`marketable_limit.reprice_until_filled`), never refused or cancelled there.

**User-facing.** The Kotak Neo tile is listed only with `ui_v2` on (the catalog route filters `requires_ui_v2`). The
tile says "Practice only"; the Connect panel shows a short guide in plain words (practice only, why, the static-IP and
market-order rules, how the MPIN and secret are kept) with the official links. Screenshots (not committed): the session
scratchpad `kotak/` folder, 390 and 1440, light and dark.

## 2. The broker's facts: verified vs not

Read on **23 Oct 2026**. kotakneo.com and kotaksecurities.com could not be opened from the build machine (DNS refused);
the official SDK could: its PyPI package (wheel downloaded and read) and its GitHub repository (cloned and read).

| Topic | Fact used | Source | State |
|---|---|---|---|
| Current SDK | `kotakneoapi` 3.0.9, released 15 Oct 2026; repository `Kotak-Neo/kotak-neo-python` (last commit 23 Oct 2026); the older `Kotak-neo-api-v2` repository is archived (10 Sep 2026) | https://pypi.org/project/kotakneoapi/ ; https://github.com/Kotak-Neo/kotak-neo-python | verified (SDK) |
| Login | consumer key in `Authorization`; `POST https://mis.kotaksecurities.com/login/1.0/tradeApiLogin` `{mobileNumber, ucc, totp}` + header `neo-fin-key: neotradeapi` -> view token + sid; `POST .../login/1.0/tradeApiValidate` `{mpin}` with `sid` + `Auth` -> trade token, sid, `baseUrl`, `dataCenter`, `ucc` | SDK `services/totp.py`, `settings.py`, `utils/urls.py`; docs `functions/authentication/totp_login.md`, `totp_validate.md` | verified (SDK) |
| Authenticated calls | `<baseUrl>/<path>` with `Auth` (trade token) + `Sid`; orders also `Authorization`; order bodies are a form field `jData` holding JSON | SDK `services/*.py`, `rest.py` | verified (SDK) |
| Paths | `quick/order/rule/ms/place`, `quick/order/vr/modify`, `quick/order/cancel`, `quick/user/orders`, `quick/user/trades`, `portfolio/v2/positions`, `portfolio/v1/holdings`, `quick/user/limits`, `quick/user/check-margin`, `script-details/1.0/masterscrip/file-paths`, `script-details/1.0/quotes/neosymbol/{seg\|tok,...}/{type}`, `market-data/1.0/watchlist/option-chain`, `market-data/1.0/historical/details`, `login/1.0/get-client-ip` | SDK `settings.py` PROD_URL | verified (SDK) |
| Products | CNC, MIS, NRML, MTF only; "Bracket and Cover orders are no longer supported" | SDK `settings.place_order_product_allowed_values`; PyPI page | verified (SDK) |
| Order types / validity | L, MKT, SL, SL-M; validity DAY / IOC (MCX: DAY only); market protection `mp` always sent "0"; AMO = `am` "YES" | SDK `settings.py`, `services/order.py` | verified (SDK) |
| Market orders for retail API orders | "not allowed for retail algo trading"; limit orders recommended; an FAQ says a placed market order is converted to a limit relative to the last price | static-IP page and FAQ (search extract only) | **unverified** (TM-3 applies regardless) |
| Static IP | order APIs only from a whitelisted static IP since 1 Apr 2026; the session must be created from the same IP; up to 10 family members may share one IP, only the parent may change it | https://www.kotakneo.com/platform/kotak-neo-trade-api/static-ip-details/ (search extract); SDK `whatsmyip()` "which IP would need to be whitelisted" | **unverified** (page not opened) |
| Rate limits | quotes: 50 instruments per call, 25 requests / s (SDK docs `quotes.md`); orders: "up to 10 orders per second" (Kotak FAQ, search extract); no per-minute or per-day figure published; the SDK's optional client limiter defaults to 10 / s, 300 / min, 5,000 / h (its own defaults, not the broker's limits) | SDK docs; search extract | quotes verified (SDK docs); orders **unverified**; the order's "10 orders / sec" matches only the search extract |
| Websocket feed | SFeed `native_batch`: `wss://sfeed.kotaksecurities.com/apifeed` (SDK default; the SDK may learn a data-centre URL from Kotak's config service), JSON auth frame (`user` = sid, `auth` = trade token), answer `message_code` 1117 / 1119 with a divider per exchange; JSON subscribe frames (`subscribeScripsLite`, `subscribeIndices`, ...); binary little-endian batched packets | SDK `websocket/feed/client.py`, `protocol.py` | verified (SDK); never run against the live feed |
| Option chain | `option-chain?exchange=nse_fo&underlying=NIFTY&expiry=YYYY-MM-DD` -> `call[]` / `put[]` with strike, LTP, OI and OI change; no Greeks | SDK docs `option_chain.md` | verified (SDK docs) |
| History | `historical/details?neosymbol=seg\|tok&interval=1min..60min\|D\|W&fromdate&todate` -> `[ts, o, h, l, c, v, oi]`; per-interval range limits not in figures | SDK docs `historical_data.md` | verified (SDK docs); range limits unverified |
| Scrip master | daily CSV per segment; `nse_cm-v1.csv` / `bse_cm-v1.csv` in `transformed-v1`, the others in `transformed`; columns `pSymbol`, `pTrdSymbol`, `pSymbolName`, `pExpiryDate` (NSE F&O seconds from 1980, +315511200), `pOptionType` (CE / PE / XX), `dStrikePrice;` (paise) | SDK docs `scrip_master.md`, `services/scrip_search.py` | verified (SDK); `lLotSize`, `dTickSize` column names and units **unverified** (parsed defensively, tick defaults to 0.05) |
| Positions | quantities in units (the docs divide by `lotSz` only to show lots); average and P&L by the documented formula | SDK docs `positions.md` | verified (SDK docs) |
| F&O order quantity | sent in units (ATP's convention) | - | **unverified** (KN-3) |
| Session end time | not published | - | **unverified** (KN-4); 06:00 IST default |
| Charges | Trade Free plans: "Effective 1st November 2025, all API orders placed under the Trade Free plans on the app will attract no per-order brokerage and API usage charges." Statutory charges apply (modelled by date in `india_costs.py`) | https://www.kotakneo.com/about-us/media-and-press/kotak-neo-introduces-zero-brokerage-zero-fee-trade-apis-for-retail-traders/ (search extract) | **unverified**; BO square-off leg charges not modelled (BO is not supported by the SDK) |

What could not be verified at all: the static-IP page and FAQ text, the market-order rule's exact wording, the order
rate limit, the session end time, the scrip master's lot / tick columns, the live feed (no account), the brokerage
page. Each is on the KN list in `docs/OPEN_QUESTIONS.md`; each must be re-read on the broker's own page, with the date,
before a gate opens.

## 3. Design

* **Adapter** `app/brokers/kotak_neo.py` (`KotakNeoBroker`, registry name `kotak_neo`), on the existing patterns: TOTP
  from `totp.py` (Angel One / Shoonya), the scrip master through `master_cache` (Angel One), the contract-symbol wrapper
  for F&O (attribute match: underlying, expiry, strike, right). The trade session (token, sid, base URL, UCC, data
  centre) is one string `kn1.<base64 JSON>` in `BrokerCredentials.access_token`, so `run_authentication` /
  `store_access_token` keep it encrypted with no new column and no migration.
* **Gates** `app/brokers/gates.py`: `GATES["kotak_neo"] = (live_orders closed, market_data closed)`; `LiveOrdersClosed`
  is a clear rejection (`BrokerOrderRejected`: nothing reached the broker), `MarketDataClosed` a plain refusal; neither
  says "token", so neither is mistaken for an expired session. The catalog row carries `gates` for the UI.
* **Price sources.** A tenant may connect Kotak Neo beside Upstox; the organisation-level price picks (data hub,
  charts, Greeks, portfolio marks, deployment previews, the worker's read-only broker) skip a row whose market-data gate
  is closed (`gates.data_sources`), so Kotak never displaces the working source. Identity for every other broker.
* **TM-3** `app/execution/marketable_limit.py`: `BrokerCapabilities.market_orders_allowed` (False for Kotak Neo);
  `to_venue_order` turns a MARKET order into a LIMIT a band past the reference (outward to the tick); the band is the
  order's `protection_pct` (the user's `market_protection_pct`), else the platform's stated default 0.5% (the same as
  PROTECTED_LIMIT); `reprice_until_filled` follows an unfilled limit from fresh references (attempts and wait from the
  caller), never cancels. Stops: `stop_market=False`, so a stop goes as SL with its limit a band past the trigger (the
  existing `stop_order_params`). The compliance rule-set maps algo MARKET entries to a marketable limit for
  `kotak_neo` (`IN-SEBI.order_type.policy`).
* **PAPER parity.** `PaperExecutionAdapter(venue="kotak_neo", venue_plan=...)` applies the same `to_venue_order`, takes
  Kotak's capabilities (stops as SL) and the stated brokerage. Test: a practice MARKET order and the LIVE transform
  produce the same LIMIT price.
* **Charges** `app/brokers/broker_charges.py`: rows of (broker, plan, channel, from, Rs per order, source, read on,
  verified). Only what the broker states; no row = the user's own brokerage setting. Kotak Neo Trade Free API: Rs 0 from
  1 Nov 2025 (unverified).
* **Rate budget** `rate_budget.py`: `kotak_neo` 3 / s, burst 5, 120 / min (a third of the order figure, as for the
  others). No per-day bucket: Kotak publishes no daily cap (TM-27 is Groww's).
* **Feed** `app/market_data/kotak_feed.py` (`KotakNeoTickStream`): the SDK's auth and subscribe frames and a decoder
  for the mini touch line, the market picture and the index packets; `stream_for` returns it only with the data gate
  open (never today).

## 4. Static IP (for the lawyer and the broker)

Kotak Neo (search extract of its static-IP page): order APIs only from a whitelisted static IP; the API session created
from that same IP; up to 10 family members per IP; only the parent account changes it. ATP runs on one server with one
egress IP. Routing many unrelated users' orders through that one IP would break the "family of 10" model and is the
TM-5 question for the lawyer and Kotak Neo. **Nothing in this PR routes any user's LIVE order through ATP's IP** (the
gate refuses every order). The question is in `docs/DATA_SOURCES.md` "For the lawyer" item 3 (the A6.3 lawyer list takes
it over when it is written; the static-IP rule itself is `IN-SEBI.static_ip.registered` in `docs/COMPLIANCE_IN.md`).

## 5. Tests

`backend/tests/test_part1b_kotak_neo.py` on recorded fixtures (`tests/fixtures/kotak_neo/recorded.json`, the SDK's
documented shapes, made-up values, tokens built at run time): login and session, refused login / session, the stored
session, token lifecycle, the gates (no request leaves for an order or a price), account reads with the gates closed,
the wire format with the gates opened in the test, TM-3 band and re-pricing, symbol mapping, feed decoder and auth,
charges, catalog, budget, policy, PAPER parity. `tests/test_p2a_broker_connect.py`: the connect / renew flow through the
API and the ui_v2-only tile. Frontend `src/broker/BrokerScreens.test.tsx`: the tile and the guide.

## 6. Opening a gate (later, not in this PR)

1. The written confirmation (lawyer's opinion for TM-5; Kotak Neo's letter for TM-5 and TM-6) filed with the owner.
2. Every "unverified" row above re-read on Kotak's own page, with the date.
3. TM-6: `broker_data_terms.json` and `docs/BROKER_DATA_TERMS.md` row to `confirmed`; then `GATES["kotak_neo"].market_data`.
4. TM-5: `GATES["kotak_neo"].live_orders`, with the static-IP model the lawyer approves; LIVE stays behind the
   existing switches (`live_trading`, `phase0_paper_only`, the step-up) as for every broker.
