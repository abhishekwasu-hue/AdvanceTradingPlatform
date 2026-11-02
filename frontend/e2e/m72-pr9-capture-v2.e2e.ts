import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";
import { choose, expectComboValue } from "./combobox";

/**
 * M72 PR-9 (CP-9): capture-only - before / after of a Copilot answer to a message that names a contract. Before (PR-8):
 * the answer only. After: "Prepare the order ticket" (his typed values marked, expiry and side picked by him) and the
 * ticket opened in place with the source chips. Made-up words; a scripted answer. No toHaveScreenshot, never runs in CI. By hand:
 * `M72_OUT=<folder> npx playwright test --project=phone e2e/m72-pr9-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M72 comparison page, never in CI");
const OUT = process.env.M72_OUT ?? "test-results/m72-pr9";
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const QUESTION = "Sell 2 lots NIFTY 25000 CE at 85, stop 120 - what does the chain show there?";

function answer() {
  const lines = ["The 25000 call's open interest is the highest on the call side of the chain read.", "Information only; the ticket is yours to fill and place."];
  return {
    job: "agent", job_number: null, title: "Answer", summary: lines, answer: lines.join("\n"), sections: [], blocks: [],
    unknowns: [], worked: [], action: null, refused: null, starters: [], concepts: [], notes: [], disclaimer: "Information and education only, not advice. Every trading decision is yours.",
    source: "ai", draft: null, followup: null, symbols: ["NIFTY"], symbol_default: false, tool_calls: [], tools_used: [],
    claims: [], disclaimers: [], proposals: [], ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null },
    headline: null, structure_card: null, headline_line: "NIFTY 25000 CE: highest call OI in the chain read", chips: [], drafts: [],
  };
}

async function open(page: Page, theme: string, after: boolean) {
  await page.clock.install({ time: new Date("2026-10-21T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true, copilot_ticket: after }, brokers: [] });
  await mockCopilotNumbers(page);
  const home = { ...F.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const q = route.request();
    if (q.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (q.method() === "GET") return route.fulfill({ json: home });
    return route.fulfill({ json: answer() });
  });
  await page.route(/\/api(?:\/v1)?\/option-chain\/NIFTY\/expiries/, (r) => r.fulfill({ json: { underlying: "NIFTY", today: "2026-10-21",
    expiries: [{ date: "2026-10-27", kind: "weekly" }, { date: "2026-11-03", kind: "weekly" }], default: "2026-10-27", source: "test" } }));
  await page.route(/\/api(?:\/v1)?\/paper\/fno\/contract/, (r) => r.fulfill({ json: { underlying: "NIFTY", tradingsymbol: "NIFTYDEMO25000CE", exchange: "NFO",
    instrument_key: "demo", expiry: "2026-10-27", strike: 25000, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 27 OCT 25000 CE", freeze_quantity: null } }));
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.locator("#copilot-question").fill(QUESTION);
  await page.locator("#copilot-question").press("Enter");
  await expect(page.locator("[data-answer='agent']")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(500);
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
      for (const after of [false, true]) {
        test(`ticket prep ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await open(page, theme, after);
          if (after) {
            const prep = page.locator("[data-ticket-prep]");
            await expect(prep).toContainText("2 lots from your message");
            await expectComboValue(prep.locator("[data-ticket-expiry]"), "");
            await expect(prep.locator("[data-ticket-open]")).toBeDisabled();
            if (w === 390) await expectPhoneLayout(page);
          }
          await page.screenshot({ fullPage: true, path: `${OUT}/pr9-${after ? "after" : "before"}-copilot-${w}-${theme}.png`, animations: "disabled" });
          if (after) {
            await choose(page, page.locator("[data-ticket-expiry]"), "2026-10-27");
            await page.locator("[data-ticket-open]").click();
            await expect(page.locator("[data-ticket='prefill-sources']")).toContainText("stop-loss 120 from your message");
            await page.waitForTimeout(400);
            await page.screenshot({ path: `${OUT}/pr9-after-ticket-${w}-${theme}.png`, animations: "disabled" });
          }
        });
      }
    });
  }
}
