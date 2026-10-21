import { defineConfig, devices } from "@playwright/test";

/**
 * Copilot redesign: browser tests against the production build (`vite preview`), with every /api call answered from
 * e2e/fixtures - no backend needed. CI: `npx playwright install --with-deps chromium`, then `npm run test:e2e`.
 * PW_CHROMIUM points at a preinstalled Chromium when the bundled one is not downloaded (dev containers).
 */
export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  // CI: failures become check-run annotations (readable without the raw log).
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    // PW1: the production build registers a service worker; page.route() cannot see requests a worker handles, so
    // the fixtures would be bypassed. The worker has its own tests (test/pwa-shell.test.ts).
    serviceWorkers: "block",
    // Software WebGL (SwiftShader) on GPU-less runners, so the ai-core-3d test sees the real scene.
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
                     ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}) },
  },
  // PW2: phone layout and visual regression (e2e/mobile.e2e.ts) run once, in their own project with their own sizes.
  // UI2-1: the v2 shell's phone and 1440 desktop checks and baselines (e2e/shell-v2.e2e.ts, `*-v2-*.png`) run there too,
  // and so do P3's front page and go-live checklist (e2e/front-page.e2e.ts), which set their own sizes.
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{ext}",
  projects: [
    { name: "desktop", testIgnore: ["**/mobile.e2e.ts", "**/*-v2.e2e.ts", "**/front-page.e2e.ts"], use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", testIgnore: ["**/mobile.e2e.ts", "**/*-v2.e2e.ts", "**/front-page.e2e.ts"], use: { ...devices["Pixel 7"] } },
    { name: "phone", testMatch: ["**/mobile.e2e.ts", "**/*-v2.e2e.ts", "**/front-page.e2e.ts"], use: { ...devices["Desktop Chrome"] } },
  ],
  // --host 127.0.0.1: on the CI runner "localhost" resolves to ::1 and the url below would never answer.
  webServer: { command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort", url: "http://127.0.0.1:4173", reuseExistingServer: !process.env.CI, timeout: 60_000 },
});
