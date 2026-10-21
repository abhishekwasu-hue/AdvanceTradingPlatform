# Building and installing the Android app

The Android app is a Capacitor shell around the same React build as the web app (`docs/design/MOBILE_APP_PLAN.md`
§3). This page covers slice **B1**: the shell, its build rules, the CI debug APK and how Abhi installs it (MA-9).
Sign-in from the app comes in a later PR (ADR-0028); until then **every APK this page produces is a layout smoke test
that cannot reach a server**.

## What is pinned

| Piece | Version | Where |
| --- | --- | --- |
| `@capacitor/core`, `@capacitor/android`, `@capacitor/cli` | 8.5.2 (exact) | `frontend/package.json` |
| `@capacitor/app` (back button, `App.getInfo()` for the native version) | 8.1.2 (exact) | `frontend/package.json` |
| Node | 22 or later (the Capacitor 8 CLI refuses older) | `.github/workflows/android.yml` |
| JDK | 21 (Temurin in CI) | `frontend/android/app/capacitor.build.gradle` |
| Gradle wrapper | 8.14.3 | `frontend/android/gradle/wrapper/gradle-wrapper.properties` |
| Android Gradle Plugin | 8.13.0 | `frontend/android/build.gradle` |
| compileSdk / targetSdk / minSdk | 36 / 36 / 24 (Android 7.0) | `frontend/android/variables.gradle` |

All four Capacitor packages were at least two weeks old when pinned (8.5.2: 11 Sep 2026; app 8.1.2: 2 Oct 2026).
Upgrade them together and re-run `npx cap sync android`.

## Build kinds and the rules every build follows

One set of rules (`frontend/scripts/mobile-rules.mjs`) is read by `capacitor.config.ts`, `vite.config.ts` and the lint
`frontend/scripts/check-mobile-config.mjs`; Gradle has its own copy as a last stop
(`frontend/android/app/atp-build-rules.gradle`). The build kind comes from `ATP_ANDROID_BUILD`.

| Kind | Debuggable | API origin | Bridge logging / WebView debugging | Package id |
| --- | --- | --- | --- | --- |
| `debug` (default) | yes | the placeholder `https://atp-not-configured.invalid`, which never resolves (`VITE_API_BASE_URL` must be unset; `ATP_APP_DOMAIN` is ignored) | on | `tech.aialgodesk.app.debug` |
| `internal` (sign-in PR) | no | `VITE_API_BASE_URL` if set, else `https://<ATP_APP_DOMAIN>` if set, else `https://aialgodesk.tech` | off (`loggingBehavior: "none"`, `webContentsDebuggingEnabled: false`) | `tech.aialgodesk.app` |
| `release` (B4) | no | as `internal` | off | `tech.aialgodesk.app` (MA-6); the old temporary id is refused |

The default production origin is `PRODUCTION_API_ORIGIN` in `frontend/scripts/mobile-rules.mjs` (MA-6, owner
2026-10-21). The CI job passes the repository variable `ATP_APP_DOMAIN` (`aialgodesk.tech`) to every step.

Refused everywhere: an API base that is not `https://`, an IP address (any spelling), `localhost`, or anything with a
path; an `ATP_APP_DOMAIN` that is not a plain host name; a debuggable build pointed at a real server (ADR-0028
decision 10); a release build with the old temporary package id `dev.atp.unpublished` (MA-6); a web bundle in
`dist/` made for another build kind (it carries `atp-native-build.json`); any `"/api/..."` literal in `frontend/src` outside `src/api/base.ts`; any IP address in
`frontend/src`, `capacitor.config.ts` or `frontend/android` (the production IP nowhere at all); a keystore,
`google-services.json` or `local.properties` in `frontend/android`; a package id in `build.gradle` (`namespace`,
`applicationId`), `strings.xml` or the `MainActivity` package that differs from `APP_ID`.

The Android project allows no plain-http traffic (`usesCleartextTraffic="false"` and
`res/xml/network_security_config.xml`) and trusts only the system's certificate authorities: no user-added or
self-signed certificates, so the old bare-IP setup with Caddy's internal certificate cannot be reached, by design.
`capacitor.config.ts` sets no `server.url`, `server.cleartext` or `allowNavigation`.

`ATP_ANDROID_BUILD`, `VITE_API_BASE_URL` and `ATP_APP_DOMAIN` are read from the **process environment only** (not
from `.env` files), so `npm run build:android` and `npx cap sync android` always see the same values.

The web build is unchanged: `npm run build` keeps the relative `/api/v1`, ignores both variables, and contains no
Capacitor code (the native code sits behind the build constant `__ATP_NATIVE__`, which is `false` there).

## Build it locally

You need Node 22+, JDK 21 and the Android SDK with platform 36 (Android Studio installs it). Point Gradle at the SDK
with `ANDROID_HOME`, or with `sdk.dir=...` in `frontend/android/local.properties` (gitignored).

```bash
cd frontend
npm ci
npm run check:mobile                # the lint; also runs in the frontend CI job
npm run build:android               # vite build --mode android (a debug build: placeholder origin)
npx cap sync android                # copies dist/ into android/app/src/main/assets/public
npm run check:mobile -- --bundle    # dist/ matches this build
cd android && ./gradlew assembleDebug
# -> android/app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk   # a phone with USB debugging on
```

`npx cap open android` opens the project in Android Studio instead. Optional Gradle properties:
`-PatpVersionCode=<n>` and `-PatpVersionName=<name>` (CI passes the run number and `<package version>+<short sha>`).

## How CI builds it

`.github/workflows/android.yml`, job `android-debug`, runs on every push or pull request that changes `frontend/**`
and on demand (Actions → Android → Run workflow). It uses no secrets:

1. validates the committed Gradle wrapper; sets up Node 22 and Temurin 21 (the runner image has the Android SDK);
2. `npm ci`, the mobile lint (also checks the `ATP_APP_DOMAIN` repository variable when it is set: a host name, never
   an IP), `npm run build:android` (debug, placeholder), `npx cap sync android`, the lint again with `--bundle`;
3. `./gradlew assembleDebug`, then checks the APK: package `tech.aialgodesk.app.debug`, debuggable, and the bundled
   `atp-native-build.json` names the placeholder origin;
4. uploads `atp-debug-<version>.apk` as the artifact **`atp-android-debug-apk`** (kept 7 days) and writes a run summary.

The production-pointing build (kind `internal`, not debuggable, signed with a non-public key from a protected
environment) is added with the sign-in PR, after ADR-0028 is accepted.

## Install the debug APK on your phone (MA-9)

The APK comes only from the workflow run's downloads on GitHub; there is no public download link.

1. On GitHub (signed in), open **Actions → Android**, pick the latest green run for the branch you want (`main` for
   what is merged), and scroll to **Artifacts**.
2. Download **`atp-android-debug-apk`**. GitHub always delivers a `.zip`; open it (the Files app can extract it) to get
   `atp-debug-<version>.apk`. On a computer, copy the APK to the phone instead (USB, or your own cloud drive).
3. Tap the APK. The first time, Android asks to allow **Install unknown apps** for the browser or file manager you
   used: allow it for that app, go back, tap **Install**. Google Play Protect may warn about an unknown developer:
   choose **More details → Install anyway**. You can switch "Install unknown apps" off again afterwards.
4. Open **ATP**. A notice says "Test build: this app is not connected to ATP..." with the version. Close it to look at
   the layout underneath (the sign-in screen; it cannot sign in).

**Updating:** each CI run signs with a fresh throwaway debug key, and Android refuses an update signed with a different
key. Uninstall the previous ATP debug build first (long-press the icon → App info → Uninstall); it holds no session
or data worth keeping. Later, the signed builds (internal, then release) replace debug builds the same way: uninstall
first, because the signatures differ.

**What to look at on the phone (plan §10 / §11):** the bottom bar and top bar clear the gesture bar and status bar
(safe areas on edge-to-edge Android), the keyboard does not cover fields, the Android back button goes back through
pages, and text and touch targets are readable at your phone's font size.

## The package id (MA-6)

The id is `tech.aialgodesk.app` (owner, 2026-10-21), the reverse of the production domain. It is permanent once on
Play. It lives in:

1. `APP_ID` in `frontend/scripts/mobile-rules.mjs` (read by `capacitor.config.ts`);
2. `namespace` and `applicationId` in `frontend/android/app/build.gradle`, `package_name` / `custom_url_scheme` in
   `frontend/android/app/src/main/res/values/strings.xml`, and `MainActivity` in
   `frontend/android/app/src/main/java/tech/aialgodesk/app/`;
3. `TEMPORARY_APP_ID` (`dev.atp.unpublished`, the first builds' id) stays as it is: release builds refuse it. The lint
   checks that steps 1 and 2 agree.

A phone with a build of the old temporary id needs a fresh install (a new id is a new app), and Firebase needs this id
registered (MA-2).

## iOS later (Phase C)

Nothing here blocks `npx cap add ios`: `capacitor.config.ts` keeps Android settings under `android`, the native code in
`frontend/src/native/` asks Capacitor for the platform instead of assuming Android, and the build rules are not
Android-specific apart from the Gradle copy.
