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

export default defineConfig({
  plugins: [react(), atpServiceWorker()],
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
