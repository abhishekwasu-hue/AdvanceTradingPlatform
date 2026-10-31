# Broker data terms and "Trade in your broker app" links

Owner-maintained record required by the beta launch spec (`docs/BETA_LAUNCH_SPEC.md` §2 and §4a). Two questions per
broker:

1. **Data terms** - may ATP show that broker's market data to the broker's own users inside ATP (Tier A, bring your
   own broker)? A broker's adapter is shown to users only when its status here is "confirmed". Keep the written
   confirmation (the email) referenced in the notes column.
2. **"Trade in your broker app" link** (Phase 0, PW5) - does the broker publish a link that opens a pre-filled
   order or basket in the user's own broker app or site, where the user reviews and places it? In Phase 0 ATP places
   **no order through any broker API**; the link only opens the broker's own page.

Status words: **launch list** (owner decision from published terms, §2), **execution-only** (terms forbid display on
third-party platforms), **second wave - written confirmation pending**. For links: **verified format** (implemented),
**not verified** (no link is shown; a URL is never guessed).

| Broker | Data terms status (§2) | "Trade in your broker app" link | Notes |
| --- | --- | --- | --- |
| Upstox | Launch list | Not verified | Official community reply: market data may be distributed to Upstox users because each call needs that user's own access token; no extra approval. No public pre-filled order link recorded yet. |
| Dhan | Launch list | Not verified | Algo-platform partner programme (apply as partner); live data needs the user's own Data API subscription. No public pre-filled order link recorded yet. |
| Zerodha | Execution-only (Kite Connect terms forbid display on third-party platforms) | **Kite Publisher basket - verified format, needs the publisher api_key** | See "Zerodha Kite Publisher" below. Users see Tier B/C data in ATP and their own app for live prices. |
| Angel One (SmartAPI) | Second wave - written confirmation pending | Not verified | |
| Fyers | Second wave - written confirmation pending | Not verified | API terms allow platforms for other Fyers clients with approvals, but restrict using exchange data to build different charting/technical tools - written consent before enabling charts/screeners on Fyers data. |
| Groww | Second wave - written confirmation pending | Not verified | |
| Kotak Neo | Second wave - written confirmation pending | Not verified | |
| ICICI Breeze | Second wave - written confirmation pending | Not verified | |
| 5paisa | Second wave - written confirmation pending | Not verified | |
| Shoonya / Finvasia | Second wave - written confirmation pending | Not verified | |
| Alice Blue | Second wave - written confirmation pending | Not verified | |

## Tier A data registry (BT1) - the table the code reads

`backend/app/market_data/broker_data_terms.json` is the machine-readable copy of this table; the entitlement guard
(`backend/app/market_data/entitlement.py`) reads it, and `backend/tests/test_bt1_byob_data_tier.py` fails when the two
disagree. Change a row only together with the JSON, and a status to **confirmed** only with the broker's written
confirmation referenced in the table above.

- **confirmed** - the broker's live data is shown to that broker's own user in ATP (Tier A), through the user's own
  session only. Phase 0: Upstox; Dhan (the user's own Dhan Data API subscription is needed - without it Dhan refuses
  the market-data call and the user sees EOD).
- **execution-only** - the broker's API terms forbid showing its market data on a third-party platform. Its users see
  Tier C (EOD) in ATP and use their own broker app for live prices. Phase 0: Zerodha.
- **pending** - second wave; written confirmation not yet received. Treated exactly like execution-only until then.
  A broker missing from this table is treated as pending.

Per-connection symbol cap = the broker's own limit on instruments per market-data connection, from its documentation or
written confirmation. **to confirm** = not recorded yet: then only the ATP per-session budget applies
(`BYOB_SESSION_SYMBOL_CAP`, default 50 - the per-session figure of the beta capacity model, spec §4, not a broker
limit). When a broker's cap is recorded, the lower of the two applies. A number is never guessed here.

| Key | Broker | Data status | Per-connection symbol cap | Needs the user's own data subscription |
| --- | --- | --- | --- | --- |
| `upstox` | Upstox | confirmed | to confirm | no |
| `dhan` | Dhan | confirmed | to confirm | yes |
| `zerodha` | Zerodha | execution-only | to confirm | no |
| `angel_one` | Angel One | pending | to confirm | no |
| `fyers` | Fyers | pending | to confirm | no |
| `groww` | Groww | pending | to confirm | no |
| `kotak_neo` | Kotak Neo | pending | to confirm | no |
| `icici_breeze` | ICICI Breeze | pending | to confirm | no |
| `five_paisa` | 5paisa | pending | to confirm | no |
| `shoonya` | Shoonya / Finvasia | pending | to confirm | no |
| `alice_blue` | Alice Blue | pending | to confirm | no |

### Why Zerodha is execution-only

The owner's decision from Zerodha's published terms (beta spec §2: Kite Connect states that its market data may not be
displayed on third-party platforms). A per-user Kite session does not change that: each call would carry the user's own
token, but ATP would still be the third-party platform displaying the data. So a Zerodha user's broker session is never
used for display in ATP; the user sees EOD data here and live prices in Kite. The "Trade in your broker app" basket
link (below) is not market data and stays available. This record was written without network access: before changing
the row, record the exact clause (document title, section, date read), and a change needs Zerodha's written permission
referenced here.

### Isolation rules the code enforces (beta spec §2)

- One user's session = the broker credential that user linked themselves (`broker_credentials.user_id`). Credentials
  are stored per tenant; a teammate's credential in the same tenant is never used for another user's data.
- Every cache entry written for a session is keyed by that session (`md:byob:u<user>.c<credential>:...`): its ticks,
  its per-request candle cache and the session's symbol set; no other session reads them.
- LD-10 (owner, 20 Oct): candles and prices are public market data. After the guard has admitted a symbol for the user
  (Tier A, own session, inside the budget), the bars come from one copy shared by every user of the *same broker*
  (`md:shared:<broker>:...`, `app/market_data/shared_candles.py`): the broker fetch that fills it goes through the
  requesting user's own session and budget, and another broker's bars are never served. `/ltp` reads the shared live
  feed's tick for the same broker before it calls the user's broker. Nothing per account is shared.
- A per-user websocket stream never feeds platform-wide consumers (the lake recorder); nothing is pooled.
- Symbols per session: open positions > open charts > watchlist > screener universe, up to the cap; beyond it the
  symbol is EOD for that user (a symbol already held keeps its slot; the latest scan wins among screener symbols), with
  a slower refresh hint.
- BT1b: every live read in ATP (screener runs and alert rules, Copilot evidence and tools, portfolio prices, Greeks,
  deployment previews, the PAPER exit price) goes through the same guard; organisation-wide stores (market memory,
  sentiment, the OI banner collector, option-chain recording, the org quote stream) are never fed from a per-user
  session. The credential's holder is the person who linked it and logs in to it; with the data tier on, another member
  cannot log in to it or keep its secrets / token by storing over it, and its smoke test is the holder's. In both modes a
  login never writes its token onto a row that changed hands during the broker round-trip.

## Zerodha Kite Publisher (basket)

- **What it is.** Kite Publisher (Kite Connect developer docs, "Kite Publisher" / offsite order execution) lets a site
  open Kite with one or more orders pre-filled; the user logs in to Kite, reviews and places them. The order is the
  user's own action in Zerodha's app - nothing is sent to an order API by ATP.
- **Format used.** An HTML form `POST` to `https://kite.zerodha.com/connect/basket` with two fields: `api_key` (the
  publisher's Kite Connect api_key) and `data`, a JSON array of orders. ATP sends one order:
  `{"variety": "regular", "tradingsymbol": "<SYMBOL>", "exchange": "NSE"|"BSE", "transaction_type": "BUY"|"SELL",
  "order_type": "MARKET", "quantity": <whole number>, "readonly": false}` (`readonly: false` = the user can still
  change the order in Kite). The product (MIS/CNC) is left for the user to choose in Kite.
- **Scope in Phase 0.** Only an EXIT_POSITION proposal on an open position, only plain NSE/BSE cash equity (ATP's
  symbol is the exchange trading symbol there; derivatives need Kite's own contract symbol, which ATP does not
  carry - no link rather than a wrong one), only a whole quantity.
- **Configuration.** `KITE_PUBLISHER_API_KEY` (backend environment, empty by default; a public per-app identifier
  that sits in the page's HTML form, not a secret, but never committed with a value). Empty key = no Zerodha link.
  Feature flag `broker_app_links` (Admin -> Feature flags), **off by default**.
- **Before turning the flag on:** re-check the format against the current Kite Connect documentation (this record was
  written without network access) and that Zerodha's terms allow a publisher key to be used by a platform for its
  users; note the date of the check here.
- **Code.** `frontend/src/proposals/brokerLinks.ts` (pure; unit tests in `brokerLinks.test.ts`),
  `backend/app/ai/proposal_card.py` (the closing order's facts, sent only with the flag on).

## Adding a broker's link

Record the broker's public documentation (title, section, date read) and the exact format here first, mark it
"verified format", then add a case to `brokerAppLink()` with a test for the payload. Until then the broker returns
no link.

## M71 PL-5 - which data option each broker's terms allow (checked 2026-10-31)

Owner order M71 PL-5 asks, for a SaaS: **(1)** each user's own broker data for that user's own use **plus** the owner's
data credential feeding the shared store (indices / universe) for every user; or **(2)** a licensed market-data vendor
for all users. This section records what the brokers' published terms and docs say on each part. It is a research
record, **not legal advice**: every "allowed" below still needs a lawyer's check before LIVE and before any data is
shared across users.

**How it was checked.** Web research on 2026-10-31. The broker sites (upstox.com, kite.trade, support.zerodha.com) could
not be opened directly from the research session (network policy), so the facts come from web-search extracts of the
URLs listed; the wording in quotes is the extract's wording and must be re-read on the page itself (title, section,
date) before a status in the registry above changes. Nothing here changes the registry: a status changes only with the
broker's written confirmation (rule above).

Words used: **allowed** = the terms / an official reply say so; **not allowed** = the terms / an official reply forbid
it; **unclear** = no text found, or official sources disagree.

### Market-wide rule that applies to every broker

| Point | What was found | Source (checked 2026-10-31) |
| --- | --- | --- |
| SEBI: real-time price data to third parties | Circular SEBI/HO/MRD/MRD-PoD-3/P/CIR/2024/56 (24 May 2024, in force from 23 Jun 2024): exchanges, clearing corporations, depositories and registered market intermediaries (brokers) "refrain from sharing real-time price data with any third parties", except where needed for the orderly functioning of the market or regulatory needs, and then under formal agreements. A later amendment allows price data for investor education with a time lag (sources disagree: one day vs 30 days). | https://nsdl.co.in/downloadables/pdf/2024-0067-Policy-SEBI_Circular_on_Norms_for_sharing_of_real_time_price_data_to_third_parties.pdf ; https://nsearchives.nseindia.com/content/circulars/COMP62283.pdf |
| NSE: delayed data | NSE's 15-minute delayed data tariff: real-time vendors may show data delayed 15 minutes or more on their own terminal / software for display without extra fees; delayed data given as **feeds** to clients needs NSE Data & Analytics' written consent. | https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Download15mindelayeddatatariff.pdf |
| NSE: real-time redistribution | Redistributing NSE real-time data needs an NSE data-vendor licence (NSE Data & Analytics price list; domestic pricing file of Mar 2026 lists vendor fixed fees, e.g. "25,50,000 (INR per annum)" for L1 Capital Market, plus per-user monthly fees; which fee applies to which user type is not clear from the extract). | https://nsearchives.nseindia.com//web/mediaattachment/2026-03/NSE_Pricing_file_-_Domestic_clients_20260309171343.pdf |
| NSE: authorised vendors | NSE publishes the list of authorised data vendors. | https://nsearchives.nseindia.com/web/sites/default/files/inline-files/List_of_Authoized_Vendors.pdf |
| BSE | No BSE data-licence text was found in this research. | not found |
| ATP's own bhavcopy (EOD) | Daily / weekly bars come from the official NSE bhavcopy already ingested (M71 PL-1a); the terms for showing EOD data to all users were not part of this check. | not checked |

### Upstox

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data in a third-party SaaS for that user | allowed | Community reply: distributing market data to Upstox users is permitted because the endpoints need that user's Upstox access token; no extra approval. Another reply: "You are permitted to utilize the data provided by the Upstox API for analysis purposes and to execute orders through the app. We recommend that only Upstox customers consume data via your platform." | https://community.upstox.com/t/api-usage-clarification/13250 ; https://community.upstox.com/t/commercialization-of-application-based-on-api-data/4469 |
| Storing it | unclear | No text found. | - |
| Showing it to other users (redistribution) | not allowed / unclear | Upstox reply on the business product: "we do not provide market data for commercial purposes". The recommendation above limits consumers to Upstox customers. | https://community.upstox.com/t/api-usage-clarification/13250 |
| One owner credential feeding a shared store for many users | not allowed (as read) / needs written confirmation | Each call needs the consuming user's own token (the reason given for "allowed" above); a shared store fed from one token serves users who are not that token's holder. | as above |
| WebSocket limits | published | Market Data Feed V3 (normal): 2 connections per user; per connection LTPC 5000, Option Greeks 3000, Full 2000 instrument keys (a second column lists 2000 / 2000 / 1500 - not explained in the extract). Upstox Plus: 5 connections; Full D30 up to 50 instruments per connection. Community: the connection limit is per client, not per app. | https://upstox.com/developer/api-documentation/v3/get-market-data-feed/ ; https://upstox.com/developer/api-documentation/announcements/websocket-plus/ |
| REST / historical limits | published, figures to re-check | Owner research R33 (M71 order): standard APIs incl. historical candles 50/s, 500/min, 2,000 per 30 min; orders 10/s, 500/min, 2,000/30 min. Older community replies: 25/s, 250/min, 1,000/30 min "for order placement and other standard APIs"; exceeding blocks API access temporarily. The official page could not be opened; PL-2 uses the official page's figures. | https://upstox.com/developer/api-documentation/rate-limiting/ ; https://community.upstox.com/t/regarding-api-calling-limit/13282 |

### Zerodha Kite Connect

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data in a third-party SaaS for that user | not allowed | FAQ: displaying or redistributing Kite Connect API data on external platforms violates exchange data vending policies; "Kite Connect is an order execution platform, not a data distribution service." Forum (Zerodha staff): live data on a website is ruled out; personal use only. | https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs ; https://kite.trade/forum/discussion/4870/show-realtime-streaming-data-on-website-or-application |
| Storing it | unclear | No text found. | - |
| Showing it to other users | not allowed | Staff: live data cannot be redistributed; buy it from an exchange-authorised vendor. Violations can lead to suspension. | https://kite.trade/forum/discussion/comment/33547/ ; https://kite.trade/forum/discussion/14068/will-sebi-new-norms-for-sharing-real-time-share-price-data-with-third-parties-affect-us |
| One owner credential feeding a shared store | not allowed | Same clauses. | as above |
| WebSocket limits | forum figures | Up to 3000 instruments per connection, up to 3 connections per api_key (staff: soft limit; more are blocked). | https://kite.trade/forum/discussion/1868/limits-of-historic-data-subscription-and-websocket-calls |
| REST / historical limits | forum figures | Historical 3 requests/s; quote 1 request/s (owner research R33 adds orders 10/s, 200/min, 3,000/day). The official "exceptions / rate limits" page could not be opened. | https://kite.trade/forum/discussion/257/api-rate-limiting ; https://kite.trade/docs/connect/v3/exceptions/ |

### Angel One SmartAPI

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data for that user | unclear | No terms text on third-party display was found. | - |
| Storing / showing to others / shared store | unclear | Not found. | - |
| WebSocket limits | forum figure | "The total limit/quota of token subscriptions is 1000 per WebSocket session"; each token + mode pair counts separately. An unofficial source: up to 3 connections per client ID (not found in Angel One's own docs). | https://smartapi.angelone.in/topic/4478/issue-with-tick-data-when-large-number-of-tokens-are-subscribed/27 |
| REST / historical limits | not found | - | - |

### Dhan (DhanHQ)

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data for that user | allowed with the user's own Data API subscription (registry: confirmed) | Data API costs ₹499 + taxes per month per user; the free-with-trades offer was discontinued. Algo-platform partner programme (registry note). | https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/ |
| Storing / showing to others / shared store | unclear | No redistribution text found. | - |
| WebSocket limits | published (client docs) | Up to 5 sockets per user, 5000 instruments per socket; up to 100 instruments per subscribe message. | https://pypi.org/project/dhanhq/1.3.3 |
| REST / historical limits | unofficial | A third-party client hard-codes historical 1 request/s; Data API 5/s and 100,000/day. To confirm on DhanHQ's docs. | https://docs.rs/crate/dhan-rs/0.1.7/source/src/constants.rs |

### Fyers

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data for that user | unclear (allowed with approvals) | API terms: exchange data only for lawful trading purposes; a client may build platforms for other Fyers clients only after the required exchange and statutory approvals; restriction on using exchange data to build different charting / technical tools (registry note). | https://fyers.in/terms-and-conditions-api |
| Storing it | not allowed (to redistribute) | Terms prohibit scraping, building databases or keeping cached copies with intent to redistribute. | https://fyers.in/terms-and-conditions-api |
| Showing to others / shared store | not allowed | Same clauses. | as above |
| WebSocket / REST limits | not found | - | - |

### Groww

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data / storing / others / shared store | unclear | Terms text not found (general terms page only). API costs ₹499 + taxes per month (listing). | https://groww.in/terms-and-conditions ; https://www.chittorgarh.com/broker/groww/api-for-algo-trading-review/173/ |
| REST limits | secondary sources | Live data 10/s and 300/min; orders 15/s and 250/min; non-trading 20/s and 500/min. | https://www.chittorgarh.com/broker/groww/api-for-algo-trading-review/173/ ; https://groww.in/blog/api-trading-on-groww |
| WebSocket limits | secondary source | Up to 1000 subscriptions at once. | https://www.chittorgarh.com/broker/groww/api-for-algo-trading-review/173/ |

### Kotak Neo

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data / storing / others / shared store | unclear | No terms text found. | - |
| WebSocket limits | published (support page) | Maximum 200 scrips per subscription request. | https://www.kotakneo.com/support/what-is-the-maximum-number-of-scrips-which-can-be-subscribed-to-in-a-websocket-simultaneously/ |
| REST limits | not found | - | - |

### Shoonya (Finvasia)

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| Every question, WebSocket and REST limits | unclear / not found | No official Finvasia terms or limits were found in this research. | - |

### CoinDCX (crypto - not NSE / BSE)

| Question | Verdict | Quote / fact | Source |
| --- | --- | --- | --- |
| User's own data / storing / others / shared store | unclear | "API License Terms govern the use of any 'Market Data' and Application Programming Interface (API) of CoinDCX"; the redistribution clause itself was not in the extract. Indian law, Mumbai courts. | https://docs.coindcx.com |
| REST limits | published (partial) | Spot limits per endpoint per 60 s, e.g. Create Order 2000, Cancel All 30, Active Order 300. | https://docs.coindcx.com |

### Answer per option

**Option (1) - own data for the user's own use + the owner's credential feeding the shared store.**

- *Own data for that user's own use:* Upstox and Dhan (with the user's own Data API subscription) are recorded as
  allowed (registry: confirmed). Zerodha: not allowed on a third-party platform (execution-only). Fyers: only with
  exchange / statutory approvals. Angel One, Groww, Kotak Neo, Shoonya, CoinDCX: unclear (pending).
- *The shared part (one owner credential feeding a store that every user reads):* **no broker's published terms were
  found to allow it.** Zerodha and Fyers forbid it in writing; Upstox's own reason for allowing per-user use (each call
  carries that user's token) does not cover a shared store, and an Upstox reply says it does not provide market data
  for commercial purposes; all others: unclear. Add the SEBI 2024 circular (brokers may not share real-time price data
  with third parties). So the shared live / intraday part of option (1) must not be switched on for other users until
  the broker confirms it in writing and a lawyer has checked it. What stays possible inside option (1) without that:
  each user's live data from his own confirmed broker for himself; the existing LD-10 shared copy only among users of
  the same broker, each fetch through the requesting user's own session (already in the code); EOD / weekly from the
  NSE bhavcopy.
- *Owner's own use:* the owner's credential feeding a store that only the owner reads is his own use (same as any
  user).

**Option (2) - a licensed market-data vendor for all users.** This is the route the brokers themselves point to
(Zerodha staff: buy from an exchange-authorised vendor). Vendors found:

| Vendor | NSE licence | Public pricing found | Source |
| --- | --- | --- | --- |
| TrueData | Says it is an authorised data vendor for NSE, BSE, MCX | Prices depend on exchange, symbols and data type; exchange fees separate. A Fyers bundle page lists a Level 1 tick API at Rs 1,599 + GST per month (undated); a comparison dated 14 Oct 2026 says the Velocity plans (from ₹1,699/month) exclude vendor API access. No published price for redistribution in a web app. | https://truedata.in/market-data-apis ; https://subscriptions.fyers.in/true-data.html ; https://onetradejournal.com/trading-apis/real-time-market-data-cost |
| Global Financial Datafeeds (Global Datafeeds) | On NSE's authorised-vendor list (CM, F&O, currency, index) | Quoted individually (no public API price). | https://nsearchives.nseindia.com/web/sites/default/files/inline-files/List_of_Authoized_Vendors.pdf |
| Accelpix | On NSE's authorised-vendor list | No public price; the API key is issued by the data vendor. | https://pypi.org/project/pix-apidata/1.2.6/ |

On top of the vendor's own fee, showing real-time NSE data to many users needs NSE's redistribution licence (vendor
or the redistributor, per NSE's price list); data delayed 15 minutes or more is cheaper and may be shown on a vendor's
own software, but given as feeds it needs NSE's written consent.

### What is not known

- The exact current clauses of Upstox, Angel One, Dhan, Groww, Kotak Neo, Shoonya and CoinDCX on storing data or
  showing it to other users (no terms text found or opened).
- Whether a SaaS with users of many brokers needs an NSE licence even for option (1), given the SEBI 2024 circular.
- The vendor's price for redistribution inside a web app with many users (no vendor publishes it), and which NSE fee
  line applies to ATP.
- Official REST / historical limits for Kite, Dhan, Angel One, Groww, Kotak Neo and Shoonya (only forum or third-party
  figures above); Upstox's current page figures (R33 vs older replies). PL-2 must read each official page before a
  number goes into a budget.
- BSE's data-licence terms.

**Needs a lawyer's check before LIVE and before any data is shared across users.** Provisional decision: OPEN_QUESTIONS
M71Q-2.
