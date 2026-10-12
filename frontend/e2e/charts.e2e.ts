import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { bars, chartRoutes, paGolden, painted } from "./chartMocks";
import { mockApi } from "./mockApi";

/**
 * N4 render smoke checks (production build, mocked API, desktop Chromium): the chart pages draw without a page error
 * and show what they should. Screenshots are attached to the test (uploaded as a CI artifact), never committed.
 *   CH3a: the price-action layer on the full-window chart, replay forward and back - while a step's layer is on its
 *         way nothing older is on screen, and every request reads as of its bar (start < at).
 *   CH5-a: the 4-chart workspace, all in link group A - the crosshair on chart 1 moves the others to the same bar.
 *   CH5-b: every chart type renders (badge, bars on the chart).
 */
test.beforeEach(({}, info) => { test.skip(info.project.name !== "desktop", "desktop only"); });

async function shot(page: Page, info: TestInfo, name: string) {
  await painted(page);
  const path = info.outputPath(`${name}.png`);
  await page.screenshot({ path });
  await info.attach(name, { path, contentType: "image/png" });
}

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

test("CH3a: price-action layer and replay - no stale layer while a step loads", async ({ page }, info) => {
  const errors = pageErrors(page);
  const b = bars(600, 15);
  const asked: URL[] = [];
  let delay = 0;
  await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* */ } });
  await mockApi(page);
  await chartRoutes(page, {
    candles: () => b,
    priceAction: (url) => {
      asked.push(url);
      const at = url.searchParams.get("at") ?? new Date(Date.parse(b[b.length - 1].timestamp) + 15 * 60_000).toISOString();
      return { body: { ...paGolden, instrument: "NSE:PAE2E", at, start: url.searchParams.get("start") ?? "", as_of: at, source: "lake:15m", note: "e2e" }, delayMs: delay };
    },
  });
  await page.goto("/?chart=PAE2E&tf=15min&exchange=NSE");
  const panel = page.getByTestId("price-action-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("pro-chart").first()).toHaveAttribute("data-bars", "600");
  await shot(page, info, "ch3a-live");

  await page.getByRole("button", { name: "Replay" }).click();
  await expect(page.getByTestId("replay-position")).toContainText("bar 500 / 600");
  await expect(panel).toBeVisible();
  const atOf = () => asked[asked.length - 1].searchParams.get("at");
  const first = atOf();
  expect(first).toBe(new Date(Date.parse(b[499].timestamp) + 15 * 60_000).toISOString());
  await shot(page, info, "ch3a-replay-step");

  delay = 1_500;                                                                // the next answers take a while
  await page.getByRole("button", { name: "◀ Bar" }).click();
  await expect(page.getByTestId("replay-position")).toContainText("bar 499 / 600");
  await expect(panel).toHaveCount(0);                                           // the bar-500 layer is gone at once
  await expect(page.getByTestId("pro-chart").first()).toHaveAttribute("data-bars", "499");
  await expect(panel).toBeVisible({ timeout: 10_000 });
  expect(atOf()).toBe(new Date(Date.parse(b[498].timestamp) + 15 * 60_000).toISOString());
  await shot(page, info, "ch3a-replay-step-back");

  await page.getByRole("button", { name: "Bar ▶" }).click();
  await expect(panel).toHaveCount(0);
  await expect(panel).toBeVisible({ timeout: 10_000 });
  expect(atOf()).toBe(first);
  for (const u of asked) {
    const at = u.searchParams.get("at"), start = u.searchParams.get("start");
    if (at && start) expect(Date.parse(start)).toBeLessThan(Date.parse(at));    // never start >= at
  }
  await page.getByRole("button", { name: "Exit replay" }).click();
  await expect(page.getByTestId("pro-chart").first()).toHaveAttribute("data-bars", "600");
  expect(errors).toEqual([]);
});

async function workspace(page: Page, grid: number) {
  await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); localStorage.removeItem("atp_workspace_draft"); } catch { /* */ } });
  await mockApi(page);
  await chartRoutes(page, { candles: () => bars(400), features: { chart_workspace: true } });
  await page.goto("/?workspace=1&chart=WSE2E&tf=5min&exchange=NSE");
  await expect(page.getByRole("heading", { name: "Chart workspace" })).toBeAttached();
  if (grid > 1) await page.getByRole("group", { name: "Charts on screen" }).getByRole("button", { name: String(grid), exact: true }).click();
  await expect(page.getByTestId("pro-chart")).toHaveCount(grid);
  for (let i = 0; i < grid; i++) await expect(page.getByTestId("pro-chart").nth(i)).toHaveAttribute("data-bars", "400");
}

test("CH5-a: four charts, crosshair in sync across link group A", async ({ page }, info) => {
  const errors = pageErrors(page);
  await workspace(page, 4);
  for (let i = 0; i < 4; i++) {
    const cell = page.getByRole("region", { name: new RegExp(`^Chart ${i + 1}:`) });
    // the first press on a chart makes it the active one (its header grows from the compact form), so press until A
    await expect(async () => {
      const button = cell.getByRole("button", { name: /^Link group / });
      if ((await button.getAttribute("aria-label")) !== "Link group A") await button.click();
      await expect(button).toHaveAccessibleName("Link group A", { timeout: 1_000 });
    }).toPass({ timeout: 10_000 });
  }
  await expect(page.getByRole("button", { name: "Link group A" })).toHaveCount(4);
  const first = page.getByRole("region", { name: /^Chart 1:/ }).locator("canvas").first();
  const box = await first.boundingBox();
  if (!box) throw new Error("chart 1 has no canvas");
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5);
  const charts = page.getByTestId("pro-chart");
  await expect(charts.nth(0)).not.toHaveAttribute("data-hover", "");
  const t = await charts.nth(0).getAttribute("data-hover");
  for (let i = 1; i < 4; i++) await expect(charts.nth(i), `chart ${i + 1}`).toHaveAttribute("data-hover", t as string);
  await shot(page, info, "ch5a-4-charts-crosshair");
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5, { steps: 5 });
  await expect(charts.nth(0)).not.toHaveAttribute("data-hover", t as string);
  const t2 = await charts.nth(0).getAttribute("data-hover");
  for (let i = 1; i < 4; i++) await expect(charts.nth(i)).toHaveAttribute("data-hover", t2 as string);
  expect(errors).toEqual([]);
});

const TYPES: [string, string][] = [["hollow", "Hollow candles"], ["heikin_ashi", "Heikin Ashi"], ["renko", "Renko"], ["range", "Range"], ["line", "Line"],
                                   ["area", "Area"], ["candles", ""]];

test("CH5-b: every chart type renders", async ({ page }, info) => {
  const errors = pageErrors(page);
  await workspace(page, 1);
  const select = page.getByRole("combobox", { name: "Chart type of chart 1" });
  const chart = page.getByTestId("pro-chart").first();
  for (const [value, label] of TYPES) {
    await select.selectOption(value);
    if (label) await expect(chart.getByText(label, { exact: false }).first()).toBeVisible();
    await expect(chart).not.toHaveAttribute("data-bars", "0");
    await shot(page, info, `ch5b-${value}`);
  }
  expect(errors).toEqual([]);
});
