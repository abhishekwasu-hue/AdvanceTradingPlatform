import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Item 5 (owner's handover, the AI Copilot as a trader's brain): the chat-first Copilot at /copilot with `ui_v2` on.
 * - Baselines `copilot-chat-v2-<width>-<theme>.png` at 1440 and 390 (new; the seven-tab Copilot's baselines are not
 *   touched).
 * - The top line, the no-key card, today's market line, the starters, a starter's answer with its table and "How I
 *   worked this out", a refusal in plain words, the phone layout (the question box above the bottom bar).
 * - Second slice: the position watch (job 4) with its warnings and table, the post-trade coach (job 5) with the week
 *   and the repeated habits, and the pre-trade check (job 3) listing what the idea does not state yet.
 * Every reply is made-up data (e2e/fixtures/copilot_chat_v2.json, the backend's rule answers on its synthetic eval
 * fixtures). After an intended change: `npx playwright test --project=phone e2e/copilot-chat-v2.e2e.ts --update-snapshots`.
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};

async function open(page: Page, theme: "dark" | "light"): Promise<string[]> {
  const asked: string[] = [];
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true }, brokers: [] });
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home)?$/, async (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({ json: F.home });
    const message = (request.postDataJSON() as { message: string }).message;
    asked.push(message);
    const answer = F.answers[message] ?? F.answers["Should I buy RELIANCE today?"];
    return route.fulfill({ json: answer });
  });
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-market-line='ok']")).toBeVisible();
  return asked;
}

const THEMES = ["dark", "light"] as const;

test.describe("v2 copilot chat 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page).toHaveScreenshot(`copilot-chat-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("the question box sits above the bottom bar; no sideways scroll, 44 px targets", async ({ page }) => {
    await open(page, "dark");
    const box = page.locator("[data-composer='bottom']");
    await expect(box).toBeVisible();
    const nav = await page.locator("nav[aria-label='Quick navigation']").boundingBox();
    const form = await box.boundingBox();
    expect(form && nav && form.y + form.height <= nav.y + 1).toBeTruthy();
    await expectPhoneLayout(page);
  });

  test("a question typed on the phone gets its answer", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("#copilot-question").fill("What does the option chain show?");
    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.locator("[data-answer='option_chain']")).toBeVisible();
    await expect(page.locator("[data-answer='option_chain'] [data-block='chart']").first()).toBeVisible();
    expect(asked).toEqual(["What does the option chain show?"]);
  });
});

test.describe("v2 copilot chat 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page).toHaveScreenshot(`copilot-chat-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("the top line, the no-key card, the market line, the starters and the eight jobs", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("[data-top-line]")).toHaveText("Ask me anything about the market or your trades.");
    await expect(page.locator("[data-no-key]")).toContainText("Add a free AI key to start");
    await expect(page.locator("[data-no-key] a")).toHaveAttribute("href", "/settings/ai");
    await expect(page.locator("[data-market-line='ok']")).toContainText("NIFTY 50 last closed at 24,850.5");
    await expect(page.locator("[data-starters] button")).toHaveCount(8);
    await expect(page.locator("[data-job]")).toHaveCount(8);
    await expect(page.locator("[data-job='positions']")).toContainText("Ready");            // jobs 3-5: the second slice
    await expect(page.locator("[data-job='strategy']")).toContainText("Ready");                 // job 6: the third slice
    await expect(page.locator("[data-composer='inline']")).toBeVisible();
  });

  test("a starter's answer shows the data, the tables and how it was worked out", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("[data-starters] button", { hasText: "Read the NIFTY chart" }).click();
    const answer = page.locator("[data-answer='chart']");
    await expect(answer).toContainText("NIFTY last closed at 24,850.5");
    await expect(answer).toContainText("Structure reading from ATP's zone layers");
    await expect(answer.locator("[data-block='table']").first()).toBeVisible();
    await answer.locator("[data-worked] summary").click();
    await expect(answer.locator("[data-step='get_chart_structure']")).toContainText("last close 24850.5");
    await expect(answer.locator("[data-disclaimer]")).toContainText("not advice");
    expect(asked).toEqual(["Read the NIFTY chart"]);
  });

  test("the position watch warns from the user's own positions and settings", async ({ page }) => {
    const asked = await open(page, "light");
    await page.locator("[data-starters] button", { hasText: "How are my open positions?" }).click();
    const answer = page.locator("[data-answer='positions']");
    await expect(answer).toContainText("You have 2 open positions");
    await expect(answer.locator("[data-section='Warnings']")).toContainText("your own limit for this is 6%: above it");
    await expect(answer.locator("[data-block='table']").first()).toContainText("NIFTY26OCTFUT");
    await expect(answer.locator("a", { hasText: "Open My positions" })).toHaveAttribute("href", "/positions");
    await answer.locator("[data-worked] summary").click();
    await expect(answer.locator("[data-step='get_my_positions']")).toContainText("total loss if stops hit 18300");
    expect(asked).toEqual(["How are my open positions?"]);
  });

  test("the post-trade coach and the pre-trade check describe; they never advise", async ({ page }) => {
    await open(page, "light");
    await page.locator("[data-starters] button", { hasText: "Review my last trade" }).click();
    const review = page.locator("[data-answer='review']");
    await expect(review.locator("[data-section='Repeated habits']")).toContainText("tagged in 3 of your last 4 weeks");
    await page.locator("[data-starters] button", { hasText: "Check my trade idea" }).click();
    const check = page.locator("[data-answer='trade_check']");
    await expect(check.locator("[data-section='What your plan does not say yet']")).toContainText("your invalidation price");
    await expect(check).toContainText("I don't add rules of my own");
  });

  test("a buy question is refused in plain words", async ({ page }) => {
    await open(page, "light");
    await page.locator("#copilot-question").fill("Should I buy RELIANCE today?");
    await page.locator("#copilot-question").press("Enter");
    const answer = page.locator("[data-answer='refuse']");
    await expect(answer).toContainText("I can't tell you to buy or sell anything.");
    await expect(answer.locator("[data-worked]")).toHaveCount(0);
  });
});
