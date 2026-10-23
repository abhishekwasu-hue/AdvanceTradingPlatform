# Easiest official login per broker (P2a)

The aim: connecting a broker takes ONE paste and less than a minute. For each broker the platform supports
(`backend/app/brokers/registry.py`), this page names the official method with the fewest steps and the longest
validity, the fields that method needs (and nothing else), and the official source. The catalogue served by
`GET /api/broker/catalog` is `backend/app/brokers/catalog.py`. Change this page and that file together.

**Date checked:** 2026-10-21.

**How it was checked.** From this build environment, the brokers' documentation pages could not be opened directly:
the page fetcher could not resolve the hosts, and a direct HTTPS request was refused by the network proxy (403). The
web search tool did work. So each choice below rests on two things: (a) what web search returned from the brokers'
official pages and official support/FAQ pages on the date above, and (b) what the existing adapters in this repository
already implement and document. Where a detail came only from a community forum, this page says so. No method was
invented. Before relying on a detail marked *forum only*, open the official page and confirm it.

## Summary

| Broker | Method (`method`) | Fields asked | Validity | Daily renewal | Official source |
|---|---|---|---|---|---|
| Upstox | Analytics Token (`analytics_token`) | Analytics Token | 1 year | No | https://upstox.com/developer/api-documentation/analytics-token/ |
| Dhan | Paste the web token (`access_token`) | Client ID, access token | 24 hours | Yes: paste the new token | https://dhanhq.co/docs/v2/authentication/ |
| Zerodha (Kite) | Login page + pasted address (`login_code`) | API key, API secret | Until the early morning of the next day | Yes: log in, paste the address | https://kite.trade/docs/connect/v3/user/ |
| Fyers | Login page + pasted address (`login_code`) | App ID, Secret ID, Redirect URL | Until 06:30 the next day | Yes: log in, paste the address | https://myapi.fyers.in/docsv3 |
| Angel One | TOTP login (`totp_auto`) | SmartAPI key, client code, PIN, TOTP secret | About 24 hours | Yes: one click | https://smartapi.angelbroking.com/docs |
| Shoonya (Finvasia) | TOTP login (`totp_auto`) | User ID, password, API key, TOTP secret | One trading day | Yes: one click | https://www.shoonya.com/api-documentation |
| CoinDCX | API key + secret (`api_key_secret`) | API key, API secret | Until revoked | No | https://docs.coindcx.com/ |
| Kotak Neo (practice only, `ui_v2`) | TOTP login (`totp_auto`) | Consumer key, mobile number, client code (UCC), MPIN, TOTP secret | One trading day (end time not published) | Yes: one click | https://www.kotakneo.com/platform/kotak-neo-trade-api/ |

## Upstox: the Analytics Token (default)

- **Method.** Sign in to Upstox, open Developer Apps, open the Analytics tab, generate the token, paste it. No OAuth
  redirect and no API key or secret are needed.
- **Validity.** One year from generation. Only one Analytics Token per account at a time. It is read-only: GET calls
  only. It cannot place, modify or cancel orders.
- **What it covers in ATP.** Market data and streaming (charts, the scanner, the option chain, the reader) and therefore
  PAPER trading. ATP does not take this from the docs on trust: the read-only probe in
  `backend/app/brokers/upstox_analytics.py` checks each market-data class when the token is saved.
- **Connected means connected.** With only an Analytics Token whose probe works, the connection is `connected` with
  `data_only: true`. There is no "login needed" state for data.
- **Second option, for LIVE orders later:** a pasted daily access token (it dies at 03:30 IST the next day). Connect also
  accepts it in place of the analytics token. The catalogue does not ask for it.
- **Not the default:** OAuth (the API key, secret and redirect login) still works through the existing routes.
- **Telling the two tokens apart.** A token pasted in either box is stored as what it is.
  1. Both kinds of token are JWTs. The token's own `exp` and `iat` claims are read, without trusting them for
     authentication. A lifetime of 7 days or more means an Analytics Token. A lifetime of 2 days or less means a daily
     token. A daily token lives at most about 27.5 hours. An `exp` that has already passed is refused as expired.
  2. When the claims say nothing, two read-only GETs settle it. These cases are a non-JWT token, no `exp` or no `iat`,
     or a lifetime between the two bounds. Both `/v2/user/profile` and one quote GET are sent.
     - A daily token reads both.
     - Upstox refuses the profile to an Analytics Token unless the token is used from a registered static IP, but the
       quote still works.
     - Quote works and profile refused: an Analytics Token. Profile works and quote refused: a daily token.
     - Both work: the box the token was pasted into decides, and the default box is the Analytics Token.
     - Both refuse: the token is rejected. If Upstox does not answer clearly, nothing is stored and the user is asked to
       try again.
  - Search did not find the exact claims Upstox puts in an Analytics Token, so no claim name is relied on. Only the
    standard `exp` and `iat` are used.
- **Sources:** https://upstox.com/developer/api-documentation/analytics-token/ and
  https://upstox.com/developer/api-documentation/api-overview (as returned by search).

## Dhan: paste the access token from Dhan Web

- **Method.** On web.dhan.co, open My Profile, then Access DhanHQ APIs. Generate an access token and paste it with the
  Client ID. These are the two fields the adapter needs (`backend/app/brokers/dhan.py`).
- **Validity.** 24 hours, as stated in the official docs and the Dhan support FAQ.
- **Renewal.** Paste the new token. Renew takes the token only; the Client ID stays stored. When the pasted token is
  a JWT with an `exp` claim, the stored session ends at that `exp` if it comes before the daily schedule. The claim is
  read for the expiry only.
- **Not used yet:** Dhan also documents a RenewToken call (it extends an active web token by another 24 hours) and an
  API key and secret consent flow. The existing adapter supports neither. Both are candidates for removing the daily
  paste later.
- **Sources:** https://dhanhq.co/docs/v2/authentication/ and
  https://dhan.co/support/platforms/dhanhq-api/what-is-the-maximum-validity-of-an-api-access-token-in-dhan-apis/

## Zerodha (Kite Connect): login page and the pasted address

- **Method.** Store the app's API key and API secret once. Each morning, "Log in" opens the Kite login page through the
  existing `GET /api/broker/zerodha/login-url`. After login, the browser lands on the app's redirect URL. The user pastes
  that whole address, or just the `request_token`. ATP exchanges the token for the day's access token
  (`backend/app/brokers/zerodha.py`).
- **Validity.** Kite has no long-lived token for retail apps. The access token lasts one trading day and is cleared early
  the next morning; ATP assumes 06:00 IST (`TOKEN_DAILY_EXPIRY_IST`). One forum post gives 07:30, so the exact time is
  *forum only*. The request token is single-use and lasts a few minutes (*forum only*).
- **Why two fields.** The exchange needs both the key and the secret. There is no token the user can copy from a console.
- **Sources:** https://kite.trade/docs/connect/v3/user/ (the official login-flow page, linked from the forum; it did not
  open from here) and the kite.trade forum threads on access-token expiry.

## Fyers: login page and the pasted address

- **Method.** The same as Kite. Store the App ID and the Secret ID. Each morning, log in on the Fyers page (the existing
  `GET /api/broker/fyers/login-url`) and paste the address. ATP exchanges the `auth_code`
  (`backend/app/brokers/fyers.py`).
- **Why three fields.** Fyers' login page needs the redirect URL registered on the app, so it must be stored. Fyers' docs
  advise using a redirect URL you control, not a public page. A Fyers-hosted page that staff suggested on the forum is
  for development only, so it is not used as a default.
- **Validity.** The access token expires daily at 06:30 (official docs, as returned by search). Fyers also issues a
  15-day refresh token (official support article). Using it needs the account PIN, and the existing adapter does not
  support it. Also, the repository's compliance rule `IN-SEBI.login.daily` (docs/COMPLIANCE_IN.md) says no reliance on
  refresh tokens. So it is not used.
- **Sources:** https://myapi.fyers.in/docsv3, https://fyers.in/api-docs/v3/api/authentication/ and
  https://support.fyers.in/portal/en/kb/articles/what-is-the-function-of-the-refresh-token-in-fyers

## Angel One (SmartAPI): TOTP login

- **Method.** Store the SmartAPI key, client code, PIN and TOTP secret once. This is the base32 key shown when TOTP is
  enabled, not the six-digit code. Each login then makes the current code from the secret
  (`backend/app/brokers/totp.py`) and calls `loginByPassword` (`backend/app/brokers/angel_one.py`).
- **Daily renewal.** One click: renew sends no fields.
- **Validity.** About 24 hours (SmartAPI team on the forum; *forum only*). The login needs a fresh TOTP each time.
- **Why four fields.** That is what the SmartAPI login takes, and it is the only way to log in daily without pasting.
- **Considered and not chosen:** the "publisher login" page (`publisher-login?api_key=...`, which redirects with an
  `auth_token`). It appears only in a forum quote of the docs, and the official page could not be opened from here, so it
  was not adopted. It would need one field plus a daily paste.
- **Sources:** https://smartapi.angelbroking.com/docs (cited by the adapter and the forum) and the smartapi.angelone.in
  forum.

## Shoonya (Finvasia): TOTP login

- **Method.** Store the user ID, password, API key and TOTP secret once. Each login makes the current code from the
  secret and calls `QuickAuth` (`backend/app/brokers/shoonya.py`). The vendor code defaults to `<user ID>_API`, as the
  adapter documents, so it is not asked for.
- **Daily renewal.** One click: renew sends no fields. The session token (`susertoken`) a login returns is stored
  encrypted and reused until the daily expiry, so other calls do not log in again.
- **Why four fields.** Shoonya has no token to paste. Its login takes the user ID, the password, a TOTP and the app key.
- **Sources:** https://www.shoonya.com/api-documentation and the official wrappers https://github.com/Shoonya-Dev/ShoonyaApi-py
  and https://github.com/Shoonya-Dev/ShoonyaApi-js. The API key is generated at prism.shoonya.com (third-party guide,
  docs.openalgo.in). Search shows the password and app-key hashing differ between sources. The existing adapter's
  scheme was kept unchanged.

## CoinDCX: API key and secret

- **Method.** Create an API key in the CoinDCX API dashboard and paste the key and the secret. The secret is shown only
  once. They do not expire on a clock (`PERMANENT_KEY_BROKERS` in `backend/app/brokers/token_lifecycle.py`).
- **Sources:** https://docs.coindcx.com/ (cited by the adapter; the page did not open from here, and search returned
  only third-party guides on creating keys).

## Open questions for the owner

- **Fully automatic daily TOTP login.** For Angel One and Shoonya, the platform could log in by itself each morning
  from the stored TOTP secret, with no click. The repository's rule `IN-SEBI.login.daily` asks for a daily 2FA login per
  broker session, so this was not built. Renewing is one click instead.
- **Dhan RenewToken and the Fyers refresh token** would remove a daily paste, but they need adapter work and a decision
  on the same rule.

## Kotak Neo: the TOTP login (Part 1B, checked 2026-10-23)

- **Method.** The official SDK `kotakneoapi` 3.0.9 (https://pypi.org/project/kotakneoapi/, 15 Oct 2026;
  https://github.com/Kotak-Neo/kotak-neo-python): the consumer key from the NEO app (More, Trade API, generate an
  application) on every call; a login with the registered mobile number, the UCC and the current TOTP, then the MPIN.
  ATP makes the TOTP from the stored secret, so each morning is one click, as for Angel One and Shoonya.
- **Why five fields.** The broker's own login needs all five; none can be derived from another.
- **Practice only.** Real orders and live prices through Kotak Neo stay off until Kotak Neo confirms in writing
  (`backend/app/brokers/gates.py`, `docs/design/BROKER_KOTAK_NEO.md`); the tile is shown with `ui_v2` on.

