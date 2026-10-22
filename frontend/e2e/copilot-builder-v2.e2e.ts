import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Item 5 (owner's handover), job 6: the Copilot's strategy builder with `ui_v2` and the Copilot switch on. The trader's
 * own idea in the chat -> a draft paper strategy in plain words ("Values you decide" for every number his words do not
 * give) -> "Review it on the Paper bot page" -> the review card (EMPTY fields, Save off until every value is his) -> his
 * own "Save as my strategy" -> the strategy picked in "Start a paper bot". The Copilot saves and starts nothing: no
 * deployment is created in this whole flow.
 * - Baselines `copilot-builder-v2-answer-<width>-<theme>.png` (the chat answer) and `copilot-builder-v2-draft-<width>-
 *   <theme>.png` (the review card), 360 / 390 / 1440, light / dark.
 * Every reply is made-up data (e2e/fixtures/copilot_chat_v2.json: the backend's rule answer on its synthetic eval fixture;
 * e2e/fixtures/setup_v2.json strategies). After an intended change:
 * `npx playwright test --project=phone e2e/copilot-builder-v2.e2e.ts --update-snapshots`.
 */
const C = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};
const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const IDEA = "Turn my idea into a strategy: on the 15-minute chart, go long when the fast EMA crosses above the slow EMA and RSI 14 is above "
  + "50 on NIFTY. Stop 1.5 x ATR 14.";

interface Server { asked: string[]; saved: unknown[]; deployments: unknown[] }

async function open(page: Page, theme: "light" | "dark" = "light"): Promise<Server> {
  const server: Server = { asked: [], saved: [], deployments: [] };
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: F.features as Record<string, boolean>, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home)?$/, async (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({ json: C.home });
    const message = (request.postDataJSON() as { message: string }).message;
    server.asked.push(message);
    return route.fulfill({ json: C.answers[message] ?? C.answers["Should I buy RELIANCE today?"] });
  });
  await page.route(api("/strategies$"), (route) => route.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: [] });
    const config = route.request().postDataJSON() as Record<string, unknown>;
    server.saved.push(config);
    return route.fulfill({ json: { id: 9, strategy_id: "custom:9", config, live_version_id: null, created_at: "2026-10-21T03:30:00Z", updated_at: "2026-10-21T03:30:00Z" } });
  });
  await page.route(api("/broker/credentials$"), (route) => route.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/deployments(\\?.*)?$"), (route) => {
    if (route.request().method() === "POST") server.deployments.push(route.request().postDataJSON());
    return route.fulfill({ json: [] });
  });
  await page.route(api("/worker/status$"), (route) => route.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
  return server;
}

async function askIdea(page: Page) {
  await page.locator("#copilot-question").fill(IDEA);
  await page.getByRole("button", { name: "Ask" }).click();
  const answer = page.locator("[data-answer='strategy']");
  await expect(answer).toBeVisible();
  return answer;
}

async function toPaperBot(page: Page) {
  await page.locator("[data-answer='strategy'] a[data-action='draft']").click();
  const card = page.locator("[data-block='copilot-draft']");
  await expect(card).toBeVisible();
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return card;
}

const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const SIZES = [
  { name: "360", width: 360, height: 780, mobile: true }, { name: "390", width: 390, height: 844, mobile: true },
  { name: "1440", width: 1440, height: 900, mobile: false },
];

for (const size of SIZES) {
  test.describe(`Copilot strategy builder ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the answer and the review card look the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        const answer = await askIdea(page);
        await expect(answer).toHaveScreenshot(`copilot-builder-v2-answer-${size.name}-${theme}.png`, SHOT);
        const card = await toPaperBot(page);
        await expect(card).toHaveScreenshot(`copilot-builder-v2-draft-${size.name}-${theme}.png`, SHOT);
      });
    }

    if (size.mobile) {
      test("phone: no sideways scroll, 44 px targets on the chat answer and the review card", async ({ page }) => {
        await open(page);
        await askIdea(page);
        await expectPhoneLayout(page);
        await toPaperBot(page);
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("Copilot strategy builder behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("job 6 is ready in What I can do; its starter asks for the idea with an example", async ({ page }) => {
    const server = await open(page);
    await expect(page.locator("[data-job='strategy']")).toContainText("Ready");
    await page.locator("[data-starters] button", { hasText: "Turn my idea into a strategy and test it" }).click();
    const answer = page.locator("[data-answer='strategy']");
    await expect(answer).toContainText("Tell me your idea in your own words");
    await expect(answer.locator("[data-section='What I can build today']")).toContainText("The volume against its average.");
    await expect(answer.locator("a[data-action='draft']")).toHaveCount(0);
    expect(server.asked).toEqual(["Turn my idea into a strategy and test it"]);
  });

  test("the draft in plain words: his values as written, the ones he did not give asked, nothing invented", async ({ page }) => {
    await open(page);
    const answer = await askIdea(page);
    await expect(answer.locator("[data-section='Long side: all of these hold on the 15-minute chart']")).toContainText(
      "The fast EMA (length to decide) crosses above the slow EMA (length to decide).");
    await expect(answer.locator("[data-section='Your settings']")).toContainText("Where the idea is wrong: 1.5 x ATR (14) from the entry.");
    const decide = answer.locator("[data-section='Values you decide']");
    for (const ask of ["The fast EMA length.", "The slow EMA length.", "Your profit objective, in R", "The size: the Paper bot page"]) {
      await expect(decide).toContainText(ask);
    }
    await expect(answer).not.toContainText("Profit objective:");
    await expect(answer.locator("[data-unknowns]")).toContainText("I don't know how this idea did in the past");
    await expect(answer.locator("[data-disclaimer]")).toContainText("not advice");
  });

  test("the hand-over: he fills in his own values, saves it himself, and the bot form picks it; nothing starts", async ({ page }) => {
    const server = await open(page);
    await askIdea(page);
    const card = await toPaperBot(page);
    await expect(page).toHaveURL(/\/deployments$/);
    await expect(card).toContainText("Nothing is saved yet");
    const save = card.getByRole("button", { name: "Save as my strategy" });
    await expect(save).toBeDisabled();
    for (const key of ["rules.0.left.period", "rules.0.right.period", "target_rr"]) {
      await expect(card.locator(`[data-ask='${key}']`)).toHaveValue("");                   // empty: never a suggested number
    }
    await expect(card.locator("[data-save-line]")).toHaveText("3 values are still yours to decide.");
    await card.locator("[data-ask='rules.0.left.period']").fill("9");
    await card.locator("[data-ask='rules.0.right.period']").fill("21");
    await expect(save).toBeDisabled();
    await card.locator("[data-ask='target_rr']").fill("2");
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.locator("[data-note='draft-saved']")).toContainText("Saved as your strategy \"My EMA + RSI idea\"");
    await expect(page.locator("[data-block='copilot-draft']")).toHaveCount(0);
    const form = page.locator("[data-block='start']");
    await expect(form.getByLabel("Pick a strategy")).toHaveValue("custom:9");
    await expect(form.getByLabel("Pick a symbol")).toHaveValue("NIFTY 50");
    expect(server.saved).toHaveLength(1);
    expect(server.saved[0]).toMatchObject({
      name: "My EMA + RSI idea", timeframe: "15min", short_conditions: [], stop_loss_atr_mult: 1.5, atr_period: 14, target_rr: [2, 2], min_rr: 1.5,
      long_conditions: [
        { operator: "CROSSES_ABOVE", left: { indicator: "EMA", period: 9 }, right: { indicator: "EMA", period: 21 } },
        { operator: "GT", left: { indicator: "RSI", period: 14 }, right: { type: "value", value: 50 } },
      ],
    });
    expect(server.deployments).toEqual([]);                                                // the Copilot started nothing
  });

  test("discarding the draft leaves the page as it was", async ({ page }) => {
    const server = await open(page);
    await askIdea(page);
    const card = await toPaperBot(page);
    await card.getByRole("button", { name: "Discard the draft" }).click();
    await expect(page.locator("[data-block='copilot-draft']")).toHaveCount(0);
    await expect(page.locator("[data-block='start']")).toBeVisible();
    expect(server.saved).toEqual([]);
    expect(server.deployments).toEqual([]);
  });
});
