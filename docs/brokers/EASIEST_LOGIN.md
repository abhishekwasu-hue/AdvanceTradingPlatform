<!-- BRK-1 (owner order 2 Nov 2026): this page replaces the P2a version of 21 Oct (in git history). Change it together with backend/app/brokers/catalog.py. -->

# Easiest official login per broker - research (checked 2026-11-02)

Scope: the 9 brokers in backend/app/brokers/catalog.py. Only official pages were used: the broker's own docs, support, notice
board or blog, its own GitHub org, and its own community forum, which is marked where used. "Not stated (checked <url>)" means
the page was read and did not say it. Field keys are those in field_specs.py / catalog.py.

Screenshots (question 4): **NO for all 9.** None of the pages checked grants a reuse licence. Upstox shows "Copyright © 2026,
Upstox". Zerodha shows "Copyright © 2015 - 2025, Zerodha Technology Pvt. Ltd.". Fyers shows "© 2026 FYERS Securities Pvt. Ltd.
All Rights Reserved.". The Dhan, Angel One, Shoonya, Kotak, Groww and CoinDCX pages state no licence. ATP must draw its own
illustrations.

---

## 1. Upstox - Analytics Token (ATP keeps this; no Upstox OAuth)

- **Method:** Analytics Token. It is a long-lived, read-only token generated on the Developer Apps page, with "no authorization
  redirect".
- **Validity:** "expiry period of 1 year from the date of generation". Only one Analytics Token per account at a time.
- **Inputs:** the token only (`analytics_token`).
- **Scope:** GET (read) APIs only: "does not support trading operations".
- **Fees:** "The Analytics Token is free to use and does not carry any cost."
- **Static IP:** not needed for Charges, Margins, Market Quote, Historical Data, Option Chain, Market Information,
  Fundamentals, News, IPO and Websocket. A static IP is required for User, Payments, Orders, GTT, Portfolio, Mutual Fund and
  Trade P&L: "Whitelist your server static IP in the Developer Apps console first."
- **Market orders:** not applicable, since the token cannot place orders.

Guide:
1. Sign in to Upstox and open Developer Apps: https://account.upstox.com/developer/apps
2. Open the **Analytics** tab: https://account.upstox.com/developer/apps#analytics
3. Click **Generate Token**.
4. Click **Confirm** in the dialog.
5. Click the copy icon next to the token. This gives **`analytics_token`**.
6. Paste it into ATP.

Sources: https://upstox.com/developer/api-documentation/analytics-token/ (checked 2026-11-02).

---

## 2. Dhan - longest validity is still 24 h; a TOTP auto-login now exists

- **Methods (official):**
  - (a) **Web token:** web.dhan.co → My Profile → "Access DhanHQ APIs" → generate an access token, valid 24 h. This is what
    ATP does today.
  - (b) **TOTP programmatic:** `POST https://auth.dhan.co/app/generateAccessToken` with query params `dhanClientId`, `pin`
    (6-digit Dhan PIN) and `totp`. It needs TOTP enabled and returns `accessToken` and `expiryTime`, which is 24 h after
    generation.
  - (c) **API key + secret (consent):** create the key under "Access DhanHQ APIs" → toggle "API key" with app name,
    Redirect URL and optional Postback URL. The **key is valid 12 months**. The flow is:
    - `POST /app/generate-consent?client_id=` with headers `app_id` and `app_secret` returns `consentAppId` (max 25 per day).
    - The browser opens `https://auth.dhan.co/login/consentApp-login?consentAppId=…`, and the user logs in with 2FA.
    - The browser gets a 302 to the Redirect URL with `tokenId`.
    - `GET /app/consumeApp-consent?tokenId=` with the same headers returns `accessToken` and `expiryTime`. The token duration
      is not stated (checked the authentication page).
  - (d) **Partner flow:** needs `partner_id` and `partner_secret` from Dhan, applied for on dhanhq.co.
  - (e) **RenewToken:** `GET https://api.dhan.co/v2/RenewToken` extends a web-generated, still-active token by 24 h and
    invalidates the old one.
- **Longest validity:** "the validity of the API Access Token has now been updated to 24 hours" (support FAQ). No longer-lived
  token is stated.
- **Easiest for ATP:** (b) TOTP. ATP stores `client_id`, `pin` and `totp_secret` once, then one click per day, with no paste.
  It is the same pattern as Angel One.
- **Fees:** "Trading APIs are free for all Dhan users." The Data API costs "₹499 plus applicable taxes per month" and renews
  every 30 days. The free-with-trades offer has ended.
- **Static IP:** "Order Placement, Modification and Cancellation APIs requires Static IP whitelisting". It is not required for
  fetching order or trade details. Primary and secondary IPs, IPv4 or IPv6, can be set. "Each individual needs a unique IP."
  The IP cannot be changed for 7 days. It is set at web.dhan.co or through `POST /v2/ip/setIP`.
- **Market orders:** a Dhan post on MadeForTrade (Dhan's forum, 13 Mar 2026) says "Market orders will no longer be allowed
  through APIs as per the regulatory framework". API market orders are converted to limit orders with MPP. The order docs list
  MARKET as an orderType and do not state any conversion (checked https://dhanhq.co/docs/v2/orders/).

Guide (TOTP way):
1. Sign in at https://web.dhan.co
2. Open **My Profile** → **DhanHQ Trading APIs** (the docs also call it "Access DhanHQ APIs").
3. Click **Setup TOTP**.
4. Enter the OTP sent to your mobile or email.
5. Copy the **key** shown under the QR code. This gives **`totp_secret`**, the long key and not the 6-digit code.
6. Add the same key to an authenticator app, then enter its first 6-digit code to confirm.
7. Note your Dhan Client ID (shown on My Profile). This gives **`client_id`**.
8. Type your 6-digit Dhan PIN into ATP as **`pin`**. It is not a broker-console value.
9. For orders, add your server's static IP under the same APIs page. This needs `copy: static_ip`.

Guide (today's ATP paste way):
1. Sign in at https://web.dhan.co
2. Open **My Profile** → **Access DhanHQ APIs**.
3. Generate an access token (24 h) and copy it. This gives **`access_token`**.
4. Copy your Client ID. This gives **`client_id`**.

Sources (checked 2026-11-02):
- https://dhanhq.co/docs/v2/authentication/
- https://dhan.co/support/platforms/dhanhq-api/what-is-the-maximum-validity-of-an-api-access-token-in-dhan-apis/
- https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/
- https://dhanhq.co/docs/v2/orders/
- https://madefortrade.in/t/dhanhq-update-safer-participation-of-retail-investors-in-algo-trading-static-ip-mpp-rate-limits/61689

---

## 3. Zerodha (Kite Connect) - OAuth redirect with request_token

- **Method:** open `https://kite.zerodha.com/connect/login?v=3&api_key=xxx`. "A successful login comes back with a
  request_token as a URL query parameter" to the app's registered redirect URL. Optional `redirect_params` are echoed back.
  The page names no other callback params.
- **Exchange:** `POST /session/token` with `api_key`, `request_token` and `checksum` = "SHA-256 hash of
  (api_key + request_token + api_secret)". The request_token lifetime is "only a few minutes".
- **Validity:** the access token expires "at 6 AM on the next day (regulatory requirement)", or earlier on logout or a master
  logout from Kite Web.
- **Prerequisite:** an active Zerodha account with 2FA TOTP enabled.
- **Fees:**
  - **Personal** is free, with "No historical or real-time data included".
  - **Connect** is "₹500 per app each month".
  - From Oct 2026, paid users with ₹2,000 or more in brokerage in a month get 500 Developer Credits for the next month.
- **Static IP:** "effective 1 April 2026, you must have a static IP for API-based order placement". At least one primary IP
  and up to two. "Exchange rules allow just one modification per calendar week." Other APIs (WebSocket, orderbook, positions)
  work from any IP. It is set under Profile (top-right) → **IP Whitelist** → **Update**.
- **Market orders:** allowed with `market_protection`: "Market protection converts market orders to limit orders" and applies
  to MARKET and SL-M. ATP already sends it (zerodha.py `apply_market_protection`).

Guide:
1. Open https://developers.kite.trade/login and sign up or log in. This needs a Zerodha account with TOTP on.
2. Create a new app. The form fields are not described on the docs page checked.
3. In the app's **Redirect URL** box, paste ATP's callback (`/api/broker/zerodha/oauth/callback` on the ATP host). This is
   `copy: redirect_url`.
4. Copy the **API key**. This gives **`api_key`**.
5. Copy the **API secret**. This gives **`api_secret`**.
6. Open **Profile** (top-right) → **IP Whitelist**, enter the server's static IP and click **Update**. This is
   `copy: static_ip`, for orders only.
7. Each morning, click Log in in ATP and sign in on Kite. ATP receives `request_token` through the callback.

Sources (checked 2026-11-02):
- https://kite.trade/docs/connect/v3/user/
- https://kite.trade/docs/connect/v3/
- https://kite.trade/docs/connect/v3/orders/
- https://support.zerodha.com/category/trading-and-markets/kite-web-and-mobile/kite-api/articles/what-are-the-charges-for-kite-apis
- https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip

---

## 4. Fyers - OAuth redirect with auth_code

- **Method:** `GET https://api-t1.fyers.in/api/v3/generate-authcode` with params:
  - `client_id`, the App ID
  - `redirect_uri`, URL-encoded and the same as registered
  - `response_type=code`
  - `state`
  - `nonce` appears in the sample URL but is not documented.
- **Callback:** the redirect "carries an auth_code". `state` is returned unchanged. The docs also list `s` and `code`, but the
  exact query string is not shown.
- **Exchange:** `POST /api/v3/validate-authcode` with `grant_type=authorization_code`, `appIdHash` = SHA-256 of
  `app_id:app_secret`, and `code` = the auth_code. It returns `access_token`, plus `refresh_token` in the sample.
- **Validity:** "Access tokens expire daily at 6:30 AM." A refresh token is "active for 15 days" (support). Whether a PIN is
  needed is not stated (checked the support article).
- **Fees:** "The Standard plan is free". Prime is paid, and its amount is not stated on the plans page.
- **Static IP:** one **Trading** app per user, where the Primary IP is mandatory and the secondary is optional. Non-trading apps
  are data-only and need no IP. The IP cannot be changed for 7 days.
  - Old apps have been "Data-Only mode from April 1, 2026".
  - The new trading app is the default **"Algo trading app"**, and its new **App ID ends with 200**.
- **Market orders:** "Market orders will be automatically converted to MPP (Market Price Protection) orders."
- **Third-party platforms:** the same notice says "External platforms must be empanelled and hosted within the broker's
  infrastructure to continue placing orders."

Guide:
1. Open the FYERS API Dashboard: https://fyers.in/web/api-dashboard/user-apps
2. Under **My apps**, click **Algo trading app** (Type: Trading). For data only, click **Create non-trading app** instead.
3. Enter the **Primary IP** (mandatory) and an optional Secondary IP. This is `copy: static_ip`.
4. Enter the **Redirect URL**: paste ATP's callback (`/api/broker/fyers/oauth/callback`). This is `copy: redirect_url`.
5. Select the required permissions.
6. Accept the API Terms and Conditions.
7. Click **Activate**.
8. Copy the new **App ID**, which ends with -200. This gives **`api_key`**.
9. Copy the **Secret**. This gives **`api_secret`**.
10. Each morning, click Log in in ATP and sign in on Fyers. ATP receives `request_token` (the auth_code).

Sources (checked 2026-11-02):
- https://fyers.in/api-docs/v3/api/authentication/
- https://fyers.in/api-docs/v3/api/authentication/individual-app/generate-auth-code
- https://fyers.in/api-docs/v3/api/authentication/individual-app/validate-auth-code
- https://fyers.in/api-docs/v3/getting-started/create-an-app/individual-app/
- https://fyers.in/api-docs/v3/getting-started/plans-and-limits/
- https://support.fyers.in/portal/en/kb/articles/how-do-i-activate-the-new-app-for-api-trading-after-april-1-2026
- https://support.fyers.in/portal/en/kb/articles/what-is-the-function-of-the-refresh-token-in-fyers
- https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026
- https://myapi.fyers.in/docsv3, which returns a 301 redirect to fyers.in/api-docs/v3/getting-started/

---

## 5. Angel One (SmartAPI) - TOTP programmatic login

- **Method:** `POST https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword` with body:
  - `clientcode`
  - `password`, which is the PIN ("Your_pin")
  - `totp`
  - `state`, which is optional
- **Headers:** the API key (X-PrivateKey) and the client IP and MAC headers.
- **Refresh:** `generateTokens` is used "on expire".
- **Validity:** "The session established via SmartAPI remains active till 12 midnight, unless the user chooses to log out."
  The FAQ says the same: "tokens are valid till 12 midnight".
- **Inputs:** `api_key`, `client_id`, `pin`, `totp_secret`. The optional `public_ip`, `local_ip` and `mac` are sent as
  headers.
- **Fees:** FAQ: "No, … this service is entirely free of cost, even for historical data."
- **Static IP:** effective 01-Apr-2026, "API order execution will only be accepted if it originates from your registered primary
  static IP". It is registered in the SmartAPI dashboard: "Log in, select 'Add App,' and enter your Primary Static IP".
  - The form says "Primary IP can be updated once in a week only". A Secondary IP is optional.
  - Docs: "For users utilizing the new static IP–based API key", Place, Modify and Cancel orders are validated against that
    IP.
- **Market orders:** the notice says "Market orders and IOC orders are now strictly prohibited for algorithmic trading." The
  docs say "All the market orders will be converted into limit orders by AngelOne as per the MPP limits".

Guide:
1. Open https://smartapi.angelone.in/signup to register, or https://smartapi.angelone.in/signin if you already have an
   account.
2. Click **+ ADD APP**.
3. Enter the **App Name**.
4. Enter the **Redirect URL** (any URL you own; the TOTP login does not use it).
5. Optionally, enter the **Postback URL**.
6. Enter the **Primary Static IP**, and optionally the Secondary Static IP. This is `copy: static_ip`.
7. Click **Add**.
8. Copy the **API Key** shown for the app. This gives **`api_key`**.
9. Open **Enable TOTP**: https://smartapi.angelone.in/enable-totp
10. Enter your **Angel One Client ID**. This gives **`client_id`**.
11. Enter your **PIN**, then click **Login**. This is **`pin`**.
12. Enter the OTP sent to your registered email and mobile, then click **Proceed**.
13. Click **"Can't Scan ? Copy the key"** and copy the key. This gives **`totp_secret`**, the long key and not the 6-digit code.
14. Optionally, add the same key to an authenticator app.

Sources (checked 2026-11-02):
- https://smartapi.angelone.in/docs/User. This page is a JavaScript app. The text was read from its official bundle
  https://smartapi.angelone.in/static/js/main.2b886565.js, which holds the User, FAQ, Orders, Enable TOTP and Add App
  strings.
- https://www.angelone.in/news/market-updates/what-s-changing-in-angel-one-s-smartapi-access-from-april-1-2026

---

## 6. Shoonya (Finvasia) - official docs now say OAuth ONLY (conflicts with ATP)

- **Method (official, now):** Quick Start: "Shoonya currently supports OAuth-based login only" and there is no TOTP auto-login
  flow.
  - The browser opens `https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=…` (the IPv6 variant is on
    trade.shoonya.com). The user enters User ID, Password and OTP/TOTP on Shoonya's page.
  - Shoonya redirects to the registered redirect URL with `code` (short-lived, single-use).
  - `POST https://api.shoonya.com/NorenWClientAPI/GenAcsTok` (`jData=` with `code` and `checksum` =
    SHA256(client_id + secret_key + auth_code)) returns `susertoken`, `uid` and `actid`.
- **Conflict:** the TOTP Setup Guide still says TOTP lets scripts "log in without manual OTP entry" and links to an
  "auto-login-totp" page. That page returned **HTTP 403** and could not be read.
- **Validity:** not stated (checked the manual-authentication-oauth and quick-start pages).
- **Inputs (OAuth):** Client ID and Secret Code, both from **API Key Generation**, plus the registered redirect URL.
- **Fees:** not stated (checked the pages listed below).
- **Static IP:** "Before you can complete the OAuth login flow or call any endpoint, you need to whitelist the static IP". This
  covers **login and every call**, not only orders. It is set under profile icon → **API Key Generation** → **Primary IP
  Address**, optional Backup IP, **Update**. A change limit is not stated.
- **Market orders:** not stated (checked rate-limits and ip-whitelisting).
- **TOTP secret:** web terminal → **Profile → Security → TOTP Setup**. The base32 key under the QR code "is shown only once".

Guide (OAuth, as documented):
1. Sign in to the Shoonya web terminal. The docs give no URL for it.
2. Click your profile icon.
3. Open **API Key Generation**.
4. Enter the server's **Primary IP Address**, and optionally a Backup IP. This is `copy: static_ip`.
5. Click **Update**.
6. Copy the **Client ID**. In ATP this maps closest to `client_id`, and there is no OAuth field yet.
7. Copy the **Secret Code**. This has no ATP field yet.
8. Register ATP's redirect URL. The docs do not say where; they mention only a "registered redirect URL". This is
   `copy: redirect_url`.
9. TOTP, if still needed: open **Profile → Security → TOTP Setup** and copy the base32 key **once**. This gives
   **`totp_secret`**.

Sources (checked 2026-11-02):
- https://www.shoonya.com/api-documentation
- https://shoonya.com/api-documentation/quick-start
- https://www.shoonya.com/api-documentation/authentication-flow-overview
- https://www.shoonya.com/api-documentation/manual-authentication-oauth
- https://www.shoonya.com/api-documentation/totp-setup-guide
- https://www.shoonya.com/api-documentation/ip-whitelisting
- https://www.shoonya.com/api-documentation/prerequisites
- https://www.shoonya.com/api-documentation/rate-limits
- https://www.shoonya.com/api-documentation/auto-login-totp, which returned 403 and was not read

---

## 7. Kotak Neo - TOTP + MPIN programmatic login

- **Method:**
  - `POST https://mis.kotaksecurities.com/login/1.0/tradeApiLogin`. Headers: `Authorization: <access token>`,
    `neo-fin-key: neotradeapi`. Body: `mobileNumber`, `ucc`, `totp`. It returns the view `token` and `sid`.
  - `POST …/login/1.0/tradeApiValidate` with `mpin`. It returns the trading `token`, `sid` and `baseUrl`.
- **Inputs:**
  - the access token from the API Dashboard (ATP calls it consumer key, `api_key`)
  - the registered mobile number with +91 (`mobile_number`)
  - the UCC, a "5-character client code" under Profile → Client Code (`client_id`)
  - the 6-digit MPIN (`pin`)
  - `totp_secret`
- **Validity:** not stated (checked the getting-started.md and the Trade API page).
- **Fees:** "free of cost at present".
- **Static IP:** from 1 April 2026. It applies only to Place, Modify and Cancel Order. "Login, report, portfolio, data, and
  websocket APIs work from any IP."
  - Primary and optional secondary IPs. Change once every 7 days, and a new session is required after a change.
  - Path: More → Trade API → Create API Application → **Add IP**.
- **Market orders:** "As per SEBI regulations, market orders are not allowed for retail algo trading". They are converted to a
  limit with an LTP band, and options orders are rejected if no LTP is available. ATP already sets market_orders_allowed=False.

Guide:
1. Open the Kotak Neo app or web: https://www.kotakneo.com/platform/kotak-neo-trade-api/
2. Go to **More** → **Trade API** → **API Dashboard**. The product page says "Invest > Trade API > API Dashboard".
3. Click **Create Application** (the IP page calls it "Create API Application").
4. Copy the token shown. This gives **`api_key`**.
5. Click **Add IP** and enter the primary static IP. This is `copy: static_ip`, for orders only.
6. Click **TOTP Registration**, in the top-right of the API Dashboard. Web link:
   https://www.kotakneo.com/platform/kotak-neo-trade-api/totp-registration/
7. Enter the registered mobile number, then the OTP, then click **Proceed**.
8. Copy the **setup key** shown with the QR code. This gives **`totp_secret`**, not the 6-digit code. The web TOTP page
   mentions only the QR, while the GitHub guide says "Scan the QR code or copy the setup key".
9. Enter the 6-digit code from the authenticator app, and wait for "TOTP successfully registered".
10. Under Profile, copy the **Client Code**. This gives **`client_id`**.
11. Type your registered mobile number with +91 (`mobile_number`) and your MPIN (`pin`) into ATP.

Sources (checked 2026-11-02):
- https://www.kotakneo.com/platform/kotak-neo-trade-api/
- https://www.kotakneo.com/platform/kotak-neo-trade-api/static-ip-details/
- https://www.kotakneo.com/platform/kotak-neo-trade-api/totp-registration/
- https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/getting-started.md

---

## 8. Groww - TOTP key (official pages disagree on daily approval)

- **Methods:** `POST https://api.groww.in/v1/token/api/access`, with three approaches:
  - (1) **Access token**, generated on the page: "(Expires daily at 6:00 AM)".
  - (2) **API key + secret**: `key_type: "approval"`, `checksum` = SHA256(secret + timestamp), and a `timestamp` valid
    10 minutes. "Requires daily approval on Groww Cloud Api Keys Page."
  - (3) **TOTP**: `key_type: "totp"`, `totp`. The python-sdk page labels it **"No Expiry"**, while the curl page heads it
    "**Requires daily approval** on Groww Cloud Api keys page". The two official pages conflict, and this is not resolved.
- **Validity:** the day's token expires daily at 6:00 AM. The token endpoint allows 150 requests per 24 h.
- **Inputs (TOTP way):** `api_key` (the TOTP token) and `totp_secret`.
- **Fees:** an active "Trading API Subscription" is required. The price is "₹499 + taxes per month", shown as an "EARLY BIRD
  OFFER" against ₹2000. It is bought at https://groww.in/user/profile/trading-apis.
- **Static IP:** "all API-based order placements must come from a trusted and pre-approved source". Set it on the API Keys page
  with **Add static IP**, Primary and optional Secondary, then **Update**. The effective date and change limit are not stated.
- **Market orders:** not stated (checked groww.in/trade-api, the curl docs and the static-IP blog).

Guide:
1. Buy the Trading API subscription: https://groww.in/user/profile/trading-apis
2. Open https://groww.in/trade-api/api-keys and log in.
3. Open the dropdown on **Generate API key**.
4. Click **Generate TOTP token**.
5. Enter a name and click **Continue**.
6. Copy the **TOTP token**. This gives **`api_key`**.
7. Copy the **TOTP secret**. This gives **`totp_secret`**, the long key and not the 6-digit code. The docs do not state whether
   it is shown only once.
8. Click **Add static IP** and enter the Primary Static IP. This is `copy: static_ip`.
9. Click **Update**.

Sources (checked 2026-11-02):
- https://groww.in/trade-api/docs/python-sdk
- https://groww.in/trade-api/docs/curl
- https://groww.in/trade-api
- https://groww.in/blog/static-ip-api-trading-setup

---

## 9. CoinDCX - API key + secret

- **Method:** headers `X-AUTH-APIKEY` and `X-AUTH-SIGNATURE`, the HMAC-SHA256 hex digest of the JSON body (with a timestamp)
  keyed by the secret.
- **Validity:** a key expiry is not stated (checked docs.coindcx.com). ATP treats the key as valid until it is deleted.
- **Fees:** "presently no fees or charges are levied by CoinDCX for the use of CoinDCX API". CoinDCX may charge later.
- **Static IP:** optional. "Bind IP Address to API key" ties the key to that IP.
- **Market orders:** allowed. Per-market `min_market_orders_qty` and `max_quantity_market` apply, with a maximum of 25 open
  orders per market.
- **Secret shown once:** the docs say only "Store the Key and Secret visible on the screen". The official help page says "The
  Secret key is forever hidden after you refresh the screen", but that page returned 403 to the fetch and the wording comes
  from a search extract.

Guide:
1. Open the API Dashboard (Profile → API dashboard): https://coindcx.com/api-dashboard
2. Click **Create API key**.
3. Enter a label.
4. Tick **Bind IP Address to API key** only if ATP's server IP is fixed. This is optional, `copy: static_ip`.
5. Enter the email OTP and the SMS OTP.
6. Copy the **Key**. This gives **`api_key`**.
7. Copy the **Secret** now, because it is hidden after refresh. This gives **`api_secret`**.

Sources (checked 2026-11-02):
- https://docs.coindcx.com/
- https://coindcx.com/api/help/API%20Dashboard/Generation%20of%20Key%20and%20Secret, which returned 403 and was read only
  through a search extract

---

## Conflicts with ATP code today

1. **Shoonya:** shoonya.py uses the password + TOTP QuickAuth flow (NorenWClientTP). Shoonya's Quick Start now says OAuth only
   (client_id, secret code, redirect `code`, GenAcsTok). The static IP is needed even for login.
   - catalog.py tells users to get the API key at "prism.shoonya.com". The official docs say web terminal → profile → API Key
     Generation.
2. **Dhan:** ATP asks the user to paste a 24 h token every day. Dhan officially offers a TOTP token API (`client_id` + `pin` +
   TOTP), so a one-click `totp_auto` is possible. No longer-lived token is stated.
3. **Angel One:** angel_one.py maps MARKET to `ordertype=MARKET` (capabilities default `market_orders_allowed=True`). Angel's
   notice says market orders are "strictly prohibited for algorithmic trading", and its docs say they are converted to limit
   orders through MPP.
   - The session ends at **12 midnight**. ATP's EASIEST_LOGIN.md says "About 24 hours".
4. **Fyers:**
   - field_specs.console_url `https://myapi.fyers.in/dashboard` is outdated. The dashboard is now
     https://fyers.in/web/api-dashboard/user-apps.
   - The example "like XB12345-100" is old: the new trading App ID ends with -200, and -100 apps are data-only since
     1 Apr 2026.
   - The notice requires external platforms to be "empanelled and hosted within the broker's infrastructure".
5. **Zerodha:** the free Personal plan has no market data. ATP's Kite data features need the ₹500 per month Connect plan.
6. **Groww:** the official pages conflict on whether the TOTP key needs daily approval. The price is ₹499 + taxes per month
   (early bird).
7. **Upstox:** field_specs.py still describes upstox as `LOGIN_OAUTH` ("Log in once each morning", api_key and api_secret
   required unless analytics_token is set). catalog.py uses the Analytics Token. The token is read-only: User and Portfolio
   calls need a static IP and are outside its no-IP list.
8. **Kotak Neo:** ATP's adapter and catalog match the official docs. The session validity is not stated anywhere.
