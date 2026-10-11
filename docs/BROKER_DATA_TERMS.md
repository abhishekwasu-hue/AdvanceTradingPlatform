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
- Every cache entry written for a session is keyed by that session (`md:byob:u<user>.c<credential>:...`): ticks,
  candles and the session's symbol set. The shared, platform-wide caches are not read or written for Tier A reads.
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
