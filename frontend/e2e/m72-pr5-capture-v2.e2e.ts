import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { madeUpBars, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-5 (CP-4): capture-only, like m78-capture-v2 - the before / after shots of a planner answer that read a chart.
 * Before (PR-4): the planner's lines only. After: the checked headline line, the headline row with the annotated mini
 * chart (the nearest area drawn as a band by code) and the structure card, the rest behind Details. Made-up symbol,
 * bars and areas; a scripted answer. No toHaveScreenshot, never runs in CI. By hand:
 * `M72_OUT=<folder> npx playwright test --project=phone e2e/m72-pr5-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M72 comparison page, never in CI");
const OUT = process.env.M72_OUT ?? "test-results/m72-pr5";
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const QUESTION = "Read the EEESTOCK chart";

function answer(after: boolean) {
  const bars = madeUpBars("day");
  const lo = Math.min(...bars.map((b) => b.low)), hi = Math.max(...bars.map((b) => b.high)), last = bars.at(-1)!.close;
  const from = Math.round(lo + (hi - lo) * 0.1), to = Math.round(lo + (hi - lo) * 0.2), up = Math.round(hi - (hi - lo) * 0.05);
  const head = `EEESTOCK daily: up, buyer area ${from} - ${to}`;
  const lines = ["The daily structure is up and the last break was a swing break.", `The buyer area of interest ${from} - ${to} is fresh, below the last close.`];
  const base = {
    job: "agent", job_number: null, title: "Answer", summary: after ? [head, ...lines] : lines, answer: lines.join("\n"), sections: [], blocks: [],
    unknowns: [], worked: [{ tool: "get_chart_structure", label: "Chart structure", ok: true, as_of: "2026-10-21T03:30:00+00:00", source: null, numbers: [], note: null, args: { symbol: "EEESTOCK" }, ran: true },
      { tool: "get_market_zones", label: "Market zones", ok: true, as_of: "2026-10-21T03:30:00+00:00", source: null, numbers: [], note: null, args: { symbol: "EEESTOCK" }, ran: true }],
    action: null, refused: null, starters: [], concepts: [], notes: [], disclaimer: "Information and education only, not advice. Every trading decision is yours.",
    source: "ai", draft: null, followup: null, symbols: [], symbol_default: false, tool_calls: ["get_chart_structure", "get_market_zones"],
    tools_used: ["Chart structure", "Market zones"], claims: [], disclaimers: [], proposals: [],
    ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null },
    chips: after ? [] : [{ kind: "chart", label: "Show on chart", symbol: "EEESTOCK" }],     // after: the card's "Open chart" opens it
  };
  if (!after) return { ...base, headline: null, structure_card: null, headline_line: null };
  return {
    ...base, headline_line: head,
    headline: { symbol: "EEESTOCK", price: last, price_label: "Last close", as_of: "2026-10-21T03:30:00+00:00", trend: "up", timeframe: "Daily",
      area: { side: "demand", from, to, where: "below", distance: Math.round((last - to) * 100) / 100, distance_pct: null, timeframe: "1d", state: "fresh" },
      chart: { symbol: "EEESTOCK", exchange: "NSE", timeframe: "day" } },
    structure_card: { symbol: "EEESTOCK", price: last, timeframe: "Daily",
      timeframes: [{ tf: "1w", label: "Weekly", ready: true, trend: "up", weak: false, protected: from, last_break: null },
        { tf: "1d", label: "Daily", ready: true, trend: "up", weak: false, protected: to, last_break: { label: "Swing break", price: up } },
        { tf: "1h", label: "1-hour", ready: true, trend: "sideways", weak: false, protected: null, last_break: null },
        { tf: "15m", label: "15-minute", ready: false, trend: null, weak: false, protected: null, last_break: null }],
      areas_above: [], areas_below: [{ side: "demand", from, to, where: "below", distance: Math.round((last - to) * 100) / 100 }], swing_levels: [],
      watch: "Whether the daily protected level holds or breaks on a closed bar, and how the price behaves when it reaches the nearest area." },
  };
}

async function open(page: Page, theme: string, after: boolean) {
  await page.clock.install({ time: new Date("2026-10-21T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true }, brokers: [] });
  await mockCopilotNumbers(page);
  await mockCharts(page, {});
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
  if (after) await expect(page.locator("[data-mini-chart] canvas").first()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(800);
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
      for (const after of [false, true]) {
        test(`planner chart answer ${after ? "after" : "before"} ${w} ${theme}`, async ({ page }) => {
          await open(page, theme, after);
          const a = page.locator("[data-answer='agent']");
          if (after) {
            await expect(a.locator("[data-headline-line]")).toContainText("EEESTOCK daily: up");
            const text = (await a.innerText()).toLowerCase();
            expect(text).not.toMatch(/\b(buy|sell|target|stop|invalidat\w*)\b/);
            if (w === 390) await expectPhoneLayout(page);
          }
          await page.screenshot({ fullPage: true, path: `${OUT}/pr5-${after ? "after" : "before"}-copilot-${w}-${theme}.png`, animations: "disabled" });
        });
      }
    });
  }
}
