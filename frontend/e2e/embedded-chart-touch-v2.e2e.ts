import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";

/**
 * P1-4 (owner order "ATP session: chart and colour", Part 2.3): an embedded chart inside a scrolling page works like a
 * map embed on a touch screen. The page is the Copilot's chart answer (its mini chart is EmbeddedChartV2), every reply
 * made up (e2e/fixtures/copilot_chat_v2.json, the made-up candles of e2e/copilotMocks.ts).
 * - The first time, the hint "Tap to move the chart"; after the first tap it is gone, and remembered on reload.
 * - Before a tap, a one-finger vertical swipe on the chart scrolls the page.
 * - A tap activates the chart (the brand outline); 3 s with no touch, or a tap outside, deactivate it.
 * At 390 (a phone) and 1440 (a touch laptop); a mouse never activates and a screen without touch shows no hint.
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};
const QUESTION = "Read the SENSEX chart";

async function open(page: Page): Promise<void> {
  await page.clock.install({ time: new Date("2026-10-21T09:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true }, brokers: [] });
  await mockCopilotNumbers(page);
  const turns: { id: number; question: string; answer: Record<string, unknown>; at: string }[] = [];
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/history")) return route.fulfill({ json: { turns, keep: 20 } });
    if (request.method() === "GET") return route.fulfill({ json: F.home });
    const answer = F.answers[QUESTION];
    turns.push({ id: turns.length + 1, question: QUESTION, answer, at: "2026-10-21T03:30:00Z" });
    return route.fulfill({ json: answer });
  });
  await page.goto("/copilot");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
}

/** Asks for the chart answer; the answer's mini chart, drawn (the library's canvas, the bars in). */
async function chartAnswer(page: Page): Promise<Locator> {
  await page.locator("#copilot-question").fill(QUESTION);
  await page.locator("#copilot-question").press("Enter");
  const embed = page.locator("[data-answer='chart'] [data-embedded-chart='SENSEX']").last();
  await expect(embed.locator(".tv-lightweight-charts canvas").first()).toBeVisible();
  await expect(embed.getByText("Loading prices…")).toHaveCount(0);
  return embed;
}

/** The scroll position of the element that scrolls around the chart (the nearest scrolling ancestor, or the page). */
async function scrollTop(box: Locator): Promise<number> {
  return box.evaluate((el) => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      const y = getComputedStyle(n).overflowY;
      if ((y === "auto" || y === "scroll") && n.scrollHeight > n.clientHeight) return n.scrollTop;
    }
    return document.scrollingElement?.scrollTop ?? 0;
  });
}

/** Scrolls the chart to the middle of the screen (the page's fixed bars never cover it), once the page's own smooth
 * scroll to the new answer has ended. */
async function centre(box: Locator): Promise<void> {
  let last = -1;
  await expect.poll(async () => { const now = await scrollTop(box); const still = now === last; last = now; return still; },
    { intervals: [250] }).toBe(true);
  await box.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
}

/** A one-finger swipe of `dy` CSS px (real touch events through the browser's input pipeline, so it can scroll). */
async function swipe(page: Page, x: number, y: number, dy: number): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  for (let i = 1; i <= 10; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y + (dy * i) / 10 }] });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }] as const) {
  test.describe(`P1-4 2.3 embedded chart on a touch screen at ${size.width}`, () => {
    test.use(size.phone ? { viewport: { width: size.width, height: size.height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 }
      : { viewport: { width: size.width, height: size.height }, hasTouch: true, deviceScaleFactor: 1 });

    test("the hint shows; a vertical swipe scrolls the page; a tap activates (outline); 3 s idle and a tap outside end it", async ({ page }) => {
      await open(page);
      const embed = await chartAnswer(page);
      const area = embed.locator("[data-chart-active]");
      const hint = embed.locator("[data-chart-hint]");
      await expect(hint).toHaveText("Tap to move the chart");
      await expect(area).toHaveAttribute("data-chart-active", "off");
      await expect(embed.locator("[data-chart-outline]")).toHaveCount(0);

      // Before a tap: a vertical swipe on the chart scrolls the page (whichever way it still can), the chart stays off.
      await centre(area);                     // clear of the question box and the bottom bar fixed over the page
      const box = (await area.boundingBox())!;
      const x = box.x + box.width / 2, y = box.y + box.height / 2;
      expect(await area.evaluate((el, [px, py]) => el.contains(document.elementFromPoint(px, py)), [x, y] as const), "the swipe starts on the chart").toBe(true);
      const before = await scrollTop(area);
      await swipe(page, x, y, before > 0 ? 120 : -120);
      await expect.poll(() => scrollTop(area)).not.toBe(before);
      await expect(area).toHaveAttribute("data-chart-active", "off");
      await expect(hint).toBeVisible();

      // A tap activates it: the outline, the hint gone.
      await centre(area);
      const now = (await area.boundingBox())!;
      await page.touchscreen.tap(now.x + now.width / 2, now.y + now.height / 2);
      await expect(area).toHaveAttribute("data-chart-active", "on");
      await expect(embed.locator("[data-chart-outline]")).toBeVisible();
      await expect(hint).toHaveCount(0);
      // Active: the same swipe moves the chart, not the page.
      const held = await scrollTop(area);
      await swipe(page, now.x + now.width / 2, now.y + now.height / 2, 120);
      await page.waitForTimeout(300);
      expect(await scrollTop(area), "an active chart keeps the swipe").toBe(held);
      await expect(area).toHaveAttribute("data-chart-active", "on");

      // 3 s with no touch: off again.
      await page.clock.runFor(2_500);
      await expect(area).toHaveAttribute("data-chart-active", "on");
      await page.clock.runFor(600);
      await expect(area).toHaveAttribute("data-chart-active", "off");
      await expect(embed.locator("[data-chart-outline]")).toHaveCount(0);

      // Again on; a tap outside the chart ends it at once.
      await page.touchscreen.tap(now.x + now.width / 2, now.y + now.height / 2);
      await expect(area).toHaveAttribute("data-chart-active", "on");
      const head = (await page.locator("[data-answer='chart'] [data-headline='SENSEX']").last().boundingBox())!;
      await page.touchscreen.tap(head.x + 4, head.y + 4);
      await expect(area).toHaveAttribute("data-chart-active", "off");

      // The hint is remembered on this device.
      await page.reload();
      await page.waitForLoadState("networkidle");
      await expect(page.locator("[data-page='copilot-v2']")).toBeVisible({ timeout: 15_000 });
      const again = await chartAnswer(page);
      await expect(again.locator("[data-chart-active]")).toHaveAttribute("data-chart-active", "off");
      await expect(again.locator("[data-chart-hint]")).toHaveCount(0);
    });
  });
}

test.describe("P1-4 2.3 embedded chart with a mouse at 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("no hint without a touch screen, and a click never activates the chart", async ({ page }) => {
    await open(page);
    const embed = await chartAnswer(page);
    await expect(embed.locator("[data-chart-hint]")).toHaveCount(0);
    await embed.locator("[data-chart-active]").click();
    await expect(embed.locator("[data-chart-active]")).toHaveAttribute("data-chart-active", "off");
  });
});
