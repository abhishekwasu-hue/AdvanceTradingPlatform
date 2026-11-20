import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * CP-CHAT (owner 20 Nov, "AI connected astana proper chating hone apeskhit aahe"): with an AI key the reply reads as a
 * chat message (no row of section chips), and "Check my trade" fills the box for him to complete - it is never sent
 * as his message. Made-up reply text from a scripted model; no baselines (the proof page holds the screenshots).
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: { jobs: { id: string; starter: string }[] } };
const PREFILL: Record<string, string> = { trade_check: "Check my trade idea: ", strategy: "Turn my idea into a strategy and test it: " };
const IDEA = "NIFTY 24500 CE, 2 lots, my risk 3000";
const REPLY = {
  job: "agent", job_number: null, title: "Answer", source: "ai", tool_calls: ["get_risk_limits"], worked: [], unknowns: [], starters: [], concepts: [],
  notes: [], refused: null, action: null, blocks: [], sections: [], draft: null, followup: null, symbols: [], headline: null, structure_card: null,
  disclaimer: "Information and education only, not advice. Every trading decision is yours.", headline_line: null, chips: [],
  summary: ["Your idea is 2 lots of the NIFTY 24500 CE, and you put your risk at 3000.",
            "Your own risk settings do not set a limit per trade yet, so there is nothing to check that 3000 against.",
            "Which expiry do you mean?"],
  answer: "", ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null },
};

async function open(page: Page, sent: string[]): Promise<void> {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, copilot_agent: true }, brokers: [] });
  await mockCopilotNumbers(page);
  const home = { ...F.home, jobs: F.home.jobs.map((j) => ({ ...j, prefill: PREFILL[j.id] ?? null })),
                 ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null } };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: home });
    sent.push(String((request.postDataJSON() as { message?: string }).message));
    return route.fulfill({ json: REPLY });
  });
  await page.goto("/copilot");
  await page.waitForLoadState("networkidle");
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }] as const) {
  test.describe(`Copilot chat feel ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1, ...(size.phone ? { isMobile: true, hasTouch: true } : {}) });

    test("Check my trade fills the box; only his completed idea is sent; the reply is a chat message", async ({ page }) => {
      const sent: string[] = [];
      await open(page, sent);
      await page.locator("[data-quick='trade_check']").first().click();
      const box = page.locator("#copilot-question");
      await expect(box).toHaveValue("Check my trade idea: ");
      await expect(box).toBeFocused();
      await expect(page.locator("[data-idea-hint]")).toBeVisible();
      await box.press("Enter");                                    // sent as is: still nothing goes out
      await expect(box).toHaveValue("Check my trade idea: ");
      expect(sent).toEqual([]);
      await box.pressSequentially(IDEA);
      await box.press("Enter");
      const reply = page.locator("[data-chat-reply]");
      await expect(reply).toContainText("Which expiry do you mean?");
      expect(sent).toEqual([`Check my trade idea: ${IDEA}`]);
      await expect(page.locator("[data-idea-hint]")).toHaveCount(0);
      await expect(page.locator("[data-answer-index]")).toHaveCount(0);
      await expect(page.locator("[data-msg='user']")).toContainText(IDEA);
      if (size.phone) await expectPhoneLayout(page);
    });
  });
}
