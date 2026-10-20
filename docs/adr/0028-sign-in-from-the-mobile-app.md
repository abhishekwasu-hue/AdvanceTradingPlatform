# ADR-0028: Sign-in from the mobile app - an origin-selected refresh token in the body, held natively behind an unlock

**Date:** 2026-10-20 · **Status:** Proposed (mobile B1, with B3 for storage; revised after two security review rounds of
PR #290) · **Phase:** mobile Phase B

Plan: `docs/design/MOBILE_APP_PLAN.md` (PR #289), §4.3 "Sign-in, cookies and CORS in the app", §5.2 (device rows,
B2) and §8 (biometric unlock and secure storage, B3). Owner decisions used here: MA-6 (temporary package id
`dev.atp.unpublished`, never published; the final id `com.<domain>.app` comes after MA-1), MA-7 (minimal push
content), MA-8 (no persistent session on a phone without a screen lock), MA-9 (APKs from the release job's downloads).
MA-1 (the domain) is still open and blocks any build that signs in to production.

Code references below are to `main` at 8c81ad8. Facts about Capacitor and Android are marked **(verify)** where they
must be re-checked at the slice that uses them.

## Context

### How the web signs in today

1. **Login.** `POST /api/auth/login` (`backend/app/auth/routes.py:214-248`) checks login protection first
   (`refusal`, `routes.py:217-222`), then an optional CAPTCHA (`routes.py:223-228`), then the password with a constant
   time path for unknown e-mails (`routes.py:230-240`). With MFA on it returns only `mfa_required` and a 5-minute
   challenge token (`routes.py:242-246`; `mfa.py:27`, `mfa.py:134-147`, `purpose: "mfa"`), and the session starts only
   at `POST /api/auth/mfa/verify` (`routes.py:680-697`). Both end in `_complete_login` (`routes.py:251-273`): new-device
   check, successful login event (`routes.py:255`), `start_session` (`routes.py:256`), audit row, and a SECURITY
   notification on a new device (`routes.py:265-272`).
2. **Session row.** `start_session` (`backend/app/auth/sessions.py:51-64`) writes a `user_sessions` row
   (`backend/app/db/models.py:156-179`: `user_id`, `tenant_id`, `refresh_token_hash`, `previous_token_hash`, IP, user
   agent, `expires_at`, `revoked_at`, `revoke_reason`, `mfa_verified_at`) with `expires_at` 30 days ahead
   (`sessions.py:59`). Only the SHA-256 of the refresh token is stored (`sessions.py:31-32`). The refresh token is
   `secrets.token_urlsafe(48)` (`sessions.py:55`).
3. **Access token.** `create_access_token` (`backend/app/auth/security.py:155-162`) signs an HS256 JWT with `sub`,
   `iss` (`JWT_ISSUER`, default `atp`), `aud` (`JWT_AUDIENCE`, default `atp-web`), `iat`, `exp` (15 minutes,
   `ACCESS_TOKEN_MINUTES`, `backend/app/core/config.py:73`) and `sid` (the session id), with a `kid` header
   (`security.py:25-27`). No e-mail is in the token. `decode_access_token` (`security.py:165-189`) checks signature
   (current or previous key by `kid`), expiry, issuer and audience. `get_current_user`
   (`backend/app/auth/dependencies.py:16-35`) also requires the session named by `sid` to be live
   (`dependencies.py:32`, `45-49`), so a revoked session kills its access tokens at once.
4. **Refresh token carriers.** `_token_response` (`routes.py:128-131`) sets the refresh cookie and, while
   `REFRESH_TOKEN_IN_BODY` is on, also returns the token in the JSON body. `_set_refresh_cookie` (`routes.py:116-125`)
   sets cookie `atp_refresh` (`REFRESH_COOKIE_NAME`, `config.py:25`) with `HttpOnly`, `SameSite=Strict`, path `/api`
   (`routes.py:113`), `Secure` in production and staging, and `max_age` of `REFRESH_TOKEN_DAYS` (30, `config.py:74`).
   **`REFRESH_TOKEN_IN_BODY` defaults to true** (`config.py:26`; `docker-compose.yml:97`; `.env.example:138-140`), kept
   for pre-P0.3 clients. So today every login and refresh response also carries the refresh token in its body, where
   page script can read it. The web client ignores that field (`frontend/src/api/client.ts:198-200`, `setToken` drops
   it); the security review confirmed that only backend tests read the body token under the default, so turning it off
   is safe for every running client (`docs/OPERATIONS.md:788` already plans that step).
5. **Token-issuing sites.** Six places start a session and answer with `_token_response`: register (`routes.py:209`),
   `_complete_login` for password login and MFA verify (`routes.py:256`), invite accept (`routes.py:390`), refresh
   (`routes.py:404`, `411`), password reset (`routes.py:612`) and password change (`routes.py:638`, after revoking the
   current session at `routes.py:634-637`).
6. **Refresh and reuse detection, and its limits.** `POST /api/auth/refresh` (`routes.py:397-411`) takes the token
   from the body or the cookie (`routes.py:401`). `rotate_refresh_token` (`sessions.py:73-119`) locks the row
   (`sessions.py:82`), issues a new token, keeps the old hash as `previous_token_hash` (`sessions.py:104-110`) and slides
   `expires_at` to 30 days from now (`sessions.py:112`). There is no absolute session lifetime.
   - **Detection is only one step deep.** A presented token is compared with the current hash and the one previous
     hash only (`sessions.py:82-96`). After the 30-second grace window (`REFRESH_REUSE_GRACE_SECONDS`, `config.py:45`),
     the previous token revokes the session. Any older token matches nothing and gets a plain 401, with no revoke.
     So a thief holding Tn who refreshes twice (Tn to Tn+1 to Tn+2) leaves the real client's Tn unrecognised: the real
     client is just logged out, the thief keeps the session, and nobody is told.
   - **The grace window can be used against the holder.** Within 30 seconds of a rotation, presenting the previous
     token rotates again from it and retires the token issued in between (`sessions.py:88-91`, `104-107`). A thief who
     replays Tn inside the window therefore retires the real client's Tn+1, and the real client's next refresh gets a
     plain 401. `backend/tests/test_sessions.py:58-61` pins this behaviour.
   - A revoke for reuse writes no audit row and sends no notification. A failed refresh clears the cookie
     (`routes.py:405-410`).
7. **Sign out.** `POST /api/auth/logout` (`routes.py:414-425`) revokes the session named by the access token's `sid`
   and clears the cookie. `POST /api/auth/logout-all` (`routes.py:428-435`) revokes every session of the user
   (`revoke_all_sessions`, `sessions.py:128-138`). `DELETE /api/auth/sessions/{id}` (`routes.py:458-467`) revokes one.
   Sessions also end on password reset (`routes.py:610`), password change (every other session, `routes.py:632-637`),
   member removal and the owner's "log out member everywhere" (`backend/app/team/routes.py:247`, `302-311`) and account
   erasure (`backend/app/retention/service.py:180`).
8. **MFA state.** `mfa_verified_at` is set on the session at an MFA login or step-up and is never aged:
   `ensure_mfa_session` (`dependencies.py:138-144`) only checks that it is set, so it lasts as long as the session.
9. **Login protection.** `backend/app/auth/lockout.py`: a per-e-mail delay that never locks the owner out
   (`lockout.py:81-86`, `103-109`), a hard per-IP lock after 50 failures in 15 minutes (`lockout.py:24-25`, `99-102`,
   status 423), and "new device" = an IP plus user-agent pair never seen in a successful login (`lockout.py:119-131`).
   Request limits per IP: 10 logins, 60 refreshes per minute (`routes.py:52-55`). The client IP comes from
   `X-Forwarded-For` only through the trusted proxy (`backend/scripts/serve_api.sh:27-28`; `docker-compose.prod.yml:38-41`).
10. **CORS.** `CORSMiddleware` with `allow_origins=ALLOWED_ORIGINS`, all methods and headers, and **no
    `allow_credentials`** (so it is false) and no `expose_headers` (`backend/app/main.py:212-217`). Production sets
    `ALLOWED_ORIGINS=https://${DOMAIN}` and `FRONTEND_URL=https://${DOMAIN}` (`docker-compose.prod.yml:36-37`).
    `config_problems` refuses `*` in hardened environments (`config.py:366-367`).
11. **The web client.** `BASE = "/api/v1"` (`client.ts:174`). The access token lives in memory only
    (`client.ts:175-185`). `tryRefresh` (`client.ts:209-239`) posts to `/auth/refresh` with `credentials: "same-origin"`
    and an empty body (or a legacy localStorage token once, `client.ts:187-196`), one refresh in flight at a time; a
    401 drops the access token, a 429 keeps it. `rawRequest` (`client.ts:251-262`) sends `Authorization: Bearer`. The
    live stream also uses a bearer header (`frontend/src/live/useLiveEvents.ts:21`).
12. **Route classes.** `backend/tests/tenancy_route_table.py:233-257` classifies login, refresh, MFA verify, register,
    invite accept and password reset as `P_SIGN_IN` (public, "the token in the path or body is the credential");
    logout, logout-all and `DELETE /api/auth/sessions/{target_id}` as `S_OWN_ACCOUNT` (`tenancy_route_table.py:237-238`,
    `255`); and `GET /api/auth/sessions` as `member` (`tenancy_route_table.py:254`).

### Why that does not work in the app

- The Capacitor app serves its bundled files from its own origin. Read from Capacitor's `main` branch source
  (`cli/src/declarations.ts`, Capacitor 8.x): `server.androidScheme` defaults to `https`, `server.hostname` to
  `localhost`, `server.iosScheme` to `capacitor`. So the app origin is **`https://localhost` on Android** and
  **`capacitor://localhost` on iOS** **(verify against the Capacitor major pinned in B1)**.
- The API is `https://<domain>` (MA-1). That is a different site. A `SameSite=Strict` cookie is never sent on a
  cross-site request, and CORS here does not allow credentials, so the cookie path cannot work.
- The Android WebView blocks third-party cookies by default for current target SDKs, and Capacitor's Android `Bridge`
  does not turn them on (no `setAcceptThirdPartyCookies` call in the `main` source) **(verify)**. iOS WKWebView blocks
  them too (ITP).
- `https://localhost` is not unique to ATP. Every Capacitor app on the phone and any local web server on a computer can
  have that origin. And the `Origin` header is set by browsers, not proven: any non-browser client (curl, a script, a
  modified app) can send any `Origin` it likes.

## Options

All options keep the access token in memory, as on the web, and use bearer headers for API calls.

### A. Origin-selected refresh token in the response body, held on the device

The API returns the refresh token in the JSON body (and sets no cookie) only when the request's `Origin` is exactly
one of the configured app origins. The app keeps it on the device and sends it in the body of `/auth/refresh`.

**What the `Origin` check is.** It is a **carrier selector, not a security boundary**:
- *What it does:* a browser sets `Origin` itself, and page script cannot change it. So a page on the web origin
  (including a script injected into it) never receives a readable refresh token once `REFRESH_TOKEN_IN_BODY` is off.
  (Today, with that setting on, it already can; this option is also what lets the operator turn it off.)
- *What it does not do:* it proves nothing about the caller. curl, a script or a modified app can send
  `Origin: https://localhost` and get a body token; so can any other Capacitor app or local web page that has that
  origin. They still need the password, login protection and MFA, exactly as through the web, so this gives them no
  access they could not already get. Binding a session to its channel by `Origin` also does not stop a **stolen**
  token: the thief forges the same header. Only keeping the token out of reach (the native refresh plugin below) or
  proof of possession (a device-held key, later) protects a token once it exists.
- *The desktop `https://localhost` case:* a page served at `https://localhost` on the victim's own computer can, with
  CORS, call login and read a body token. It needs the victim to type their password into it (phishing) or something
  already running on that computer (local compromise). It would then sign in **from the victim's own IP and browser
  user agent**, so the "new device" alert (IP plus user agent) would not fire. This is a real but narrow gap; it is
  why the flag is per organisation and off by default, why app sessions are listed as "ATP app" in the session list,
  and why the optional hardening below exists.

**What actually protects the token:** TLS in transit; on the device, native storage behind a per-use unlock (B3) with
the token never handed to JavaScript; rotation with full reuse detection (below); server-side session revocation checked
on every request (`dependencies.py:32`); and a short, capped app session lifetime.

- **XSS in the app's WebView** is the main risk, and it is stated plainly here:
  - In B1, and in any B3 design that hands the token to JavaScript, the refresh token sits in JS memory after login or
    unlock. Injected script can read it, or wrap `fetch` and copy the next `/auth/refresh` response, and then use it
    from anywhere until the session is revoked.
  - Script in the WebView can call any installed Capacitor plugin through the bridge. A secure-storage plugin that
    returns the token to JS gives the token to injected script too. If its Keystore key uses an authentication
    validity window, injected script can call it silently inside that window.
  - Therefore the preferred B3 design (Decision, "Client") is a small native refresh plugin that never returns the
    refresh token to JS.
  - Defences that apply in every phase: only bundled code runs (no `server.url`, plan §3.2), a strict CSP
    (`script-src 'self'`, no inline script, plan §3.2), React escaping, and the deep-link allow-list (plan §6.2).
- **CSRF:** none. The app's credential is never ambient: no cookie is honoured for app requests, the refresh token is in
  the body, API calls use a bearer header, and CORS keeps `allow_credentials` false.
- **Cost:** a small server change in existing routes, no new route, two schema additions; on the client, a native
  plugin in B3.

### B. Native HTTP layer (CapacitorHttp) with cookies

Turn on `CapacitorHttp` (it overrides `fetch` and `XMLHttpRequest` on native; default off, Capacitor `main`
declarations) and `CapacitorCookies`. Requests then leave through the platform HTTP stack, outside CORS, and cookies
live in the native cookie store, where SameSite does not apply the browser way **(verify)**.

- **No server change**, and the cookie stays `HttpOnly` to JS.
- **But the cookie becomes ambient inside the app:** any script in the WebView (XSS) sends it just by calling `fetch`,
  and the store is a plain file in the app's data directory, not Keystore-protected. It cannot be held behind the
  unlock (B3), and it survives on a phone with no screen lock unless the app clears it, which conflicts with MA-8.
- **Patching every request** changes behaviour for the whole app. Streaming responses (the live stream), `FormData`
  uploads (contract notes) and blob downloads (exports) are known weak spots of the patched layer **(verify)**.
- **Rejected:** it buys "no server change" and loses the unlock gate, MA-8 and request uniformity. (Option A's
  preferred B3 design also uses native HTTP, but only inside one plugin for the four auth calls, not for every request.)

### C. PKCE-style app login with a dedicated client id (OAuth 2.0 for native apps, RFC 8252 pattern)

The app opens the system browser (Custom Tab) at the web login with `client_id=atp-android` and a PKCE
`code_challenge`. The user signs in on the real web origin. The server redirects through a verified App Link to the app
with a one-time code, which the app exchanges with the verifier for a body refresh token.

- **Good:** the password is never typed into the WebView; the code is useless without the verifier, so `Origin` plays
  no part; a separate audience makes app tokens distinguishable.
- **But** the result lands where option A's does (a refresh token on the device), so storage and WebView XSS are
  unchanged. It needs App Links (MA-1, a stable signing fingerprint, B2/B4), a code table, an authorise route, a token
  route and a second login flow.
- **Deferred, not rejected:** revisit for a third-party client, an external identity provider or a store review that
  needs it. Option A's server pieces carry over.

### D. A different cookie configuration

`SameSite=None; Secure` on the refresh cookie (or a second app-only cookie), with CORS `allow_credentials=true` for the
app origin and `credentials: "include"` in the app.

- **It does not work by default:** the cookie is third-party for the WebView, which blocks third-party cookies
  (Android default, iOS ITP) **(verify)**.
- **It weakens the web:** `SameSite=None` makes `/api/auth/refresh` and `/api/auth/logout` reachable with the cookie
  from any site, so the web would need a CSRF token. An app-only cookie avoids that but keeps every problem of option B.
- **Serving the app from the API's own host** (`server.hostname` set to the production domain) is also rejected: the
  local server would intercept requests meant for the real host, and Capacitor recommends keeping `localhost`.
- **Rejected.**

## Decision

**Option A**, with the rules below. Flag off by default; the web sign-in flow is unchanged except for the reuse
detection fix (decision 1), which ships as its own code PR because it hardens the web too.

### 1. Detect reuse of any retired token (separate code PR, before B1; all sessions)

**Choice: a token family id carried in the token.** Each session gets a random 128-bit family id, and every refresh
token of that session has the form `<family_id>.<secret>`.

- **Schema** (one Alembic migration, no new table): `user_sessions.token_family` (String(32), unique, nullable for rows
  created before the change) and `user_sessions.grace_superseded_hash` (String(64), nullable, not unique; see the grace
  rule). `refresh_token_hash` and `previous_token_hash` stay as they are and hold the SHA-256 of the whole token.
- **Issue:** `start_session` draws the family id with `secrets.token_urlsafe(16)` (128 bits) and the secret as today
  (`secrets.token_urlsafe(48)`). `token_urlsafe` never produces a `.`, so the format is unambiguous. Every rotation
  keeps the family id and draws a new secret.
- **Lookup:** parse the family id, load the session by `token_family` (unique index, row lock as today,
  `sessions.py:82`). Then:
  - hash equals `refresh_token_hash`: normal rotation;
  - hash equals `previous_token_hash` and the last rotation was within the grace window: grace rotation (below);
  - **any other secret under a known, live family is reuse**, at any depth, with no history kept: the previous token
    after the window, a token from ten or ten thousand rotations ago, or a token retired by a grace rotation.
  - No session with that family id (or a revoked or expired one): plain 401, as today.
- **Grace rotation, specified for the family scheme:** within `REFRESH_REUSE_GRACE_SECONDS` of a rotation, the previous
  token may rotate once more from the same row (two tabs, a retried request). The token issued in between is retired by
  that rotation; its hash is written to `grace_superseded_hash` (one nullable column, overwritten each time, never
  unique, so the two-tab path cannot hit a constraint). `previous_token_hash` keeps the presented token, as today
  (`sessions.py:104-107`). A later presentation of the token retired in between is "another secret under a known live
  family", so it is reuse like any other.
- **Reuse handling, every session, web included:** revoke the session, write an audit row `refresh_token_reuse`
  (session id, client kind and whether the hash matched `grace_superseded_hash`, never a token), and notify:
  - native session: a SECURITY notification worded so a lost connection is not read as an attack: "ATP signed you out
    on your phone because its sign-in was used twice. This can happen after a lost connection; if it happens again or
    you did not expect it, change your password.";
  - web session, hit on `grace_superseded_hash`: no notification (two tabs racing produce it; the browser was already
    getting a 401 today, and the shared cookie jar means nothing usable is lost by revoking);
  - web session, any other reuse: the same SECURITY notification, worded for "a browser".
- **Effect on the attacks in Context item 6:** after any number of thief rotations, the real client's next refresh
  presents an unknown secret under the live family, so the session is revoked and the user told. A grace replay retires
  the holder's token; the holder's next refresh revokes the session. Nothing an attacker does can push the holder's
  token out of reach, because nothing is pruned. The thief's window is at most until the real client's next refresh
  (15 minutes while the app is open).
- **Why the family id needs no MAC key:** it is 128 random bits and only ever travels inside a real token of that
  session, so guessing one is not feasible. Anyone who sees any token of the session (current or retired, for example
  one leaked into a log) can revoke that session. That is a denial of service on one session that requires a leaked
  token, and revoking is the safe outcome of a leak.
- **Old-format tokens** (issued before the change, no `.`) keep today's checks (current and previous hash) until their
  next rotation, which assigns a family id and returns a new-format token. A stolen old-format token that has already
  been rotated past stays undetected, as today; the rollout closes that gap (step 1).
- **Rejected alternatives:**
  - *A retired-token table* (the previous revision of this ADR): any cap on rows lets a thief flush the holder's token by
    rotating past the cap (at 60 refreshes per minute per IP, 1000 rows take about 17 minutes), which brings silent
    eviction back for web and app alike; never pruning a live session's rows needs a rotation-rate ceiling and grows
    with use. The family id gives unlimited depth with one column.
  - *`<sid>.<generation>.<secret>`*: session ids are sequential integers, so anyone could send `<sid>.x.y` and revoke
    any live session unless the token is MAC'd with a server key.
- **Cost on the hot path:** a refresh still makes one locked read of one row by a unique index (`token_family` instead
  of `refresh_token_hash`) and one update of that row; no extra insert. Measured under load before production
  (rollout step 1).
- **Tests change on purpose:** `test_sessions.py:58-61` today asserts that the token retired by a grace rotation gets a
  plain 401 and the session lives on (`/me` 200 at line 62). After this change the same presentation revokes the session
  (401, then `/me` 401), with an audit row and no notification for a web session.

### 2. Configuration (`backend/app/core/config.py`, `docker-compose.yml`, `.env.example`)

- `NATIVE_APP_ORIGINS`: comma-separated exact origins, **default empty**. The Android value after verification
  (`https://localhost`); `capacitor://localhost` only in Phase C.
- `NATIVE_REFRESH_TOKEN_DAYS` (proposed default 14): sliding lifetime of an app session.
- `NATIVE_SESSION_MAX_DAYS` (proposed default 30): absolute lifetime from `created_at`. The owner may tune both.
- All three go into the backend `environment:` block of `docker-compose.yml` next to `REFRESH_TOKEN_IN_BODY`
  (`docker-compose.yml:97`; the stack has no `env_file`): `${NATIVE_APP_ORIGINS:-}`, `${NATIVE_REFRESH_TOKEN_DAYS:-14}`,
  `${NATIVE_SESSION_MAX_DAYS:-30}`. `.env.example` documents them.
- `config_problems` (`config.py:353-372`) in production and staging refuses any `NATIVE_APP_ORIGINS` entry that is `*`,
  `null`, has a path, query, fragment or user info, or uses `http://`; and any entry that, **after normalising**
  (lowercase scheme and host, default port removed, no trailing slash), equals a normalised `ALLOWED_ORIGINS` entry or
  the origin of `FRONTEND_URL`. `FRONTEND_URL` defaults to `/` (`config.py:118`); the comparison uses it only when it
  is an absolute `http(s)://` URL and skips a relative value. The web origin must never receive a body token.
- **Changing `NATIVE_APP_ORIGINS` is a transition, not a swap.** A native session's next refresh from an origin that
  is no longer listed would be classified web, and channel binding (decision 5) would revoke every app session at
  once. So a new origin (a distinctive `server.hostname`, decision 13, or Phase C) is **added** first, the app update
  that uses it ships, and the old origin is removed only after every install has updated or `NATIVE_SESSION_MAX_DAYS`
  has passed. The B1 PR's upgrade notes say this.

### 3. Feature flag and kill switch

- Flag `native_app_signin` in `FEATURE_FLAGS` (`backend/app/platform/controls.py`), **not** in `DEFAULT_ON_FLAGS`,
  read for the user's tenant (`flag_enabled(session, name, tenant_id)`).
- **It is a kill switch.** With it off for a tenant, every native token-issuing request is refused with **403**
  `native_app_signin_disabled`, **including refresh**. On refresh the check uses the session row's `tenant_id` and runs
  after a read-only lookup and **before `rotate_refresh_token` changes anything**: the token is not consumed, nothing
  rotates, and turning the flag back on lets the same token work. (Reuse handling from decision 1 still revokes on a
  retired token, flag or not, because that is a theft response, not a sign-in.) Access tokens already issued live out
  their 15 minutes; "Sign out everywhere" ends them at once.
- **The client keeps its stored token on this 403.** `native_app_signin_disabled` is not a sign-out: the app shows
  "Sign-in from the app is switched off for your organisation; use ATP in the browser" (with the browser link, so
  positions and exits stay reachable) and does not wipe the stored token, which works again when the flag is back on.
- With `NATIVE_APP_ORIGINS` empty, no request is ever native and nothing changes.

### 4. Client kind, decided at every token-issuing site

A request is *native* when its `Origin` is exactly in `NATIVE_APP_ORIGINS`; anything else, a missing `Origin`
included, is *web*. One helper decides, and **the native check always comes before any write**.

| Site | Native request |
|---|---|
| Register (`routes.py:169-211`) | Refused at the top of the handler, before any read or write: 403 `finish_on_web` ("Create your account on the web, then sign in here"). |
| Invite accept (`routes.py:363-392`) | Refused at the top, before any write: 403 `finish_on_web`. |
| Password reset (`routes.py:594-614`) | Refused at the top, before the link is burned: 403 `finish_on_web`. Reset links open in the browser anyway. |
| Password login (`routes.py:214-248`) | After the password check and **before** the MFA branch (`routes.py:242`): flag check for the user's tenant. Off: 403, and a login event with reason `native_disabled` (added to `REFUSED_REASONS`, `lockout.py:30`, so it never counts as a failure). No challenge, no audit row, no session. |
| MFA verify (`routes.py:680-697`) | Flag check **before** `mfa.verify_code` (`routes.py:691`), which can spend a backup code. |
| `_complete_login` (`routes.py:251-273`) | The same check at the top, **before** `record_login_event(success=True)` (`routes.py:255`) and `start_session` (`routes.py:256`), as a guard. On: the session is created with `client_kind = "native"` and native lifetimes; the response carries the refresh token in the body and **sets no cookie**. |
| Refresh (`routes.py:397-411`) | Decision 5. |
| Password change (`routes.py:617-640`) | The client kind comes from the **current session row** (the access token's `sid`), never from `Origin`. A native current session with the flag off: 403 before any write (change it on the web). Otherwise the new session inherits the kind; for native it gets the body token and no cookie. A password change is a re-authentication, so it starts a new absolute lifetime. |

Rationale for refusing three flows rather than supporting them: they are rare, first-time or recovery flows that
carry legal acceptance, invite and beta codes and e-mailed links; doing them on the web keeps the native surface to
login, MFA, refresh, password change and logout.

### 5. Refresh rules (`/api/auth/refresh`, `rotate_refresh_token`)

In this order, with no write before step 5:
1. **Carrier.** A native request reads the token **only from the body** and ignores any cookie; a web request keeps
   today's order (`routes.py:401`).
2. **Lookup** (read-only apart from the row lock): by family id for a new-format token, by hash for an old-format one
   (decision 1). No session, or a session not live: 401.
3. **Channel binding.** A session refreshes only through the channel it was created on. A `native` session's token on
   the web channel, or a `web` session's token on the native channel, is treated as theft: revoke, audit
   `refresh_token_wrong_channel`, SECURITY notification, 401. No legitimate client crosses channels (the web never
   holds a native token; the app never holds a web token). This cannot be abused for denial of service, because it
   needs a real token.
4. **Reuse** (decision 1, any secret under the live family other than the current one or a grace-window previous
   one): revoke, audit, notify as decision 1 says, 401.
5. **Kill switch** (decision 3): native and the flag off for the session's tenant: 403, nothing changed.
6. **Rotate.** For a native session `expires_at` becomes the earlier of now plus `NATIVE_REFRESH_TOKEN_DAYS` and
   `created_at` plus `NATIVE_SESSION_MAX_DAYS`. A native 401 clears no cookie (there is none).

### 6. Session row and lifetimes

- One Alembic migration adds `user_sessions.client_kind` (`String(16)`, NOT NULL, server default `web`; values `web`,
  `native`). `GET /api/auth/sessions` (class `member`, `tenancy_route_table.py:254`) returns it so the web's session
  list can say "ATP app".
- `start_session` for a native session sets `expires_at` to now plus `NATIVE_REFRESH_TOKEN_DAYS` (the same
  `min(now + 14 d, created_at + 30 d)` rule, which at creation is now + 14 d), not today's 30 days (`sessions.py:59`).
- **Code rollback:** the column stays, but the cap, the channel binding and the kill switch are lost; native sessions
  would slide 30 days again and could refresh through either carrier. After a rollback of this code, revoke the app
  sessions from the session list or with "Sign out everywhere".

### 7. CORS (`main.py:212-217`)

`allow_origins = ALLOWED_ORIGINS + NATIVE_APP_ORIGINS`; `allow_credentials` stays false and a test pins it;
`expose_headers` lists the headers the client reads (`X-Request-Id`, `X-Content-SHA256`, `X-Export-Rows`,
`X-Audit-Chain-Intact`, `Content-Disposition`), none of which carry a secret. CORS does not apply to the native plugin's
requests (decision 9); it matters for the WebView's ordinary API calls.

### 8. Routes and tokens

- **No new route.** Login, MFA verify, refresh, register, invite accept and password reset stay `P_SIGN_IN`; logout,
  logout-all and session revoke stay `S_OWN_ACCOUNT`; `GET /api/auth/sessions` stays `member`
  (`tenancy_route_table.py:233-257`). No new table rows in B1. B2's `/api/mobile/devices` rows are in the plan (§5.2).
- Access tokens keep the issuer, audience (`atp-web`) and key id. `client_kind` on the session row distinguishes app
  sessions server-side; option C would bring its own audience if adopted.

### 9. Client

- **B1** (`frontend/src/api/client.ts`, `frontend/src/native/`): on native, `tryRefresh` sends `{refresh_token}` in the
  body with `credentials: "omit"`, and `setToken` keeps the refresh token in JS memory only; the web path is untouched.
  A restarted app asks for sign-in again. **Exposure accepted for B1 only:** the refresh token is readable by any
  script in the WebView; this is acceptable because the flag is on only for the owner's organisation, the build runs
  only bundled code under a strict CSP, and sessions are short and capped. B3 removes the exposure.
- **B3, preferred design: a small native refresh plugin** (Kotlin, `frontend/android/.../AuthSessionPlugin`, wrapped by
  `frontend/src/native/secureSession.ts`). It performs login, MFA verify, refresh and logout over native HTTP, sets
  `Origin` to the app origin itself, keeps the refresh token only in native memory and in Keystore-backed storage, and
  returns **only the access token** (and the MFA challenge) to JavaScript. Injected script can then still obtain fresh
  access tokens while the app is open and unlocked (as web XSS can by riding the cookie), but can never read or copy the
  long-lived refresh token.
  - **The plugin takes no target from JavaScript.** The API origin is compiled in (`BuildConfig`, generated from
    `ATP_APP_DOMAIN` at build time, `https://` only, refused if empty or an IP, as plan §4.2). No plugin method accepts
    a URL, host, path or header from JS; each method calls one fixed auth path. Otherwise injected script could point
    the plugin at its own server and receive the refresh token in a request body.
  - **No method returns, exports or forwards the refresh token** (no "get token", no debug dump, no event carrying it,
    no logging of it). The only outputs are the access token, its expiry, the MFA challenge and error codes.
  - **Redirects are off for the auth calls** (`instanceFollowRedirects = false` or the HTTP client's equivalent): a 307
    or 308 would re-send the body, refresh token included, to wherever the redirect points. Any 3xx is an error.
  - **Retry on a network error:** if the refresh request fails before a response arrives (timeout, connection reset),
    the plugin retries the **same** token at once, within the grace window, and only then reports failure. The server
    either never saw the first request or grace-rotates from the same token, so a lost connection does not turn into a
    reuse revoke. A retry later than the window is not attempted with the old token; the user unlocks or signs in
    again.
  - **Per-use authentication, no validity window.** The stored token is decrypted once per unlock with a Keystore key
    that requires user authentication for every use, through `BiometricPrompt` with a `CryptoObject`. Writing a new
    token after each rotation must not need a prompt: encrypt it under the public half of a Keystore key pair (or a data
    key wrapped by it), so only decryption needs the unlock **(verify the exact Keystore construction and API levels)**.
    Device credential (PIN, pattern) together with a `CryptoObject` needs Android 11 or later **(verify)**; on older
    phones the app keeps no stored session (sign in each time), so a missing or broken fingerprint sensor never blocks
    anyone. A validity window is rejected: inside it, any script could make the plugin decrypt silently.
  - On lock (timeout, plan §8) the plugin drops the token from native memory. A key that Android invalidates (screen
    lock removed, new biometric enrolled) **(verify)** means signed out.
  - Write the new token before using the new access token (plan §8). A crash in between is absorbed by the 30-second
    grace window (`sessions.py:88`); later than that, reuse detection revokes the session and the user signs in again.
  - **Fallback** if the plugin is not built: an off-the-shelf plugin from plan §8 (`@aparajita/capacitor-secure-storage`
    with `@aparajita/capacitor-biometric-auth`, or `@capgo/capacitor-native-biometric`) **(candidates, verify)**. These
    return the token to JS, so the B1 exposure stays and must be recorded in the B3 PR. **Not**
    `@capacitor/preferences` (plain storage).
  - Exclude stored session files from Android Auto Backup and device-to-device transfer (`android:allowBackup`,
    `dataExtractionRules`) **(verify)**.
- **MA-8 (no screen lock) is enforced in the app.** The plugin stores a token only when the device is secure
  (`KeyguardManager.isDeviceSecure`) **(verify)**; otherwise memory only and the user signs in each time. The server
  cannot see whether a phone has a lock, and a modified or rooted client can ignore the rule. The server-verifiable
  route is hardware key attestation of a Keystore key that requires user authentication (optional hardening below).
  The capped lifetime bounds a session either way.
- **Sign out in the app:** `POST /api/auth/logout` with the current access token (refresh first if needed), then
  `DELETE /api/mobile/devices/{id}` (B2), then wipe native storage and memory. Offline: wipe locally anyway; the session
  dies at its cap or by "Sign out everywhere" from the web.

### 10. Builds that can sign in to production

- **Debuggable APKs never point at production.** Capacitor's default `loggingBehavior: "debug"` logs bridge calls in
  debug builds, which can put plugin arguments and results (in B1, the refresh token) into `adb logcat`; WebView
  debugging, enabled automatically during development, exposes JS memory to anyone with USB access; and a debuggable
  app's data can be read with `adb run-as`.
- So the B1 CI job builds two variants: a debuggable APK against the placeholder origin (layout smoke test only, as the
  plan already says for builds without MA-1), and a **non-debuggable internal build type** (never published) for
  `https://<ATP_APP_DOMAIN>`, with `loggingBehavior: "none"`,
  `android.webContentsDebuggingEnabled: false`, `android.allowMixedContent: false`, no cleartext traffic and no
  `server.url` **(verify how the Android build type maps to Capacitor's logging and debugging switches)**. This amends
  the plan's B1 `android-debug` job (PR #289, §3.2), which today builds a debug APK for the production domain; B1
  reconciles it. Release builds (B4) use the same settings.
- **Any build that can sign in to production is signed with a non-public key:** either an internal signing key kept
  only in CI secrets (a protected environment with the owner as required reviewer, like `android-release`) or the
  MA-3 upload key. Never a shared, committed or well-known debug key. Android lets an APK signed with the same key
  install as an update of the same package id and keep its data and Keystore access, so a debug key that others can
  obtain would let their APK replace ATP on the phone and use its stored session. The internal key is generated once
  by the owner, like MA-3; its secret names are set in the B1 PR (this amends the plan's "the debug job needs no
  secrets" for the production-pointing variant only). Moving from the internal key to the MA-3 key needs an uninstall
  and a fresh sign-in, as the plan already notes for debug to release.

### 11. Sessions, devices and sign out everywhere

- One app login = one `user_sessions` row with `client_kind = "native"`. Rotation keeps the row, so the B2 device row's
  `session_id` stays valid across refreshes. A new login on the same phone re-registers the device to the new session
  (plan §5.2, by `token_hash`).
- Every way a session ends - logout, logout-all, revoke one session, password change or reset, member removal, the
  owner's member logout, account erasure, reuse or wrong-channel detection, the lifetime cap - also stops pushes: the
  FCM sender sends only to devices whose session is live (plan §5.2). The access token stops at once
  (`dependencies.py:32`).

### 12. MFA and login protection

- MFA is unchanged: the body token is issued only at the final step. The 5-minute `mfa_token` is not a session
  credential, and an access token always needs a live `sid`. The biometric unlock is a local check and never replaces
  TOTP or the server's LIVE step-up (`dependencies.py:156-162`, `backend/app/ai/routes.py:659`).
- `mfa_verified_at` lasts as long as the session (`dependencies.py:138-144`). **A recency rule for the step-up on native
  sessions (for example a TOTP check within the last hours) is a precondition of enabling LIVE in the app.** PAPER only
  now.
- **Explicit rules:**
  - No CAPTCHA, login protection or rate-limit relaxation may ever key on `client_kind` or `Origin`. Both are
    attacker-chosen.
  - A client-supplied device id (B2 or later) must never suppress the "new device" alert. Device signals may only add
    alerts, never remove them.
- CAPTCHA (`LOGIN_CAPTCHA_AFTER_FAILURES`, default 0 = never, `config.py:33`): if it is turned on, the widget's site key
  must allow the app origin **(verify per provider)**; otherwise app sign-in stops after failures. B1 records which.
- Mobile networks: many phones share one public IPv4 address (carrier-grade NAT) and IPv6 addresses change. The per-IP
  lock and limits can hit a user for someone else's failures, and "new device" will fire on most app logins. No change
  in B1 (one beta user). Follow-up: count IPv6 by /64. The per-e-mail delay stays the main protection; refresh does not
  run login protection, because the token is the credential.

### 13. Optional hardening (not in B1; each **(verify)**)

- A distinctive `server.hostname` (for example an `.invalid` name), so the app origin is not the shared
  `https://localhost`. Capacitor recommends keeping `localhost` for secure-context APIs; `https://` with another host is
  also a secure context, but plugin and WebView behaviour must be checked. Switching follows the transition rule in
  decision 2: list both origins until every install has the new build, or every app session is revoked by channel
  binding on its next refresh.
- Play Integrity or hardware key attestation at login, so the server can tell a genuine, unmodified app on a locked
  device from a script claiming the app origin.
- Proof of possession: a non-exportable Keystore key pair per install, its public key registered at login, every
  refresh signed (DPoP style). Then a copied refresh token is useless off the device.
- Certificate pinning stays rejected for now: Let's Encrypt keys rotate and a wrong pin would lock the user out of
  exits.

### 14. Tenant isolation

Nothing here takes a tenant from the client. The user and tenant come from the password check or the session row
(ADR-0001). The flag is read for the user's or session's own tenant. `client_kind` is not a tenant input. The new
columns live on `user_sessions`, whose `tenant_id` is already a foreign key with ON DELETE CASCADE (`models.py:168`);
no new table is added. (Should a later change add one, its `tenant_id` must be a foreign key to `tenants` with ON
DELETE CASCADE, like every tenant-owned table.) No new route exists, and the Z10-c sweep keeps covering every existing
auth route.

### 15. Never logged

Refresh tokens, access tokens, the `mfa_token`, passwords, TOTP and backup codes, CAPTCHA tokens, the `Authorization`,
`Cookie` and `Set-Cookie` headers, native-storage contents and (B2) FCM registration tokens: not on the server (logs,
audit details, login events, notifications, metrics labels), not in the app (console, `adb logcat`, crash or "Send
feedback" reports), not in CI output. The server already stores only token hashes and does not echo 422 input
(`main.py:175`). Audit rows name session ids, never tokens.

### 16. Safety rules

None of this touches an order path. The app uses the same approval routes as the web; the AI never places an order
(ADR-0006); PAPER only in Phase 0 (`phase0_paper_only`); exits are never blocked (ADR-0004). A failed sign-in or unlock
never stands between a user and the server-side protective exits, which run whatever the phone does, and the password
sign-in always remains available.

## Tests to add

Reuse PR (decision 1), all sessions:
- **Two rotations then the old token:** Tn to Tn+1 to Tn+2 outside the grace window; presenting Tn gets 401, the
  session is revoked, an audit row and a SECURITY notification exist, and Tn+2 and its access token stop working.
- **Deep chain:** rotate 1100 times from one chain (beyond any cap a table would have had), then present the original
  token: 401, session revoked, audit and notification written.
- **Grace replay retires the holder:** rotate Tn to Tn+1; replay Tn inside the window (200, Tn+2); then Tn+1 gets 401
  and the session is revoked with an audit row, on native (with notification) and on web (no notification).
  `test_sessions.py:58-62` is updated to this expectation: the retired in-between token now revokes the session, and
  `/me` with the grace rotation's access token gets 401.
- **Two tabs at once on the web:** two concurrent refreshes with the same token both succeed (grace), no constraint
  error and no 500; `grace_superseded_hash` is overwritten, not inserted.
- **Lost connection:** the same token presented twice within the window (the plugin's retry) succeeds both times and
  revokes nothing.
- **Formats:** an old-format token (no `.`) rotates as today and comes back in the new format with a family id; a token
  whose family id is unknown gets 401 and revokes nothing; a made-up secret under a real family id revokes that session
  (documented behaviour: it needs a leaked token).
- No token in audit details, notifications or logs (`caplog`).

B1 PR:
- Config: the three keys' defaults; `config_problems` refuses `*`, `null`, `http://`, a path, and an entry equal after
  normalisation to an `ALLOWED_ORIGINS` origin or `FRONTEND_URL`'s origin (case, default port, trailing slash).
- CORS: the app origin allowed only when configured; `Access-Control-Allow-Credentials` never sent; exposed headers
  present.
- Login, MFA verify, refresh and password change from the app origin with the flag on: refresh token in the body, no
  `Set-Cookie`, `client_kind = "native"`, `expires_at` = now + 14 days at creation.
- Flag off: login gets 403 with no challenge, no session, no audit row, and the event not counted as a failure; MFA
  verify gets 403 and a backup code is not spent; **native refresh gets 403, the token is not consumed, nothing rotates,
  and the same token works after the flag is turned back on**; password change from a native session gets 403 with no
  write.
- Register, invite accept and password reset from the app origin: 403 `finish_on_web`, no row written, the invite and
  reset link still usable on the web.
- Password change takes the client kind from the session row: a web session with a forged app `Origin` stays web.
- Channel binding: a native token on the web channel and a web token on the native channel are revoked, audited and
  notified; a native request carrying only a cookie gets 401.
- Native lifetime: `expires_at` never passes `created_at + NATIVE_SESSION_MAX_DAYS`; after it, refresh gets 401.
- Web requests unchanged: the existing `test_auth_api.py`, `test_sessions.py`, `test_mfa.py` and
  `test_phase_p0_3_tokens_audit.py` pass untouched; with `REFRESH_TOKEN_IN_BODY=false` a web login has no body token.
- Logout and logout-all end native sessions; their access tokens get 401 at once.
- Logs: `caplog` across a native login and refresh contains neither token.
- Config: a relative `FRONTEND_URL` (the default `/`) is skipped by the origin comparison, not compared or rejected.
- The Z10-c sweep stays green with no table change.

B3 PR (plugin, instrumented or unit tests on the Kotlin side plus the TS wrapper):
- The API origin comes from `BuildConfig`; no plugin method accepts a URL, host, path or header; a JS call with extra
  arguments cannot change the target.
- No plugin method, event or log line returns or contains the refresh token (a test greps the plugin's outputs and
  `logcat` in a test build for the token value).
- A 307 or 308 on any auth call is an error and the request is not re-sent; nothing is sent to the redirect target.
- A network error before a response retries the same token once within the window; a later failure does not retry.
- A 403 `native_app_signin_disabled` keeps the stored token; a 401 wipes it.
- Lock state machine, per-use unlock, no stored session on a phone without a screen lock or below the Android level
  that allows device credential with a `CryptoObject`.

## Rollout

1. Merge the reuse PR (decision 1). It changes only what happens to already-invalid tokens, but it touches every
   refresh, so its refresh latency is measured under the existing load test (the LD-7 setup) before it reaches
   production, and the PR records the before and after p95. After deploying it, the operator may revoke all sessions
   created before the change ("everyone signs in once") to close the old-format gap; with one beta user that costs one
   sign-in.
2. Merge B1's server part with `NATIVE_APP_ORIGINS` empty and `native_app_signin` off: production behaves as today.
3. Operator step, independent of the app: set `REFRESH_TOKEN_IN_BODY=false` in `/opt/atp/.env` **after the 21 October
   load test, not before**. The review confirmed it is safe for every running client. Until then a web XSS can read a
   body refresh token, so the origin selector's benefit to the web is not yet real.
4. After MA-1 and the internal B1 APK: set `NATIVE_APP_ORIGINS=https://localhost` (the verified value), turn
   `native_app_signin` on for the owner's organisation only, sign in from the phone, check the session list shows the
   app.
5. B3 adds the native refresh plugin and the unlock.
6. Phase C adds `capacitor://localhost`.

Turning the flag off stops app sign-ins and app refreshes at once (decision 3); access tokens already issued expire
within 15 minutes, or at once with "Sign out everywhere".

## Out of scope

Option C, the optional hardening in decision 13, a separate access-token audience, changes to the login protection
thresholds, push (B2), deep links (B2), signed releases (B4), iOS (Phase C), and any change to orders, approvals or
risk.

## Consequences

- **Buys:** the app signs in with the same login, MFA and login protection as the web; the web's cookie flow is
  unchanged; reuse of any retired token is caught and reported for every session; the web can stop exposing the refresh
  token to page script; app sessions are visible, short, capped, revocable, switchable off and tied to device rows for
  push.
- **Costs:** a token format with a family id (two columns), one config block, one flag, one more column, branching
  in six token sites, a CORS change, a native plugin in B3, an extra Android build type and a non-public signing key
  for it. Changing an app origin needs a transition with both origins listed.
- **Residual risks:** in B1, WebView XSS can read the refresh token (accepted for one owner-only build). From B3, WebView
  XSS can still mint access tokens while the app is open and unlocked. A rooted or compromised phone can read native
  memory while unlocked. The `Origin` header is attacker-chosen (harmless by design: it selects a carrier, never grants
  access). A local page at `https://localhost` on the victim's computer can sign in without a "new device" alert if the
  victim types their password into it.
