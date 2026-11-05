import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { api, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * CH-1 (owner 5 Nov, "Default yes"): the Copilot's chat on every page - the round button opens the same Copilot chat in a
 * sheet, with the page's symbol and three ready questions. Mocked API only. CH1_SHOTS=<dir> writes the proof shots.
 */
const SHOTS = process.env.CH1_SHOTS;
const CHAT = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const CONNECTED = [{ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: true, renew: false }];

async function open(page: Page, flags: Record<string, boolean>, asked: string[] = []) {
  await page.clock.install({ time: new Date("2026-11-05T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "copilot-chat"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: flags, brokers: CONNECTED });
  await mockCharts(page, {});
  await page.route(api("/ai/chat/history$"), (r) => r.fulfill({ json: { turns: [], keep: 20 } }));
  await page.route(api("/ai/chat(/home)?$"), (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: { ...CHAT.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null, code: null, own_key: false } } });
    }
    asked.push(route.request().postData() ?? "");
    return route.fulfill({ status: 503, json: { detail: "made-up stop after the question" } });
  });
  await page.goto("/charts");
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
}

const FLAGS = { ui_v2: true, chart_workspace: true, ai_copilot: true };

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`CH-1 chat bubble ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: SHOTS ? 2 : 1 });

    test("the bubble opens the Copilot chat with the page's symbol and ready questions; a question carries the symbol", async ({ page }) => {
      const asked: string[] = [];
      await open(page, FLAGS, asked);
      const bubble = page.locator("[data-chat-bubble]");
      await expect(bubble).toBeVisible();
      await expect(bubble).toHaveAttribute("aria-label", /NIFTY 50/);
      if (size.phone) await expectPhoneLayout(page);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/ch1-bubble-${size.width}-dark.png` });
      await bubble.click();
      const sheet = page.locator("[data-chat-sheet]");
      const starters = sheet.locator("[data-chat-starters] button");
      await expect(starters).toHaveCount(3);
      await expect(starters.first()).toHaveText("What is the structure of NIFTY 50 now?");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/ch1-sheet-${size.width}-dark.png` });
      await starters.first().click();
      await expect.poll(() => asked.length).toBe(1);
      expect(asked[0]).toContain("What is the structure of NIFTY 50 now?");
      expect(asked[0]).toContain('"symbol":"NIFTY 50"');
    });

    test("no bubble while ai_copilot is off", async ({ page }) => {
      await open(page, { ...FLAGS, ai_copilot: false });
      await expect(page.locator("[data-chat-bubble]")).toHaveCount(0);
    });
  });
}
