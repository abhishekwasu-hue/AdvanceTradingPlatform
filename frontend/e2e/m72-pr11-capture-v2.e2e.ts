import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-11 (CP-11): capture-only - before / after of "What the Copilot remembers" on Settings › AI. Before (PR-10): no
 * card. After: his saved instruments and timeframe, suggestions from his own activity (tap to keep), the style Not set,
 * Save and Delete. Made-up data. No toHaveScreenshot, never runs in CI. By hand:
 * `M72_OUT=<folder> npx playwright test --project=phone e2e/m72-pr11-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M72 comparison page, never in CI");
const OUT = process.env.M72_OUT ?? "test-results/m72-pr11";
const VIEW = {
  memory: { instruments: ["NIFTY", "DEMOCO"], timeframes: ["1d"], style: null },
  suggested: { instruments: [{ symbol: "NIFTY", count: 4, from: ["your trades"] }, { symbol: "BANKNIFTY", count: 3, from: ["your Copilot questions"] },
                             { symbol: "SAMPLECO", count: 2, from: ["your watchlists"] }],
               timeframes: [{ timeframe: "15m", count: 2, from: ["your Copilot questions"] }] },
  styles: { intraday: "Intraday", swing: "Swing", positional: "Positional", options_selling: "Options selling", options_buying: "Options buying", investing: "Investing" },
};

async function open(page: Page, theme: string, after: boolean) {
  await page.clock.install({ time: new Date("2026-10-21T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_memory: after }, brokers: [] });
  await page.route(/\/api(?:\/v1)?\/copilot-memory$/, (r) => after ? r.fulfill({ json: VIEW }) : r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await page.goto("/settings/ai");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForTimeout(800);
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
      for (const after of [false, true]) {
        test(`memory ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await open(page, theme, after);
          if (after) {
            await expect(page.locator("[data-copilot-memory]")).toContainText("+ BANKNIFTY");
            await expect(page.locator("[data-memory='style']")).toHaveAttribute("data-combobox", "");
            if (w === 390) await expectPhoneLayout(page);
            await page.locator("#copilot-memory").screenshot({ path: `${OUT}/pr11-after-settings-${w}-${theme}.png`, animations: "disabled" });
          } else {
            await expect(page.locator("[data-copilot-memory]")).toHaveCount(0);
            await page.screenshot({ fullPage: true, path: `${OUT}/pr11-before-settings-${w}-${theme}.png`, animations: "disabled" });
          }
        });
      }
    });
  }
}
