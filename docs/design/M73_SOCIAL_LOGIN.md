# M73 - Sign-in page: Google + Facebook login (owner order M73, SL-1..SL-7)

## Plan (written after the audit, before the build)
1. Keep GS-1 (Google, OIDC + PKCE + nonce) and lift it into a provider-generic layer: `app/auth/social.py` (registry,
   flow cookie, pending steps, `user_identities`), `app/auth/social_routes.py` (the routes, one set per provider),
   `app/auth/facebook.py` (Facebook protocol). `google.py` stays as it is; `google_routes.py` stays as the import point.
2. Data: `user_identities` (provider, subject, user_id, email_at_link, created_at), alembic `c4e8a2f6b913`, additive,
   ROLLBACK_SAFE True. Accounts made by a provider get an unusable password (`!`-marked bcrypt hash, as erasure's `!erased`).
3. One sign-up / join path: `self_signup_account` and `join_by_invite` in `app/auth/routes.py` are used by the forms
   and by the providers (terms, beta gate, tenant + default plan, OWNER / SUPER_ADMIN role, activation step).
4. Linking never silent: e-mail match -> pending link confirmed by password (+ two-factor code). Facebook without an
   e-mail -> a finish step asks for it; a mailed confirmation link must be opened before anything is created.
5. Frontend: provider list at the top of Sign in / Create account / Join team + "or use email" (ui_v2), the link and
   finish steps (both layouts), Settings › Account "Ways to sign in" (ui_v2), admin health row.
6. Tests SL-7 (backend fakes, vitest), the GS-1 suite kept green, screenshots, owner block.

## Audit of GS-1 (origin/main before this PR)
| Part | Where | Finding |
|---|---|---|
| Button | `frontend/src/auth/GoogleSignInButton.tsx` | Neutral "G" (no brand mark), 32 px tall, only in Sign in mode, under the form |
| Return | `frontend/src/auth/google.ts` | `?google=ok|new|mfa`, `#google_mfa=`, `?google_error=` - read once, address bar cleaned |
| Protocol | `backend/app/auth/google.py` | OIDC code + PKCE S256 + state + nonce, ID token RS256 verified (iss, aud, azp, exp, nonce, email_verified) |
| Routes | `backend/app/auth/google_routes.py` | start / callback; signed 10-min flow cookie (Lax, HttpOnly, path /api/auth/google) |
| Session | `_complete_login` | same refresh cookie (HttpOnly, Strict, /api), login event, new-device alert, audit `user_login` |
| MFA | callback | same 5-minute challenge token as the password login, in the fragment |
| Existing e-mail | callback | **signed the account in directly** (no link step) - changed by SL-2 |
| Sign-up | `_sign_up` | own copy of the register rules; beta gate / legal pages -> "use the form" refusal - replaced by SL-3 |
| Rate limits | `google_rate_limit` | 20 / min / network for start and callback; /login is 10 / min - callback now shares /login's |
| Logs | `logging_config.redact_query` | access-log `code` / `state` blanked; no token logged |
| Settings › Account | `pages/AccountPage.tsx` | no sign-in methods card |
| Admin health | `admin/console.py system()` | no provider row |

## What was built
- **SL-1** registry kinds `oidc` (Google: ID token + nonce), `oauth2` (Facebook: code -> access token -> Graph `/me?fields=id,name,email`
  with the token in the Authorization header, then dropped), `broker` (reserved for M74). `GET /api/auth/providers`
  -> `{google, facebook, providers:[{id,label,kind,start}], ui_v2}`; a provider is listed only when its credentials are set.
  Buttons: official four-colour "G" and white "f" as inline SVG, colours as `--social-*` tokens (Google light #FFFFFF /
  #747775 / #1F1F1F, dark #131314 / #8E918F / #E3E3E3; Facebook #1877F2 + white), labels "Continue with Google" /
  "Continue with Facebook", 48 px, then "or use email". Admin › System: "Sign-in providers" row (live / not set).
- **SL-2** callback: linked identity -> sign in (two-factor step if on); e-mail of an account -> `#social_pending` link
  step, `POST /api/auth/social/link/confirm` (password, + code when two-factor is on; wrong password counts toward the
  lockout); no e-mail (Facebook) -> finish step asks for one: a new address gets a mailed confirmation link and
  the account only once it is opened (see "Security review fixes"), an account's address becomes a link step (409 `link_required`). Settings › Account: `GET /api/auth/identities`,
  `POST /api/auth/<slug>/link` (password + code; the signed-in request sets the flow cookie - a link cannot be started from someone else's URL),
  `DELETE /api/auth/identities/<provider>` refused (409) when it is the last way to sign in.
- **SL-3** new users: `self_signup_account` / `join_by_invite` (the same functions as `/register` and
  `/invite/<token>/accept`). What only a form collects (terms while `legal_pages`, a beta code while `beta_cohort_gate`,
  an e-mail) is asked on the finish step (`POST /api/auth/social/complete`) and refused exactly like the form.
- **SL-4** no provider token stored or logged (test captures logs); callback / confirm / complete / pending share the
  `/login` limit (`auth_login`, 10 / min) and the account lockout; audit rows `user_login` (detail = provider),
  `social_link`, `social_link_pending`, `social_unlink`, `user_registered` "(provider)"; erasure deletes identities.
- **SL-5** behind `ui_v2` (platform-wide, read at /auth/providers): providers on top, 48 px inputs and buttons, no intro
  paragraph on Sign in / Create account, "Forgot password?" visible, plain-word errors. Without `ui_v2`: GS-1's page.
  Screenshots: `docs/design/M73_SIGNIN_COMPARE.html`.

## Security review fixes (independent review of #832)
1. **Facebook without an e-mail (blocker):** a typed address is never trusted. The finish step creates nothing and signs
   nobody in: it mails a 24-hour, single-use confirmation link (`/account?social_verify=`); only `POST
   /api/auth/social/verify` (the link opened) makes the account - same sign-up path, the terms / beta code given on the
   finish step - and links the provider. A password reset and the e-mail verification link remove any provider linked
   while the account's address was unverified (unless that provider verified this very address); audit
   `social_unlink` "<provider> (password reset | email verified)".
2. **Join team:** a provider signs into an invite only when it verified the invited address itself
   (`invite_email_mismatch` otherwise).
3. **Settings link:** `POST /api/auth/<slug>/link` needs the account's password (+ the authenticator code when on); an
   account without a password sets one first. A SECURITY notification on every link to an existing account and every
   unlink; the reset mail and the reset page name the linked providers.
4. Pending steps are single use (their `jti` is spent on success) and so is a flow `state` (server-side, Redis + this
   process).
5. Graph `/me` carries `appsecret_proof` (HMAC-SHA256 of the token with the app secret); blanked in every log line.
6. A hardened environment needs an absolute `FRONTEND_URL` (as `PUBLIC_BASE_URL`), else no provider is live.

**Re-review fixes:** `email_at_link` is stored only for an address a provider (or our mailed link) proved - a typed one
is never stored - and the owner's reset / verification drop every link without a proven address that matches his own
(with a SECURITY notice). The finish step is spent on a 409 too; a link step made from a typed address says nothing
about that account's two-factor state. The confirmation mail says plainly that the provider will be linked, and the new
account gets a SECURITY notice naming it.

**Re-review 3 fixes:** a security stamp (HMAC of the account's password hash, e-mail, verification state and active
flag) is bound into the Settings link's flow cookie and every pending link token; a password reset or change, an e-mail
change or verification, or switching the account off invalidates every in-flight link (the callback answers "expired",
the step 400). A typed link step on an account without a password answers "Wrong password." after the same bcrypt work.

## Extension points (M74 "Continue with your broker")
- `social.Provider(kind="broker", id="broker:<name>", slug="broker-<name>", ...)` + `social_routes.add_provider(p)`
  serves `/api/auth/<slug>/start|link|callback` with the same flow cookie, pending steps and session.
- `identity_for_code` may return `ProviderIdentity(email=None)`: the finish step asks for an e-mail (as Facebook).
- `ProviderIdentity.session_token` is handed only to the generic `Provider.on_signed_in(session, user, identity, request)`
  hook after a sign-in (never stored by this layer; a hook failure never breaks the sign-in).
- `user_identities`: (provider, subject) unique = one broker account -> one ATP account; `email_at_link` nullable.
- Frontend: `SignInOptionsList` takes `extra: SignInOption[]` - "Continue with your broker" is one more entry.

## Owner decisions (defaults in effect)
1. GS-1 change: a Google e-mail that matches an account which never linked Google now asks for the password once
   (SL-2 "never silent"). Default: as built. (No production user is affected: Google credentials were never set.)
2. Accounts made by a provider have no password; they set one with "Forgot password?" (then they can unlink).
   Default: as built.
3. Facebook Graph API version pinned by `FACEBOOK_GRAPH_VERSION` (default v21.0). Default: keep, raise when Meta retires it.

## SL-6 Owner block - set up the providers (one time)
**Google Cloud (OAuth client)**
1. console.cloud.google.com -> select / create the project -> APIs & Services -> OAuth consent screen: User type
   External; app name AiAlgoDesk; support e-mail; authorised domain `aialgodesk.tech`; scopes `openid`, `email`,
   `profile`; Publish app (In production).
2. APIs & Services -> Credentials -> Create credentials -> OAuth client ID -> Web application.
3. Authorised JavaScript origins: `https://aialgodesk.tech`
4. Authorised redirect URIs: `https://aialgodesk.tech/api/auth/google/callback`
5. Copy the Client ID and Client secret into the server .env (below).

**Meta for Developers (Facebook Login)**
1. developers.facebook.com -> My Apps -> Create app -> use case "Authenticate and request data from users with Facebook
   Login" (type Consumer).
2. App settings -> Basic: App domains `aialgodesk.tech`; Privacy Policy URL and Terms URL (the site's legal pages);
   category; app icon. Copy App ID and App secret.
3. Facebook Login -> Settings: Client OAuth login ON, Web OAuth login ON, Enforce HTTPS ON, Use Strict Mode ON;
   Valid OAuth Redirect URIs: `https://aialgodesk.tech/api/auth/facebook/callback`
4. App Review -> Permissions and features: `email` and `public_profile` -> Advanced Access (switch on).
5. Switch the app from Development to Live (top bar).
6. Business Verification (Business settings -> Security centre) when the company is registered.

**Server .env names** (values only on the server, never in the repo): `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `PUBLIC_BASE_URL` (= `https://aialgodesk.tech`,
already used), optional `FACEBOOK_GRAPH_VERSION`. Then deploy; Admin › System › Sign-in providers shows "live".
