/// <reference types="vitest/config" />
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { APP_ID, NATIVE_BUILD_MARKER, resolveNativeBuild } from "./scripts/mobile-rules.mjs";

/** PW1: stamps public/sw.js (copied to the build as-is) with a build id - a hash of the emitted file names, which
 * change whenever any code or style changes. A new id makes the browser install the new worker, and the page then
 * offers "Reload". An unchanged build keeps the same id, so a redeploy of identical files prompts nobody. */
export function atpServiceWorker(): Plugin {
  let outDir = "dist";
  let files: string[] = [];
  return {
    name: "atp-service-worker",
    apply: "build",
    configResolved(config) { outDir = config.build.outDir; },
    generateBundle(_options, bundle) { files = Object.keys(bundle).sort(); },
    writeBundle() {
      const id = createHash("sha256").update(files.join("\n")).digest("hex").slice(0, 12);
      const path = join(outDir, "sw.js");
      const source = readFileSync(path, "utf8");
      if (!source.includes("__ATP_BUILD__")) throw new Error("sw.js has no __ATP_BUILD__ placeholder");
      writeFileSync(path, source.replace("__ATP_BUILD__", id));
    },
  };
}

/** AUD-G2.3: the body font's Latin file (self-hosted by @fontsource-variable/inter, emitted under a hashed name) is
 * preloaded from index.html, so the first paint does not wait for the stylesheet before asking for it. Only this one
 * file: the other subsets load on demand (unicode-range) and the fixed-width font is not above the fold everywhere.
 * A build without the file stops here (a renamed package file must not drop the preload silently). */
export const PRELOAD_FONT = /^assets\/inter-latin-wght-normal-[\w-]+\.woff2$/;
export function atpFontPreload(): Plugin {
  let base = "/";
  return {
    name: "atp-font-preload",
    apply: "build",
    configResolved(config) { base = config.base; },
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const file = Object.keys(ctx.bundle ?? {}).find((name) => PRELOAD_FONT.test(name));
        if (!file) throw new Error("atp-font-preload: the Inter Latin font file is not in the build");
        return [{ tag: "link", attrs: { rel: "preload", href: `${base}${file}`, as: "font", type: "font/woff2", crossorigin: true }, injectTo: "head" }];
      },
    },
  };
}

/** PW6: the build id shown in "Send feedback" - package.json's version plus the short git commit when the build has
 * one (the Docker build copies no .git, so it gets the version alone), "dev" when neither can be read. */
function appVersion(): string {
  let version = "";
  try {
    version = String(JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).version ?? "");
  } catch { /* no package.json version */ }
  let sha = "";
  try {
    sha = execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch { /* not a git checkout */ }
  if (!/^[0-9a-f]{4,40}$/.test(sha)) sha = "";
  if (!version) return sha ? `dev+${sha}` : "dev";
  return sha ? `${version}+${sha}` : version;
}

/** Mobile B1: `vite build --mode android` (npm run build:android) is the bundle inside the Android app. It records
 * which build kind and API origin it was made for (dist/atp-native-build.json; capacitor.config.ts, the CI check and
 * Gradle refuse a mismatch). Every other mode is the web build and emits nothing extra. */
function atpNativeBuildMarker(marker: { kind: string; apiOrigin: string; appId: string }): Plugin {
  return {
    name: "atp-native-build-marker",
    apply: "build",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: NATIVE_BUILD_MARKER, source: `${JSON.stringify(marker, null, 2)}\n` });
    },
  };
}

export default defineConfig(({ mode }) => {
  // The Android build reads ATP_ANDROID_BUILD, VITE_API_BASE_URL and ATP_APP_DOMAIN from the process environment only
  // (not .env files), exactly as `cap sync` does (capacitor.config.ts), so the two can never disagree. A bad value stops here.
  const native = mode === "android" ? resolveNativeBuild(process.env, APP_ID) : null;
  return {
    plugins: [
      react(),
      atpServiceWorker(),
      atpFontPreload(),
      ...(native ? [atpNativeBuildMarker({ kind: native.kind, apiOrigin: native.apiBase, appId: native.appId })] : []),
    ],
    define: {
      __APP_VERSION__: JSON.stringify(appVersion()),
      // Constants, so the web build drops every native-only branch and chunk (src/native/), and its API URLs stay the
      // relative /api/v1 whatever the environment says.
      __ATP_NATIVE__: JSON.stringify(native !== null),
      __ATP_API_ORIGIN__: JSON.stringify(native ? native.apiBase : ""),
    },
    build: {
      // The import graph per entry (dist/.vite/manifest.json): scripts/check-bundle-budget.mjs measures each route from it.
      manifest: true,
      rollupOptions: {
        output: {
          // P1.1: the chart library is its own chunk, fetched only by pages that draw a chart.
          manualChunks: { charts: ["lightweight-charts"] },
        },
      },
    },
    test: {
      // U5: the token contrast test reads tokens.css as text; every other stylesheet stays out of the unit tests
      css: { include: [/styles\/tokens\.css/] },
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
