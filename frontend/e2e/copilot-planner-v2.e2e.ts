import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-1c (CP-1): the Copilot chat with the AI planner (`copilot_agent`). The question asks about his own waiting orders
 * and a distance: the planner read "Your orders", the distance came from a "Calculation" step (code, never the model),
 * and "How I worked this out" lists both. The answers are the backend's own replies on made-up rows (a scripted model,
 * no network). Baselines (NEW): `copilot-planner-v2-<width>-<theme>.png` at 390 and 1440, light and dark.
 * After an intended change: `npx playwright test --project=phone e2e/copilot-planner-v2.e2e.ts --update-snapshots`.
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const QUESTION = "What orders are waiting, and how far is the DDDSTOCK one from 99?";
const PLANNED = {"job": "agent", "job_number": null, "title": "Answer", "summary": ["Your waiting order is DDDSTOCK at 101.5, a limit order placed in PAPER. It is 2.5 above 99."], "answer": "Your waiting order is DDDSTOCK at 101.5, a limit order placed in PAPER. It is 2.5 above 99.", "sections": [], "blocks": [], "unknowns": [], "worked": [{"tool": "get_my_orders", "label": "Your orders", "ok": true, "as_of": "2026-10-21T03:30:00+00:00", "source": null, "numbers": [], "note": null, "args": {}, "ran": true}, {"tool": "calc_difference", "label": "Calculation", "ok": true, "as_of": "2026-10-21T03:30:00+00:00", "source": null, "numbers": [], "note": null, "args": {"a": 101.5, "b": 99}, "ran": true}], "action": null, "refused": null, "starters": [], "concepts": [], "notes": [], "disclaimer": "Information and education only, not advice. Every trading decision is yours.", "source": "ai", "draft": null, "followup": null, "symbols": [], "symbol_default": false, "headline": null, "structure_card": null, "tool_calls": ["get_my_orders", "calc_difference"], "tools_used": ["Your orders", "Calculation"], "claims": [], "disclaimers": [], "proposals": [], "numbers": {"verified": ["101.5", "2.5", "99"], "user_provided": [], "unsupported": []}, "ai": {"ready": true, "title": null, "line": null, "settings": "/settings/ai", "reason": null}, "usage": {"calls": 3, "tokens_input": 4200, "tokens_output": 180, "cost_inr": 0.42}};

async function open(page: Page, theme: "dark" | "light"): Promise<void> {
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true }, brokers: [] });
  await mockCopilotNumbers(page);
  const home = { ...F.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: home });
    return route.fulfill({ json: PLANNED });
  });
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await page.locator("#copilot-question").fill(QUESTION);
  await page.locator("#copilot-question").press("Enter");
  await expect(page.locator("[data-answer='agent']")).toBeVisible({ timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }] as const) {
  test.describe(`Copilot planner ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1, ...(size.phone ? { isMobile: true, hasTouch: true } : {}) });
    for (const theme of ["light", "dark"] as const) {
      test(`a planner answer looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        const answer = page.locator("[data-answer='agent']");
        await expect(answer).toContainText("2.5 above 99");
        await expect(answer).toHaveScreenshot(`copilot-planner-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
    test("the answer stays on the page and names its steps", async ({ page }) => {
      await open(page, "light");
      const before = page.url();
      await expect(page.locator("[data-answer='agent']")).toHaveAttribute("data-source", "ai");
      expect(page.url()).toBe(before);
      if (size.phone) await expectPhoneLayout(page);
    });
  });
}
