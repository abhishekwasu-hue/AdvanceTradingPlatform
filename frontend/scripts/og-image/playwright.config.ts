import { defineConfig } from "@playwright/test";

/**
 * M95 F1: the link-preview image generator (make-og-image.ts) and its preview proof (preview-proof.ts, M95-Q7). Not part of the e2e suites: run it by
 * hand to re-make `public/og/front-door.png`, against the production build (`npx vite build` first) on its own port
 * so it never meets another preview server. How to run: the header of make-og-image.ts.
 */
export default defineConfig({
  testDir: ".",
  testMatch: ["make-og-image.ts", "preview-proof.ts"],
  timeout: 90_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4191",
    trace: "off",
    serviceWorkers: "block",
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  webServer: { command: "npm run preview -- --host 127.0.0.1 --port 4191 --strictPort", cwd: "../..", url: "http://127.0.0.1:4191",
               reuseExistingServer: false, timeout: 60_000 },
});
