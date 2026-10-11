# Installable app (PWA)

Phase 0 ships the mobile and desktop app as a Progressive Web App (owner decision, `docs/BETA_LAUNCH_SPEC.md` PWA-first
addendum). Store builds (Capacitor) and the desktop wrapper (Tauri) wait for Phase 1. This page covers PW1, the shell.

## What is installed

| Piece | File | Notes |
| --- | --- | --- |
| Manifest | `frontend/public/manifest.webmanifest` | name, `ATP` short name, standalone, `start_url=/?source=pwa`, dark surface token as theme/background, shortcuts: Dashboard, Screener, Option chain, Alerts |
| Icons | `frontend/public/icons/` | 192/512 "any" + 192/512 "maskable" + 180 Apple touch icon, drawn from the logo by `frontend/scripts/gen-icons.py` (Pillow; PNGs are committed) |
| Service worker | `frontend/public/sw.js` | Web Push (Phase O3) + the app-shell cache |
| Offline page | `frontend/public/offline.html` | shown for any page opened without a connection |
| Install / update UI | `frontend/src/pwa/` | `beforeinstallprompt` button (Android, desktop Chrome/Edge), "Add to Home Screen" guide on iPhone/iPad Safari, "A new version is ready - Reload" |

## Caching rules (never cache market data)

- Cached: hashed build files under `/assets/` (immutable names), `/icons/`, and the precache list (offline page, favicon,
  one icon, the manifest). Nothing else.
- Never cached: `/api/*` (every price, position, alert and account response), other origins (fonts), non-GET requests.
  The worker does not even intercept them - they go to the network exactly as before.
- Pages (navigations) always come from the network. Offline, the user gets `offline.html`, never a stale screen.
- Tests: `frontend/test/pwa-shell.test.ts` runs the real `sw.js` in a sandbox and checks each rule.

## Updates

The build stamps `sw.js` with a hash of the emitted file names (`atpServiceWorker` in `frontend/vite.config.ts`). Any
code change gives a new worker; the browser finds it on the next visit (nginx serves `sw.js` with `no-cache`) or within
30 minutes / when the tab becomes visible. The new worker waits; the page shows "A new version is ready" and **Reload**
switches to it (it never swaps code under a running screen). Old `atp-shell-*` caches are deleted when the new worker
activates. A redeploy of identical files keeps the same id and prompts nobody.

## Platform notes

- Android / desktop Chrome, Edge: installable from the in-app "Install app" button or the browser menu.
- iPhone / iPad: Safari -> Share -> Add to Home Screen (the in-app guide says so). Web push on iOS works only in the
  installed app (iOS 16.4+), which PW3 relies on.
- The service worker registers only in production builds (`npm run build`); the dev server is never cached.

## Checks done for PW1

- Chromium `Page.getInstallabilityErrors`: none (normal profile). Manifest errors: none.
- Offline navigation returns the offline page; the cache holds only `/assets/*`, icons and the precache list
  (screenshot `docs/screenshots/pw1/pw1-offline-360.png`; iOS guide `pw1-ios-guide-390.png`).
- Lighthouse 12 removed its PWA category; the installability criteria it used (manifest fields, 192/512 icons, a
  service worker with a fetch handler, an offline response) are what the checks above and the unit tests cover.

## Push notifications (PW3)

- **Where.** Settings -> Alert delivery -> Push notifications. One push channel per organisation, one subscription per
  device (up to 20); "Send test" goes out at once; the last push deliveries are listed with their status.
- **Asking.** The browser is asked only after the user has read what they will get (the categories ticked, the severity
  floor, "emergencies always come through") and pressed Continue. A blocked permission is explained (site settings),
  never re-asked in a loop.
- **iPhone / iPad.** Web push works only in the app added to the Home Screen (iOS 16.4+). In Safari the section says
  so and the Enable button stays off until the app is opened from the Home Screen.
- **Categories.** proposals, trades, alerts, risk, account, reports (`backend/app/alerts/deeplink.py`). A category
  that is off creates no outbox row. EMERGENCY notifications ignore the preference. A channel saved before PW3 has no
  preference, which means every category.
- **Deep links.** Every push carries the in-app path of its subject: `/copilot/watchtower?action=<id>` for a
  proposal, `/positions?trade=<id>`, `/orders?order=<id>`, `/option-chain` for an OI banner, `/settings` for a broker
  login, else `/notifications?n=<id>`. Paths only, so a payload cannot open another site. The service worker focuses
  an open app window and navigates it, or opens a new one; the Watchtower and the Notifications feed scroll to and
  outline the item.
