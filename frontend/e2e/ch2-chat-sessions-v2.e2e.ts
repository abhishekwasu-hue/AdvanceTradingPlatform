import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { api, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * CH-2 (owner 5 Nov, "Ho finish all job"): Claude-like chat sessions in the Copilot chat - "New chat", the list of his
 * earlier conversations (titled by their first question), open one, rename it. Mocked API only. CH2_SHOTS=<dir> writes
 * the proof shots.
 */
const SHOTS = process.env.CH2_SHOTS;
const CHAT = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown>; answers: Record<string, Record<string, unknown>> };
const BASE = { ...Object.values(CHAT.answers)[0], structure_card: null, headline: null, headline_line: null };
const answer = (lines: string[], conversation: { id: number; title: string }) => ({ ...BASE, summary: lines, conversation });
const SESSIONS = [
  { id: 2, title: "What is the structure of NIFTY 50 now?", turns: 2, updated_at: "2026-11-05T05:20:00+00:00" },
  { id: 1, title: "Earlier conversation", turns: 4, updated_at: "2026-11-03T10:05:00+00:00" },
];
const CONNECTED = [{ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: true, renew: false }];

async function open(page: Page, asked: string[]) {
  await page.clock.install({ time: new Date("2026-11-05T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "copilot-chat"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_workspace: true, ai_copilot: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  let sessions = [...SESSIONS];
  await page.route(api("/ai/chat/sessions(/\\d+)?$"), async (route) => {
    const method = route.request().method();
    if (method === "PATCH") {
      const id = Number(route.request().url().split("/").pop());
      const title = (JSON.parse(route.request().postData() ?? "{}") as { title: string }).title;
      sessions = sessions.map((s) => (s.id === id ? { ...s, title } : s));
      return route.fulfill({ json: { id, title } });
    }
    return route.fulfill({ json: { sessions, keep: 50 } });
  });
  await page.route(/\/api(?:\/v1)?\/ai\/chat\/history(\?session_id=\d+)?$/, (route) => {
    const id = new URL(route.request().url()).searchParams.get("session_id");
    if (id === "1") {
      return route.fulfill({ json: { keep: 20, session: { id: 1, title: "Earlier conversation" }, turns: [
        { id: 11, question: "What is RSI divergence?", at: "2026-11-03T10:00:00+00:00",
          answer: answer(["## Summary\nPrice makes a new high while RSI makes a lower high: momentum is weakening."], { id: 1, title: "Earlier conversation" }) },
      ] } });
    }
    return route.fulfill({ json: { keep: 20, session: { id: 2, title: SESSIONS[0].title }, turns: [
      { id: 21, question: SESSIONS[0].title, at: "2026-11-05T05:20:00+00:00",
        answer: answer(["## Summary\nUp structure; price pulled back toward the demand area."], { id: 2, title: SESSIONS[0].title }) },
    ] } });
  });
  await page.route(api("/ai/chat(/home)?$"), (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: { ...CHAT.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null, code: null, own_key: false } } });
    }
    const body = route.request().postData() ?? "";
    asked.push(body);
    const sent = JSON.parse(body) as { session_id?: number; new_chat?: boolean };
    if (sent.new_chat) sessions = [{ id: 3, title: "Which areas is NIFTY 50 near?", turns: 1, updated_at: "2026-11-05T05:30:00+00:00" }, ...sessions];
    const into = sessions.find((s) => s.id === sent.session_id) ?? sessions[0];
    return route.fulfill({ json: answer(["## Summary\nDemand 23,860 - 23,900 below; supply 24,300 - 24,340 above (made-up)."], { id: into.id, title: into.title }) });
  });
  await page.goto("/charts");
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`CH-2 chat sessions ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: SHOTS ? 2 : 1 });

    test("the last conversation comes back; New chat starts another; an earlier one opens; one is renamed", async ({ page }) => {
      const asked: string[] = [];
      await open(page, asked);
      await page.locator("[data-chat-bubble]").click();
      const sheet = page.locator("[data-chat-sheet]");
      const toggle = sheet.locator("[data-chat-sessions-toggle]");
      await expect(toggle).toContainText(SESSIONS[0].title);
      await expect(sheet.locator("[data-turn]")).toHaveCount(1);
      if (size.phone) await expectPhoneLayout(page);
      // New chat: an empty screen with the ready questions; the question goes into a new conversation
      await sheet.locator("[data-new-chat]").click();
      await expect(sheet.locator("[data-turn]")).toHaveCount(0);
      await expect(toggle).toContainText("Conversations");
      await sheet.locator("[data-chat-starters] button").nth(1).click();
      await expect.poll(() => asked.length).toBe(1);
      expect(JSON.parse(asked[0])).toMatchObject({ message: "Which areas is NIFTY 50 near?", new_chat: true });
      await expect(toggle).toContainText("Which areas is NIFTY 50 near?");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/ch2-new-chat-${size.width}-dark.png` });
      // The list: most recent first, the open one marked
      await toggle.click();
      const items = sheet.locator("[data-chat-session]");
      await expect(items).toHaveCount(3);
      await expect(items.first()).toContainText("Open now");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/ch2-list-${size.width}-dark.png` });
      // Open the earlier conversation
      await items.nth(2).getByRole("button", { name: /Earlier conversation/ }).first().click();
      await expect(sheet.locator("[data-turn]")).toHaveCount(1);
      await expect(sheet.locator("[data-turn]").first()).toContainText("What is RSI divergence?");
      await expect(toggle).toContainText("Earlier conversation");
      // A question now continues that conversation
      await sheet.locator("textarea").fill("And on the hourly chart?");
      await sheet.locator("textarea").press("Enter");
      await expect.poll(() => asked.length).toBe(2);
      expect(JSON.parse(asked[1])).toMatchObject({ session_id: 1 });
      await expect(toggle).toContainText("Earlier conversation");
      // Rename
      await toggle.click();
      await sheet.getByRole("button", { name: `Rename: ${SESSIONS[0].title}` }).click();
      await sheet.getByLabel("Conversation name").fill("NIFTY structure notes");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/ch2-rename-${size.width}-dark.png` });
      await sheet.getByRole("button", { name: "Save" }).click();
      await expect(sheet.locator("[data-chat-sessions-list]")).toContainText("NIFTY structure notes");
    });
  });
}
