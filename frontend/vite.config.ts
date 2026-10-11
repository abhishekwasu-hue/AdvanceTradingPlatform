/// <reference types="vitest/config" />
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

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

export default defineConfig({
  plugins: [react(), atpServiceWorker()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
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
});
