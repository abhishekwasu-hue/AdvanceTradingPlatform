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

Per-broker instrument / connection caps (Tier A universe) are to be added here with each broker's confirmation.

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
