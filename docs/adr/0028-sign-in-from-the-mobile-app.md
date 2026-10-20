# ADR-0028: Sign-in from the mobile app - an origin-gated refresh token in the body, held in device secure storage

**Date:** 2026-10-20 · **Status:** Proposed (mobile B1, with B3 for storage) · **Phase:** mobile Phase B

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
   check, login event, `start_session`, audit row, and a SECURITY notification on a new device (`routes.py:265-272`).
2. **Session row.** `start_session` (`backend/app/auth/sessions.py:51-64`) writes a `user_sessions` row
   (`backend/app/db/models.py:156-179`: `user_id`, `tenant_id`, `refresh_token_hash`, `previous_token_hash`, IP, user
   agent, `expires_at`, `revoked_at`, `revoke_reason`, `mfa_verified_at`). Only the SHA-256 of the refresh token is
   stored (`sessions.py:31-32`). The refresh token is `secrets.token_urlsafe(48)` (`sessions.py:55`).
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
   it), so turning the setting off does not break the web (`docs/OPERATIONS.md:788` already plans that step).
5. **Refresh.** `POST /api/auth/refresh` (`routes.py:397-411`) takes the token from the body or the cookie
   (`routes.py:401`). `rotate_refresh_token` (`sessions.py:73-119`) locks the row (`sessions.py:82`), issues a new token,
   keeps the old hash as `previous_token_hash` for one step (`sessions.py:104-110`), and slides `expires_at` to 30 days
   from now (`sessions.py:112`). There is no absolute session lifetime. **Reuse detection exists:** a token that matches
   `previous_token_hash` after the 30-second grace window (`REFRESH_REUSE_GRACE_SECONDS`, `config.py:45`;
   `sessions.py:84-96`) revokes the whole session with reason "refresh token reuse detected". That revoke writes no
   audit row and sends no notification. A failed refresh clears the cookie (`routes.py:405-410`).
6. **Sign out.** `POST /api/auth/logout` (`routes.py:414-425`) revokes the session named by the access token's `sid`
   and clears the cookie. `POST /api/auth/logout-all` (`routes.py:428-435`) revokes every session of the user
   (`revoke_all_sessions`, `sessions.py:128-138`). `DELETE /api/auth/sessions/{id}` (`routes.py:458-467`) revokes one.
   Sessions also end on password reset (`routes.py:610`), password change (every other session, `routes.py:632-637`),
   member removal and the owner's "log out member everywhere" (`backend/app/team/routes.py:247`, `302-311`) and account
   erasure (`backend/app/retention/service.py:180`).
7. **Login protection.** `backend/app/auth/lockout.py`: a per-e-mail delay that never locks the owner out
   (`lockout.py:81-86`, `103-109`), a hard per-IP lock after 50 failures in 15 minutes (`lockout.py:24-25`, `99-102`,
   status 423), and "new device" = an IP plus user-agent pair never seen in a successful login (`lockout.py:119-131`).
   Request limits per IP: 10 logins, 60 refreshes per minute (`routes.py:52-55`). The client IP comes from
   `X-Forwarded-For` only through the trusted proxy (`backend/scripts/serve_api.sh:27-28`; `docker-compose.prod.yml:38-41`).
8. **CORS.** `CORSMiddleware` with `allow_origins=ALLOWED_ORIGINS`, all methods and headers, and **no
   `allow_credentials`** (so it is false) and no `expose_headers` (`backend/app/main.py:212-217`). Production sets
   `ALLOWED_ORIGINS=https://${DOMAIN}` (`docker-compose.prod.yml:36`). `config_problems` refuses `*` in hardened
   environments (`config.py:366-367`).
9. **The web client.** `BASE = "/api/v1"` (`client.ts:174`). The access token lives in memory only
   (`client.ts:175-185`). `tryRefresh` (`client.ts:209-239`) posts to `/auth/refresh` with `credentials: "same-origin"`
   and an empty body (or a legacy localStorage token once, `client.ts:187-196`), one refresh in flight at a time; a 401
   drops the access token, a 429 keeps it. `rawRequest` (`client.ts:251-262`) sends `Authorization: Bearer`. The live
   stream also uses a bearer header (`frontend/src/live/useLiveEvents.ts:21`).
10. **Route classes.** `backend/tests/tenancy_route_table.py:233-257` classifies login, refresh, MFA verify, register,
    invite accept and password reset as `P_SIGN_IN` (public, "the token in the path or body is the credential"), and
    logout, logout-all and the session routes as `S_OWN_ACCOUNT`.

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

### A. Origin-gated refresh token in the response body, kept in device secure storage

The API returns the refresh token in the JSON body (and sets no cookie) only when the request's `Origin` is exactly
one of the configured app origins. The app keeps it in memory (B1) or in hardware-backed secure storage released after
an unlock (B3), and sends it in the body of `/auth/refresh`.

- **XSS on the web origin:** a script injected into `https://<domain>` cannot change the browser's `Origin` header, so
  it never receives a body token once `REFRESH_TOKEN_IN_BODY` is off. (Today, with that setting on, it already can:
  this option is also what lets the operator turn it off.)
- **XSS in the app's WebView:** this is the main risk. Script in the WebView can call any installed Capacitor plugin
  through the bridge, including the secure storage plugin, and can read the token while it is in JS memory during a
  refresh. Then the token can be used from elsewhere until it is revoked. The defences are: only bundled code runs (no
  `server.url`, plan §3.2), a strict CSP (`script-src 'self'`, no inline script; plan §3.2), React escaping, the deep
  link allow-list (plan §6.2), and (B3) a Keystore key that needs user authentication, so a silent read is not possible
  **(verify that the chosen plugin can bind its key to user authentication)**. Reuse detection then limits the damage:
  when the thief and the real app both refresh, the second one presents a rotated token and the session is revoked
  (`sessions.py:84-96`).
- **CSRF:** none. The app's credential is never ambient: no cookie is honoured for app-origin requests, the refresh
  token is in the body, and API calls use a bearer header. CORS keeps `allow_credentials` false.
- **Origin spoofing:** a non-browser client can claim the app origin and get a body token. That gains nothing: it must
  still pass the password, login protection and MFA, exactly as through the web; anyone who has those can already get
  tokens. **The origin gate is not authentication.** It only chooses the carrier for a response the caller has already
  earned, so that a browser page on the web origin never receives a readable refresh token. What protects the token is
  TLS in transit, hardware-backed storage and the unlock at rest, rotation with reuse detection, server-side session
  revocation checked on every request (`dependencies.py:32`), and a bounded session lifetime.
- **A browser page on `https://localhost`** (a local dev server, a malicious local page) can, with CORS, call the login
  route and read the response. It still needs the user to type their password into it, which is phishing, and a
  phishing site can relay to the API from its own server anyway. It gets no cookie, because CORS sends none.
- **Cost:** small server change in existing routes, no new route, one column. The client needs a secure storage plugin
  (B3).

### B. Native HTTP layer (CapacitorHttp) with cookies

Turn on `CapacitorHttp` (it overrides `fetch` and `XMLHttpRequest` on native; default off, Capacitor `main`
declarations) and `CapacitorCookies`. Requests then leave through the platform HTTP stack, outside CORS, and cookies
live in the native cookie store, where SameSite does not apply the browser way **(verify)**.

- **No server change**, and the cookie stays `HttpOnly` to JS.
- **But the cookie becomes ambient inside the app:** any script in the WebView (XSS) sends it just by calling `fetch`,
  as on the web, and the store is a plain file in the app's data directory, not Keystore-protected. It cannot be held
  behind the biometric unlock (B3), and it survives on a phone with no screen lock unless the app clears it, which
  conflicts with MA-8.
- **Patching every request** changes behaviour for the whole app. Streaming responses (the live stream), `FormData`
  uploads (contract notes) and blob downloads (exports) are known weak spots of the patched layer **(verify)**. The
  web and the app would also take different network paths, so a bug would show in only one of them.
- **CSRF:** another origin cannot use the app's native layer, so there is no new CSRF path, but the app loses the
  SameSite guarantee the web relies on, and the server cannot tell an app request from a web request.
- **Rejected:** it buys "no server change" and loses the unlock gate, MA-8 and request uniformity.

### C. PKCE-style app login with a dedicated client id (OAuth 2.0 for native apps, RFC 8252 pattern)

The app opens the system browser (Custom Tab) at the web login with `client_id=atp-android` and a PKCE
`code_challenge`. The user signs in on the real web origin (the existing page, CAPTCHA and MFA). The server redirects
through a verified App Link to the app with a one-time code. The app exchanges the code plus the verifier at a token
route and gets a body refresh token, with an access token audience such as `atp-android`.

- **Good:** the password is never typed into the WebView; the code is useless without the verifier, so origin spoofing
  does not matter at all; a separate audience makes app tokens distinguishable; it is the pattern a third-party client
  or a future SSO provider would need.
- **But** the result lands in the same place as option A (a body refresh token in device storage), so storage, XSS in
  the WebView and the unlock are unchanged. It needs App Links, which need the domain (MA-1) and a stable signing
  fingerprint in `assetlinks.json` (B2/B4, MA-3; CI debug keys change per runner), a code table, an authorise route, a
  token route and a second login screen flow. That is far more surface than B1 needs for a first-party app with one
  user.
- **Deferred, not rejected:** revisit if a third-party client, an external identity provider or a store review needs
  it. Option A's server pieces (client kind on the session, native lifetime, refresh channel binding) carry over.

### D. A different cookie configuration

`SameSite=None; Secure` on the refresh cookie (or a second app-only cookie), with CORS `allow_credentials=true` for the
app origin and `credentials: "include"` in the app.

- **It does not work by default:** the cookie is third-party for the WebView, which blocks third-party cookies
  (Android default, iOS ITP; see Context) **(verify)**. Turning that on needs native code, and Apple's ITP cannot be
  turned off by an app.
- **It weakens the web:** `SameSite=None` makes `/api/auth/refresh` and `/api/auth/logout` reachable with the cookie
  from any site, so the web would need a CSRF token where today it needs none. A second, app-only cookie avoids that but
  keeps every problem of option B (ambient credential in the WebView, no unlock gate, MA-8).
- **Serving the app from the API's own host** (`server.hostname` set to the production domain, so the cookie is
  first-party) is also rejected: Capacitor's docs recommend keeping `localhost`, and the local server would intercept
  requests meant for the real host.
- **Rejected.**

### Considered and left for later

- **Proof of possession** (a non-exportable Keystore key pair per install, its public key registered at login, and
  every refresh signed, in the style of DPoP). A stolen refresh token would then be useless off the device. It needs a
  native plugin that signs with a Keystore key and a server verifier. Recorded as the next hardening if WebView XSS or
  token theft becomes a real concern; not needed for one PAPER user.
- **Certificate pinning.** Rejected for now: Let's Encrypt keys rotate and a wrong pin locks the user out of exits.

## Decision

**Option A**, with these hardening rules. Flag off by default; the web flow is unchanged.

### Server changes (one B1 PR, backend)

1. **Config** (`backend/app/core/config.py`, next to the other P0.3 keys):
   - `NATIVE_APP_ORIGINS`: comma-separated exact origins, **default empty**. The Android value after verification
     (`https://localhost`); `capacitor://localhost` is added only in Phase C.
   - `NATIVE_REFRESH_TOKEN_DAYS` (proposed default 14): the sliding lifetime of an app session.
   - `NATIVE_SESSION_MAX_DAYS` (proposed default 30): the absolute lifetime of an app session from `created_at`. After
     it, the app signs in again (with MFA if enabled). The owner may tune both.
   - `config_problems` (`config.py:353-372`) refuses in production and staging any `NATIVE_APP_ORIGINS` entry that is
     `*`, `null`, has a path, query or trailing slash, uses `http://`, or **equals an `ALLOWED_ORIGINS` entry** (the web
     origin must never receive a body token).
   - `docker-compose.prod.yml` passes `NATIVE_APP_ORIGINS: ${NATIVE_APP_ORIGINS:-}`; `.env.example` documents it,
     empty.
2. **Feature flag `native_app_signin`** in `FEATURE_FLAGS` (`backend/app/platform/controls.py`), **not** in
   `DEFAULT_ON_FLAGS`, read per tenant (`flag_enabled(session, name, tenant_id)`).
3. **Client detection** (`backend/app/auth/routes.py`, one helper): a request is *native* when its `Origin` header is
   exactly in `NATIVE_APP_ORIGINS`. Everything else is *web*, including a missing `Origin`. This is a carrier choice,
   not authentication (see option A).
4. **Token-issuing routes** (login via `_complete_login`, MFA verify, refresh, password change; register, invite
   accept and password reset may follow the same helper):
   - Native and the user's tenant has `native_app_signin` off: **403** with code `native_app_signin_disabled`, checked
     after the password (and MFA) and **before** `start_session`, so no session is created. The login event records
     reason `native_disabled`, added to `REFUSED_REASONS` (`lockout.py:30`) so it never counts as a failure.
   - Native and on: `start_session` records `client_kind = "native"`; the response has the refresh token in the body
     and **sets no cookie** (`_token_response` takes the client kind).
   - Web: exactly as today (cookie; body only while `REFRESH_TOKEN_IN_BODY` is on).
   - With `NATIVE_APP_ORIGINS` empty, no request is ever native, so nothing changes.
5. **Session row:** one Alembic migration adds `user_sessions.client_kind` (`String(16)`, NOT NULL, server default
   `web`; values `web`, `native`). `GET /api/auth/sessions` returns it so the web's session list can say "ATP app".
6. **Refresh channel binding** in `/api/auth/refresh` and `rotate_refresh_token`:
   - A native request reads the token **only from the body** and ignores any cookie. A web request keeps today's order
     (`routes.py:401`).
   - A session refreshes only through the channel it was created on: a `native` session presented by a web request,
     or a `web` session presented by a native request, gets 401 and the session is not rotated. So a forged `Origin`
     cannot turn a web session into a body-token session, and the native lifetime rules cannot be skipped.
   - For a native session, `expires_at` becomes the earlier of now plus `NATIVE_REFRESH_TOKEN_DAYS` and `created_at`
     plus `NATIVE_SESSION_MAX_DAYS`.
   - The 401 for a native request clears no cookie (there is none).
7. **Reuse detection, made visible for native sessions:** when `sessions.py:93-96` revokes a native session for reuse,
   it also writes an audit row `refresh_token_reuse` (session id only) and a SECURITY notification to the user ("Your
   ATP app was signed out because its sign-in was used from two places; sign in again and, if this was not you, change
   your password"). Web reuse stays silent as today; it can adopt the same later.
8. **CORS** (`main.py:212-217`): `allow_origins = ALLOWED_ORIGINS + NATIVE_APP_ORIGINS`; `allow_credentials` stays
   false and a test pins it; add `expose_headers` for the headers the client reads (`X-Request-Id`,
   `X-Content-SHA256`, `X-Export-Rows`, `X-Audit-Chain-Intact`, `Content-Disposition`), none of which carry a secret.
   Exposing them changes nothing for the same-origin web.
9. **No new route.** Login, MFA verify and refresh stay `P_SIGN_IN`, logout and logout-all stay `S_OWN_ACCOUNT`
   (`tenancy_route_table.py:235-255`), so the table needs **no new rows** in B1. B2's `/api/mobile/devices` rows are in
   the plan (§5.2).
10. **Access tokens:** unchanged issuer, audience (`atp-web`) and key id for native sessions in B1. A separate audience
    is not needed while the same API serves both; the session's `client_kind` already distinguishes them server-side.
    Option C would bring its own audience if adopted.

### Client changes

- **B1** (`frontend/src/api/client.ts` plus `frontend/src/native/`): on the native platform, `tryRefresh` sends
  `{refresh_token}` from memory in the body with `credentials: "omit"`; `setToken` keeps the refresh token in memory
  only on native; the web path is untouched (it still ignores the body field). A restarted app asks for sign-in again.
  `capacitor.config.ts`: `loggingBehavior: "none"` for release builds (Capacitor's default `debug` logs in debug
  builds only), `android.allowMixedContent: false`, `android.webContentsDebuggingEnabled` false outside debug builds,
  no `server.url`. AndroidManifest: no cleartext traffic.
- **B3** (`frontend/src/native/secureSession.ts`, `lock.ts`): candidate plugins from plan §8,
  `@aparajita/capacitor-secure-storage` with `@aparajita/capacitor-biometric-auth`, or
  `@capgo/capacitor-native-biometric` **(candidates, verify)**: maintained for the pinned Capacitor major,
  Android-Keystore backed, device-credential fallback, iOS Keychain and Face ID for Phase C, and whether the key can
  require user authentication. **Not** `@capacitor/preferences` (plain storage).
  - Store the refresh token only when the device has a screen lock (Android `KeyguardManager.isDeviceSecure`, through
    the plugin) **(verify)**. Otherwise memory only (MA-8): the user signs in each time, and the app explains how to set
    a screen lock. The server cannot know whether the phone has a lock, so this rule lives in the app; the native
    lifetime cap bounds a session either way.
  - Unlock (biometric or device credential, never biometric-only, so a broken sensor never blocks an exit) → read the
    token → `/auth/refresh` → **write the new token before using the access token** (plan §8). A crash in between is
    absorbed by the 30-second grace window (`sessions.py:88`); later, reuse detection revokes the session and the user
    signs in again, which is the safe outcome.
  - A key invalidated by Android (screen lock removed, new biometric enrolled, if the plugin binds the key that way)
    **(verify)** is treated as signed out.
  - Exclude the secure storage files from Android Auto Backup and device-to-device transfer
    (`android:allowBackup` / `dataExtractionRules`) **(verify)**. A copied blob is useless without the Keystore key, but
    it must not leave the phone.
- **Sign out in the app:** call `POST /api/auth/logout` with the current access token (refresh first if needed), then
  `DELETE /api/mobile/devices/{id}` (B2), then wipe secure storage and memory. Offline: wipe locally anyway; the session
  then dies at its lifetime cap or by "Sign out everywhere" from the web.

### Sessions, devices and sign out everywhere

- One app login = one `user_sessions` row with `client_kind = "native"`. Rotation keeps the same row, so the B2 device
  row's `session_id` stays valid across refreshes. A new login on the same phone re-registers the device to the new
  session (plan §5.2, by `token_hash`).
- Every way a session ends - logout, logout-all, revoke one session, password change or reset, member removal, the
  owner's member logout, account erasure, reuse detection, lifetime cap - also stops pushes: the FCM sender sends only
  to devices whose session is live (plan §5.2). The access token stops at once because `get_current_user` checks the
  session on every request (`dependencies.py:32`).

### MFA and login protection

- MFA is unchanged: the body token is issued only at the final step. The 5-minute `mfa_token` is not a session
  credential, and an access token always needs a live `sid`. The biometric unlock is a local check and never replaces
  TOTP or the server's LIVE step-up (`dependencies.py:156-162`, `backend/app/ai/routes.py:659`).
- `mfa_verified_at` lives as long as the session. For an app session that is bounded by `NATIVE_SESSION_MAX_DAYS`.
  When LIVE trading becomes possible, a recency rule for the step-up on app sessions is a follow-up (PAPER only now).
- CAPTCHA (`LOGIN_CAPTCHA_AFTER_FAILURES`, default 0 = never, `config.py:33`): if it is turned on, the widget's site key
  must allow the app origin **(verify per provider)**; otherwise app sign-in stops after failures. B1 records which.
- Mobile networks: many phones share one public IPv4 address (carrier-grade NAT), and IPv6 addresses change. The
  per-IP lock (50 failures in 15 minutes) and the per-IP request limits can then hit a user for someone else's failures,
  and "new device" (IP plus user agent) will fire on most app logins. No change in B1 (one beta user). Follow-ups:
  count IPv6 by /64, and judge "new device" for native sessions by the device rather than the IP. The per-e-mail delay
  stays the main protection; refresh does not run login protection, because the token is the credential.

### Tenant isolation

Nothing in this design takes a tenant from the client. The user and tenant come from the password check or the
session row (ADR-0001). The flag is read for the user's own tenant. `client_kind` is not a tenant input. No new route
exists in B1, and the Z10-c sweep keeps covering every existing auth route.

### Never logged

Refresh tokens, access tokens, the `mfa_token`, passwords, TOTP and backup codes, CAPTCHA tokens, the `Authorization`,
`Cookie` and `Set-Cookie` headers, secure-storage contents and (B2) FCM registration tokens: not on the server (logs,
audit details, login events, notifications, metrics labels), not in the app (console, `adb logcat`, crash or "Send
feedback" reports), not in CI output. The server already stores only token hashes and does not echo 422 input
(`main.py:175`). Audit rows name session ids, never tokens.

### Safety rules

None of this touches an order path. The app uses the same approval routes as the web; the AI never places an order
(ADR-0006); PAPER only in Phase 0 (`phase0_paper_only`); exits are never blocked (ADR-0004), and a failed sign-in or
unlock never stands between a user and the server-side protective exits, which run whatever the phone does.

### Tests to add (B1 PR)

- Config: `NATIVE_APP_ORIGINS` empty by default; `config_problems` refuses `*`, `null`, `http://`, a path, and an
  entry equal to an `ALLOWED_ORIGINS` origin.
- CORS: the app origin is allowed only when configured; `Access-Control-Allow-Credentials` is never sent; the exposed
  headers are present.
- Login, MFA verify, refresh and password change from the app origin with the flag on: refresh token in the body, no
  `Set-Cookie`, session `client_kind = "native"`. With the flag off: 403 `native_app_signin_disabled`, no session row,
  the login event not counted as a failure.
- Web requests unchanged: the existing `test_auth_api.py`, `test_sessions.py`, `test_mfa.py` and
  `test_phase_p0_3_tokens_audit.py` pass untouched; with `REFRESH_TOKEN_IN_BODY=false` a web login has no body token.
- Channel binding: a native session's token via a web request gets 401 and is not rotated, and the reverse; a native
  request with only a cookie gets 401.
- Native lifetime: `expires_at` never passes `created_at + NATIVE_SESSION_MAX_DAYS`; after it, refresh gets 401.
- Reuse on a native session: revoked, audit row and SECURITY notification written, no token in either.
- Logout and logout-all end native sessions; their access tokens get 401 at once.
- Logs: `caplog` across a native login and refresh contains neither token.
- Z10-c sweep stays green with no table change.

## Rollout

1. Merge with `NATIVE_APP_ORIGINS` empty and `native_app_signin` off: production behaves exactly as today.
2. Operator step, independent of the app: set `REFRESH_TOKEN_IN_BODY=false` in `/opt/atp/.env` once the web runs the
   P0.3 client (it already ignores the field). Until then a web XSS can read a body refresh token, and the origin gate's
   benefit to the web is not yet real.
3. After MA-1 and the B1 debug APK: set `NATIVE_APP_ORIGINS=https://localhost` (the verified value), turn
   `native_app_signin` on for Abhi's organisation only, sign in from the phone, check the session list shows the app.
4. B3 adds secure storage and the unlock. Until then the app keeps the token in memory only.
5. Phase C adds `capacitor://localhost`.

Turning the flag off ends new app sign-ins at once. Existing app sessions keep working until they expire; "Sign out
everywhere" ends them immediately.

## Out of scope

Option C (PKCE through the system browser), proof of possession, certificate pinning, a separate access-token
audience, web reuse notifications, changes to the login protection thresholds, push (B2), deep links (B2), signed
releases (B4), iOS (Phase C), and any change to orders, approvals or risk.

## Consequences

- **Buys:** the app signs in with the same routes, MFA and login protection as the web; the web's cookie flow is
  unchanged; the web can finally stop exposing the refresh token to page script; app sessions are visible, bounded,
  revocable and tied to device rows for push.
- **Costs:** one config block, one flag, one column and migration, branching in four token routes, and a CORS change.
  The app holds a bearer refresh token on the device, so XSS in the WebView is the most important risk to keep closed
  (bundled code only, strict CSP, the unlock-bound key, reuse detection).
- **Residual risks:** a rooted or compromised phone can read the token while the app is unlocked; a non-browser client
  can always claim the app origin (harmless by design, because it still needs the password and MFA); `https://localhost`
  is shared by other apps and local servers (harmless for the same reason).
