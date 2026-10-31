import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

/**
 * Z5 / P1-c: the Options Builder page against the builder API's own answers (e2e/fixtures/options_builder.json, made by
 * the engine at fixed dates with synthetic prices). The page draws what the engine returns: the expiry and T+0 curves,
 * the max-loss line, the strike handles (dragged, or moved by keyboard), the metrics, the hedge-first basket. Runs in
 * the desktop and the mobile (Pixel 7) projects; phone-only and desktop-only checks say so.
 */
const fx = JSON.parse(readFileSync(new URL("./fixtures/options_builder.json", import.meta.url), "utf8")) as {
  catalog: unknown; template: { legs: { strike: number; direction: string }[] }; evaluate: unknown; evaluate_3d: unknown; basket: unknown;
  strangle: { legs: unknown[]; evaluate: unknown };
};

type Body = { legs: { strike: number }[]; days_forward?: number };

async function builder(page: Page) {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ } });
  const calls = await mockApi(page);
  const evaluations: Body[] = [];
  const baskets: { underlying: string; legs: unknown[] }[] = [];
  await page.route(/\/api(\/v1)?\/options-builder\//, async (route) => {
    const req = route.request();
    const url = req.url();
    if (/\/catalog$/.test(url)) return route.fulfill({ json: fx.catalog });
    if (/\/template$/.test(url)) return route.fulfill({ json: fx.template });
    if (/\/basket$/.test(url)) {
      baskets.push(req.postDataJSON() as { underlying: string; legs: unknown[] });
      return route.fulfill({ json: fx.basket });
    }
    if (/\/evaluate$/.test(url)) {
      const body = req.postDataJSON() as Body;
      evaluations.push(body);
      if (body.legs.length === 2) return route.fulfill({ json: fx.strangle.evaluate });
      return route.fulfill({ json: (body.days_forward ?? 0) > 0 ? fx.evaluate_3d : fx.evaluate });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });                // saving: the flag is off
  });
  await page.goto("/options-builder");
  return { calls, evaluations, baskets };
}

async function fillAndPickCondor(page: Page) {
  const set = async (label: string, value: string) => page.getByLabel(label, { exact: true }).fill(value);
  await set("Underlying", "NIFTY");
  await set("Spot", "22000");
  await set("Strike step", "50");
  await set("Lot size", "75");
  await set("Template width", "100");
  await set("Base IV %", "14");
  await set("Near expiry", "2026-10-15");
  await page.getByRole("tab", { name: "Neutral" }).click();
  await page.getByTestId("template-card").filter({ hasText: /^Iron Condor$/ }).click();
  await expect(page.getByTestId("leg-row")).toHaveCount(4);
  await expect(page.getByTestId("curve-expiry")).toBeVisible();
}

test("an iron condor from the gallery: expiry and T+0 curves, max-loss line, the engine's numbers", async ({ page }) => {
  await builder(page);
  await fillAndPickCondor(page);
  await expect(page.getByTestId("curve-date")).toBeVisible();
  await expect(page.getByTestId("max-loss-line")).toContainText("Max loss −₹2,061");
  await expect(page.getByTestId("breakeven")).toHaveCount(2);
  await expect(page.getByTestId("loss-area").first()).toBeVisible();
  await expect(page.getByTestId("strike-handle")).toHaveCount(4);
  await expect(page.getByTestId("metric-net")).toContainText("Net credit");
  await expect(page.getByTestId("metric-margin")).toContainText("₹2,061");
  await expect(page.getByTestId("metric-margin")).toContainText("at least the max loss");
  await expect(page.getByTestId("metric-cost")).toContainText("4 orders at ₹20.00 each");
  await expect(page.getByTestId("risk-gauge")).toBeVisible();
  // a later date: the T+0 curve stays, the dated curve is dashed
  await page.getByLabel("Days forward").fill("3");
  await expect(page.getByTestId("curve-today")).toBeVisible();
  await expect(page.getByTestId("curve-date")).toHaveAttribute("stroke-dasharray", "6 4");
});

test("a strike moves by keyboard and the next evaluation asks for the new strike", async ({ page }) => {
  const { evaluations } = await builder(page);
  await fillAndPickCondor(page);
  const handle = page.getByTestId("strike-handle").first();
  const before = Number(await handle.getAttribute("aria-valuenow"));
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  await expect(handle).toHaveAttribute("aria-valuenow", String(before + 50));
  await expect.poll(() => evaluations.at(-1)?.legs[0].strike).toBe(before + 50);
  await page.keyboard.press("PageDown");
  await expect(handle).toHaveAttribute("aria-valuenow", String(before + 50 - 250));
});

test("a strike follows the pointer when dragged", async ({ page, isMobile }) => {
  test.skip(isMobile, "pointer drag is checked on the desktop project");
  const { evaluations } = await builder(page);
  await fillAndPickCondor(page);
  const handle = page.getByTestId("strike-handle").nth(3);
  const before = Number(await handle.getAttribute("aria-valuenow"));
  await page.getByTestId("strike-hit").nth(3).scrollIntoViewIfNeeded();
  const box = (await page.getByTestId("strike-hit").nth(3).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2, { steps: 6 });
  await page.mouse.up();
  const after = Number(await handle.getAttribute("aria-valuenow"));
  expect(after).toBeGreaterThan(before);
  expect(after % 50).toBe(0);
  await expect.poll(() => evaluations.at(-1)?.legs.some((l) => l.strike === after)).toBe(true);
});

test("Place as basket shows the hedge-first basket; placing waits for the PAPER ticket", async ({ page }) => {
  const { calls, baskets } = await builder(page);
  await fillAndPickCondor(page);
  await page.getByTestId("place-basket").click();
  const orders = page.getByTestId("basket-order");
  await expect(orders).toHaveCount(4);
  await expect(orders.nth(0)).toContainText("BUY");
  await expect(orders.nth(1)).toContainText("BUY");
  await expect(orders.nth(2)).toContainText("SELL");
  await expect(orders.nth(0)).toContainText("75 qty");
  await expect(page.getByTestId("basket-place")).toBeDisabled();
  await expect(page.getByTestId("basket-why-not")).toContainText("PAPER");
  expect(calls.posts.some((p) => /order|trading|execution/.test(p.url))).toBe(false);          // nothing placed anywhere
  expect(baskets.at(-1)?.underlying).toBe("NIFTY");
  expect(baskets.at(-1)?.legs).toHaveLength(4);
});

test("Save strategy says it is not turned on while its flag is off", async ({ page }) => {
  await builder(page);
  await expect(page.getByTestId("save-strategy")).toBeDisabled();
  // PROSE-1: the explanation sits in the actions card's closed Details; open it to read.
  await page.locator("[data-details='optionsBuilder.actions'] summary").click();
  await expect(page.getByText("Saving strategies is not turned on for your organisation yet.")).toBeVisible();
});

test("an undefined-risk strangle says so instead of drawing a max-loss line", async ({ page }) => {
  await builder(page);
  await fillAndPickCondor(page);
  for (let i = 0; i < 2; i++) await page.getByRole("button", { name: /^Remove leg 1$/ }).click();
  await expect(page.getByTestId("leg-row")).toHaveCount(2);
  await expect(page.getByTestId("risk-undefined")).toBeVisible();
  await expect(page.getByTestId("max-loss-line")).toHaveCount(0);
  await expect(page.getByTestId("metric-margin")).toContainText("From the broker");
});

test("phone: the payoff comes first, the legs below it, and every strike handle is a 44 px target", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout is checked on the mobile project");
  await builder(page);
  await fillAndPickCondor(page);
  const top = async (name: string) => (await page.locator(`[aria-label="${name}"]`).first().boundingBox())!.y;
  expect(await top("Payoff")).toBeLessThan(await top("Legs"));
  expect(await top("Legs")).toBeLessThan(await top("Metrics"));
  expect(await top("Metrics")).toBeLessThan(await top("Templates"));
  for (const hit of await page.getByTestId("strike-hit").all()) {
    const box = (await hit.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(43.5);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
