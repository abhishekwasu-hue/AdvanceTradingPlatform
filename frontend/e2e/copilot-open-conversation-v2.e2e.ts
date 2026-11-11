import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { mockCopilotNumbers, readChart } from "./copilotMocks";
import { mockApi } from "./mockApi";

/**
 * Owner 8 Nov (production, phone): Copilot with his own Claude key connected and a conversation already open. Asking
 * ("Read the NIFTY chart", or a quick button) left the screen on the chips and the OLDEST kept answer under them - an
 * earlier chart read with its own close and date (30 Oct) - for the whole read, while the four quick buttons were
 * disabled with no visible change ("pressing does nothing"). Now: the asked turn comes into view at once with its
 * "Reading ATP's data…" line, a kept turn says it was asked earlier, and the buttons show they wait. Mocked API only;
 * CPFIX_SHOTS=<dir> writes the proof shots.
 */
const SHOTS = process.env.CPFIX_SHOTS;
const TAG = process.env.CPFIX_TAG ?? "after";
const CHAT = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};
const CONVO = { id: 7, title: "Read the NIFTY chart" };
const CHART = CHAT.answers["Read the NIFTY chart"];
const OLD_HEADLINE = { symbol: "NIFTY", price: 23053.1, price_label: "Last close", as_of: "2026-10-30", trend: "down", timeframe: "Daily",
  area: { side: "supply", from: 23020.95, to: 23162.7, where: "inside", distance: 0, distance_pct: 0 }, chart: { symbol: "NIFTY 50", exchange: "NSE", timeframe: "day" } };
const NEW_HEADLINE = { ...OLD_HEADLINE, price: 25030.35, as_of: "2026-11-06", trend: "up", area: null };
const KEPT = [
  { id: 70, question: "Read the NIFTY chart", at: "2026-11-08T03:00:00+00:00", answer: { ...CHART, headline: OLD_HEADLINE, structure_card: null, conversation: CONVO } },
  { id: 71, question: "What does the option chain show?", at: "2026-11-08T03:05:00+00:00", answer: { ...CHAT.answers["What does the option chain show?"], conversation: CONVO } },
];

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: SHOTS ? 2 : 1 });

test("a question in an open conversation shows its own turn at once; kept turns say they were asked earlier", async ({ page }) => {
  const asked: string[] = [];
  let release: () => void = () => undefined;
  const held = new Promise<void>((done) => { release = done; });
  await page.clock.install({ time: new Date("2026-11-08T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["copilot-chat"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, ai_one_key: true, copilot_agent: true }, brokers: [] });
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/connection$/, (r) => r.fulfill({ json: { connected: true, label: "Claude", provider: "anthropic" } }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat\/sessions$/, (r) => r.fulfill({ json: { sessions: [{ ...CONVO, turns: 2, updated_at: "2026-11-08T03:05:00+00:00" }], keep: 50 } }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat\/history$/, (r) => r.fulfill({ json: { keep: 20, session: CONVO, turns: KEPT } }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home)?$/, async (r) => {
    if (r.request().method() === "GET") {
      return r.fulfill({ json: { ...CHAT.home, keep: 20, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null, code: null, own_key: true } } });
    }
    asked.push(r.request().postData() ?? "");
    await held;                                         // the AI planner's read takes a while
    return r.fulfill({ json: { ...CHART, headline: NEW_HEADLINE, structure_card: null, conversation: CONVO } });
  });
  await page.goto("/copilot");
  await expect(page.locator("[data-quick]")).toHaveCount(4);
  await expect(page.locator("[data-turn]")).toHaveCount(2);
  // the kept turns say they are earlier answers
  await expect.soft(page.locator("[data-turn='1'] [data-turn-at]")).toContainText("Asked earlier");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${TAG}-open-390-dark.png` });

  await readChart(page);
  await expect.poll(() => asked.length).toBe(1);
  expect(JSON.parse(asked[0])).toMatchObject({ message: "Read the NIFTY chart", session_id: CONVO.id });
  if (SHOTS) { await page.waitForTimeout(1000); await page.screenshot({ path: `${SHOTS}/${TAG}-wait-390-dark.png` }); }
  // while the read runs: the asked turn is in view with its waiting line; the buttons visibly wait
  const mine = page.locator("[data-turn='3']");
  await expect(mine).toContainText("Reading ATP's data");
  await expect.soft(mine).toBeInViewport();
  await expect(mine.locator("[data-turn-at]")).toHaveCount(0);
  await expect(page.locator("[data-quick='brief']")).toBeDisabled();
  await expect.soft(page.locator("[data-quick='brief']")).toHaveCSS("opacity", "0.5");

  release();
  await expect(mine.locator("[data-headline-price]")).toHaveText(/25,030\.35/);
  await expect(page.locator("[data-quick='brief']")).toBeEnabled();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${TAG}-answer-390-dark.png` });
});

test("owner 9 Nov: a conversation from an earlier day does not open by itself; the next question starts a new chat", async ({ page }) => {
  const asked: string[] = [];
  await page.clock.install({ time: new Date("2026-11-09T10:00:00+05:30") });   // the kept turns are of 8 Nov
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["copilot-chat"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, ai_one_key: true, copilot_agent: true }, brokers: [] });
  await mockCopilotNumbers(page);
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/connection$/, (r) => r.fulfill({ json: { connected: true, label: "Claude", provider: "anthropic" } }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat\/sessions$/, (r) => r.fulfill({ json: { sessions: [{ ...CONVO, turns: 2, updated_at: "2026-11-08T03:05:00+00:00" }], keep: 50 } }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat\/history(\?session_id=\d+)?$/, (r) => r.fulfill({ json: { keep: 20, session: CONVO, turns: KEPT } }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home)?$/, async (r) => {
    if (r.request().method() === "GET") {
      return r.fulfill({ json: { ...CHAT.home, keep: 20, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null, code: null, own_key: true } } });
    }
    asked.push(r.request().postData() ?? "");
    return r.fulfill({ json: { ...CHART, headline: NEW_HEADLINE, structure_card: null, conversation: { id: CONVO.id + 1, title: "Read the NIFTY chart" } } });
  });
  await page.goto("/copilot");
  await expect(page.locator("[data-quick]")).toHaveCount(4);
  await page.waitForTimeout(300);                                                  // the kept conversation has been read
  await expect(page.locator("[data-turn]")).toHaveCount(0);                        // the old answer is not on screen
  await readChart(page);
  await expect.poll(() => asked.length).toBe(1);
  expect(JSON.parse(asked[0])).toMatchObject({ message: "Read the NIFTY chart", new_chat: true });
  await expect(page.locator("[data-turn]")).toHaveCount(1);
  await expect(page.locator("[data-turn='1'] [data-turn-at]")).toHaveCount(0);
});
