# Mobile app plan: phone-first pages, Android app, iOS later

Owner decision (Abhi, 20 Oct 2026): **bring mobile forward.** This supersedes the "no Capacitor until Phase 1" lines in
`docs/BETA_LAUNCH_SPEC.md` (§4a and the PWA-first addendum) and in `docs/PWA.md`. The Tauri desktop wrapper is not
part of this decision and stays in Phase 1.

Three phases:

- **Phase A, with UI v2:** every page phone-first. Each UI2 PR ships its own phone layout and phone screenshot checks.
- **Phase B, Android:** Capacitor wraps the same React app (one codebase, no second UI). B1 starts after UI2-1
  (navigation) merges.
- **Phase C, iOS later:** the same Capacitor project. No iOS work now.

The trading rules do not change on a phone: the AI never places an order, every approval goes through the existing
approval flow (PAPER in Phase 0), and an exit is never blocked (§7).

What Abhi must provide is in `docs/OPEN_QUESTIONS.md`, section "Mobile app - what Abhi must provide" (MA-1 to MA-9).

**About facts in this plan.** File paths, routes and code behaviour below were read from `main` at 8c81ad8. Plugin
names, versions and platform rules are **proposals to verify at the slice that uses them**. Each one is marked
**(verify)** and the PR records what it found. Nothing here is a pinned version.

---

## 1. What exists today (PW1-PW6)

| Item | What it gives a phone today | Where |
|---|---|---|
| PW1 PWA shell | Manifest (standalone, shortcuts), maskable icons, service worker that caches only the app shell (never `/api/*`), offline page, "Install app" button (`beforeinstallprompt`), the iPhone "Add to Home Screen" guide, and "A new version is ready - Reload" | `frontend/public/manifest.webmanifest`, `frontend/public/sw.js`, `frontend/src/pwa/pwa.ts`, `frontend/src/pwa/PwaBanners.tsx`, `frontend/test/pwa-shell.test.ts` |
| PW2 phone layout | Bottom bar below 768 px (Home, Screener, Chain, Positions, More), safe-area insets, 44 px targets on coarse pointers, 16 px field text (no iOS zoom), keyboard-aware layout, chart touch gestures, chart tools in a bottom sheet | `frontend/src/components/BottomNav.tsx`, `index.css`, `frontend/e2e/mobile.e2e.ts` |
| PW3 web push | Explain-first permission, iOS "install first", per-category preferences, deep links in every push, test send, delivery log. One PUSH channel per organisation holding up to 20 device subscriptions | `backend/app/alerts/webpush.py`, `channels.py` (`PushConfig`), `dispatcher.py` (`send_push`), `deeplink.py` (`app_path`, `PUSH_CATEGORIES`), `frontend/src/alerts/push.ts`, `WebPushSection.tsx` |
| PW4 Telegram and app | "Open in app" button on every Telegram alert and proposal (absolute URL from `PUBLIC_BASE_URL`, else `FRONTEND_URL`), the one-time link code, and the live stream `GET /api/live/stream`, so a decision on one side shows on the other within about 2 s | `deeplink.py` (`app_url`, `open_in_app_row`), `backend/app/alerts/links.py`, `frontend/src/live/useLiveEvents.ts` |
| PW5 proposal card | Approve (PAPER) / Reject / Snooze with 44 px targets. It fits a 360 px phone (Playwright check). LIVE keeps the server step-up. "Trade in your broker app" is behind the `broker_app_links` flag (off) | `frontend/src/copilot/tabs/ProposalCard.tsx`, `POST /api/ai/actions/{id}/approve|reject|snooze`, `frontend/src/proposals/` |
| PW6 | Invite gate, install and update counters (`POST /api/pwa/events`), and "Send feedback" with the app version (`__APP_VERSION__` from `vite.config.ts`) | `frontend/src/pwa/events.ts`, `frontend/src/feedback/` |

**Phone screenshot checks today** (`frontend/e2e/mobile.e2e.ts`, Playwright project `phone`):

- **Pages:** 10 of the 29 page routes (dashboard, screener, option-chain, positions, orders, notifications, settings,
  copilot, scanner, signals).
- **Sizes and themes:** 360 x 800 and 390 x 844, dark and light. That is 40 committed baselines in
  `frontend/e2e/__screenshots__/`.
- **Layout rules:** no sideways page scroll, 44 px targets, the last control reachable above the bottom bar, the More
  drawer, and no bottom bar on a desktop.
- **Gap:** the sideways-scroll rule skips anything inside an `overflow-x` scroller. A wide table inside
  `overflow-x-auto` passes today, so sideways-scrolling tables are not caught.

**Two blockers on the production host:**

1. **Install and push do not work there.** The host runs on the bare IP (`<server-ip>`) with Caddy's internal
   certificate (`deploy/Caddyfile.ip`, `tls internal`). Browsers do not allow service workers on a page whose
   certificate they do not trust. The user may click through the warning, but the service worker still does not
   register. So there is no install prompt, no web push and no update banner. Today the install banner stays hidden
   and gives no reason. A trusted certificate needs a domain (MA-1). Let's Encrypt does not issue certificates for a
   bare IP in this setup. With a domain, `deploy/Caddyfile` gets one automatically.
2. **The API address is hard-coded as a relative path.** `frontend/src/api/client.ts` has `BASE = "/api/v1"`. Seven
   more files contain `/api/...` literals: `journal/api.ts`, `journal/types.ts`, `pwa/events.ts`, `legal/legal.ts`,
   `status/publicStatus.ts`, `live/useLiveEvents.ts` and `pages/StatusPage.tsx`. That is right for the web, where
   nginx and Caddy serve both on one origin, but wrong for an app whose pages are bundled inside it (§4.2).

---

## 2. Phase A: phone-first pages, delivered with UI v2

Every UI2 PR includes its phone layout. There is no separate "mobile PR" after the fact.

**Phone rules (each UI2 PR):**

- One column at < 768 px with a 16 px gutter (`--space-4`, UI2-0).
- No table that scrolls sideways on a phone. Rows become cards or two-line list rows. A wide grid gets a phone
  layout: the option chain becomes a strike-centred three-column row (call | strike | put), and tapping a row opens the
  rest of it.
- Tap targets of at least 44 x 44 px. List rows are 48-52 px.
- Nothing hidden behind the bottom bar.
- The pages' own phone mockups in `docs/design/ui-v2/mockups/`.

### 2.1 Which slice delivers which phone item

| Slice | Phone items it delivers | Phone checks it adds |
|---|---|---|
| UI2-0 Foundations (merged, #283) | Phone tokens: `--space-4` gutter, `t20` phone page title, `useFeatures()`, the `BrokerGate` component | Token contrast test, colour lint (exist) |
| UI2-4a1 (merged, #287) | Per-user chart preferences and watchlist rows (data the phone watchlist sheet uses) | none |
| **UI2-1 Shell** | **Bottom bar Home / Charts / Signals / Positions / More** (Signals badge = proposals waiting). The **More bottom sheet** with search and every other page. The top bar on a phone. `/charts` route. **The install screen says why install is missing** (§2.2). Also the `ui_v2` fixture switch in `e2e/mockApi.ts` and the route-coverage guard (§2.3) | `shell-v2-{360,390,1440}-{light,dark}`. Bottom-bar check updated to the v2 labels. Route-coverage guard with its pending list |
| UI2-2 Dashboard | One column in the §5 order, title "Today", Setup chip, friendly empty states | `dashboard-v2-{360,390}-{light,dark}` |
| UI2-3 Signals | "Waiting for you" tab; cards that expand in place; Approve (PAPER) / Skip / Snooze at 44 px on the card | `signals-v2-*`; e2e approve, skip and expired at 360 px |
| UI2-4 Charts | Interval under the price; Indicators / Draw / Alert as three 44 px buttons; a full-width Trade button; More in the top bar. **Watchlist as a bottom sheet** (owner brief; this replaces "inside the symbol picker" in UI v2 §6 - the picker still lists the watchlist). A Share button in the More menu, which the native share sheet takes over in B2 | `charts-v2-*`, with the canvas masked except the toolbar and axes |
| UI2-5 Broker field map | none (API only) | none |
| UI2-6 Connect broker + Settings | Steps 1-3 of the connect flow as full-screen phone steps; Settings groups as a list with search; the push section says "needs the secure web address" while the host has no trusted certificate (§2.2) | `settings-v2-*`, `broker-connect-v2-{1,2,3}-*` |
| UI2-7 Setup wizard | Wizard steps at 390 | `setup-v2-step{1..4}-390-light` (planned) plus dark |
| UI2-8 Merges | Find setups results as cards; option chain phone row (above); Orders and Trade history as cards; Journal tabs | `screener-v2-*`, `option-chain-v2-*`, `orders-v2-*`, `journal-v2-*` |
| UI2-9a-e Page sweep | (a) Positions, Portfolio, Risk - **Positions as cards with "Exit now" on each card**; (b) Library, Builder, Backtest; (c) Autopilot, Copilot shell; (d) Performance, News & events, Company research; (e) Notifications, Team, More pages | One `*-v2-*` set per page |
| UI2-10 Switch | v2 baselines become the plain names; old phone baselines removed | The route-coverage pending list must be empty |

### 2.2 The install screen when the host has no trusted certificate

**Today:** on the IP host, no service worker registers and `beforeinstallprompt` never fires. `usePwa()` returns
`install: null` and the banner is not shown, so the user is not told why.

**Change (UI2-1).** The banner work is small; if UI2-1 is already large, it can ship as its own small PR before it.

- `frontend/src/pwa/pwa.ts` gets a third install state, `"needs-domain"`. It is set when the page's host is an IP
  literal (`location.hostname` matches IPv4 or IPv6), or when `navigator.serviceWorker.register()` rejects with a
  `SecurityError`, which is what an untrusted certificate gives (verify the exact error per browser).
- `PwaBanners.tsx` then shows a dismissible line instead of nothing: "Installing the app and phone alerts need ATP's
  secure web address. They are not available on this address yet. You can keep using ATP in the browser; alerts still
  reach you on Telegram."
- `frontend/src/alerts/push.ts` gets the same readiness state (`"needs-domain"`), so the web push section in Settings
  says the same instead of "unsupported".
- Tests: unit tests for the IP-literal and `SecurityError` cases; one phone screenshot with the banner, light and
  dark.

### 2.3 Phone screenshot checks for every route, light and dark

- **Shared route list.** A new `frontend/e2e/phoneRoutes.ts` exports `PHONE_ROUTES` (name, path, fixture state) and
  `PHONE_PENDING` (route, slice that removes it). `mobile.e2e.ts` reads `PHONE_ROUTES` instead of its own `PAGES`.
- **Coverage guard (vitest, `frontend/src/phoneRoutes.test.ts`).** It fails when any `Page` in `routes.ts` `PAGES`,
  `/charts`, or a Copilot tab is neither in `PHONE_ROUTES` nor in `PHONE_PENDING`, so a new page cannot ship without a
  phone check. It also fails when a route is in both lists.
- **Pending routes today:** the 19 routes without a check - strategies, strategy-builder, deployments, fundamentals,
  news-events, quant, backtest, marketplace, coach, options-builder, instruments, portfolio, analytics, journal,
  risk-management, team, admin, system-logs and account. Each one is tagged with its UI2-8 / UI2-9 slice. A route that
  UI2-8 turns into a redirect gets a redirect check instead of a screenshot.
- **Every route** gets, at 360 x 800 and 390 x 844 in light and dark, the existing layout rules plus one new rule:
  - **No sideways-scrolling table.** On a phone, no `table`, `[role=table]` or `[role=grid]` may be wider than its
    scroll container.
  - **Approved carousels only.** A horizontal scroller is allowed only with `data-phone-x-scroll` (chip rows, the
    chart canvas), and the test lists every such element so a reviewer sees them.
- **Size of the job.** About 30 routes x 2 sizes x 2 themes, roughly 120 screenshots. Measure the phone project's
  CI time at UI2-1 and shard it if it goes over the frontend job's budget (time unknown today).
- **Fixtures.** The admin page needs a SUPER_ADMIN fixture user in `mockApi.ts`. Pages behind a flag are shot with the
  flag on in the fixture.

---

## 3. Phase B: Android with Capacitor

The Android app is a Capacitor shell around the same Vite build. Native features are thin wrappers in
`frontend/src/native/`. Each wrapper does nothing on the web and is loaded with a dynamic `import()`, so the web bundle
budget (`scripts/check-bundle-budget.mjs`) does not grow.

### 3.0 Platform baseline (verify at B1)

- **Capacitor.** 7 was the current major in 2025. Per its docs it needs Node 20+, JDK 21, Android minSdk 23 and
  targetSdk 35. A newer major may exist by B1: use the newest stable major, and record its Node, JDK, Gradle and SDK
  requirements in the B1 PR. **(verify)**
- **Google Play target API.** Google Play requires a recent target API for new apps and updates. Check the current
  rule before B4. **(verify)**
- **No web push in the app.** The Android WebView has no Push API, so web push (PW3) cannot work inside the app. That
  is why B2 uses FCM.
- **App origin.** Recent Capacitor majors serve bundled files from `https://localhost` on Android (`androidScheme`
  `https` is the default since Capacitor 6). **(verify)** Call this the **app origin**.

### 3.1 Slices

| Slice | Starts | Scope | Done when |
|---|---|---|---|
| **B1 Shell + config + CI debug APK** | after UI2-1 merges; uses the temporary MA-6 id (MA-1 to log in) | See §3.2 | A debug APK from CI installs on Abhi's phone, logs in to the production domain and shows the v2 shell |
| **B2 FCM push + deep links** (+ share sheet) | after B1, needs MA-1, MA-2 | See §5 and §6 | A PAPER proposal pushed to the phone opens its card; a Telegram "Open in app" link opens the app on the same screen |
| **B3 Biometric unlock + secure storage** | after B1 (can run beside B2) | See §8 | The app is locked behind the fingerprint or device PIN; Approve asks again; the session survives an app restart only in platform secure storage |
| **B4 Signed release + update prompt** | after B2 and B3, needs MA-3 | See §3.5 and §9 | CI builds a signed AAB and APK from a protected job; the app shows "Update available" from the server's version endpoint |

Store prep (BT7): listing text and screenshots are prepared but not published (§3.6).

### 3.2 B1: files to add

**Status (20 Oct 2026): shell part built** on `claude/mobile-b1-capacitor-shell`: Capacitor 8.5.2 (`@capacitor/app`
8.1.2), `frontend/capacitor.config.ts`, the committed `frontend/android/` project, the API base as a build setting
(`frontend/src/api/base.ts`, `frontend/scripts/mobile-rules.mjs`, `frontend/scripts/check-mobile-config.mjs`),
`frontend/src/native/` (platform, versions, the debug build's "not connected" notice) and the `android-debug` job in
`.github/workflows/android.yml`. Per ADR-0028 decision 10 that job builds only a debuggable APK against the `.invalid`
placeholder origin (a layout smoke test that cannot sign in). Still to come in the B1 sign-in PR, after ADR-0028 is
accepted and the 21 Oct load test: the backend (`NATIVE_APP_ORIGINS`, origin-selected body refresh), the client's native
refresh path, the app CSP meta tag, the non-debuggable internal build type with its non-public signing key, the
back-button "Leave ATP?" prompt and the Settings → About versions. How to build and install: `docs/mobile/BUILD.md`.

- **`frontend/capacitor.config.ts`**
  - `appId` from MA-6: `tech.aialgodesk.app` (owner, 2026-10-21; permanent once on Play). The first builds used the temporary, never-published `dev.atp.unpublished`; a build check still refuses a release (signed) build with it. Internal and release builds talk to `https://aialgodesk.tech` by default (D3).
  - `appName` "ATP"; `webDir` "dist"; `android.allowMixedContent: false`.
  - No `server.url`: the app runs its bundled files and never loads the live site into the shell. Capacitor
    documents `server.url` for live reload during development. Loading the live site would also expose the native
    bridge to whatever that site serves.
  - The B1 PR lists every other key it sets.
- **`frontend/android/`**, the native project made by `npx cap add android` and committed, as Capacitor intends:
  - `AndroidManifest.xml`: INTERNET; POST_NOTIFICATIONS from B2; App Links intent filters from B2.
  - `app/build.gradle`: `versionCode` and `versionName` come from Gradle properties that CI passes; the release signing
    config reads environment variables (B4), so no path or password is in git.
  - `.gitignore` covers build outputs, `local.properties`, `app/google-services.json`, `*.jks` and `*.keystore`.
- **`frontend/package.json`**
  - Dependencies: `@capacitor/core`, `@capacitor/android` and `@capacitor/app` (back button, `appUrlOpen`,
    `getInfo()` for the native version). Dev dependency: `@capacitor/cli`. Exact versions are pinned in the B1 PR
    **(verify)**.
  - Scripts: `build:android` (Vite build with the Android mode and env) and `cap:sync`.
- **`frontend/src/native/platform.ts`**
  - `isNativeApp()` wraps `Capacitor.isNativePlatform()`.
  - On the native platform, `pwa.ts` does not register the service worker (files are bundled; no web push in a
    WebView), the install banner is not shown, and Settings shows the native push section (B2) instead of
    `WebPushSection`.
- **`frontend/src/native/backButton.ts`.** Android back goes back in the router's history; at Home it asks "Leave
  ATP?" (no silent exit with an open dialog).
- **About.** Settings → About shows the web build version (`__APP_VERSION__`) and the native version and build
  number (`App.getInfo()`).
- **API base URL** as a build setting (§4.2).
- **Backend: allow the app origin** (§4.3). `NATIVE_APP_ORIGINS` is added to the CORS list. The refresh token is
  returned in the body only to the app origin.
- **`frontend/index.android.html` or a Vite transform** that adds a Content-Security-Policy meta tag for the app build.
  - The web CSP (`frontend/nginx.conf`, `deploy/Caddyfile`) says `connect-src 'self'`. In the app, `'self'` is the
    app origin, so the API domain must be listed.
  - The policy is generated from `VITE_API_BASE_URL` and is otherwise identical to the web one.
- **CI job `android-debug`**, in `.github/workflows/android.yml`:
  - Runs on PRs that touch `frontend/**`, and on `workflow_dispatch`.
  - Steps: setup-node; setup-java (Temurin, at the version from the platform baseline); `npm ci`; `npm run build:android`
    with `VITE_API_BASE_URL=https://${{ vars.ATP_APP_DOMAIN }}`; `npx cap sync android`; `./gradlew assembleDebug`;
    upload `app-debug.apk` as a workflow artifact (7-day retention).
  - The debug APK is signed only with the throwaway Android debug key, never the release key.
  - It needs no secrets. Until B2, the build runs without `google-services.json`; the Gradle plugin is applied only
    when the file exists (Capacitor's template does this).
  - Before MA-1, `ATP_APP_DOMAIN` is unset. The job then builds against a placeholder origin and says so in the job
    summary. Such an APK is only a layout smoke test and cannot log in.

### 3.3 B2 and B3: files to add

**Status (23 Oct 2026, readiness B8):** the B2 push and deep-link part is built as `docs/design/MOBILE_PUSH.md` says
(no migration: phones live in the organisation's FCM alert channel; the native push plug-in waits for MA-2, MA-10).

- B2: `frontend/src/native/push.ts`, `frontend/src/native/deepLinks.ts`, `frontend/src/native/share.ts`,
  `frontend/src/alerts/PhonePushSection.tsx`; backend `app/alerts/fcm.py`, `app/mobile/devices.py` (model + routes),
  one Alembic migration; `frontend/public/.well-known/assetlinks.json` generated at build (§6.3).
- B3: `frontend/src/native/lock.ts` (lock screen + biometric prompt), `frontend/src/native/secureSession.ts` (secure
  storage of the refresh token), `frontend/src/native/LockScreen.tsx`.

### 3.4 Distribution now

- **APK for Abhi's own phone only** (internal testing).
- He downloads the CI artifact while signed in to GitHub, allows "Install unknown apps" for the browser or file
  manager once, and installs it.
- The B4 signed APK replaces debug builds. A debug APK and a release APK have different signatures and cannot update
  each other: uninstall the debug build first.
- No public download link. Firebase App Distribution is an optional later step **(verify terms)**.

### 3.5 B4: signed release build

- **Triggers.** A separate workflow job `android-release`, on a tag `android-v*` or `workflow_dispatch`.
- **Protection.** The job runs in a GitHub **environment `android-release` with Abhi as required reviewer**, so no
  release build runs without his click. Fork PRs never see its secrets.
- **Steps:**
  1. Decode `ANDROID_UPLOAD_KEYSTORE_BASE64` into `$RUNNER_TEMP/upload.jks`.
  2. Decode `ANDROID_GOOGLE_SERVICES_JSON_BASE64` into `frontend/android/app/google-services.json`.
  3. Build web with `VITE_API_BASE_URL=https://${{ vars.ATP_APP_DOMAIN }}`, then `cap sync`.
  4. `./gradlew bundleRelease assembleRelease`, signed through env vars `ANDROID_UPLOAD_KEYSTORE_PASSWORD`,
     `ANDROID_UPLOAD_KEY_ALIAS` and `ANDROID_UPLOAD_KEY_PASSWORD`.
  5. `apksigner verify`.
  6. Upload the AAB and APK as artifacts (14-day retention).
  7. `if: always()`: delete the keystore and `google-services.json` from the runner.
- **Versions.**
  - `versionCode` is `github.run_number` offset by a constant, so it always increases.
  - `versionName` is `package.json` version plus the short sha (the same form as `__APP_VERSION__`).
- **Refusals.**
  - The build refuses to run if `ATP_APP_DOMAIN` is empty, is an IP literal, or the resulting base is not `https://`.
  - A grep step fails if a `.jks`, `.keystore` or `google-services.json` is tracked in git.
- **Secret names** (all in GitHub Actions secrets, environment `android-release`; MA-2 and MA-3 say how to make
  them):

| Name | Kind | Holds |
|---|---|---|
| `ANDROID_UPLOAD_KEYSTORE_BASE64` | secret | the upload keystore file, base64 |
| `ANDROID_UPLOAD_KEYSTORE_PASSWORD` | secret | keystore password |
| `ANDROID_UPLOAD_KEY_ALIAS` | secret | key alias |
| `ANDROID_UPLOAD_KEY_PASSWORD` | secret | key password (same as the store password for a PKCS12 keystore) |
| `ANDROID_GOOGLE_SERVICES_JSON_BASE64` | secret | Firebase `google-services.json`, base64 |
| `ATP_APP_DOMAIN` | variable (not secret) | the production host name, for example `atp.<domain>` |

The FCM **server** credential (a Firebase service account key) is not a CI secret: the backend sends pushes, so it
lives in the server's `/opt/atp/.env` (MA-2).

### 3.6 Play Store preparation (BT7): prepare, do not publish

- **Waits for** company registration (an owner blocker), the compliance pack (BT7) and a Play Console account (MA-4).
- **Prepared in a later docs PR, in `docs/store/android/`:** listing text (short and full description, plain English,
  no performance claims, the PAPER-only and "no recommendations" statements), the privacy policy URL (the existing
  legal pages, Z10-f), answers for the Data safety form, and the content rating questionnaire.
- **Screenshots.** Phone screenshots come from the same Playwright fixtures as the v2 baselines, at the size Play
  asks for **(verify current screenshot rules)**.
- **Policy checks before any submission (verify):** Play's rules for financial-feature declarations, and its rules
  for apps that mainly show web content. Each one gets a note on how ATP meets it.
- Nothing is uploaded to Play until Abhi says so.

---

## 4. Configuration and sign-in in the app

### 4.1 Same app, three hosts

| Build | `VITE_API_BASE_URL` | API calls go to |
|---|---|---|
| Web (today, unchanged) | empty | `/api/v1` on the page's own origin |
| Android debug / release | `https://<ATP_APP_DOMAIN>` | `https://<domain>/api/v1` |
| Local dev | empty (Vite proxy) | `http://localhost:8000` through the proxy |

### 4.2 The API base URL becomes a build setting (B1)

1. **New `frontend/src/api/base.ts`.**
   - It exports `API_ORIGIN`: `import.meta.env.VITE_API_BASE_URL` trimmed, without a trailing slash, `""` when unset.
   - It also exports `apiUrl(path)`, which returns `${API_ORIGIN}/api/v1${path}`.
   - `client.ts` `BASE` and the seven other files listed in §1 use it. The web build has no env value, so it produces
     exactly the URLs it produces today.
2. **New `frontend/scripts/check-api-base.mjs`, run in CI next to the colour lint.** It fails on:
   - any `"/api/` string literal outside `api/base.ts` and tests;
   - a `VITE_API_BASE_URL` that is not `https://`, or whose host is an IPv4 or IPv6 literal;
   - any IP literal (a public one anywhere, tests included) in `frontend/src`, `capacitor.config.ts` or `android/`.
3. **Where the value comes from.**
   - In CI, it comes only from the repository variable `ATP_APP_DOMAIN`.
   - Locally, it comes from `frontend/.env.android.local` (gitignored); a committed `.env.android.example` holds a
     placeholder.
   - It is never written into source files.
4. **Unit tests:** `base.ts` with no value, a domain, and a trailing slash. The IP and http refusals are covered by
   the script's own test.

### 4.3 Sign-in, cookies and CORS in the app (B1, with an ADR)

**How the web signs in today.**

- The access token is kept in memory.
- The refresh token is in an HttpOnly cookie, `SameSite=Strict`, path `/api` (`backend/app/auth/routes.py`
  `_set_refresh_cookie`).
- `REFRESH_TOKEN_IN_BODY` (default true, "for clients built before P0.3") also returns it in the JSON.

**What the app needs.**

- The app origin (`https://localhost`) and the API (`https://<domain>`) are different sites. A `SameSite=Strict`
  cookie is not sent cross-site, so the cookie path cannot work in the app.
- **CORS:** `docker-compose.prod.yml` sets `ALLOWED_ORIGINS=https://${DOMAIN}`. B1 adds `NATIVE_APP_ORIGINS` (default
  empty; the Android value after verification, plus `capacitor://localhost` for iOS in Phase C) and appends it to the
  CORS list.
- **Refresh token:** the server returns the refresh token in the JSON body **only when the request's `Origin` is in
  `NATIVE_APP_ORIGINS`**.
  - A script injected into the web origin cannot fake the `Origin` header, so the web keeps the cookie-only rule.
  - `REFRESH_TOKEN_IN_BODY` can then be turned off for browsers without breaking the app.
- **Where the app keeps tokens:**
  - B1: the refresh token in memory only, so a restarted app asks for login again.
  - B3: in platform secure storage, released only after an unlock (§8).
- **The live stream:** `GET /api/live/stream` already uses `fetch` with a bearer header, so it needs no cookie. It
  only needs the absolute URL and CORS. Check that streaming responses work in the WebView **(verify)**.
- **Broker logins (Upstox OAuth, the UI2-5 Zerodha and Fyers callbacks) open in the system browser, not inside the
  WebView.** Some identity pages refuse embedded WebViews **(verify per broker)**. The callback lands on
  `https://<domain>/...`, which the App Link (§6) hands back to the app. Test this per broker at B2.
- **ADR.** This is an auth change, so it gets a short ADR in B1 (the next free number under `docs/adr/`):
  - origin-gated body refresh;
  - the CORS addition;
  - the threat that a malicious page on `https://localhost` of the user's own computer could call the API. It still
    needs a valid token, because CORS gives it no cookie.

---

## 5. Push notifications (B2): FCM through the existing notification fan-out

### 5.1 What is pushed

The PW3 categories stay. The owner's list maps onto them:

| Owner's item | Notification types (`backend/app/alerts/deeplink.py`) | Category |
|---|---|---|
| Approvals waiting | `AI_PROPOSAL` (and the snooze reminder, which re-announces it) | proposals |
| Alerts | `SCREEN_ALERT`, `OI_BANNER`, `NEWS_ALERT` | alerts |
| Broker login needed | `TOKEN_EXPIRED`, `BROKER_DISCONNECT` (the broker login reminder, `app/brokers/login_reminder.py`, sends `TOKEN_EXPIRED`) | account |
| Also as today | trades, risk, reports | trades, risk, reports |

EMERGENCY ignores preferences, as in PW3.

### 5.2 Backend

- **Flag `mobile_push`.** Registered in `backend/app/platform/controls.py` `FEATURE_FLAGS` and not in
  `DEFAULT_ON_FLAGS`, so it is **off by default**. With it off, the device routes answer 404 with
  `X-Feature-Disabled` (the convention for per-user features), and no FCM outbox row is created.
- **Table `mobile_devices` (one migration):**

| Column | Meaning |
|---|---|
| `id` | key |
| `tenant_id` | the user's organisation (NOT NULL, indexed) |
| `user_id` | the device's owner (NOT NULL) |
| `session_id` | the login session that registered it (FK to user sessions, nullable) |
| `platform` | `android` (later `ios`) |
| `token_encrypted` | the FCM registration token, envelope-encrypted per tenant (`app/secrets_store`, a new purpose) |
| `token_hash` | SHA-256 of the token, unique, for de-duplication without decrypting |
| `label` | a device name the user sees (60 chars) |
| `categories` | JSON list or null (null = all), same values as `PUSH_CATEGORIES` |
| `app_version` | native version and build, for support |
| `created_at`, `last_seen_at`, `revoked_at` | lifecycle |

- **Routes** (all under `/api/mobile/`; each row is added to `backend/tests/tenancy_route_table.py` in the same PR):

| Method and path | Class in `tenancy_route_table.py` | Rule |
|---|---|---|
| `POST /api/mobile/devices` | `self: registers the caller's own phone for push` | Creates or refreshes the caller's device for the caller's tenant and the current session. If the same token is registered to another user, that row is revoked first (a phone changing hands); it is never re-pointed across tenants silently. |
| `GET /api/mobile/devices` | `member: the caller's own devices` | Only the caller's rows; never another member's. |
| `PATCH /api/mobile/devices/{device_id}` | `self: the caller's own device preferences` | Label and categories; another user's or organisation's id answers 404. |
| `DELETE /api/mobile/devices/{device_id}` | `self: removes the caller's own device` | Sets `revoked_at`; another user's or organisation's id answers 404. |
| `GET /api/app/version` | `public: the latest app version and the minimum supported (no user data)` | B4 (§9). |

  The Z10-c sweep (`tests/test_z10c_tenancy_sweep.py`) then checks anonymous refusal, the other-tenant probe and the
  id routes automatically.
- **Session link.** A device whose `session_id` is revoked (sign out, "Sign out everywhere", password change) gets no
  push, so a lost phone stops receiving proposals once Abhi signs out everywhere from the web.
- **Fan-out.** A new `AlertChannelType.FCM` reuses the existing outbox (`dispatcher.enqueue_for_notification`,
  `dispatch_pending`). That keeps idempotency (Z10-b), the stale-alert guard, retries, dead letters and the delivery
  log.
  - **Implicit channel.** One FCM channel row per tenant is created when its first device registers, with the same
    `min_severity` meaning as PUSH. The user never configures it by hand.
  - **Who gets the push.** At send time the FCM sender loads that tenant's active devices. If the notification has a
    `user_id`, only that user's devices get it. Otherwise every active device in the tenant gets it, filtered by each
    device's categories (`push_wanted`).
  - **One outbox row per device.** The rows are keyed by `delivery_key(notification_id, channel_id, address=device_id)`,
    the per-address form the dispatcher already has. One phone failing then never re-sends to the others.
  - **Invalid tokens.** A token that FCM reports gone (`UNREGISTERED`, `NOT_FOUND`) sets `revoked_at` **(verify the
    exact v1 error codes)**.
- **Sender, `backend/app/alerts/fcm.py`.** FCM HTTP v1 (`POST https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send`).
  The legacy server-key API is gone, so v1 needs an OAuth access token minted from a Firebase service account.
  - **No new dependency:** sign the RS256 assertion with `cryptography`, exchange it at
    `https://oauth2.googleapis.com/token` with `httpx` (the same approach as `webpush.py`), and cache the access token
    until shortly before it expires.
  - **Alternative** if that turns out fragile: the `google-auth` library, which needs the owner's OK for a new
    dependency.
  - Both hosts go on the egress allow-list (`app/core/egress.py`).
  - Config: `FCM_PROJECT_ID` and `FCM_SERVICE_ACCOUNT_FILE` (a path to the JSON key mounted read-only into the
    backend and worker) in `/opt/atp/.env`. Without them the FCM channel reports "not configured" in the delivery log
    and sends nothing.
- **Payload.** A notification block (title and short body) plus a data block `{path, notification_id, category}`.
  - `path` is `app_path(notification)`, a same-origin path only, as in PW3.
  - Android notification channels mirror the categories (Proposals high importance; Alerts; Account; Trades; Risk;
    Reports), so the user can tune them in Android settings.
  - **Privacy (MA-7, owner decision 2026-10-20):** FCM payloads are not end-to-end encrypted the way web push
    (RFC 8291) is, so Google's servers can read them. **Minimal content only**, for example "A trade is waiting for
    your approval" or "Broker login needed". No symbol, price, quantity or P&L anywhere in the payload, including the
    data block: `path` carries only opaque ids (for example `/signals/1234`), never a symbol or other query text. The
    details show only inside the app, after unlock (B3). The AI-usage and privacy disclosure gets one line about it.
- **Android 13+.** Notification permission is a runtime prompt. The app asks only after an explain-first screen
  (the PW3 rule), and a refusal is explained, never re-asked in a loop.

### 5.3 Frontend (native only)

- **`frontend/src/native/push.ts`:**
  - `@capacitor/push-notifications` **(verify version and Android channel API)**: `requestPermissions()` after the
    explain screen, then `register()`, then the `registration` token goes to `POST /api/mobile/devices`.
  - The token is refreshed on every app start (`last_seen_at`).
  - `pushNotificationActionPerformed` passes `data.path` to the deep-link router (§6).
- **Settings → Alert delivery → "Phone notifications" (`PhonePushSection.tsx`):** on/off, categories, "Send test"
  (the existing `POST /api/alert-channels/{type}/test` with type FCM), and the device list with Remove.

---

## 6. Deep links (B2)

### 6.1 Scheme

- **Android App Links on the production domain**, `https://<domain>/...`, verified with
  `https://<domain>/.well-known/assetlinks.json`.
- **No custom `atp://` scheme.** Another app can claim a custom scheme; a verified https link cannot be taken that
  way.
- Telegram's existing "Open in app" buttons are already absolute https links (`app_url`, from `FRONTEND_URL`, which
  `docker-compose.prod.yml` derives from `DOMAIN`). Once the domain exists and the app is installed, they open the
  app with **no Telegram code change**. Without the app, they open the web page.

### 6.2 Routes the app opens (allow-list in `frontend/src/native/deepLinks.ts`)

| Link path | Opens | Source today |
|---|---|---|
| `/copilot/watchtower?action=<id>` | the proposal card | `app_path` for a notification with `ai_action_id` |
| `/signals?proposal=<id>` | the proposal in the Signals inbox (after UI2-3; `app_path` switches to it then, and the Watchtower path keeps redirecting) | new with UI2-3 |
| `/positions?trade=<id>` | the position | `app_path` (`related_trade_id`) |
| `/orders?order=<id>` | the order | `app_path` (`related_order_id`) |
| `/charts/<symbol>?tf=<tf>` | the chart (after UI2-1 adds `/charts`) | new: `app_path` gives a screen alert with a symbol this path once `/charts` exists |
| `/option-chain` | option chain (OI banner) | `app_path` |
| `/settings` | broker login needed | `app_path` |
| `/notifications?n=<id>` | the notification | `app_path` |
| `/?broker=...` and the broker callback return pages | the connect flow step 3 | broker OAuth return |

- **Handling.** `App.addListener("appUrlOpen")` and the push tap both call `openDeepLink(url)`. It:
  - accepts only the app's own domain, or a bare path;
  - matches the path against the allow-list and only the listed query keys, with ids as integers and symbols against
    the instrument pattern;
  - navigates with the router.
  Anything else opens Home. A link never triggers an action by itself: it opens a screen, and the user still presses
  Approve. With the B3 lock on, the app unlocks first and then opens the screen.
- **`assetlinks.json` (B2).**
  - It is generated at build time into `frontend/public/.well-known/assetlinks.json` from the package name (MA-6) and
    the SHA-256 fingerprints: the upload key (MA-3) for sideloaded builds, plus the Play app-signing key later
    (MA-4). Fingerprints are public, not secrets.
  - nginx (`frontend/nginx.conf`) serves it as `application/json` without a redirect **(verify Caddy passes it
    through unchanged)**.

### 6.3 Share sheet for charts (B2)

- Charts → More → Share. On the web it uses the Web Share API, falling back to "Download image".
- In the app it uses `@capacitor/share` with a PNG of the chart (lightweight-charts `takeScreenshot()`), written to
  the cache with `@capacitor/filesystem`, plus the `https://<domain>/charts/<symbol>?tf=<tf>` link **(verify `files`
  support in the share plugin)**.
- The image is the user's own chart. No account data is drawn on it unless the user turned on the trades layer, and
  the share dialog says so.

---

## 7. Safety rules (every slice)

1. **The AI never places an order.** Nothing in the app adds an order path. The Z10-c static order-path guard and the
   `phase0_paper_only` lock stay as they are, and the native code has no broker client.
2. **Approvals only through the existing flow.** The app calls the same `POST /api/ai/actions/{id}/approve|reject|snooze`
   as the web card. In Phase 0 that is Approve (PAPER). LIVE keeps the server step-up unchanged. The biometric check
   (§8) is an extra local confirmation before the request; it never replaces a server check.
3. **Exits are never blocked.**
   - Protective exits run on the server whatever the phone does.
   - "Exit now" needs no extra prompt beyond the app unlock. The unlock always accepts the device PIN or pattern, so
     a failed fingerprint sensor never locks anyone out.
   - The "update required" screen (§9) keeps an "Open ATP in the browser" link, so an old app never stands between
     the user and Positions.
   - No push preference or snooze holds an exit.
4. **PAPER only in Phase 0.** No LIVE switch appears in the app that the web does not have.
5. **No secrets in git or in the APK.**
   - The keystore and passwords live only in CI secrets.
   - `google-services.json` is in CI secrets too. Its values do end up inside the APK; that is how Firebase works and
     they are not server credentials.
   - The FCM service account key lives only in the server `.env`.
   - The API domain is a variable; there is no IP anywhere.
6. **Flags off by default.** `mobile_push`, and anything else the app adds on the server, is off until Abhi turns it
   on.

---

## 8. Biometric unlock and secure storage (B3)

- **Candidate plugins (evaluate at B3; none chosen yet):**
  - Android BiometricPrompt with device-credential fallback: `@aparajita/capacitor-biometric-auth`, or
    `@capgo/capacitor-native-biometric` (which also stores credentials).
  - Secure storage backed by the Android Keystore: `@aparajita/capacitor-secure-storage`.
  - **Not** `@capacitor/preferences`, which is plain storage.
  - Pick on: maintained for the Capacitor major from B1, licence, Keystore-backed, device-credential fallback, and an
    iOS Keychain / Face ID equivalent for Phase C. Record versions and the licence in the PR. **(verify all)**
- **Lock.**
  - On a cold start, and on resume after 5 minutes in the background (a setting, 1-30 min), the app shows the lock
    screen before any page renders.
  - Page content is not mounted until unlock. A privacy screen keeps the recent-apps thumbnail blank (Android
    `FLAG_SECURE`, through a small plugin or native code **(verify)**).
- **Unlock** uses BiometricPrompt with device credential allowed. On success, `secureSession.ts` reads the refresh
  token from secure storage and calls `/auth/refresh`. The access token stays in memory, as on the web.
- **Before Approve.** The card asks for a fresh biometric or device-credential confirmation, then sends the request.
  Reject, Snooze and Exit now do not ask again.
- **No screen lock on the phone (owner decision, MA-8).** The app does not keep the session; the user signs in each
  time. Approve then shows the confirm dialog only, and the app explains how to set a screen lock.
- **Sign out** clears secure storage, calls `DELETE /api/mobile/devices/{id}`, and revokes the session on the server.
  "Sign out everywhere" on the web stops this phone's session and its pushes (§5.2).
- **Rotation.** The refresh token rotates on every use (existing). The app writes the new one to secure storage
  before using the access token, so a crash in between costs at most one login.

---

## 9. Version and "Update available" (B1 shows the version; B4 adds the prompt)

- **The web app stays as it is:** the service worker update banner (PW1).
- **The Android app bundles its web files,** so a server deploy does not change the app's code. Two things follow:
  - **API compatibility.** Backend changes stay backward compatible with the oldest supported app version.
    Additions are fine; removals and renames wait for a minimum-version bump.
  - **Version endpoint.** `GET /api/app/version` (public, no user data) returns
    `{android: {latest_version_code, latest_version_name, min_supported_version_code, notes}}`. The values come from
    server settings that Abhi updates after a release (`ANDROID_APP_LATEST_VERSION_CODE`, `..._NAME`,
    `ANDROID_APP_MIN_SUPPORTED_VERSION_CODE`, `ANDROID_APP_DOWNLOAD_URL`), defaulting to 0, which means "no prompt".
- **The prompt:**
  - On start and on resume (at most once an hour), the app compares its `versionCode` with the endpoint.
  - Behind: a dismissible "Update available" banner (the PwaBanners pattern).
  - Below the minimum: a full screen "This version is too old. Install the update." with "Open ATP in the browser"
    (safety rule 3).
  - Sideloaded builds link to the release page Abhi names (MA-9). After Play, Play's in-app update API can replace
    the link (`@capawesome/capacitor-app-update` is one option **(verify)**).
- **Live updates of the web files** (over-the-air bundles) are out of scope. They add a signing and rollback system of
  their own. Revisit only if store review times hurt.

---

## 10. Tests per slice

| Slice | Tests |
|---|---|
| Phase A (each UI2 PR) | Phone layout rules + screenshots for the routes it touches (360, 390; light, dark); route-coverage guard; no sideways-scrolling table; the "needs-domain" install and push states (unit + one screenshot) in UI2-1 / UI2-6 |
| B1 | `api/base.ts` unit tests; `check-api-base.mjs` and its own test (refuses IP, http, stray `/api/` literals); backend: CORS allows `NATIVE_APP_ORIGINS` only when set, body refresh only for an app origin (both cookie paths unchanged for the web), the existing auth tests unchanged; frontend: `isNativeApp()` false on web, so the service worker, install banner and web push section behave as today (existing tests stay green); CI `android-debug` builds the APK; manual: install on Abhi's phone, sign in, bottom bar, back button, keyboard over fields, safe areas (§11) |
| B2 | Backend: device routes in `tenancy_route_table.py`, so the Z10-c sweep covers them (anonymous refused, other organisation 404, viewer rules); a device of user A never gets user B's user-targeted notification; a revoked session gets no push; the flag off gives 404 and no outbox row; FCM sender with a mocked HTTP v1 and OAuth endpoint (payload shape, minimal content, gone-token revoke, retry, no secret in logs); per-device idempotency; EMERGENCY ignores categories; `app_path` new targets. Frontend: deep-link allow-list (accepted paths, rejected hosts, bad ids, unknown keys open Home); push tap routes; share fallback on web. Manual: a PAPER proposal pushed, tapped, decided; a Telegram "Open in app" opens the app; Upstox login returns to the app |
| B3 | Lock state machine (cold start, resume after the timeout, under it); Approve asks for confirmation and Reject / Snooze / Exit now do not; no screen lock gives no stored session; sign out clears storage and unregisters the device; refresh rotation writes before use. Manual on the phone: fingerprint, PIN fallback, lockout fallback |
| B4 | The release job refuses an empty, IP or http domain; no keystore or `google-services.json` tracked (grep step); `apksigner verify` passes; `versionCode` increases; `GET /api/app/version` public and data-free (tenancy table class `public`); banner and the below-minimum screen with the browser link |

---

## 11. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| No domain yet (MA-1) | No PWA install, no web push, no App Links, no release build | Phase A ships anyway and says "needs the secure web address"; B1 can build against a placeholder; B2 and B4 wait for MA-1 |
| Safe-area insets in the Android WebView (edge-to-edge on newer Android) | Bottom bar under the gesture bar, top bar under the status bar | Test on Abhi's phone in B1; Capacitor's status-bar or system-bars options **(verify)**; fixed padding fallback |
| Bundled app vs a newer server | Old app calls a changed API | Backward-compatible API rule, the minimum version and the browser link (§9) |
| Cross-site cookies in the app | Refresh fails | Origin-gated body refresh in secure storage (§4.3, ADR) |
| FCM payloads readable by Google | Privacy | Minimal content (owner decision MA-7: no symbol, price or P&L), disclosure line |
| FCM delivery under Doze / battery saver | Late proposal push | High priority only for the proposals and account channels; Telegram stays the second path; a proposal expiry is never shortened for push |
| Broker login pages refusing a WebView | Cannot connect a broker in the app | System browser plus App Link return; per-broker test at B2 |
| Upload keystore lost before Play | Sideloaded installs cannot update; reinstall needed | Two offline backups (MA-3); after Play, Play App Signing allows an upload-key reset |
| Play policy (financial features, web-content apps) | Listing refused | Checked in BT7 before any submission; app has native push, biometric lock, share and offline screen |
| Plugin abandonment | Stuck on an old Capacitor major | Prefer official `@capacitor/*` plugins; community plugins behind a small wrapper in `src/native/`, so one can be replaced |
| CI time | Phone screenshots for every route plus Android builds | Shard the phone project; Android jobs only on `frontend/**` changes |

---

## 12. Phase C: iOS (later, not now)

- **Owner prerequisites:**
  - an Apple Developer Program membership (MA-5, annual fee - check Apple's current price);
  - a macOS CI runner. GitHub-hosted macOS minutes cost more than Linux minutes **(verify current rates)**; a
    self-hosted Mac is the alternative.
- **Same project:** `npx cap add ios` adds `frontend/ios/`. No second UI.
- **Push:** FCM can relay to APNs with an APNs auth key uploaded to the same Firebase project, so `fcm.py` stays as
  it is and the device row says `platform = ios` **(verify)**.
- **Universal Links:** `https://<domain>/.well-known/apple-app-site-association` with the same allow-listed paths.
- **Biometrics and storage:** Face ID / Touch ID and the Keychain through the plugins chosen in B3, which is why B3
  must pick plugins that support iOS. `NSFaceIDUsageDescription` in `Info.plist`.
- **App origin:** `capacitor://localhost` **(verify)**, added to `NATIVE_APP_ORIGINS`.
- **Keep it a build step:**
  - no Android-only branches in shared TypeScript beyond `Capacitor.getPlatform()` checks inside `src/native/`;
  - no Android-only API in the backend (device `platform` is a column, not a code path);
  - the deep-link allow-list and version endpoint are platform-neutral (the endpoint grows an `ios` block).
- **App Store review** has its own rules on financial apps and wrapped web apps; check them in BT7 **(verify)**.

---

## 13. Order and dependencies

```
UI2-0 (merged) -> UI2-1 shell (+ needs-domain message, route-coverage guard) -> UI2-2 .. UI2-10 (each with its phone layout)
                  |
                  +-> B1 shell + CI debug APK (temporary MA-6 id)
                        |-> B2 FCM push + deep links + share (needs MA-1, MA-2)
                        |-> B3 biometric unlock + secure storage
                        +-> after B2 and B3: B4 signed release + update prompt (needs MA-3)
                              +-> BT7 store prep, not published (needs MA-4 and company registration)
Phase C iOS: after B4, needs MA-5 and a macOS runner.
```
