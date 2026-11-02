import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-7 (CP-7): capture-only - the before / after shots of the Copilot watch. Before (PR-6): Settings › Alerts has no
 * watch card and a notification has no Ask why. After: the "Copilot watch" card (every type off, every number Not set
 * until the user types his own) and a watch card in Notifications with "Ask why". Made-up data. No toHaveScreenshot, never
 * runs in CI. By hand: `M72_OUT=<folder> npx playwright test --project=phone e2e/m72-pr7-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M72 comparison page, never in CI");
const OUT = process.env.M72_OUT ?? "test-results/m72-pr7";

const SETTINGS = {
  prefs: { telegram: false, near_levels: true, near_levels_room_left_pct: 20, strike_distance: false, strike_distance_pct: null, greeks_jump: false,
           iv_jump_points: null, delta_jump: null, oi_shift: false, oi_shift_pct: null, watchlist_signal: true },
  version: 2, telegram: { connected: false, reaches_you: false }, risk_free_rate_set: false,
};
const CARD = { id: 901, event_type: "COPILOT_WATCH", severity: "INFO", title: "DEMO (long) near your own invalidation level",
  message: "DEMO (long): mark 91.5, entry 100, your own invalidation level 90 - 15 % of the room to your own invalidation level is left.",
  related_trade_id: 77, related_order_id: null, read: false, created_at: "2026-10-21T05:40:00+00:00" };

async function open(page: Page, theme: string, after: boolean, path: string) {
  await page.clock.install({ time: new Date("2026-10-21T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true, copilot_watch: after }, brokers: [] });
  await page.route(/\/api(?:\/v1)?\/copilot-watch\/settings$/, (r) => after ? r.fulfill({ json: SETTINGS }) : r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await page.route(/\/api(?:\/v1)?\/notifications(\?.*)?$/, (r) => r.fulfill({ json: [after ? { ...CARD, ask: { symbol: "DEMO", object: { kind: "position", id: 77 } } }
    : { ...CARD, event_type: "SETTLEMENT_WARNING", title: "Delivery ahead: DEMO", message: "A made-up earlier notification." }] }));
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForTimeout(800);
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
      for (const after of [false, true]) {
        test(`watch settings ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await open(page, theme, after, "/settings/alerts");
          if (after) {
            await page.locator("#copilot-watch").scrollIntoViewIfNeeded();
            await expect(page.locator("[data-watch-kind='strike_distance'] input[type='checkbox']")).toBeDisabled();
            await expect(page.locator("#watch-strike_distance_pct")).toHaveValue("");
            if (w === 390) await expectPhoneLayout(page);
            await page.locator("#copilot-watch").screenshot({ path: `${OUT}/pr7-after-settings-${w}-${theme}.png`, animations: "disabled" });
          } else {
            await page.screenshot({ fullPage: true, path: `${OUT}/pr7-before-settings-${w}-${theme}.png`, animations: "disabled" });
          }
        });
        test(`watch card ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await open(page, theme, after, "/notifications");
          if (after) await expect(page.getByRole("link", { name: /Ask why/ })).toBeVisible();
          if (after && w === 390) await expectPhoneLayout(page);
          await page.screenshot({ fullPage: true, path: `${OUT}/pr7-${after ? "after" : "before"}-notifications-${w}-${theme}.png`, animations: "disabled" });
        });
      }
    });
  }
}
