import { expect, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";

/**
 * M76 (MP-0 baseline, MP-2 check card): the shared ticket flows of e2e/ticket-metrics-baseline-v2.e2e.ts and
 * e2e/check-card-v2.e2e.ts - the OC1 hand-made chain, every /api answer made up here (round numbers, not market data),
 * and a probe that counts at Place what the user sees: the warning surfaces ("cards"), their lines and the lines in view.
 */
export const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
export const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };
const LOT = 75;
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
// Flow B: the texts today's ticket shows as warnings (fno_ticket.assess) and an event before the expiry.
export const WARNINGS = ["This contract expires today.", "No live price just now, so the price shown is from the chain."];
export const EVENTS = { underlying: "NIFTY", from: "2026-10-27", until: "2026-11-03", clipped: false, more: 0, calendar_empty: false, message: null,
  source: "calendar", blocks: false, events: [{ date: "2026-10-29", kind: "policy", name: "Made-up policy meeting", scope: "market", rule: null, source: "market" }] };

export type Flow = "A" | "B" | "C" | "D";
type Leg = { underlying: string; expiry: string; strike: number; right: "CE" | "PE"; side: "BUY" | "SELL"; lots: number; price: number | null; stop_loss: number | null };
const label = (l: Leg) => `${l.underlying} ${l.expiry.slice(8, 10)} ${MONTHS[Number(l.expiry.slice(5, 7)) - 1]} ${l.strike} ${l.right}`;

export function preview(body: Record<string, unknown>, flow: Flow) {
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
    warnings: flow === "B" ? WARNINGS : [], warning_ids: flow === "B" ? ["expiry_day", "price_source"] : [], can_place: lots <= 4, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

export function basketPreview(legs: Leg[]) {
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

export function evaluation(legs: Leg[]) {
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
export function installProbe(): void {
  type Probe = { open: number | null; review: number | null; place: number | null; at: Record<string, number> | null; places: number };
  const w = window as unknown as { __tm: Probe };
  w.__tm = { open: null, review: null, place: null, at: null, places: 0 };
  const count = (root: Element) => {
    const n = (sel: string) => root.querySelectorAll(sel).length;
    const warn = n("[data-risk-part='warn']");
    const refusals = n("[data-ticket='refusals'] li") + n("[data-basket='confirm-refusals'] li");
    const warnings = n("[data-ticket='warnings'] li");
    const legLists = [...root.querySelectorAll("[data-basket='confirm-legs'] [data-confirm-leg] ul")];
    const legLines = legLists.reduce((s, ul) => s + ul.querySelectorAll("li").length, 0);
    const blocked = n("[data-ticket='blocked-by']") + n("[data-basket='blocked-by']") + n("[data-testid='ticket-risk-block']");
    const dialogs = document.querySelectorAll("[role='alertdialog']").length;
    // M76 MP-2: the ONE check card - its lines (data-lines) and the ones in view (folded lines are not rendered).
    const cardEl = root.querySelector("[data-check-card]");
    const cardTotal = Number(cardEl?.getAttribute("data-lines") ?? 0);
    const cardShown = n("[data-check-line]");
    const cards = (warn ? 1 : 0) + (refusals ? 1 : 0) + (warnings ? 1 : 0) + legLists.length + blocked + dialogs + (cardTotal ? 1 : 0);
    const lines = warn + refusals + warnings + legLines + blocked + dialogs + cardTotal;
    return { cards, lines, visible: lines - cardTotal + cardShown, pending: cardEl?.getAttribute("data-pending") ? 1 : 0 };
  };
  document.addEventListener("click", (e) => {
    const t = e.target as Element | null;
    if (!t?.closest) return;
    if (t.closest("[data-action='buy'], [data-strip='basket']") && w.__tm.open === null) w.__tm.open = performance.now();
    if (t.closest("[data-ticket='review'], [data-basket='execute']")) w.__tm.review = performance.now();
    const place = t.closest("[data-ticket='place'], [data-basket='place']");
    if (place) {
      w.__tm.places += 1;
      w.__tm.place = performance.now();
      const root = place.closest("[data-order-ticket], [data-basket-panel]") ?? document.body;
      w.__tm.at = count(root);
    }
  }, true);
}

export interface Seen {
  metrics: Record<string, unknown>[];
  /** M76 MP-2: the check calls, the "accepted today" calls, Trade settings saves and the orders sent. */
  checks: Record<string, unknown>[]; accepts: Record<string, unknown>[]; saves: Record<string, unknown>[]; orders: Record<string, unknown>[];
}

/** M76 MP-2: the check card on (flag trade_warnings) and what the made-up check service answers. */
export interface CardOptions {
  findings?: { id: string; severity: "mistake" | "fact"; text: string }[];
  /** The first check call answers after this long (a slow check service); Place must not wait for it. */
  delayMs?: number;
  /** His saved warning switches, as Trade settings fields ({key, value}). */
  saved?: Record<string, boolean>;
}

const WARNING_IDS = ["duplicate_order", "limit_far_from_ltp", "margin_short", "market_state", "expiry_day", "mis_cutoff", "fo_ban", "event",
  "price_source", "liquidity_limits", "own_limits", "counter_trend", "trend_conflict", "area_conflict", "strike_in_area", "room"];

function settingsView(saved: Record<string, boolean>) {
  const field = (key: string, start: boolean) => ({ key, label: key, kind: "switch", help: "", unit: "", choices: [], low: null, high: null, low_open: false,
    term: null, fact: false, wired: true, scope: "user", value: key in saved ? saved[key] : null, saved: key in saved, default: start, default_by: "owner" });
  return { admin: false, page: "/settings/trade-settings", sections: [{ id: "warnings", title: "Warnings", today: null,
    fields: WARNING_IDS.flatMap((id) => [field(`warnings.${id}`, true), field(`warnings.${id}_ask`, false)]) }] };
}

export async function open(page: Page, theme: "light" | "dark", flow: Flow, card?: CardOptions): Promise<Seen> {
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
  await mockApi(page, card ? { ...ON, features: { ...ON.features, trade_warnings: true } } : ON);
  const seen: Seen = { metrics: [], checks: [], accepts: [], saves: [], orders: [] };
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
      seen.orders.push(body);
      return route.fulfill({ json: { ...preview(body, flow), status: "PLACED", reasons: [], order_id: 9, trade_id: 41, filled: { price: 150.5, quantity: LOT * Number(body.lots), lots: Number(body.lots) } } });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  // Registered last: a later route wins over the general /paper/fno/ one above.
  if (card) {
    let first = true;
    await page.route(/\/api(?:\/v1)?\/paper\/fno\/order\/checks\/accept$/, (r) => {
      seen.accepts.push(r.request().postDataJSON() as Record<string, unknown>);
      return r.fulfill({ status: 204, body: "" });
    });
    await page.route(/\/api(?:\/v1)?\/paper\/fno\/order\/checks$/, async (r) => {
      const body = r.request().postDataJSON() as Record<string, unknown>;
      seen.checks.push(body);
      const only = body.only as string[] | undefined;
      const delay = first ? card.delayMs ?? 0 : 0;
      first = false;
      if (delay) await new Promise((res) => setTimeout(res, delay));
      const findings = (card.findings ?? []).filter((f) => !only || only.includes(f.id));
      return r.fulfill({ json: { findings, pending: [], accepted: [], ms: 12, instrument: "NIFTY|2026-11-03|24500|CE", budget_ms: 300, switches: {} } });
    });
    let saved = { ...(card.saved ?? {}) };
    await page.route(/\/api(?:\/v1)?\/me\/trade-settings$/, (r) => {
      if (r.request().method() === "PUT") {
        const values = (r.request().postDataJSON() as { values: Record<string, boolean> }).values;
        seen.saves.push(values);
        saved = { ...saved, ...values };
      }
      return r.fulfill({ json: settingsView(saved) });
    });
  }
  await page.goto("/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  return seen;
}

export async function ticketToReview(page: Page, lots = 1): Promise<Locator> {
  await page.locator("tr[data-strike='24500'] td:visible").first().click();
  await page.locator("[data-chain-actions] [data-action='buy']").click();
  const ticket = page.locator("[data-order-ticket]");
  await expect(ticket).toBeVisible();
  // M68 PR-2: lots may be Not set (the ticket asks); he types them then, else steps up from his saved size.
  if (await ticket.locator("[data-ticket='lots']").inputValue() === "") await ticket.locator("[data-ticket='lots']").fill(String(lots));
  else for (let i = 1; i < lots; i += 1) await ticket.locator("[data-ticket='lots-more']").click();
  if (await ticket.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) {
    await ticket.locator("[data-ticket='order-type'] [data-option='MARKET']").click();
  }
  await ticket.locator("[data-ticket='product'] [data-option='NRML']").click();
  await ticket.locator("[data-ticket='review']").click();
  await expect(ticket.locator("[data-ticket='confirm']")).toBeVisible();
  return ticket;
}

export interface Measured {
  flow: Flow; cards: number; lines: number; visible: number; ms_open_to_place: number; ms_review_to_place: number; reported: Record<string, unknown> | null;
}

export async function placeAndMeasure(page: Page, flow: Flow, place: Locator, done: Locator, seen: Seen): Promise<Measured> {
  await place.click();
  await expect(done).toBeVisible();
  const tm = await page.evaluate(() => (window as unknown as { __tm: { open: number; review: number; place: number; at: { cards: number; lines: number; visible: number } } }).__tm);
  // The report is fire and forget: it may arrive after the result is on screen.
  if (process.env.TICKET_METRICS_BEFORE !== "1") await expect.poll(() => seen.metrics.some((m) => m.outcome === "placed"), { timeout: 5_000 }).toBe(true);
  return { flow, cards: tm.at.cards, lines: tm.at.lines, visible: tm.at.visible, ms_open_to_place: Math.round(tm.place - tm.open), ms_review_to_place: Math.round(tm.place - tm.review),
    reported: seen.metrics.find((m) => m.outcome === "placed") ?? null };
}

