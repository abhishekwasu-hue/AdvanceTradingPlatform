import { defineConfig, devices } from "@playwright/test";

/**
 * N4 CH6-2: the chart render timing (e2e/render.perf.ts) - production build, mocked API, one desktop Chromium, no
 * retries (a timing is reported as measured). The CI perf job runs it with ATP_CI_FACTOR from the Z10-d calibration.
 * PW_CHROMIUM points at a preinstalled Chromium when the bundled one is not downloaded (dev containers).
 */
export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.perf.ts",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    serviceWorkers: "block",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    launchOptions: { ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}) },
  },
  webServer: { command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort", url: "http://127.0.0.1:4173", reuseExistingServer: !process.env.CI, timeout: 60_000 },
});
