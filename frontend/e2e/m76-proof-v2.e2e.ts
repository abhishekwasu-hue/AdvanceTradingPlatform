import { expect, test, type Locator, type Page } from "@playwright/test";
import { READING_WITH_AREAS, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T } from "./tradingMocks";

/**
 * M76 MP-15 (owner order M76 F; flag `copilot_workspace`): the proof shots of the workspace facilities built in
 * MP-7..MP-14, on made-up answers only (no backend; no real account, price or id): the events strip (S-3), teach-on-chart
 * (L-2), similar past setups (L-1), watch cards set up in place (D-1) and a watch card in Notifications with "Open here",
 * the part-fill card with the re-price helper (E-2), the live position cards with the one-tap exits (E-3 / E-4), the plan
 * helpers (E-1) and the close summary (D-3). Each test also checks the facility is there, read-only (no order route is
 * called while it is only looked at) and, on a phone, the phone layout rules.
 * The check card, the risk preview, the what-if and the journal's accepted warnings have their own specs with shot
 * folders (check-card-v2, check-card-structure-v2, copilot-plan-v2, journal-v2); docs/design/M76_PROOF.html shows all.
 * `MP15_SHOTS=<folder>` writes `<facility>-<width>-<theme>.png` (no baseline is stored).
 */
const SHOTS = process.env.MP15_SHOTS;
const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

// --- made-up reading with annotations: the last close is inside the middle buyer area (similar setups key) ---------------
const area = (role: "supply" | "demand", low: number, high: number, state: string, tests: number) => ({
  role, side: role === "demand" ? "buyer" : "seller", low, high, low_text: low.toFixed(2), high_text: high.toFixed(2), timeframe: "15m", state,
  state_words: state, tests, words: `${role === "demand" ? "Buyer" : "Seller"} area ${low.toFixed(2)} - ${high.toFixed(2)} (15-minute): ${state}.` });
const AREAS = [area("supply", 24700, 24780, "fresh", 0), area("demand", 24000, 24060, "tested", 2), area("demand", 23450, 23520, "fresh", 0)];
const READING = { ...READING_WITH_AREAS, tf: "5m", annotations: {
  source: "atp", source_words: "Drawn from ATP's chart readers on this chart's closed bars.", timeframe: "5m", as_of: "2026-10-19T05:25:00+00:00",
  last_close: 24030, areas: AREAS, swings: [], events: [],
  trend: [{ timeframe: "1d", direction: "up", words: "daily: up" }, { timeframe: "5m", direction: "up", words: "5-minute: up" }],
  next_areas: { above: AREAS[0], below: AREAS[2], inside: AREAS[1] }, divergence: null, level: null,
  summary: ["Trend - daily: up; 5-minute: up."], not_given: [] } };

// --- S-3 events (dates near the made-up clock) -----------------------------------------------------------------------
const EVENTS = { underlying: "NIFTY", from: "2026-10-19", until: "2026-11-06", more: 0, calendar_empty: false, message: null,
  source: "Your events calendar", blocks: false,
  expiries: [{ date: "2026-10-21", kind: "weekly", name: "Weekly expiry" }, { date: "2026-10-28", kind: "monthly", name: "Monthly expiry" }],
  events: [{ date: "2026-10-20", kind: "RBI_POLICY", name: "RBI policy", scope: "all indices", rule: null, source: "market" },
           { date: "2026-10-23", kind: "OWN", name: "My review", scope: "NIFTY", rule: null, source: "own" }] };

// --- L-1 similar past setups ----------------------------------------------------------------------------------------------
const match = (id: number, closed: string, r: number, result: "gain" | "loss", outcome: string) => ({ trade_id: id, symbol: "NIFTY 24000 CE",
  entered_at: closed, closed_at: closed, mode: "PAPER", r, result, outcome, how_closed: "target" });
const SIMILAR = { context: { source: "atp", tf: "15m", side: "buyer", state: "tested" }, as_of: "2026-10-19T05:30:00Z", count: 3, line: null, blocks: false,
  sides: [{ side: "LONG", side_words: "Long", count: 3, enough: true, line: "3 trades · 2 gains, 1 loss · average +0.6 R", wins: 2, losses: 1, avg_r: 0.6,
    matches: [match(51, "2026-10-09T08:10:00Z", 1.4, "gain", "+1.4 R"), match(47, "2026-10-02T06:40:00Z", -1, "loss", "-1.0 R"),
              match(40, "2026-09-24T09:05:00Z", 1.3, "gain", "+1.3 R")] }] };

// --- D-1 watch settings and a raised card ----------------------------------------------------------------------------------
const WATCH = { version: 3, square_off_time: "15:10", telegram: { connected: true, reaches_you: true }, blocks: false,
  prefs: { telegram: true, area_entry: true, area_rejection: true, strike_points: true, strike_points_n: 60, iv_spike: false, iv_spike_points: null,
    margin_use: false, margin_use_pct: null, cut_off_reminder: true, cut_off_minutes: 15,
    areas: [{ id: "w1", exchange: "NSE", symbol: "NIFTY 50", tf: "15m", side: "buyer", low: 24000, high: 24060, since: "2026-10-19T05:00:00Z" }] } };
const WATCH_CARD = { id: 31, event_type: "COPILOT_WATCH", severity: "INFO", title: "NIFTY 50: the price is inside your watched buyer area",
  message: "NIFTY 50 at 24,030.00 is inside your watched buyer area 24,000.00 - 24,060.00 (15m).", related_trade_id: null, related_order_id: null,
  read: false, created_at: "2026-10-19T05:28:00", open_here: { symbol: "NIFTY 50" } };

// --- E-3 / E-4 positions: a bull put spread placed as one basket + one sold call --------------------------------------------
const SOLD_PE = "NIFTY 24000 PE 28 OCT 26";
const BOUGHT_PE = "NIFTY 23900 PE 28 OCT 26";
const SOLD_CE = "NIFTY 24300 CE 28 OCT 26";
const position = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number, lots: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75 * lots, stop_loss: null,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-10-28", mark_price: entry,
  leg_group_id: null });
const POSITIONS = [position(301, SOLD_PE, "SHORT", 62, 1), position(302, BOUGHT_PE, "LONG", 38, 1), position(303, SOLD_CE, "SHORT", 48, 2)];
const PRICES: Record<string, number> = { [SOLD_PE]: 55.5, [BOUGHT_PE]: 33, [SOLD_CE]: 41.2 };
const facts = (symbol: string) => {
  const m = symbol.match(/^NIFTY (\d+) (CE|PE)/);
  return { symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", right: m?.[2] ?? "CE", strike: Number(m?.[1]), expiry: "2026-10-28",
    lot_size: 75, label: symbol, expired: false, days_left: 9, spot_symbol: "NIFTY 50", spot_exchange: "NSE", roll: null, roll_note: null };
};
const GREEKS = { as_of: "", spot: { "NIFTY 50": 24030 }, legs: [{ trade_id: 301, position_delta: 28.4 }, { trade_id: 302, position_delta: -21.1 },
  { trade_id: 303, position_delta: -34.6 }], groups: [], skipped: [], net: { net_delta: -27.3, net_gamma: 0, net_theta: 0, net_vega: 0 } };
const WAITING = [{ id: 91, purpose: "EXIT", kind: "STOP", trade_id: 303, symbol: SOLD_CE, side: "BUY", quantity: 150, lots: 2, order_type: "MARKET",
  price: null, trigger_price: 80, trigger: { source: "PREMIUM", symbol: SOLD_CE, level: 80, side: "ABOVE", mode: "TOUCH", timeframe_minutes: null },
  status: "RESTING", status_words: "Waiting", note: null }];

// --- E-2 a spread that filled in part -------------------------------------------------------------------------------------
const INCOMPLETE = { group_id: "g7", underlying: "NIFTY 50", strategy: "bear call spread", mode: "PAPER", state: "INCOMPLETE", message: "",
  failure: "The sold leg was not filled at the limit", deadline: null, seconds_left: null, on_no_answer: null, rule: null,
  actions: ["complete_rest", "close_filled", "keep"],
  filled: [{ trade_id: 311, symbol: "NIFTY26OCT24500CE", side: "BUY", role: "hedge", quantity: 75, entry_price: 22, open: true },
           { trade_id: 312, symbol: "NIFTY26OCT24400CE", side: "SELL", role: "sold", quantity: 75, entry_price: 31, open: true }],
  pending: [{ symbol: "NIFTY26OCT24400CE", side: "SELL", role: "sold", quantity: 75, exchange: "NFO" }] };

// --- D-3 close summary ------------------------------------------------------------------------------------------------------
const SUMMARY = { as_of: "2026-10-19T05:30:00Z", from: "2026-10-19T03:45:00Z", market_open: true, checklist_saved: true, blocks: false, modes: { PAPER: {
  trades: { entered: 4, closed: 2, gains: 1, losses: 1, net_pnl: 1250, charges: 86 },
  accepted: [{ id: "duplicate_order", label: "Same contract and side already open or waiting", count: 1 }, { id: "event", label: "Event in the window", count: 2 }],
  checklist: { rules: 3, trades: 4, followed: 9, not_followed: 1, not_answered: 2 },
  fill_vs_plan: { trades: 2, average_points: 0.75, largest_points: 1 },
  open: { positions: 3, unrealised_pnl: -297.5, at_risk_to_your_stops: 2340, without_your_stop: 1, unmarked: 0 } } } };

interface Seen { orders: string[] }

async function open(page: Page, theme: "light" | "dark"): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true, paper_account: true, smart_execution: true,
    trade_warnings: true }, brokers: CONNECTED });
  await mockCharts(page, { prefs: { own_events: { events: [{ date: "2026-10-23", name: "My review", underlying: "NIFTY" }] } } });
  const seen: Seen = { orders: [] };
  // Any write to an order / exit route is recorded: looking at a facility must send nothing.
  await page.route(api("/(paper|trading)/.*"), (r) => {
    const req = r.request();
    const path = new URL(req.url()).pathname;
    if (/\/paper\/fno\/contracts$/.test(path)) {
      const items = (req.postDataJSON() as { items: { symbol: string }[] }).items;
      return r.fulfill({ json: { items: items.map((i) => facts(i.symbol)) } });
    }
    if (req.method() !== "GET") seen.orders.push(path);
    if (/\/paper\/fno\/positions\/groups$/.test(path)) {
      return r.fulfill({ json: { groups: [{ key: "b1", kind: "basket", name: "Bull put spread · NIFTY 28 OCT", trade_ids: [301, 302], hedge_ids: [302] }] } });
    }
    if (/\/paper\/fno\/resting$/.test(path)) return r.fulfill({ json: WAITING });
    if (/\/paper\/fno\/group-exits$/.test(path)) return r.fulfill({ json: [] });
    if (/\/trading\/incomplete$/.test(path)) return r.fulfill({ json: { items: [INCOMPLETE], as_of: "2026-10-19T05:30:00Z" } });
    if (/\/paper\/account$/.test(path)) return r.fulfill({ json: T.paper_account });
    return r.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.route(api("/positions\\?"), (r) => r.fulfill({ json: POSITIONS }));
  await page.route(api("/positions/greeks"), (r) => r.fulfill({ json: GREEKS }));
  await page.route(api("/portfolio/exposure"), (r) => r.fulfill({ json: { ...(T.exposure as object), by_symbol: [], prices: PRICES,
    priced_symbols: Object.keys(PRICES), price_source: "ltp" } }));
  await page.route(api("/market-data/quotes$"), (r) => r.fulfill({ json: { items: [{ symbol: "NIFTY26OCT24400CE", exchange: "NFO", ltp: 29.4, missing: false }] } }));
  await page.route(api("/charts/read\\?"), (r) => r.fulfill({ json: READING }));
  await page.route(api("/chart-analyze/workspace$"), (r) => r.fulfill({ json: { status: "off", message: null, notes: [], card: null } }));
  await page.route(api("/charts/workspace/events\\?"), (r) => r.fulfill({ json: EVENTS }));
  await page.route(api("/charts/workspace/similar\\?"), (r) => r.fulfill({ json: SIMILAR }));
  await page.route(api("/charts/workspace/watch$"), (r) => r.fulfill({ json: WATCH }));
  await page.route(api("/charts/workspace/close-summary$"), (r) => r.fulfill({ json: SUMMARY }));
  await page.route(api("/notifications"), (r) => r.fulfill({ json: [WATCH_CARD] }));
  return seen;
}

async function settle(page: Page): Promise<void> {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForTimeout(300);
}

/**
 * The phone rules of e2e/phoneLayout.ts (no sideways scroll; every button / field a 44 px+ touch target), for the M76
 * parts only: the older Market pulse panels lower on the same page are outside this proof (listed on the proof page).
 */
async function expectPhoneParts(page: Page, scope: string): Promise<void> {
  const report = await page.evaluate((sel) => {
    const target = (e: Element) => {
      const box = e.getBoundingClientRect();
      const after = getComputedStyle(e, "::after");
      const grow = after.content !== "none" && after.position === "absolute" ? -2 * Math.min(0, parseFloat(after.top) || 0) : 0;
      return box.height + grow;
    };
    const small: string[] = [];
    for (const root of document.querySelectorAll(sel)) {
      for (const e of root.querySelectorAll("button, a, input, select, textarea, [role=tab], [role=switch]")) {
        if (e.closest("details:not([open])") && !e.closest("summary")) continue;
        const box = e.getBoundingClientRect();
        if (!box.width || !box.height || getComputedStyle(e).visibility === "hidden") continue;
        if (e.closest("table, p, li")) continue;                                   // links inside text and table cells
        if (e.matches("input[type=checkbox], input[type=radio]")) {
          if ((e.closest("label")?.getBoundingClientRect().height ?? 0) < 43.5) small.push(`label ${e.tagName}`);
          continue;
        }
        if (e.tagName === "A" && !e.closest("header, nav")) continue;              // in-text links
        if (target(e) < 43.5) small.push(`${Math.round(target(e))}px ${e.tagName}:${(e.getAttribute("aria-label") ?? e.textContent ?? "").trim().slice(0, 30)}`);
      }
    }
    return { small, scrollWidth: document.documentElement.scrollWidth, width: window.innerWidth };
  }, scope);
  expect(report.scrollWidth, "page wider than the screen").toBeLessThanOrEqual(report.width);
  expect(report.small, "touch targets under 44 px").toEqual([]);
}

async function shot(target: Locator, name: string, width: number, theme: string): Promise<void> {
  if (!SHOTS) return;
  // The phone's fixed bottom navigation would cover a part near the screen's foot: hidden for the picture only
  // (expectPhoneParts does not read it; the Notifications test runs expectPhoneLayout before its shot).
  await target.page().addStyleTag({ content: "nav[aria-label='Quick navigation'] { visibility: hidden !important; }" });
  await target.evaluate((e) => e.scrollIntoView({ block: e.getBoundingClientRect().height < window.innerHeight ? "center" : "start" }));
  await target.screenshot({ path: `${SHOTS}/${name}-${width}-${theme}.png`, animations: "disabled" });
}

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`M76 proof ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`workspace facilities in place, nothing sent (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        await page.goto("/copilot/read?symbol=NIFTY%2050");
        await settle(page);
        const ws = page.locator("[data-copilot-workspace='NIFTY 50']");
        await expect(ws.locator("[data-embedded-chart='NIFTY 50'] canvas").first()).toBeVisible({ timeout: 15_000 });

        // S-3: expiries, market events and his own event, as dated chips under the chart.
        const events = page.locator("[data-workspace-events]");
        await expect(events.locator("[data-event-chip]")).toHaveCount(4, { timeout: 10_000 });
        await expect(events).toContainText("RBI policy");
        await shot(events, "events", width, theme);

        // L-2: tap an area -> its one-line meaning.
        const teach = page.locator("[data-teach]");
        await teach.locator("[data-teach-area='1']").click();
        await expect(page.locator("[data-teach-line]")).toBeVisible();
        await shot(teach, "teach", width, theme);

        // L-1: his own closed trades at the same kind of area.
        const similar = page.locator("[data-similar]");
        await expect(similar).toHaveAttribute("data-similar", "3", { timeout: 10_000 });
        await expect(similar.locator("[data-similar-trade]")).toHaveCount(3);
        await shot(similar, "similar", width, theme);

        // D-1: his watch cards, set up in place (his numbers; nothing pre-set).
        const watch = page.locator("[data-watch-cards]");
        await expect(watch).toHaveAttribute("data-watch-cards", "4", { timeout: 10_000 });
        await watch.locator("summary").first().click();
        await expect(watch.locator("[data-watch-number='strike_points']")).toHaveValue("60");
        await shot(watch, "watch-setup", width, theme);

        // E-2: the part fill and the re-price helper (the new last price; his tap).
        const fills = page.locator("[data-workspace-part-fills]");
        await expect(fills).toBeVisible({ timeout: 10_000 });
        await expect(fills).toContainText("29.40");
        await shot(fills, "part-fills", width, theme);

        // E-3 / E-4: position cards with P&L, distance to the sold strikes, delta, days to expiry and the one-tap exits.
        const positions = page.locator("[data-workspace-positions]");
        await expect(positions).toHaveAttribute("data-workspace-positions", "2", { timeout: 10_000 });
        await expect(positions.locator("[data-position-card]").first()).toContainText("Spot to sold");
        await shot(positions, "positions", width, theme);

        // D-3: his day in numbers.
        const summary = page.locator("[data-close-summary]");
        await expect(summary).toHaveAttribute("data-close-summary", "1", { timeout: 10_000 });
        await shot(summary, "close-summary", width, theme);

        if (width < 768) await expectPhoneParts(page, "[data-copilot-workspace], [data-close-summary]");
        expect(seen.orders).toEqual([]);                                   // looking sends nothing
      });

      test(`plan helpers prepare the plan only (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        await page.route(api("/options-builder/evaluate$"), (r) => r.fulfill({ status: 503, json: { detail: "Not in this proof" } }));
        await page.goto("/copilot/read?symbol=NIFTY%2050");
        await settle(page);
        const panel = page.locator("[data-plan-panel]");
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await panel.locator("[data-option='OPTIONS']").click();
        await panel.locator("[data-plan='spot']").fill("24030");
        await panel.locator("[data-plan='lot-size']").fill("75");
        await panel.locator("[data-plan='step']").fill("50");
        const words: Record<string, string> = { SELL: "Sell", PE: "Put (PE)" };
        for (const [field, value] of [["leg-side", "SELL"], ["leg-type", "PE"]] as const) {
          await panel.locator(`[data-plan='${field}']`).click();
          await page.getByRole("option", { name: words[value], exact: true }).click();
        }
        await panel.locator("[data-plan='leg-strike']").fill("24000");
        await panel.locator("[data-plan='leg-expiry']").fill("2026-10-28");
        await panel.locator("[data-plan='leg-lots']").fill("1");
        await panel.locator("[data-plan='leg-premium']").fill("62");
        await panel.locator("[data-plan='add-leg']").click();
        const helpers = panel.locator("[data-plan='helpers']");
        await expect(helpers).toBeVisible({ timeout: 10_000 });
        await helpers.locator("[data-helper='mirror']").click();
        await shot(helpers, "helpers", width, theme);
        if (width < 768) await expectPhoneParts(page, "[data-plan-panel]");
        expect(seen.orders).toEqual([]);                                   // a helper never sends
      });

      test(`a watch card in Notifications opens the workspace in place (${width} ${theme})`, async ({ page }) => {
        await open(page, theme);
        await page.goto("/notifications");
        await settle(page);
        const card = page.locator("#notification-31");
        await expect(card).toContainText("inside your watched buyer area", { timeout: 15_000 });
        await expect(card.locator("[data-open-here]")).toBeVisible();
        if (width < 768) await expectPhoneLayout(page);
        await shot(card, "watch-card", width, theme);
        await card.locator("[data-open-here]").click();
        await expect(page.locator("[data-copilot-workspace='NIFTY 50']")).toBeVisible({ timeout: 15_000 });
      });
    });
  }
}
