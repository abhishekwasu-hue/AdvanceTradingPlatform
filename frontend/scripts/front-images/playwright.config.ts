import { defineConfig } from "@playwright/test";

/**
 * M95: makes the front door's six benefit images (public/front/<slug>.png) from the app's own screens on a mocked API.
 * Run from frontend/: `npx vite build`, then `npx playwright test -c scripts/front-images/playwright.config.ts`
 * (PW_CHROMIUM may point at a preinstalled Chromium), then `python3 scripts/front-images/shrink.py`.
 */
export default defineConfig({
  testDir: ".",
  testMatch: "front-images.e2e.ts",
  timeout: 60_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    serviceWorkers: "block",
    viewport: { width: 360, height: 450 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    colorScheme: "light",
    launchOptions: { ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}) },
  },
  webServer: { command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort", url: "http://127.0.0.1:4173", reuseExistingServer: true, timeout: 60_000 },
});
