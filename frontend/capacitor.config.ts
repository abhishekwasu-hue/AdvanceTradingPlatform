import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { CapacitorConfig } from "@capacitor/cli";
import { APP_ID, NATIVE_BUILD_MARKER, resolveNativeBuild } from "./scripts/mobile-rules.mjs";

/**
 * Mobile B1 (docs/design/MOBILE_APP_PLAN.md §3.2, ADR-0028 decision 10): the Android shell around the same Vite build.
 *
 * - appId: MA-6, the temporary never-published id `dev.atp.unpublished` until the domain (MA-1) and company name are
 *   settled; then `com.<domain>.app`, permanent once on Play. Set in scripts/mobile-rules.mjs (APP_ID); it must match
 *   `applicationId` in android/app/build.gradle. A release build with the temporary id is refused.
 * - webDir: the bundled web files. There is deliberately no `server.url`: the app never loads the live site into the
 *   shell (that would hand the native bridge to whatever the site serves).
 * - Bridge logging and WebView debugging only in a debug build, which can never reach a real server (its API origin is
 *   the `.invalid` placeholder).
 *
 * ATP_ANDROID_BUILD (debug | internal | release) and VITE_API_BASE_URL come from the environment, the same for
 * `npm run build:android` and `npx cap sync android`. A bad combination, or a web build in dist/ made for another
 * build kind, makes this file throw, so no Capacitor command runs with it.
 */
const build = resolveNativeBuild(process.env, APP_ID);

// Capacitor runs from the frontend directory (it finds this file there).
const markerPath = join(process.cwd(), "dist", NATIVE_BUILD_MARKER);
if (existsSync(markerPath)) {
  const made = JSON.parse(readFileSync(markerPath, "utf8")) as { kind?: string; apiOrigin?: string };
  if (made.kind !== build.kind || made.apiOrigin !== build.apiBase) {
    throw new Error(
      `dist/ was built for a ${made.kind} build against ${made.apiOrigin}, but this is a ${build.kind} build against ` +
      `${build.apiBase}. Run \`npm run build:android\` with the same ATP_ANDROID_BUILD and VITE_API_BASE_URL first.`,
    );
  }
}

const config: CapacitorConfig = {
  appId: APP_ID,
  appName: "ATP",
  webDir: "dist",
  // Capacitor's "debug" logs bridge calls only in a debuggable build; a build that can reach a server logs nothing.
  loggingBehavior: build.debuggable ? "debug" : "none",
  server: {
    // The default since Capacitor 6, stated so the app origin (https://localhost, ADR-0028) cannot drift.
    androidScheme: "https",
  },
  android: {
    allowMixedContent: false,
    webContentsDebuggingEnabled: build.debuggable,
  },
};

export default config;
