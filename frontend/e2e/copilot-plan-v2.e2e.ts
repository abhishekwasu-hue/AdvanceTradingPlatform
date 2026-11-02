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

const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N2524700CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-25",
  strike: 24700, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 25 NOV 24700 CE", freeze_quantity: null };

/** The ticket's confirm step as the server answers it (made up). */
function preview(body: Record<string, unknown>) {
  const lots = Number(body.lots ?? 1);
  const units = lots * 75;
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: 75, units, order_type: body.order_type, product: body.product, price: 85, price_source: "live", fill_price: 84.5,
    fill_note: "Fills at the live price 84.50", stop_loss: body.stop_loss ?? null,
    margin: { value: 120000, basis: "A sold option: ATP's estimate.", estimated: true },
    max_loss: { value: 6000, basis: "At your stop-loss." }, charges: { total: 42.5, estimated: true },
    risk: { risk_per_trade: 50000, allowed_lots: 4 }, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-19T05:30:00+00:00",
  };
}

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

interface Seen { evaluate: WireLeg[][]; orders: string[]; bodies: Record<string, unknown>[] }

async function open(page: Page, theme: "light" | "dark"): Promise<Seen> {
  return openWith(page, theme, {});
}

async function openWith(page: Page, theme: "light" | "dark", extra: Record<string, boolean>): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true, paper_account: true, ...extra }, brokers: CONNECTED });
  await mockCharts(page, {});
  await mockChartReading(page);
  const seen: Seen = { evaluate: [], orders: [], bodies: [] };
  await page.route(/\/api(?:\/v1)?\/options-builder\/evaluate$/, async (route) => {
    const body = route.request().postDataJSON() as { legs: WireLeg[]; spot: number };
    seen.evaluate.push(body.legs);
    return route.fulfill({ json: evaluation(body.legs, body.spot) });
  });
  // W-7: the ONE ticket's routes, made up (contract read, Review = preview, Place = order) and the resting exit.
  await page.route(/\/api(?:\/v1)?\/paper\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/paper/fno/contract")) {
      return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right"), expiry: url.searchParams.get("expiry") } });
    }
    if (/risk-budget|event-risk/.test(url.pathname)) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    if (request.method() === "GET") return route.fallback();
    const body = request.postDataJSON() as Record<string, unknown>;
    seen.orders.push(url.pathname);
    seen.bodies.push(body);
    if (url.pathname.endsWith("/order/preview")) return route.fulfill({ json: preview(body) });
    if (url.pathname.endsWith("/paper/fno/order")) {
      return route.fulfill({ json: { ...preview(body), status: "PLACED", reasons: [], order_id: 9, trade_id: 41, filled: { price: 84.5, quantity: 150, lots: 2 } } });
    }
    if (url.pathname.endsWith("/resting/exit")) return route.fulfill({ json: { id: 5, kind: "TARGET", status: "RESTING", ...body } });
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
        expect(seen.orders).toEqual([]);                                                    // nothing is sent before his own taps

        // W-7: "Review and place" opens the ONE ticket with his plan's numbers; Review and Sell are his taps.
        await panel.locator("[data-plan='review-place']").click();
        const ticket = page.getByRole("dialog");
        await expect(ticket).toContainText("from your trade plan", { timeout: 10_000 });
        await expect(ticket.locator("[data-ticket='lots']")).toHaveValue("2");
        expect(seen.orders).toEqual([]);                                                    // opening the ticket sends nothing
        if (await ticket.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) await ticket.locator("[data-ticket='order-type'] [data-option='LIMIT']").click();
        await ticket.locator("[data-ticket='product'] [data-option='NRML']").click();
        if (OUT) await page.screenshot({ path: `${OUT}/plan-ticket-${width}-${theme}.png`, animations: "disabled" });
        await ticket.locator("[data-ticket='review']").click();
        await ticket.locator("[data-ticket='place']").click();
        await expect.poll(() => seen.orders.length).toBe(2);
        expect(seen.orders[0]).toMatch(/\/paper\/fno\/order\/preview$/);                  // Review = the confirm step first
        expect(seen.orders[1]).toMatch(/\/paper\/fno\/order$/);                           // then the ONE order route
        const placedBody = seen.bodies[1];
        expect(placedBody).toMatchObject({ side: "SELL", lots: 2, right: "CE", strike: 24700, stop_loss: 125 });
        expect(Object.keys(placedBody)).not.toContain("mode");                             // PAPER = LIVE: no mode in the body
        await page.keyboard.press("Escape");
        // the status, the fill and the position in the same panel; his target armed only on his tap, from the fill
        const placed = panel.locator("[data-plan='placed']");
        await expect(placed).toHaveAttribute("data-status", "PLACED");
        await expect(placed).toContainText("2 lots at 84.50");
        await expect(panel.locator("[data-plan='arm-target']")).toContainText("42.25");
        expect(seen.orders.filter((o) => o.endsWith("/resting/exit"))).toEqual([]);
        await panel.locator("[data-plan='arm-target'] [data-option='LIMIT']").click();
        await panel.locator("[data-plan='arm']").click();
        await expect(panel.locator("[data-plan='armed']")).toContainText("Target armed at 42.25");
        expect(seen.bodies.at(-1)).toEqual({ trade_id: 41, kind: "TARGET", order_type: "LIMIT", level: 42.25, client_ref: "plan-41-target" });
        expect(new URL(page.url()).pathname).toBe("/copilot/read");                        // never left the tab
        if (OUT) await panel.screenshot({ path: `${OUT}/plan-placed-${width}-${theme}.png`, animations: "disabled" });
      });
    });
  }
}

// M75 W-8 (CW-6, flag `trigger_plans`): Save as trigger plan from the panel and his plans list, in the same tab. Every
// field Not set; arming sends only his values to /api/trigger-plans (no order route); Pause is his tap.
for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Copilot trigger plans ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`arm a plan from the panel, then pause it (${width} ${theme})`, async ({ page }) => {
        const plans: Record<string, unknown>[] = [];
        const sent: Record<string, unknown>[] = [];
        const seen = await openWith(page, theme, { trigger_plans: true });
        await page.route(/\/api(?:\/v1)?\/trigger-plans(\/.*)?$/, async (route) => {
          const req = route.request();
          const url = new URL(req.url());
          if (req.method() === "GET") return route.fulfill({ json: plans });
          if (url.pathname.endsWith("/pause")) {
            plans[0] = { ...plans[0], status: "PAUSED", status_words: "Paused" };
            return route.fulfill({ json: plans[0] });
          }
          const body = req.postDataJSON() as Record<string, unknown>;
          sent.push(body);
          const row = { id: 7, name: null, underlying: "NIFTY", status: "ARMED", status_words: "Armed", condition: body.condition, orders: body.orders,
            approval: body.approval ?? "AUTO", valid_until: body.valid_until, note: null, last_price: null };
          plans.unshift(row);
          return route.fulfill({ json: row });
        });
        const panel = page.locator("[data-plan-panel]");
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await panel.locator("[data-option='OPTIONS']").click();
        await panel.locator("[data-plan='spot']").fill("24500");
        await panel.locator("[data-plan='lot-size']").fill("75");
        await pick(page, "leg-side", "BUY");
        await pick(page, "leg-type", "CE");
        await panel.locator("[data-plan='leg-strike']").fill("24700");
        await panel.locator("[data-plan='leg-expiry']").fill("2026-11-25");
        await panel.locator("[data-plan='leg-lots']").fill("2");
        await panel.locator("[data-plan='leg-premium']").fill("80");
        await panel.locator("[data-plan='add-leg']").click();
        const form = panel.locator("[data-trigger='form']");
        await expect(form.locator("[data-trigger='save']")).toBeDisabled();
        await expect(form.locator("[data-trigger='side']")).toHaveText(/Not set/);
        await expect(form.locator("[data-trigger='approval']")).toHaveText(/Not set/);
        await form.locator("[data-trigger='level']").fill("24650");
        await pickTrigger(page, "side", "Above it");
        await pickTrigger(page, "mode", "A touch");
        await form.locator("[data-trigger='valid-until']").fill("2026-10-21T15:00");
        await pickTrigger(page, "order-type", "Market");
        await pickTrigger(page, "product", "Carry forward (NRML)");
        await pickTrigger(page, "approval", "Ask me first");
        await form.locator("[data-trigger='save']").click();
        await expect(panel.locator("[data-plan-row='ARMED']")).toContainText("above 24,650");
        expect(sent).toHaveLength(1);
        expect(sent[0]).toMatchObject({ underlying: "NIFTY 50", approval: "APPROVAL", condition: { level: 24650, side: "ABOVE", mode: "TOUCH" },
          orders: [{ strike: 24700, right: "CE", side: "BUY", lots: 2, order_type: "MARKET", product: "NRML", price: null }] });
        if (OUT) await panel.locator("[data-trigger-plans]").screenshot({ path: `${OUT}/plan-trigger-${width}-${theme}.png`, animations: "disabled" });
        await panel.locator("[data-trigger-act='pause']").click();
        await expect(panel.locator("[data-plan-row='PAUSED']")).toBeVisible();
        expect(seen.orders).toEqual([]);                                                   // arming a plan sends no order
        expect(new URL(page.url()).pathname).toBe("/copilot/read");
        if (width < 768) await noSidewaysScroll(page);
      });
    });
  }
}

async function pickTrigger(page: Page, field: string, label: string): Promise<void> {
  await page.locator(`[data-trigger-plans] [data-trigger='${field}']`).click();
  await page.getByRole("option", { name: label, exact: true }).click();
}
