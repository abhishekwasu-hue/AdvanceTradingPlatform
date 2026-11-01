import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-6 (CP-5): capture-only - the before / after shots of a planner answer that built a screen. Before (PR-5):
 * the lines only. After: the editable screen draft (the value the user did not state is empty, no symbols pre-filled,
 * Run waits) and a proposed alert that links to the Watchtower. Made-up words; a scripted answer. No toHaveScreenshot, never runs in CI. By hand:
 * `M72_OUT=<folder> npx playwright test --project=phone e2e/m72-pr6-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M72 comparison page, never in CI");
const OUT = process.env.M72_OUT ?? "test-results/m72-pr6";
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const QUESTION = "Make a daily screen: RSI(14) above 60 and the close above its moving average, and alert me";

function answer(after: boolean) {
  const lines = ["A draft screen: RSI(14) above 60 and the close above its moving average.", "You decide the average's period; nothing runs until you tap Run."];
  return {
    job: "agent", job_number: null, title: "Answer", summary: ["Screen draft: RSI(14) above 60", ...lines], answer: lines.join("\n"), sections: [], blocks: [],
    unknowns: [], worked: [{ tool: "build_screen", label: "Screen check", ok: true, as_of: "2026-10-21T03:30:00+00:00", source: null, numbers: [], note: null, args: {}, ran: true }],
    action: null, refused: null, starters: [], concepts: [], notes: [], disclaimer: "Information and education only, not advice. Every trading decision is yours.",
    source: "ai", draft: null, followup: null, symbols: [], symbol_default: false, tool_calls: ["build_screen"], tools_used: ["Screen check"],
    claims: [], disclaimers: [], proposals: [], ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null },
    headline: null, structure_card: null, headline_line: "Screen draft: RSI(14) above 60", chips: [],
    drafts: after ? [{ kind: "screen", text: "RSI(14) > 60 AND close > SMA(close, $period)", base_tf: "1d", params: [{ name: "period", value: null }],
      stages: ["Momentum"], problems: [], ok: false }, { kind: "alert", status: "proposed", proposal_id: 41 }] : [],
  };
}

async function open(page: Page, theme: string, after: boolean) {
  await page.clock.install({ time: new Date("2026-10-21T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true, screener_v2: true, screener_copilot: true }, brokers: [] });
  await mockCopilotNumbers(page);
  const home = { ...F.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const q = route.request();
    if (q.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (q.method() === "GET") return route.fulfill({ json: home });
    return route.fulfill({ json: answer(after) });
  });
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
        test(`planner screen draft ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await open(page, theme, after);
          if (after) {
            const d = page.locator("[data-draft='screen']");
            await expect(d.locator("[data-draft-param='period']")).toHaveValue("");
            await expect(d.locator("[data-draft-symbols]")).toHaveValue("");
            await expect(d.locator("[data-draft-run]")).toBeDisabled();
            await expect(page.locator("[data-draft='alert']")).toContainText("Alert proposed");
            if (w === 390) await expectPhoneLayout(page);
          }
          await page.screenshot({ fullPage: true, path: `${OUT}/pr6-${after ? "after" : "before"}-copilot-${w}-${theme}.png`, animations: "disabled" });
        });
      }
    });
  }
}
