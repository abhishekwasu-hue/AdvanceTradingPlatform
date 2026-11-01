import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-10 (CP-10): capture-only - before / after of the Copilot answer language and voice. Before (PR-9): no choice,
 * English answers, no microphone. After: Settings › AI "Copilot answers" (Marathi picked, voice on), a Marathi answer
 * with the numbers as in the data, and the microphone in the Copilot box. Made-up words; a scripted answer. No toHaveScreenshot, never runs in CI. By hand:
 * `M72_OUT=<folder> npx playwright test --project=phone e2e/m72-pr10-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M72 comparison page, never in CI");
const OUT = process.env.M72_OUT ?? "test-results/m72-pr10";
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const MR = JSON.parse(readFileSync(new URL("./fixtures/m72_pr10_marathi.json", import.meta.url), "utf8")) as { question: string; lines: string[] };
const QUESTION = MR.question;

function answer(after: boolean) {
  const lines = after ? MR.lines
    : ["NIFTY 50 last closed at 24,850.5, up 118.4 from the previous close.", "Information only."];
  return {
    job: "agent", job_number: null, title: "Answer", summary: lines, answer: lines.join("\n"), sections: [], blocks: [],
    unknowns: [], worked: [], action: null, refused: null, starters: [], concepts: [], notes: [], disclaimer: "Information and education only, not advice. Every trading decision is yours.",
    source: "ai", draft: null, followup: null, symbols: ["NIFTY 50"], symbol_default: false, tool_calls: [], tools_used: [],
    claims: [], disclaimers: [], proposals: [], ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null },
    headline: null, structure_card: null, headline_line: after ? "NIFTY 50: 24,850.5 (+118.4)" : "NIFTY 50: 24,850.5 (+118.4)", chips: [], drafts: [],
  };
}

async function setup(page: Page, theme: string, after: boolean) {
  await page.clock.install({ time: new Date("2026-10-21T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
    (window as unknown as { webkitSpeechRecognition: unknown }).webkitSpeechRecognition = function Fake() { /* a made-up recogniser */ };
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true, copilot_languages: after }, brokers: [] });
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: { prefs: { ai: { language: "mr", voice: true } }, versions: { ai: 2 } } }));
  const home = { ...F.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const q = route.request();
    if (q.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (q.method() === "GET") return route.fulfill({ json: home });
    return route.fulfill({ json: answer(after) });
  });
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
      for (const after of [false, true]) {
        test(`languages copilot ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await setup(page, theme, after);
          await page.goto("/copilot");
          await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', 'Noto Sans Devanagari', sans-serif !important; caret-color: transparent !important; }" });
          await page.locator("#copilot-question").fill(QUESTION);
          await page.locator("#copilot-question").press("Enter");
          await expect(page.locator("[data-answer='agent']")).toBeVisible({ timeout: 15_000 });
          if (after) await expect(page.locator("[data-voice]")).toBeVisible();
          else await expect(page.locator("[data-voice]")).toHaveCount(0);
          if (after && w === 390) await expectPhoneLayout(page);
          await page.waitForTimeout(400);
          await page.screenshot({ fullPage: true, path: `${OUT}/pr10-${after ? "after" : "before"}-copilot-${w}-${theme}.png`, animations: "disabled" });
        });
        test(`languages settings ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await setup(page, theme, after);
          await page.goto("/settings/ai");
          await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
          await page.waitForTimeout(800);
          if (after) {
            await expect(page.locator("[data-answer-language]")).toBeVisible();
            await page.locator("#copilot-answers").screenshot({ path: `${OUT}/pr10-after-settings-${w}-${theme}.png`, animations: "disabled" });
          } else {
            await expect(page.locator("[data-answer-language]")).toHaveCount(0);
            await page.screenshot({ fullPage: true, path: `${OUT}/pr10-before-settings-${w}-${theme}.png`, animations: "disabled" });
          }
        });
      }
    });
  }
}
