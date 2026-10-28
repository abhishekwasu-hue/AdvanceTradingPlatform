import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * F&O workspace 8b, slice 2 (owner order ATP_SESSION_FNO_WORKSPACE.md sections 8b e, f, g and 9) on My positions, behind
 * `ui_v2` with the PAPER account on:
 * - the premium kept so far on short premium (one sold option, legs placed together that took in a net premium);
 * - "Tell me when" on the user's OWN levels (breakeven, short strike, his exit level, a share of the premium he types):
 *   one instrument rule of the existing alert rules (`screener_v2`), which only notifies - no order, no exit, no action;
 * - "Compare a change": the legs now beside the same legs with the change he builds (close legs in the comparison only,
 *   the legs of his own basket), information only.
 * Baselines `fno8b2-positions-v2-<360|390|1440>-<theme>.png` (the group's premium kept with the alert choices open) and
 * `fno8b2-compare-v2-<360|390|1440>-<theme>.png` (the comparison). Every reply is made-up data (no real account, price,
 * order or id). After an intended change: `npx playwright test --project=phone e2e/fno8b-slice2-v2.e2e.ts --update-snapshots`.
 */

const THEMES = ["light", "dark"] as const;
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const FONT = [
  "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
  "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
].join("\n");

// --- made-up contracts and positions (the clock is 19 Oct 2026, 11:00 IST) -------------------------------------------------
const SHORT_CE = "NIFTY 24900 CE 27 OCT 26";
const LONG_CE = "NIFTY 25000 CE 27 OCT 26";
const SHORT_PE = "NIFTY 24500 PE 27 OCT 26";
const contract = (symbol: string, right: "CE" | "PE", strike: number) => ({
  symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", lot_size: 75, spot_symbol: "NIFTY 50", spot_exchange: "NSE",
  expired: false, days_left: 8, roll: null, roll_note: null, expiry: "2026-10-27", right, strike, label: `NIFTY 27 OCT ${strike} ${right}`,
});
const FACTS: Record<string, unknown> = {
  [SHORT_CE]: contract(SHORT_CE, "CE", 24900), [LONG_CE]: contract(LONG_CE, "CE", 25000), [SHORT_PE]: contract(SHORT_PE, "PE", 24500),
};
const leg = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number, stop: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75, stop_loss: stop,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-10-27",
});
const POSITIONS = [leg(301, SHORT_CE, "SHORT", 80, 120), leg(302, LONG_CE, "LONG", 45, 20), leg(303, SHORT_PE, "SHORT", 60, 90)];
const LTP: Record<string, number> = { [SHORT_CE]: 72.5, [LONG_CE]: 41, [SHORT_PE]: 33 };
const EXPOSURE = (() => {
  const e = T.exposure as Record<string, unknown>;
  const rows = POSITIONS.map((p) => ({ symbol: p.symbol, positions: 1, quantity: 75, notional: 75 * LTP[p.symbol], pct_of_capital: 1, unrealised_pnl: 0,
    risk_at_stop: 0, strategies: ["manual_fno"] }));
  return { ...e, open_positions: 3, by_symbol: rows, priced_symbols: rows.map((r) => r.symbol) };
})();
const SPOT = 24850;

interface Seen { writes: { method: string; url: string; body: unknown }[]; alerts: unknown[]; evaluations: { legs: { strike: number; side: string; lots: number; price: number }[] }[] }

/** A made-up engine answer: only the margin and the lot sizes matter here (the payoff is drawn from the legs). */
const evaluation = (legs: { side: string }[]) => ({
  ok: true, errors: [], prices: [], at_expiry: [], today: [], on_date: [], extremes: null, breakevens: [], net_premium: 0,
  summary: { pop: 0.6, greeks: { delta: 0, gamma: 0, theta: 0, vega: 0 } },
  margin: { value: 4875, estimated: true, basis: "", note: "", hedge: { with_hedge: { value: legs.length * 2400 + 75, estimated: true, basis: "" }, without_hedge: null, benefit: null, hedged: true, source: "estimate", note: "" } },
  lot_sizes: legs.map(() => 75), labels: [], order: [],
});

async function openPositions(page: Page, theme: "light" | "dark", opts: { alerts?: boolean; basket?: unknown[] } = {}): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(([t, basket]) => {
    try {
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "positions"]));
      sessionStorage.setItem("atp_fno_basket", JSON.stringify(basket));
    } catch { /* storage unavailable */ }
  }, [theme, opts.basket ?? []] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), paper_account: true, screener_v2: opts.alerts ?? true }, brokers: T.token_status as unknown[] });
  const writes = await mockTrading(page, { paperAccount: true });
  const seen: Seen = { writes: writes.calls, alerts: [], evaluations: [] };
  const record = (method: string, url: string, body: unknown) => seen.writes.push({ method, url: url.replace(/^.*\/api(?:\/v1)?/, ""), body });
  await page.route(api("/positions$"), (r) => r.fulfill({ json: POSITIONS }));
  await page.route(api("/portfolio/exposure"), (r) => r.fulfill({ json: EXPOSURE }));
  await page.route(api("/paper/fno/contracts$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    record("POST", r.request().url(), null);
    return r.fulfill({ json: { items: items.map((i) => FACTS[i.symbol] ?? { symbol: i.symbol, exchange: i.exchange, known: false }) } });
  });
  await page.route(api("/paper/fno/positions/groups$"), (r) => r.fulfill({ json: { groups: [{ key: "basket:made-up", kind: "basket", name: "Basket", trade_ids: [301, 302] }] } }));
  await page.route(api("/market-data/quotes$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    record("POST", r.request().url(), items);
    return r.fulfill({ json: { items: items.map((i) => ({ ...i, ltp: i.symbol === "NIFTY 50" ? SPOT : null, change: null, change_pct: null, missing: i.symbol !== "NIFTY 50" })) } });
  });
  await page.route(api("/alerts/rules$"), (r) => {
    if (r.request().method() === "GET") return r.fulfill({ json: [] });     // 8b slice 3: the cockpit lists his alerts
    const body = r.request().postDataJSON();
    record(r.request().method(), r.request().url(), body);
    seen.alerts.push(body);
    return r.fulfill({ status: 201, json: { id: 9, ...body, status: "active" } });
  });
  await page.route(api("/paper/fno/basket/evaluate$"), (r) => {
    const body = r.request().postDataJSON() as Seen["evaluations"][number];
    record("POST", r.request().url(), body);
    seen.evaluations.push(body);
    return r.fulfill({ json: evaluation(body.legs) });
  });
  await page.goto("/positions");
  await page.addStyleTag({ content: FONT });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return seen;
}

/** The writes that are not reads: anything but the session refresh, the contract lookup, the quote, the engine's numbers and the alert. */
const actionsOf = (seen: Seen) => seen.writes.filter((w) => !/\/auth\/refresh$|\/paper\/fno\/contracts$|\/market-data\/(quotes|candles)$|\/paper\/fno\/basket\/evaluate$|\/alerts\/rules$/.test(w.url));

const BASKET = [
  { id: "a", underlying: "NIFTY", expiry: "2026-10-27", strike: 25000, right: "CE", side: "SELL", lots: 1, price: 41, label: null, stopLoss: 60, iv: null },
  { id: "b", underlying: "NIFTY", expiry: "2026-10-27", strike: 25100, right: "CE", side: "BUY", lots: 1, price: 22, label: null, iv: null },
];

const SIZES = [{ width: 360, height: 800, phone: true }, { width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
for (const size of SIZES) {
  test.describe(`8b slice 2 positions ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });
    for (const theme of THEMES) {
      test(`the premium kept and Tell me when look the same (${theme})`, async ({ page }) => {
        await openPositions(page, theme);
        const group = page.locator("[data-group='basket']");
        await expect(group.locator("[data-capture='kept']")).toContainText("You have kept ₹263 of the ₹2,625 premium received (10%).");
        await group.locator("[data-group-insights]").getByTestId("fno-alert").click();
        await expect(group.locator("[data-alert-choice='be-0']")).toContainText("NIFTY 50 reaches 24,935 (your breakeven)");
        const insights = group.locator("[data-group-insights]");
        await insights.scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        await expect(insights).toHaveScreenshot(`fno8b2-positions-v2-${size.width}-${theme}.png`, SHOT);
      });

      test(`Compare a change looks the same (${theme})`, async ({ page }) => {
        await openPositions(page, theme, { basket: BASKET });
        await page.locator("[data-group='basket']").getByTestId("fno-compare").click();
        const dialog = page.locator("[data-adjust-compare]");
        await expect(dialog.locator("[data-practice-frame]")).toHaveText("PAPER");
        await dialog.locator("[data-change-leg='301'] [data-option='close']").click();
        await dialog.locator("[data-change-leg='302'] [data-option='close']").click();
        await expect(dialog.locator("[data-figure-row='Booked now by closing']")).toContainText("₹262.50");
        await expect(dialog.locator("[data-figure-row='Margin']")).toContainText("₹4,875.00");
        await dialog.locator("[data-compare-body]").evaluate((el) => { el.scrollTop = 0; });
        await page.mouse.move(0, 0);
        await expect(dialog).toHaveScreenshot(`fno8b2-compare-v2-${size.width}-${theme}.png`, SHOT);
      });
    }
    if (size.phone) {
      test("no sideways scroll, 44 px targets, with the alert choices and the comparison open", async ({ page }) => {
        await openPositions(page, "dark", { basket: BASKET });
        await page.locator("[data-group='basket'] [data-group-insights]").getByTestId("fno-alert").click();
        await expectPhoneLayout(page);
        await page.locator("[data-group='basket']").getByTestId("fno-compare").click();
        await expect(page.locator("[data-adjust-compare] [data-compare-figures]")).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width);
      });
    }
  });
}

test.describe("8b slice 2 flows, 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Tell me when saves ONE alert rule on his own level - an instrument rule with no action; nothing else is sent", async ({ page }) => {
    const seen = await openPositions(page, "light");
    const single = page.locator("[data-position='303']");
    await expect(single.locator("[data-capture='kept']")).toContainText("You have kept ₹2,025 of the ₹4,500 premium received (45%).");
    await single.getByTestId("fno-alert").click();
    // His own levels only: the breakeven and the strike on NIFTY 50, his exit level on the contract.
    await expect(single.locator("[data-alert-choice]")).toHaveText([
      "NIFTY 50 reaches 24,440 (your breakeven)", "NIFTY 50 reaches 24,500 (your short put strike)", "NIFTY 27 OCT 24500 PE reaches 90 (your exit level)",
      /I have kept\s*% of the premium/,
    ]);
    await single.locator("[data-alert-kept]").fill("50");
    await expect(single.locator("[data-alert-choice='kept']")).toContainText("(NIFTY 27 OCT 24500 PE at 30.00)");
    await single.locator("[data-alert-choice='be-0'] input").check();
    await single.locator("[data-alert-save]").click();
    await expect(single.locator("[data-alert-note='done']")).toContainText("Alert saved: you will be told when NIFTY 50 is at or below 24,440 (your breakeven). An alert only tells you.");
    expect(seen.alerts).toEqual([{ name: "NIFTY 50 at or below 24,440 (your breakeven)", kind: "instrument", symbol: "NIFTY 50", exchange: "NSE",
      condition: "close <= 24440", base_tf: "1m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly" }]);
    // No order, no exit, no basket, no "then" action: only reads and the one alert.
    expect(actionsOf(seen)).toEqual([]);
    await expect(single.getByTestId("exit-now")).toBeEnabled();
  });

  test("Compare a change sends nothing but the engine's numbers; an exit still works after it", async ({ page }) => {
    const seen = await openPositions(page, "light", { basket: BASKET });
    await page.locator("[data-group='basket']").getByTestId("fno-compare").click();
    const dialog = page.locator("[data-adjust-compare]");
    // Kept as they are: both columns hold the open legs; the basket's two legs are added on the right.
    await expect(dialog.locator("[data-figure-row='Breakevens'] td").first()).toHaveText("24,935");
    await expect(dialog.locator("[data-change-added]")).toContainText("Short 1 lot · NIFTY 27 OCT 25000 CE at 41.00");
    await dialog.locator("[data-change-leg='301'] [data-option='close']").click();
    await expect(dialog.locator("[data-figure-row='Booked now by closing'] td").nth(1)).toHaveText("₹562.50");
    await expect.poll(() => seen.evaluations.at(-1)?.legs.map((l) => `${l.side} ${l.strike}`)).toEqual(["BUY 25000", "SELL 25000", "BUY 25100"]);
    expect(seen.evaluations[0].legs.map((l) => [l.side, l.strike, l.lots, l.price])).toEqual([["SELL", 24900, 1, 80], ["BUY", 25000, 1, 45]]);
    await expect(dialog.getByRole("button", { name: /place|execute|exit/i })).toHaveCount(0);
    await page.keyboard.press("Escape");
    expect(actionsOf(seen)).toEqual([]);
    await page.locator("[data-position='301']").getByTestId("exit-leg").click();       // TT-8: a basket leg exits on its own
    await page.getByTestId("group-exit-confirm").click();
    await expect.poll(() => seen.writes.map((c) => `${c.method} ${c.url}`)).toContain("POST /paper/fno/positions/301/exit-leg");
  });

  test("without the alert rules' flag: no Tell me when; the premium kept and Compare stay", async ({ page }) => {
    await openPositions(page, "light", { alerts: false });
    await expect(page.getByTestId("fno-alert")).toHaveCount(0);
    await expect(page.locator("[data-position='303'] [data-capture]")).toBeVisible();
    await expect(page.getByTestId("fno-compare")).toHaveCount(1);
  });
});
