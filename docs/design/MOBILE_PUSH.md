# Readiness B8: phone notifications, deep links and the store listing prep

Order: `docs/owner-orders/ATP_SESSION_PRODUCTION_READINESS.md` B8. Plan this builds on (not restated here):
`docs/design/MOBILE_APP_PLAN.md` §5 (push), §6 (deep links), §3.6 (store prep). Owner items: MA-2, MA-3, MA-4, MA-7,
MA-10..MA-13 in `docs/OPEN_QUESTIONS.md`.

## 1. What this slice built

| Part | Where | State |
|---|---|---|
| Web Push key rule | `backend/app/alerts/webpush.py` (`accepting_subscriptions`, `status_line`), `alerts/routes.py` | In production / staging a server without `VAPID_PRIVATE_KEY` refuses new browser subscriptions (409, plain sentence) and `GET /api/alert-channels/push/public-key` says `accepting: false`. A throwaway key made every subscription die silently at the next restart. Development keeps the ephemeral key. |
| Health line | `deploy/atp.sh` `report_settings` (printed by `health`, `deploy`, `bootstrap`) | `browser notifications (Web Push): VAPID_PRIVATE_KEY is not set - the app refuses new browser subscriptions until it is`, or `... set - subscriptions survive restarts`; a warning when `VAPID_SUBJECT` is still the example address; one line for FCM. Admin → Readiness has the same two items (`push`, `fcm`). |
| New alerts channel `FCM` | `backend/app/alerts/fcm.py` (sender), `push_kinds.py` (kinds, screens, text), `channels.py` (`FcmConfig`), `dispatcher.py` (`_fcm_rows`, `send_fcm`) | One outbox row per phone through the existing outbox (idempotency keys, retries, dead letters, delivery log). Flag `mobile_push` (OFF). Nothing sent without the server key. |
| Device routes | `backend/app/mobile/devices.py`, `/api/mobile/devices` | The caller's own phones only (list, register, rename / kinds, remove, test). Classified in `tests/tenancy_route_table.py`. |
| Deep-link router | `frontend/src/native/deepLinks.ts` (pure), `native/links.ts` (Capacitor glue), `NativeFrame.tsx` | App Links (`appUrlOpen`, the launch URL) and notification taps open allow-listed screens; anything else opens Home. |
| App Links filter | `frontend/android/app/src/main/AndroidManifest.xml` | `https://aialgodesk.tech` with the allow-listed paths, `autoVerify`. Verification waits for `assetlinks.json` (MA-11). |
| Phone section | `frontend/src/alerts/PhonePushV2.tsx` in Settings → Alerts (`ui_v2` + `mobile_push`, inside the app only) | Explain first, then Android's permission prompt; the four kinds per phone; Send a test; Remove. |
| Native push wrapper | `frontend/src/native/push.ts` | Reaches Capacitor's `PushNotifications` plug-in through `registerPlugin`; builds without it. The plug-in itself is added with MA-2 (MA-10). |
| Store listing prep | `docs/store/android/LISTING.md` | Text, Data safety answers (from A5), screenshots list, content rating, policy checks. iOS later. |

**Migration: none.** Phones are stored like browser subscriptions: inside the organisation's alert channel
(`alert_channels`, `channel_type = 'FCM'`, the existing `String(20)` column), in its encrypted config. The plan's
`mobile_devices` table is not needed for this slice; it becomes useful only for the session link (MA-13).

## 2. The four kinds

| Kind | Notifications | Opens | Text on the phone (MA-7: fixed, no symbol / price / P&L) |
|---|---|---|---|
| signals | `AI_PROPOSAL` whose AI action is `ENTRY` (the user's own strategy signal) | `/signals?proposal=<id>` | "A signal is waiting for you" |
| approvals | every other `AI_PROPOSAL` (exit now?, reduce, pause, review) | `/copilot/watchtower?action=<id>` | "A decision is waiting for you" |
| exits | `EXIT`, `EMERGENCY_EXIT` | `/positions?trade=<id>` | "A position was closed" |
| renew | `TOKEN_EXPIRED`, `BROKER_DISCONNECT` (the pre-open login reminder) | `/broker` | "Broker login needed" |

- **Routing.** A notification with a `user_id` reaches only that member's phones; an organisation-wide one reaches
  every active member's phones. Each phone has its own kinds (default: all four). EMERGENCY severity always goes and
  opens Positions. Other notification types are not sent to phones.
- **Quiet hours.** The organisation's quiet hours (`notification_policies`, the same window the alert engine uses)
  deliver a non-urgent push without sound (Android channel `quiet`, normal priority); CRITICAL and EMERGENCY ring.
  Nothing is held or dropped, so an approval never expires waiting for the morning.
- **Not sent on purpose** = SUPPRESSED with a reason, never retried and never a silent "sent": `flag_off`,
  `not_configured` (no server key), `device_removed`, `device_gone` (FCM said the token no longer exists; the phone
  is removed).
- **Browsers.** The same entry signals now open their Signals item in a browser push too (`web_push_path`).
- **Telegram** is unchanged.

## 3. Server settings (names only; values only in `/opt/atp/.env`)

| Name | What |
|---|---|
| `VAPID_PRIVATE_KEY` | Web Push private key (base64url). Generate once (runbook section 4, "Browser and phone notifications"); keep it stable. |
| `VAPID_SUBJECT` | `mailto:` contact the push services may write to. |
| `FCM_SERVICE_ACCOUNT_JSON_BASE64` | The Firebase service account key (JSON), base64 on one line. |
| `FCM_SERVICE_ACCOUNT_FILE` | Alternative: a path inside the containers to the key file (needs a mount; the base64 value needs none). |
| `FCM_PROJECT_ID` | Optional; the key's own `project_id` when empty. |

FCM HTTP v1 is called with no new dependency: an RS256 assertion signed with `cryptography`, exchanged at
`oauth2.googleapis.com` with `httpx`, the access token cached.

## 4. Safety

- A notification or a link opens a screen; it never approves, places, blocks or changes anything (ADR-0006). The
  user still presses Approve (PAPER in Phase 0). Exits are never blocked (ADR-0004): nothing here sits in an exit path.
- Signal wording: structure words only, no buy / sell / target / stop-loss, no numbers (`push_kinds.PHONE_TEXT`,
  tested).
- No secret in git or the APK: the server key lives only in `.env`; `google-services.json` only in CI secrets
  (MA-2); tokens never leave the encrypted channel config.

## 5. Left for later (each logged in `docs/OPEN_QUESTIONS.md`)

- MA-10: add the pinned `@capacitor/push-notifications` plug-in and the `google-services.json` CI step with MA-2.
- MA-11: `assetlinks.json` with the signing fingerprints (MA-3 / MA-4) so App Links verify.
- MA-12: one line in the privacy notice about phone notifications passing through Google (lawyer, next version).
- MA-13: the session link (sign out everywhere stops the phone's notifications) with B3.
