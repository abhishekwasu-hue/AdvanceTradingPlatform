import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Chart order 2.1-2.2 (docs/design/CHART_COLOUR_REVIEW.md, PR 2): both axes and the floating controls on the v2 chart,
 * at 1440 and 390. The canvas cannot be read for prices, so the chart's root tells its state: `data-autoscale`
 * (Auto on / off), `data-latest-visible` (the latest bar on screen) and `data-crosshair` (the readout shows).
 * - a vertical drag on the plot turns Auto off; "Auto" turns it back on;
 * - a sideways drag pans time: "Latest" comes, and goes again once pressed;
 * - a drag on the price axis turns Auto off (the library's own stretch); a double-click on it turns it back on; a
 *   double-click on the time axis brings the latest bar back;
 * - "Reset" restores both; Shift + wheel pans time; the readout follows the crosshair;
 * - 44 px targets and no sideways scroll on the phone; tooltips on the desktop.
 * Every reply is made-up data (e2e/chartsMocks.ts). The page-level baselines live in charts-v2.e2e.ts.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

async function open(page: Page, theme: "light" | "dark" = "light") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_workspace: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  await page.goto("/charts");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
  await expect(page.getByText("Loading prices…")).toHaveCount(0);
  await expect(controls(page)).toBeVisible();
}

const root = (page: Page) => page.locator("[data-page='charts-v2'] [data-autoscale]");
const controls = (page: Page) => page.getByRole("group", { name: "Chart view" });
const button = (page: Page, name: string) => controls(page).getByRole("button", { name, exact: true });

/** The price pane's plot (left of the price axis, above the lower panes), from the price axis cell of the library's table. */
async function plotBox(page: Page) {
  const table = page.locator("[data-page='charts-v2'] .tv-lightweight-charts table").first();
  const box = (await table.boundingBox())!;
  const paneCell = (await table.locator("tr").first().locator("td").nth(1).boundingBox())!;
  const axisCell = (await table.locator("tr").first().locator("td").nth(2).boundingBox())!;
  return { plot: paneCell, axis: axisCell, chart: box };
}

async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 10 });
  await page.mouse.up();
}

/** Scrolls the page so the whole price pane is in the window (the phone page is taller than the screen). */
async function inView(page: Page, el: Locator) {
  await el.evaluate((e) => e.scrollIntoView({ block: "center" }));
}

for (const width of [1440, 390] as const) {
  test.describe(`chart axes ${width}`, () => {
    test.use(width === 1440 ? { viewport: { width: 1440, height: 900 } }
      : { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    test("a vertical drag on the plot turns Auto off; Auto turns it back on", async ({ page }) => {
      await open(page);
      await inView(page, root(page));
      const { plot } = await plotBox(page);
      await expect(root(page)).toHaveAttribute("data-autoscale", "on");
      await expect(button(page, "Auto")).toHaveAttribute("aria-pressed", "true");
      const c = { x: plot.x + plot.width / 2, y: plot.y + plot.height / 3 };
      await drag(page, c, { x: c.x + 4, y: c.y + 80 });
      await expect(root(page)).toHaveAttribute("data-autoscale", "off");
      await expect(button(page, "Auto")).toHaveAttribute("aria-pressed", "false");
      await button(page, "Auto").click();
      await expect(root(page)).toHaveAttribute("data-autoscale", "on");
    });

    test("a sideways drag pans time: Latest comes, and takes the chart back to the latest bar", async ({ page }) => {
      await open(page);
      await inView(page, root(page));
      const { plot } = await plotBox(page);
      await expect(button(page, "Latest")).toHaveCount(0);
      const y = plot.y + plot.height / 3;
      await drag(page, { x: plot.x + plot.width * 0.2, y }, { x: plot.x + plot.width * 0.9, y });
      await expect(root(page)).toHaveAttribute("data-latest-visible", "no");
      await expect(root(page)).toHaveAttribute("data-autoscale", "on");           // a sideways pan keeps Auto
      const reset = (await button(page, "Reset").boundingBox())!;
      await button(page, "Latest").click();
      await expect(root(page)).toHaveAttribute("data-latest-visible", "yes");
      await expect(button(page, "Latest")).toHaveCount(0);
      expect(await button(page, "Reset").boundingBox()).toEqual(reset);          // no layout shift when Latest comes / goes
    });

    test("the price axis: a drag stretches it (Auto off), a double-click resets it; a double-click on the time axis brings the latest bar back", async ({ page }) => {
      await open(page);
      await inView(page, root(page));
      const { plot, axis, chart } = await plotBox(page);
      const ax = { x: axis.x + axis.width / 2, y: axis.y + axis.height / 3 };
      await drag(page, ax, { x: ax.x, y: ax.y + 60 });
      await expect(root(page)).toHaveAttribute("data-autoscale", "off");
      await page.mouse.dblclick(ax.x, ax.y);
      await expect(root(page)).toHaveAttribute("data-autoscale", "on");
      const y = plot.y + plot.height / 3;
      await drag(page, { x: plot.x + plot.width * 0.2, y }, { x: plot.x + plot.width * 0.9, y });
      await expect(root(page)).toHaveAttribute("data-latest-visible", "no");
      await page.mouse.dblclick(plot.x + plot.width / 2, chart.y + chart.height - 8);   // the time axis
      await expect(root(page)).toHaveAttribute("data-latest-visible", "yes");
    });

    test("Reset restores Auto and the latest bar", async ({ page }) => {
      await open(page);
      await inView(page, root(page));
      const { plot } = await plotBox(page);
      const c = { x: plot.x + plot.width * 0.3, y: plot.y + plot.height / 3 };
      await drag(page, c, { x: c.x + plot.width * 0.5, y: c.y + 90 });
      await expect(root(page)).toHaveAttribute("data-autoscale", "off");
      await expect(root(page)).toHaveAttribute("data-latest-visible", "no");
      await button(page, "Reset").click();
      await expect(root(page)).toHaveAttribute("data-autoscale", "on");
      await expect(root(page)).toHaveAttribute("data-latest-visible", "yes");
    });

    test("the controls: plain tooltips, 44 px targets on the phone, inside the price pane", async ({ page }) => {
      await open(page, "dark");
      await expect(button(page, "Auto")).toHaveAttribute("title", "Fit prices to the screen");
      await expect(button(page, "Reset")).toHaveAttribute("title", /^Back to the start view/);
      const { plot } = await plotBox(page);
      const group = (await controls(page).boundingBox())!;
      expect(group.x + group.width).toBeLessThanOrEqual(plot.x + plot.width);    // left of the price axis
      expect(group.y + group.height).toBeLessThanOrEqual(plot.y + plot.height);  // above the lower panes
      for (const name of ["Auto", "Reset"]) {
        const b = (await button(page, name).boundingBox())!;
        expect(b.height).toBeGreaterThanOrEqual(width === 390 ? 44 : 36);
      }
      if (width === 390) await expectPhoneLayout(page);
    });
  });
}

test.describe("chart axes 1440: wheel and crosshair", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Shift + wheel pans time; Auto stays on", async ({ page }) => {
    await open(page);
    const { plot } = await plotBox(page);
    await page.mouse.move(plot.x + plot.width / 2, plot.y + plot.height / 3);
    await page.keyboard.down("Shift");
    for (let i = 0; i < 4; i += 1) await page.mouse.wheel(0, -400);
    await page.keyboard.up("Shift");
    await expect(root(page)).toHaveAttribute("data-latest-visible", "no");
    await expect(root(page)).toHaveAttribute("data-autoscale", "on");
  });

  test("the readout follows the crosshair and goes when the pointer leaves", async ({ page }) => {
    await open(page);
    const { plot } = await plotBox(page);
    await expect(root(page)).toHaveAttribute("data-crosshair", "off");
    await page.mouse.move(plot.x + plot.width / 2, plot.y + plot.height / 2);
    await expect(root(page)).toHaveAttribute("data-crosshair", "on");
    const readout = page.getByRole("definition").first();
    await expect(readout).toBeVisible();
    await expect(page.locator("[data-chart-readout]")).toContainText(/Open.*High.*Low.*Close/);
    await page.mouse.move(5, 5);
    await expect(root(page)).toHaveAttribute("data-crosshair", "off");
  });
});
