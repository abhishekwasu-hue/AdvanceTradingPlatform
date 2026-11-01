# M74 - "Continue with your broker" sign-in (design)

Order: docs/owner-orders/ATP_ORDER_M74.md (BL-1..BL-7). Builds ON the M73 provider-generic social login (SL-1..SL-7,
branch `claude/m73-social-login`): one provider registry, one `user_identities` table, one link / unlink page. M74 adds
providers of kind `broker:<id>`; it does not add a second provider layer.

## 1. Audit of what ATP has today (code on main)

| Piece | Where | What it does |
|---|---|---|
| Adapters | `backend/app/brokers/registry.py` (`BROKER_ADAPTERS`) | 9 brokers: upstox, zerodha, fyers, dhan, angel_one, shoonya, kotak_neo, groww, coindcx. `authenticate()` exchanges a one-time code (Upstox, Kite, Fyers) or logs in (TOTP brokers); `get_profile()` returns `BrokerProfile(user_id, name, email)`. |
| Connect catalogue | `backend/app/brokers/catalog.py`, `docs/brokers/EASIEST_LOGIN.md` | Per broker the easiest way with the user's OWN app / keys (analytics token, pasted token, login code, TOTP, key + secret). |
| Daily OAuth | `backend/app/brokers/routes.py` `_oauth_start` / `_oauth_callback`; `token_lifecycle.py` | Upstox, Kite, Fyers: start -> broker login -> `/api/broker/<id>/oauth/callback`. Signed state (JWT, 10 min) + single-use nonce hash inside the encrypted row; code exchanged with the user's own key + secret. |
| Session store | `BrokerCredentialRecord` (`broker_credentials`, unique tenant + broker + label), `store_access_token`, `cap_expiry_at_token_exp` | Payload encrypted per tenant (`encrypt_text`, `PURPOSE_BROKER_CREDENTIAL`); `token_status` VALID until the broker's daily expiry (`TOKEN_DAILY_EXPIRY_IST`). |
| Sign-in | `backend/app/auth/google.py`, `google_routes.py` (GS-1) | Google OIDC + PKCE + state cookie; `_complete_login` (refresh cookie, MFA step, audit, login event); new users through `new_owner_account` with the register form's rules. M73 makes this provider-generic. |

Gap: every broker login today uses the USER's own developer app. Sign-in needs ATP's own platform app per broker (one
API key + one registered redirect URL, each user authorising his own session). Nothing for that exists yet.

## 2. BL-4 capability table

Checked 2026-11-01. The brokers' documentation hosts do not open from this build environment (DNS refused by the
session's network policy; the same as on 2026-10-21, see `docs/brokers/EASIEST_LOGIN.md`), so each cell rests on the
web-search extract of the cited page on that date plus what the adapter in this repo already implements. *forum* = only a
community / forum source said it; confirm on the official page before relying on it.

| Broker | Redirect login | Email from login / profile | Token lifetime | Refresh | Daily re-login | Platform / partner programme | Status for sign-in |
|---|---|---|---|---|---|---|---|
| Upstox | Yes, OAuth code: `api.upstox.com/v2/login/authorization/dialog`, `state` echoed [U1] | Yes: token response carries the profile; `/v2/user/profile` has `email`, `user_name`, `user_id` (adapter `upstox.py:224`) [U1] | Until 03:30 IST next day [U1] | No (extended token is read-only) [U1] | Yes | Multi-client app needs Upstox's business / partner arrangement: "Business Trading API - Get in touch" form or Upstox's API support e-mail; multi-client automated trading for others needs algo-vendor empanelment *forum* [U2] | **Start** (after approval) |
| Zerodha (Kite) | Yes: `kite.zerodha.com/connect/login?v=3&api_key=..&redirect_params=..` -> `request_token` [Z1] | Yes: session / profile have `user_id`, `user_name`, `email` (adapter `zerodha.py:119`) [Z1] | Until about 06:00 IST next day *forum* [Z1] | No for retail apps *forum* [Z2] | Yes | Apps are single-user by default; multi-user only after a case-by-case compliance review: Kite Connect support e-mail *forum* [Z2] | **Start** (after approval) |
| Fyers | Yes: `api-t1.fyers.in/api/v3/generate-authcode` with `state` -> `auth_code`; official "third-party app" type for platforms where other clients log in [F1][F2] | Yes: `/api/v3/profile` has `fy_id`, `name`, `email_id` [F3] | Until 06:30 next day [F2] | Refresh token exists *forum*; not used (`IN-SEBI.login.daily`) [F4] | Yes | "Third-party app" on myapi.fyers.in; approval criteria not published [F2] | **Start** (after approval) |
| Dhan | Yes, partner consent: `partner/generate-consent` -> `auth.dhan.co/login/consentApp-login` -> `tokenId` -> `partner/consume-consent` [D1] | No email; returns client id, client name, UCC, expiry [D1] | 24 hours [D1] | No | Yes | DhanHQ partner integration (partner_id / partner_secret via the form on dhanhq.co) [D1] | **Start later**: field names differ between the doc's own samples [D1]; built when the partner credentials arrive and a sandbox call confirms them |
| Angel One | Publisher login redirect returns `auth_token` / `feed_token` in the query [A1] | No: getProfile email is blank *forum* [A2] | Up to 28 hours [A1] | No | Yes | No published platform / multi-client programme found [A1] | Own keys only |
| Shoonya (Finvasia) | No OAuth; vendor code + TOTP login [S1] | Login returns `email` (adapter `shoonya.py:143`) | One trading day | No | Yes | Vendor code from prism.shoonya.com, per user [S1] | Own keys only |
| Kotak Neo | No redirect URL registration documented; TOTP + MPIN login [K1] | No | One trading day (end time not published) | No | Yes | None found; third-party platforms use the client's own credentials [K1] | Own keys only |
| Groww | No documented third-party OAuth; TOTP key or daily approval [G1] | No | Until 06:00 next day | No | Yes | None found [G1] | Own keys only |
| CoinDCX | No; API key + secret | No | Until revoked | n/a | No | None | Own keys only |

Sources (all checked 2026-11-01):
[U1] https://upstox.com/developer/api-documentation/get-token and https://upstox.com/developer/api-documentation/authentication ·
[U2] https://community.upstox.com/t/clarification-on-multi-user-access-api-usage-and-algo-vendor-registration-requirements/13377, https://community.upstox.com/t/access-to-the-multi-client-api-as-a-partner-of-upstox/5858 ·
[Z1] https://kite.trade/docs/connect/v3/user/ ·
[Z2] https://kite.trade/forum/discussion/15842/how-do-i-provide-access-to-multiple-users-to-kite-connect-api-via-my-app, https://kite.trade/forum/discussion/comment/52854/ ·
[F1] https://fyers.in/api-docs/v3/api/authentication/third-party-app/generate-access-token/ ·
[F2] https://fyers.in/api-docs/v3/getting-started/authentication/ ·
[F3] https://fyers.in/api-docs/v3/api/account/profile/ ·
[F4] https://fyers.in/community/t/facing-issues-when-trying-to-refresh-token-to-get-new-token/21850 ·
[D1] https://dhanhq.co/docs/v2/authentication/ ·
[A1] https://smartapi.angelbroking.com/docs · [A2] https://smartapi.angelone.in/smartapi/forum/topic/5298/issues-with-oauth-callback-and-missing-fields-in-getprofile-endpoint/1 ·
[S1] https://www.shoonya.com/api-documentation, https://docs.openalgo.in/connect-brokers/brokers/shoonya ·
[K1] https://www.kotakneo.com/platform/access-to-kotak-neo-trade-api/, https://faq-neo.kotaksecurities.com/support/solutions/articles/82000893244 ·
[G1] https://groww.in/trade-api/docs/python-sdk ·
SEBI retail-algo framework (OAuth only, 2FA per session, vendor-specific key + static IP, exchange empanelment of algo
providers): https://zerodha.com/z-connect/general/a-comprehensive-overview-of-nses-circular-on-the-new-retail-algo-trading-framework,
https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail_Algo_03112025_NSE.pdf

**Start now (code in this PR): Upstox, Zerodha, Fyers.** Dhan next (partner flow, after confirmation). Angel One,
Shoonya, Kotak Neo, Groww, CoinDCX: own keys only (today's connect flow after sign-in, unchanged).

## 3. Design

### 3.1 Provider (BL-1, BL-5) - `backend/app/auth/broker_signin.py`
- One `BrokerSignInProvider` per starting broker, `kind = "broker:<id>"`, registered in the M73 provider registry next to
  `google` / `facebook`.
- ATP's platform app comes only from the environment: `BROKER_SIGNIN_<ID>_API_KEY`, `BROKER_SIGNIN_<ID>_API_SECRET`,
  `BROKER_SIGNIN_<ID>_APPROVED` (`1` once the broker has approved the platform app - only the owner knows that).
  `configured()` = all three set. `configured_brokers()` is what the sign-in page's provider list reads (M73
  `/api/auth/providers`); a broker not configured is absent - no "coming soon" (BL-1).
- The code exchange reuses the existing adapter (`authenticate()` then `get_profile()`), with the platform key + secret,
  so one exchange path serves both sign-in and the daily connect. Result: `BrokerIdentity(kind, subject = broker user id,
  email, name, access_token)`; the token is excluded from `repr` and never logged.
- Callback paths (register exactly these on the platform apps): `/api/auth/broker/upstox/callback`,
  `/api/auth/broker/zerodha/callback`, `/api/auth/broker/fyers/callback`. Separate from the own-keys connect callbacks
  (`/api/broker/<id>/oauth/callback`), which stay unchanged.
- State / PKCE: none of the three brokers documents PKCE, so the M73 flow cookie (signed, HttpOnly, SameSite=Lax,
  10 minutes) carries the `state` and the provider kind; Upstox and Fyers echo `state`, Kite returns it through
  `redirect_params`. A mismatch, a missing cookie or a second use ends in the sign-in page's "expired" message.

### 3.2 Flow (BL-2)
start -> broker login page -> callback -> state check -> code exchange -> `BrokerIdentity` -> M73 `user_identities`
lookup by (`broker:<id>`, subject):
- linked: sign in that user (same `_complete_login`: refresh cookie, MFA challenge when on, login event, audit row);
- not linked, email returned and an account uses it: M73 link step (password / MFA confirmation, never silent);
- not linked, no account: the register form's rules (terms, plan, beta gate, 2FA) exactly as email sign-up; a broker
  that returns no email (Dhan later) asks for one and verifies it (M73's no-email path).
- After the ATP session exists (and after the MFA step when on), `store_signin_session()` stores that day's broker
  session through the existing store: `broker_credentials` row, encrypted per tenant, `token_status` VALID until the
  broker's daily expiry (capped at a JWT `exp`), holder = the signed-in user, account row ensured. The row keeps the
  platform API key (needed on every call: Kite / Fyers send `key:token`) and `platform_app: true`; the platform
  secret is never written to a tenant row. One login a day = app + broker.
- Rows: label `primary` when free or already this broker account's platform row; a `primary` row with the user's own
  keys is never overwritten (that session goes to label `broker-login`). OPEN_QUESTIONS M74-1.
- Renewal of a sign-in row is the next day's sign-in (or "Continue with your broker" again); the own-keys renew /
  OAuth-start routes need the user's own secret and do not apply to it.
- Built: `backend/app/auth/broker_signin.py` (providers, `configured_brokers()`, `identity_for_code()`,
  `store_signin_session()`, and the M73 wiring: `social_routes.add_provider()` with slug `broker/<id>`, so the routes are
  exactly `/api/auth/broker/<id>/start | link | callback`), `config.BROKER_SIGNIN_APPS`, `.env.example`; frontend
  `BrokerSignIn` / `brokerOptions` in `frontend/src/auth/SocialButtons.tsx`, passed to `SignInOptionsList` as `extra`.
  No migration (M73's `user_identities` columns suffice).
- Minimal M73 extensions (M73 behaviour unchanged for Google / Facebook): `Provider.read_code` (default = `?code=` /
  `?error=`; brokers read `request_token` / `auth_code` and their own refusal words); the flow cookie name turns `/`
  into `_` (`atp_broker_upstox_flow`; a `/` is not allowed in a cookie name); `on_signed_in` also runs after a new
  account is created and after a Settings link; the access-log redaction also blanks `request_token` and `auth_code`.
- When the session is NOT stored this round (OPEN_QUESTIONS M74-3): an account with two-factor on (the challenge
  comes first; the broker token is not carried through it) and a link confirmed with the password. The person is
  signed in; the broker is connected on the next sign-in without those steps, or with the connect card.

### 3.3 Linking (BL-3)
M73's Settings > Account list shows `broker:<id>` identities with the same link / unlink buttons; keep one way to sign
in. `user_identities` unique (provider, subject) = one broker identity -> one ATP account. No extra columns are needed:
the broker's display name and email fit M73's columns, the session lives in `broker_credentials`.

### 3.4 Security (BL-5)
State in a signed cookie (above); codes, tokens and secrets never in a log, audit text, redirect or error (errors are
fixed short codes); token encrypted in the existing secrets store; audit rows `broker_signin` (per sign-in, with the
provider kind) and `broker_signin_session_stored`; callbacks rate-limited as `/login`; the code length capped.
LIVE stays off: storing a session changes no LIVE switch.

## 4. BL-6 owner block (per broker; env names only, values go only into the server's env file)

| Broker | Apply for | Redirect URL to register | Env names |
|---|---|---|---|
| Upstox | Business / multi-client app: "Get in touch" on the Upstox Business Trading API page, or Upstox's API support e-mail (on that page) with a one-page brief (sign-in + data + each user's own orders) | https://aialgodesk.tech/api/auth/broker/upstox/callback | `BROKER_SIGNIN_UPSTOX_API_KEY`, `BROKER_SIGNIN_UPSTOX_API_SECRET`, `BROKER_SIGNIN_UPSTOX_APPROVED` |
| Zerodha | A new Kite Connect app (developers.kite.trade), then multi-user enablement by e-mail to Kite Connect support (address on kite.trade) | https://aialgodesk.tech/api/auth/broker/zerodha/callback | `BROKER_SIGNIN_ZERODHA_API_KEY`, `BROKER_SIGNIN_ZERODHA_API_SECRET`, `BROKER_SIGNIN_ZERODHA_APPROVED` |
| Fyers | A "third-party app" on myapi.fyers.in | https://aialgodesk.tech/api/auth/broker/fyers/callback | `BROKER_SIGNIN_FYERS_API_KEY` (App ID), `BROKER_SIGNIN_FYERS_API_SECRET` (Secret ID), `BROKER_SIGNIN_FYERS_APPROVED` |
| Dhan (later) | DhanHQ partner integration form (partner_id / partner_secret) | given by Dhan's partner set-up | `BROKER_SIGNIN_DHAN_PARTNER_ID`, `BROKER_SIGNIN_DHAN_PARTNER_SECRET`, `BROKER_SIGNIN_DHAN_APPROVED` (not read yet) |
| Others | Nothing: own keys only | - | - |

Set `..._APPROVED=1` only after the broker confirms the app may serve other clients. Note: LIVE automated trading for
many clients may also need exchange empanelment as an algo provider (SEBI retail-algo framework, via the broker); it is
NOT needed for sign-in, data, or the owner's own trading.

## 5. Tests (BL-7) - `backend/tests/test_m74_broker_signin.py`
Fake brokers on an httpx mock transport: provider hidden when not configured / not approved; happy path per broker
(identity from the profile, token present); new-user identity without an account; link-by-email input (email
normalised); session stored as that day's VALID row (expiry = the daily expiry, platform secret absent, own-keys row
untouched); no code / token / secret in logs or `repr`. The route-level cases (sign-in, link, new user through the
register rules, hidden button) run against M73's routes once that branch is merged in.
