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
    // Software WebGL (SwiftShader) on GPU-less runners, so the ai-core-3d test sees the real scene.
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
                     ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}) },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  // --host 127.0.0.1: on the CI runner "localhost" resolves to ::1 and the url below would never answer.
  webServer: { command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort", url: "http://127.0.0.1:4173", reuseExistingServer: !process.env.CI, timeout: 60_000 },
});
