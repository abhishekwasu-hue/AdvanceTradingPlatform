import { defineConfig } from "@playwright/test";

/**
 * M92 V2: the tutorial videos' capture run - NOT a test suite (the CI config, ../../playwright.config.ts, matches
 * `*.e2e.ts` only and never runs these). Driven by docs/video/build.sh, which sets:
 *   VIDEO_EPISODE  E1 ... E6          VIDEO_SHOTS  the folder the stills go to (outside git)
 *   VIDEO_CARDS    a JSON file of intro / outro cards to draw (docs/video/tools/build.py writes it)
 * Its own preview port (4190) and a fresh server every run, so it never meets a dev server or the e2e run.
 */
const PORT = Number(process.env.VIDEO_PORT ?? 4190);
const chromium = process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {};

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.video.ts",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: process.env.VIDEO_SHOTS ? `${process.env.VIDEO_SHOTS}/.pw` : "../../test-results/video",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    serviceWorkers: "block",
    trace: "off",
    actionTimeout: 8_000,
    video: "off",
    launchOptions: { args: ["--font-render-hinting=none"], ...chromium },
  },
  projects: [
    // 1280 x 720 CSS px at 1.5 = a 1920 x 1080 frame: the desktop layout, text big enough for a video.
    { name: "desktop", use: { viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5 } },
    // 405 x 720 CSS px at 8/3 = a 1080 x 1920 (9:16) frame: the phone layout.
    { name: "phone", use: { viewport: { width: 405, height: 720 }, deviceScaleFactor: 8 / 3, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: `npm run preview -- --host 127.0.0.1 --port ${PORT} --strictPort`,
    cwd: "../..",
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
