import { expect, test, type Page } from "@playwright/test";
import { mockChartReading, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * M75 W-6 (owner order M75 CW-4; flag `copilot_workspace`): the trade plan panel under the Copilot's chart, in a browser
 * with every reply made up here (no backend). Proofs: every field opens Not set; a leg is added only once complete; its
 * numbers are the Options Builder's engine reply (one `POST /api/options-builder/evaluate` with the builder's legs); the
 * exits at entry show his own levels; no order route is called; no sideways scroll on a phone.
 * `W6_OUT=<folder>` also writes screenshots (the comparison page), never baselines.
 */
const OUT = process.env.W6_OUT;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

interface WireLeg { direction: "BUY" | "SELL"; option_type: "CE" | "PE" | "FUT"; strike: number; premium: number; lots: number; lot_size: number; expiry: string }

/** A made-up engine reply with the shape the server returns: the expiry payoff of the legs sent. */
function evaluation(legs: WireLeg[], spot: number) {
  const prices = Array.from({ length: 241 }, (_, i) => spot * 0.92 + (i * spot * 0.16) / 240);
  const pnl = (p: number) => legs.reduce((sum, l) => {
    const value = l.option_type === "FUT" ? p : l.option_type === "CE" ? Math.max(p - l.strike, 0) : Math.max(l.strike - p, 0);
    return sum + (l.direction === "BUY" ? value - l.premium : l.premium - value) * l.lots * l.lot_size;
  }, 0);
  const expiry = prices.map(pnl);
  const credit = legs.reduce((s, l) => s + (l.option_type === "FUT" ? 0 : (l.direction === "SELL" ? 1 : -1) * l.premium * l.lots * l.lot_size), 0);
  return {
    prices, today: expiry.map((v) => v * 0.8), on_date: expiry.map((v) => v * 0.8), at_expiry: expiry, days_forward: 0, iv_shift: 0, single_expiry: true,
    extremes: { max_profit: Math.max(...expiry), max_loss: null, unbounded_profit: false, unbounded_loss: true }, profitable: null,
    breakevens: [legs[0].strike + legs[0].premium],
    summary: { expiry: legs[0].expiry, t_years: 0.06, sigma: 0.13, pop: 0.68, expected_move: 410, expected_pnl: 900,
      greeks: { delta: -42.1, gamma: -0.004, theta: 610, vega: -1520, rho: -3 }, method: "black-scholes on the typed premiums", model: "bs" },
    legs: legs.map((l) => ({ ...l, iv: 0.13, iv_source: "solved", greeks: { delta: -0.28, gamma: 0, theta: 4, vega: -10, rho: 0 }, theoretical: l.premium })),
    disclaimer: "Model estimates, information only.", net_premium: credit, reward_to_risk: null,
    margin: { value: null, estimated: true, basis: "Undefined risk: only the broker can say.", note: "ATP's estimate, never the broker's number." },
    costs: { to_open: { stt: 6, exch: 7, sebi: 0, stamp: 0, brokerage: 0, tax: 1, total: 14, other: 8 }, round_trip: 30, brokerage_per_order: 0, orders: legs.length, as_of: "", note: "Statutory charges only." },
  };
}

interface Seen { evaluate: WireLeg[][]; orders: string[] }

async function open(page: Page, theme: "light" | "dark"): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  await mockChartReading(page);
  const seen: Seen = { evaluate: [], orders: [] };
  await page.route(/\/api(?:\/v1)?\/options-builder\/evaluate$/, async (route) => {
    const body = route.request().postDataJSON() as { legs: WireLeg[]; spot: number };
    seen.evaluate.push(body.legs);
    return route.fulfill({ json: evaluation(body.legs, body.spot) });
  });
  await page.route(/\/api(?:\/v1)?\/paper\//, async (route) => {
    if (route.request().method() !== "GET") seen.orders.push(route.request().url());
    return route.fallback();
  });
  await page.goto("/copilot/read?symbol=NIFTY%2050");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return seen;
}

/** Picks a choice in the shared Combobox (a list on desktop, a sheet on a phone). */
async function pick(page: Page, field: string, value: string): Promise<void> {
  const words: Record<string, string> = { SELL: "Sell", BUY: "Buy", CE: "Call (CE)", PE: "Put (PE)", POINTS: "Points from entry", PCT: "% from entry" };
  await page.locator(`[data-plan-panel] [data-plan='${field}']`).click();
  await page.getByRole("option", { name: words[value], exact: true }).click();
}

async function noSidewaysScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(over).toBeLessThanOrEqual(1);
}

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Copilot trade plan ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`every field Not set; a complete leg shows the builder's numbers; nothing is placed (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        const panel = page.locator("[data-plan-panel]");
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await expect(panel).toHaveAttribute("data-plan-panel", "none");
        await expect(panel.locator("[data-plan='ask-kind']")).toBeVisible();
        if (OUT) { await panel.scrollIntoViewIfNeeded(); await page.screenshot({ path: `${OUT}/plan-empty-${width}-${theme}.png`, animations: "disabled" }); }

        await panel.locator("[data-option='OPTIONS']").click();
        await expect(panel.locator("[data-plan='leg-side']")).toHaveText(/Not set/);        // Not set
        await expect(panel.locator("[data-plan='leg-type']")).toHaveText(/Not set/);
        await expect(panel.locator("[data-plan='leg-lots']")).toHaveValue("");
        await expect(panel.locator("[data-plan='add-leg']")).toBeDisabled();
        await expect(panel.locator("[data-plan='leg-missing']")).toContainText("Lot size, Side, Type, Strike, Expiry, Lots, Premium");

        await panel.locator("[data-plan='spot']").fill("24500");
        await panel.locator("[data-plan='lot-size']").fill("75");
        await panel.locator("[data-plan='step']").fill("50");
        await pick(page, "leg-side", "SELL");
        await pick(page, "leg-type", "CE");
        await panel.locator("[data-plan='leg-strike']").fill("24700");
        await panel.locator("[data-plan='leg-expiry']").fill("2026-11-25");
        await panel.locator("[data-plan='leg-lots']").fill("2");
        await panel.locator("[data-plan='leg-premium']").fill("85");
        await panel.locator("[data-plan='add-leg']").click();
        await expect(panel.locator("[data-testid='metrics']")).toBeVisible({ timeout: 10_000 });
        expect(seen.evaluate.at(-1)).toEqual([{ direction: "SELL", option_type: "CE", strike: 24700, premium: 85, lots: 2, lot_size: 75, expiry: "2026-11-25", iv: null }]);
        await expect(panel.locator("[data-testid='metric-margin']")).toContainText("Margin (estimate)");

        await pick(page, "stop-kind", "POINTS");
        await panel.locator("[data-plan='stop']").fill("40");
        await pick(page, "target-kind", "PCT");
        await panel.locator("[data-plan='target']").fill("50");
        await expect(panel.locator("[data-exit='stop']")).toContainText("at 125");
        await expect(panel.locator("[data-exit='target']")).toContainText("at 42.5");
        if (width < 768) await noSidewaysScroll(page);
        if (OUT) { await panel.scrollIntoViewIfNeeded(); await panel.screenshot({ path: `${OUT}/plan-leg-${width}-${theme}.png`, animations: "disabled" }); }
        expect(seen.orders).toEqual([]);                                                    // preview only: no order route
      });
    });
  }
}
