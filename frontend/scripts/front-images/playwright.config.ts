import { defineConfig } from "@playwright/test";

/**
 * M95 + the owner's review (14 Nov): makes the front door's six benefit images (public/front/<slug>.png) from the app's own
 * screens on a mocked API, made-up data only. Run from frontend/:
 * 1. `npx vite build`; 2. `npx playwright test -c scripts/front-images/playwright.config.ts front-images.e2e.ts`
 *    (PW_CHROMIUM may point at a preinstalled Chromium; raw shots and their visible words go to FRONT_RAW, /tmp/front-raw;
 *    the Telegram image runs scripts/front-images/telegram_message.py, which imports backend/app);
 * 3. `python3 scripts/front-images/shrink.py` writes public/front/<slug>.png; look at each before committing;
 * 4. `npx playwright test -c scripts/front-images/playwright.config.ts cards.e2e.ts` shoots the six cards as the sign-in
 *    page shows them (FRONT_CARDS, /tmp/front-cards), for the before / after page.
 */
/** FRONT_PORT: another preview server may already hold 4173 (reuseExistingServer would then shoot ITS build). */
const PORT = Number(process.env.FRONT_PORT ?? 4173);
export default defineConfig({
  testDir: ".",
  testMatch: ["front-images.e2e.ts", "cards.e2e.ts"],
  timeout: 60_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    serviceWorkers: "block",
    // The shot is the top 432 x 270 (the card's 16:10) of a phone-width window; 720 / 432 makes the file 720 x 450.
    viewport: { width: 432, height: 540 },
    deviceScaleFactor: 720 / 432,
    isMobile: true,
    hasTouch: true,
    colorScheme: "light",
    launchOptions: { ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}) },
  },
  webServer: { command: `npm run preview -- --host 127.0.0.1 --port ${PORT} --strictPort`, url: `http://127.0.0.1:${PORT}`, reuseExistingServer: true, timeout: 60_000 },
});
