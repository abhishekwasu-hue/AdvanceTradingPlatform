import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";

/**
 * M76 MP-0 (docs/design/M76_REVIEW.md section 8, "Metrics"): the BASELINE of today's tickets, before any check card
 * exists. Four ticket flows on the OC1 hand-made chain (e2e/fixtures/option_chain_v2.json), every /api answer made up
 * here (round numbers, not market data):
 *   A. the chain ticket, nothing to warn about;           B. the chain ticket with today's warning texts;
 *   C. the chain ticket refused (5 lots), Back, 4 lots, Place;   D. the basket (a bull put spread from the chain).
 * At the moment Place is pressed the page counts the warning surfaces the user sees (the same selectors the review
 * names: `[data-ticket=warnings] li`, `[data-ticket=refusals] li`, `[data-risk-part=warn]`, the "cannot place" lines,
 * dialogs) and the time from opening the ticket to Place (mocked API, so this is the UI's own cost, not a person's).
 * The ticket's own report (POST /api/ui/ticket-metrics) must carry the same counts - the client and this spec measure
 * the same thing. No screenshot baseline here: `TICKET_METRICS_OUT` (a JSON file) and `TICKET_SHOTS_DIR` (the review
 * step, 390 + 1440, light + dark) are written only when set, for the milestone's proof page.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };
const LOT = 75;
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
// Flow B: the texts today's ticket shows as warnings (fno_ticket.assess) and an event before the expiry.
const WARNINGS = ["This contract expires today.", "No live price just now, so the price shown is from the chain."];
const EVENTS = { underlying: "NIFTY", from: "2026-10-27", until: "2026-11-03", clipped: false, more: 0, calendar_empty: false, message: null,
  source: "calendar", blocks: false, events: [{ date: "2026-10-29", kind: "policy", name: "Made-up policy meeting", scope: "market", rule: null, source: "market" }] };

type Flow = "A" | "B" | "C" | "D";
type Leg = { underlying: string; expiry: string; strike: number; right: "CE" | "PE"; side: "BUY" | "SELL"; lots: number; price: number | null; stop_loss: number | null };
const label = (l: Leg) => `${l.underlying} ${l.expiry.slice(8, 10)} ${MONTHS[Number(l.expiry.slice(5, 7)) - 1]} ${l.strike} ${l.right}`;

function preview(body: Record<string, unknown>, flow: Flow) {
  const lots = Number(body.lots ?? 1);
  const units = lots * LOT;
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: LOT, units, order_type: body.order_type, product: body.product, price: 150, price_source: "live", fill_price: 150.5,
    fill_note: "Fills at the live price 150.50", stop_loss: body.stop_loss ?? null,
    margin: { value: 150.5 * units, basis: "Long options only: the premium paid.", estimated: true },
    max_loss: { value: 150.5 * units, basis: "The premium you pay, if the option expires worthless." }, charges: { total: 42.5, estimated: true },
    risk: { risk_per_trade: 50000, allowed_lots: 4 },
    errors: [], refusals: lots > 4 ? ["Your risk per trade (₹50,000) allows 4 lot(s) at this stop-loss. Lower the lots or set a closer stop-loss."] : [],
    warnings: flow === "B" ? WARNINGS : [], can_place: lots <= 4, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

function basketPreview(legs: Leg[]) {
  const ordered = legs.map((l, i) => ({ l, i })).sort((a, b) => (a.l.side === b.l.side ? a.i - b.i : a.l.side === "BUY" ? -1 : 1));
  return {
    mode: "PAPER", practice: "Practice mode (PAPER)", product: "MIS", as_of: "2026-10-27T05:00:00+00:00", refusals: [], totals_errors: [],
    legs: ordered.map(({ l, i }, k) => ({
      seq: k + 1, leg: i, role: l.side === "BUY" ? "hedge" : "short", mode: "PAPER", practice: "", contract: null, label: label(l), side: l.side, lots: l.lots,
      lot_size: LOT, units: l.lots * LOT, order_type: "MARKET", product: "MIS", price: l.price, price_source: "live", fill_price: l.price, fill_note: null,
      stop_loss: l.stop_loss, margin: null, max_loss: null, charges: null, risk: {}, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "" })),
    totals: { margin: { value: 11400, estimated: true, basis: "Defined risk: at least the max loss.", note: "",
      hedge: { with_hedge: { value: 11400, estimated: true, basis: "Defined risk: at least the max loss." }, without_hedge: null, benefit: null, hedged: true,
        source: "estimate", note: "ATP's estimate." } },
    extremes: { max_profit: 3600, max_loss: -11400, unbounded_profit: false, unbounded_loss: false }, net_premium: 3600, costs: 57, breakevens: [24452] },
    can_execute: true, confirm_label: `Place ${legs.length} PAPER orders, hedge legs first`,
  };
}

function evaluation(legs: Leg[]) {
  const prices = Array.from({ length: 41 }, (_, i) => 24000 + i * 25);
  const pay = prices.map((s) => legs.reduce((sum, l) => {
    const intrinsic = l.right === "CE" ? Math.max(s - l.strike, 0) : Math.max(l.strike - s, 0);
    return sum + (l.side === "BUY" ? intrinsic - (l.price ?? 0) : (l.price ?? 0) - intrinsic) * l.lots * LOT;
  }, 0));
  const max = Math.max(...pay);
  const min = Math.min(...pay);
  return {
    ok: true, errors: [], prices, at_expiry: pay, today: pay, on_date: pay, days_forward: 0, iv_shift: 0, single_expiry: true,
    extremes: { max_profit: max, max_loss: min, unbounded_profit: false, unbounded_loss: false }, profitable: [], breakevens: [24452],
    summary: { expiry: legs[0]?.expiry ?? "2026-11-03", t_years: 0.02, sigma: 0.12, pop: 0.58, expected_move: 410, expected_pnl: 120,
      greeks: { delta: 18.4, gamma: -0.003, theta: 620, vega: -940, rho: 2 }, method: "", model: "black-scholes" },
    legs: [], disclaimer: "Model estimates.", net_premium: 0, reward_to_risk: null,
    margin: { value: -min, estimated: true, basis: "Defined risk: at least the max loss.", note: "",
      hedge: { with_hedge: { value: -min, estimated: true, basis: "Defined risk: at least the max loss." }, without_hedge: null, benefit: null, hedged: true, source: "estimate", note: "" } },
    costs: { to_open: { stt: 5, exch: 4, sebi: 0, stamp: 0, brokerage: 40, tax: 8, total: 57, other: 12 }, round_trip: 114, brokerage_per_order: 20, orders: legs.length, as_of: "2026-10-27", note: "" },
    lot_sizes: legs.map(() => LOT), labels: legs.map(label), order: legs.map((_, i) => i),
  };
}

/** What the page counts when Place is pressed (installed before the app runs; the click is seen in the capture phase,
 * before the app's own handler). Kept in step with `src/fno/ticketMetrics.ts`. */
function installProbe(): void {
  type Probe = { open: number | null; review: number | null; place: number | null; at: Record<string, number> | null };
  const w = window as unknown as { __tm: Probe };
  w.__tm = { open: null, review: null, place: null, at: null };
  const count = (root: Element) => {
    const n = (sel: string) => root.querySelectorAll(sel).length;
    const warn = n("[data-risk-part='warn']");
    const refusals = n("[data-ticket='refusals'] li") + n("[data-basket='confirm-refusals'] li");
    const warnings = n("[data-ticket='warnings'] li");
    const legLists = [...root.querySelectorAll("[data-basket='confirm-legs'] [data-confirm-leg] ul")];
    const legLines = legLists.reduce((s, ul) => s + ul.querySelectorAll("li").length, 0);
    const blocked = n("[data-ticket='blocked-by']") + n("[data-basket='blocked-by']") + n("[data-testid='ticket-risk-block']");
    const dialogs = document.querySelectorAll("[role='alertdialog']").length;
    const cards = (warn ? 1 : 0) + (refusals ? 1 : 0) + (warnings ? 1 : 0) + legLists.length + blocked + dialogs;
    const lines = warn + refusals + warnings + legLines + blocked + dialogs;
    return { cards, lines };
  };
  document.addEventListener("click", (e) => {
    const t = e.target as Element | null;
    if (!t?.closest) return;
    if (t.closest("[data-action='buy'], [data-strip='basket']") && w.__tm.open === null) w.__tm.open = performance.now();
    if (t.closest("[data-ticket='review'], [data-basket='execute']")) w.__tm.review = performance.now();
    const place = t.closest("[data-ticket='place'], [data-basket='place']");
    if (place) {
      w.__tm.place = performance.now();
      const root = place.closest("[data-order-ticket], [data-basket-panel]") ?? document.body;
      w.__tm.at = count(root);
    }
  }, true);
}

interface Seen { metrics: Record<string, unknown>[] }

async function open(page: Page, theme: "light" | "dark", flow: Flow): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["option-chain"]));
      sessionStorage.removeItem("atp_fno_basket");
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.addInitScript(installProbe);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, ON);
  const seen: Seen = { metrics: [] };
  const sessions = { NSE: { is_open: true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/ui\/ticket-metrics$/, (r) => { seen.metrics.push(r.request().postDataJSON() as Record<string, unknown>); return r.fulfill({ status: 204, body: "" }); });
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
    if (path.endsWith("/held")) return route.fulfill({ json: [] });
    if (path.endsWith("/risk-budget")) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    if (path.endsWith("/event-risk")) return flow === "B" ? route.fulfill({ json: EVENTS }) : route.fulfill({ status: 404, json: { detail: "Not found" } });
    if (path.endsWith("/basket/settings")) {
      return route.fulfill({ json: { source: null, width_steps: 2, lots: 1, stop_pct: 50, note: "You have no options settings yet. Edit any leg." } });
    }
    const body = request.postDataJSON() as Record<string, unknown> & { legs?: Leg[] };
    if (path.endsWith("/basket/evaluate")) return route.fulfill({ json: evaluation(body.legs ?? []) });
    if (path.endsWith("/basket/preview")) return route.fulfill({ json: basketPreview(body.legs ?? []) });
    if (path.endsWith("/basket/execute")) {
      const legs = body.legs ?? [];
      return route.fulfill({ json: { mode: "PAPER", status: "PLACED", reasons: [], placed: legs.length, next_steps: [],
        legs: legs.map((l, k) => ({ seq: k + 1, leg: k, label: label(l), side: l.side, lots: l.lots, status: "PLACED", reasons: [],
          filled: { price: l.price, quantity: l.lots * LOT, lots: l.lots }, order_id: 10 + k, trade_id: 20 + k })) } });
    }
    if (path.endsWith("/order/preview")) return route.fulfill({ json: preview(body, flow) });
    if (path.endsWith("/order")) {
      return route.fulfill({ json: { ...preview(body, flow), status: "PLACED", reasons: [], order_id: 9, trade_id: 41, filled: { price: 150.5, quantity: LOT * Number(body.lots), lots: Number(body.lots) } } });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  return seen;
}

async function ticketToReview(page: Page, lots = 1): Promise<Locator> {
  await page.locator("tr[data-strike='24500'] td:visible").first().click();
  await page.locator("[data-chain-actions] [data-action='buy']").click();
  const ticket = page.locator("[data-order-ticket]");
  await expect(ticket).toBeVisible();
  for (let i = 1; i < lots; i += 1) await ticket.locator("[data-ticket='lots-more']").click();
  if (await ticket.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) {
    await ticket.locator("[data-ticket='order-type'] [data-option='MARKET']").click();
  }
  await ticket.locator("[data-ticket='product'] [data-option='NRML']").click();
  await ticket.locator("[data-ticket='review']").click();
  await expect(ticket.locator("[data-ticket='confirm']")).toBeVisible();
  return ticket;
}

interface Measured { flow: Flow; cards: number; lines: number; ms_open_to_place: number; ms_review_to_place: number; reported: Record<string, unknown> | null }

async function placeAndMeasure(page: Page, flow: Flow, place: Locator, done: Locator, seen: Seen): Promise<Measured> {
  await place.click();
  await expect(done).toBeVisible();
  const tm = await page.evaluate(() => (window as unknown as { __tm: { open: number; review: number; place: number; at: { cards: number; lines: number } } }).__tm);
  // The report is fire and forget: it may arrive after the result is on screen.
  if (process.env.TICKET_METRICS_BEFORE !== "1") await expect.poll(() => seen.metrics.some((m) => m.outcome === "placed"), { timeout: 5_000 }).toBe(true);
  return { flow, cards: tm.at.cards, lines: tm.at.lines, ms_open_to_place: Math.round(tm.place - tm.open), ms_review_to_place: Math.round(tm.place - tm.review),
    reported: seen.metrics.find((m) => m.outcome === "placed") ?? null };
}

const RESULTS: Measured[] = [];
const EXPECTED: Record<Flow, { cards: number; lines: number }> = {
  A: { cards: 0, lines: 0 },   // nothing to warn about: no warning surface
  B: { cards: 2, lines: 3 },   // "Before you place" (the event) + the warnings list (two texts)
  C: { cards: 0, lines: 0 },   // at Place (4 lots) nothing is left; the refusal was on the earlier Review
  D: { cards: 0, lines: 0 },   // the basket with every leg clear
};

function check(m: Measured): void {
  expect({ cards: m.cards, lines: m.lines }).toEqual(EXPECTED[m.flow]);
  // The ticket's own report counts the same surfaces (MP-0 client metrics; absent before the client existed).
  if (process.env.TICKET_METRICS_BEFORE !== "1") {
    expect(m.reported).toMatchObject({ cards_shown: m.cards, lines_total: m.lines, lines_visible: m.lines, outcome: "placed", pending_at_place: 0 });
    expect(typeof m.reported?.ms_open_to_place).toBe("number");
  }
  RESULTS.push(m);
}

test.describe("M76 MP-0: ticket warning surfaces and time to Place (baseline)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test.describe.configure({ mode: "serial" });

  test("A - the chain ticket with nothing to warn about", async ({ page }) => {
    const seen = await open(page, "light", "A");
    const ticket = await ticketToReview(page);
    check(await placeAndMeasure(page, "A", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("B - the chain ticket with today's warning texts", async ({ page }) => {
    const seen = await open(page, "light", "B");
    const ticket = await ticketToReview(page);
    await expect(ticket.locator("[data-ticket='warnings'] li")).toHaveCount(2);
    await expect(ticket.locator("[data-risk-part='warn']")).toHaveCount(1);
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();      // a warning never disables Place
    check(await placeAndMeasure(page, "B", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("C - refused at 5 lots, Back, 4 lots, Place", async ({ page }) => {
    const seen = await open(page, "light", "C");
    const ticket = await ticketToReview(page, 5);
    await expect(ticket.locator("[data-ticket='refusals'] li")).toHaveCount(1);
    await expect(ticket.locator("[data-ticket='place']")).toBeDisabled();     // a refusal (his own limit) is a block, as today
    await ticket.locator("[data-ticket='back']").click();
    await ticket.locator("[data-ticket='lots-less']").click();
    await ticket.locator("[data-ticket='review']").click();
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    check(await placeAndMeasure(page, "C", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("D - the basket (bull put spread)", async ({ page }) => {
    const seen = await open(page, "light", "D");
    await page.locator("[data-strip='basket']").click();
    const panel = page.locator("[data-basket-panel]");
    await expect(panel).toBeVisible();
    await panel.locator("[data-strategy='Bull put spread']").click();
    await expect(panel.locator("[data-leg]")).toHaveCount(2);
    if (await panel.locator("[data-basket='product-unset']").count()) await panel.locator("[data-basket='product'] [role='radio']").first().click();
    await panel.locator("[data-basket='execute']").click();
    await expect(panel.locator("[data-basket='confirm-legs']")).toBeVisible();
    check(await placeAndMeasure(page, "D", panel.locator("[data-basket='place']"), panel.locator("[data-basket='result']"), seen));
  });

  test.afterAll(() => {
    const out = process.env.TICKET_METRICS_OUT;
    if (!out || !RESULTS.length) return;
    writeFileSync(out, JSON.stringify({ flows: RESULTS, cards_per_ticket: RESULTS.reduce((s, r) => s + r.cards, 0) / RESULTS.length }, null, 2));
  });
});

// The review step with today's warning texts (flow B) at both sizes and themes, for the proof page only.
for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  test.describe(`M76 MP-0 review step at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of ["light", "dark"] as const) {
      test(`the review step with today's warnings (${theme})`, async ({ page }) => {
        const dir = process.env.TICKET_SHOTS_DIR;
        test.skip(!dir, "proof shots only (TICKET_SHOTS_DIR)");
        await open(page, theme, "B");
        const ticket = await ticketToReview(page);
        await expect(ticket.locator("[data-ticket='warnings'] li")).toHaveCount(2);
        mkdirSync(dir as string, { recursive: true });
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `${dir}/review-${width}-${theme}.png`, animations: "disabled" });
      });
    }
  });
}
