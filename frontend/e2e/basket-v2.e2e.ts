import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockCalls, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * FNO-2: the basket panel (owner order section 5, 8b-c), opened from the chain's basket badge, on the OC1 hand-made
 * chain (e2e/fixtures/option_chain_v2.json) with `ui_v2` and the PAPER account on. Every /api/paper/fno/basket answer and
 * the saved baskets are made up here (round numbers, not market data); the payoff curve is the plain expiry payoff of the
 * legs sent, so the chart matches them.
 * - Baselines (NEW, only the panel): `basket-panel-v2-<width>-<theme>.png` at 1440 and 390, light and dark. No existing
 *   baseline changes (the badge looks as before).
 * - The flows: a ready strategy from the chain, editing a leg, the confirm step (total margin and max loss before placing),
 *   Execute basket with a partial fill shown per leg and "Place the remaining legs" only on the user's tap, Save / Load.
 * After an intended change: `npx playwright test --project=phone e2e/basket-v2.e2e.ts --update-snapshots`.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;
const LOT = 75;
const SPOT = 24480;
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

type Leg = { underlying: string; expiry: string; strike: number; right: "CE" | "PE"; side: "BUY" | "SELL"; lots: number; price: number | null; stop_loss: number | null };
const label = (l: Leg) => `${l.underlying} ${l.expiry.slice(8, 10)} ${MONTHS[Number(l.expiry.slice(5, 7)) - 1]} ${l.strike} ${l.right}`;
const pnl = (legs: Leg[], s: number) => legs.reduce((sum, l) => {
  const intrinsic = l.right === "CE" ? Math.max(s - l.strike, 0) : Math.max(l.strike - s, 0);
  return sum + (l.side === "BUY" ? intrinsic - (l.price ?? 0) : (l.price ?? 0) - intrinsic) * l.lots * LOT;
}, 0);

/** A made-up evaluation in the engine's shape: the expiry payoff of the legs, today a little inside it. */
function evaluation(legs: Leg[]) {
  const prices = Array.from({ length: 241 }, (_, i) => Math.round((SPOT * 0.96 + (i * SPOT * 0.08) / 240) * 100) / 100);
  const expiry = prices.map((p) => pnl(legs, p));
  const max = Math.max(...expiry);
  const min = Math.min(...expiry);
  const breakevens: number[] = [];
  for (let i = 1; i < prices.length; i += 1) {
    if ((expiry[i - 1] < 0 && expiry[i] > 0) || (expiry[i - 1] > 0 && expiry[i] < 0)) {
      breakevens.push(Math.round(prices[i - 1] + ((prices[i] - prices[i - 1]) * -expiry[i - 1]) / (expiry[i] - expiry[i - 1])));
    }
  }
  const net = legs.reduce((s, l) => s + (l.side === "SELL" ? 1 : -1) * (l.price ?? 0) * l.lots * LOT, 0);
  const hedged = legs.some((l) => l.side === "BUY") && legs.some((l) => l.side === "SELL");
  return {
    ok: true, errors: [], prices, at_expiry: expiry, today: expiry.map((y) => y * 0.85), on_date: expiry.map((y) => y * 0.85), days_forward: 0, iv_shift: 0,
    single_expiry: true, extremes: { max_profit: max, max_loss: min, unbounded_profit: false, unbounded_loss: false }, profitable: [], breakevens,
    summary: { expiry: legs[0].expiry, t_years: 0.02, sigma: 0.12, pop: 0.58, expected_move: 410, expected_pnl: 120,
      greeks: { delta: 18.4, gamma: -0.003, theta: 620, vega: -940, rho: 2 }, method: "", model: "black-scholes" },
    legs: [], disclaimer: "Model estimates (Black-Scholes, one volatility) for research; not a forecast.", net_premium: net, reward_to_risk: null,
    margin: { value: -min, estimated: true, basis: "Defined risk: at least the max loss.", note: "",
      hedge: { with_hedge: { value: -min, estimated: true, basis: "Defined risk: at least the max loss." },
        without_hedge: hedged ? { value: null, estimated: true, basis: "Short options not covered by long ones: needs the broker's margin." } : null,
        benefit: null, hedged, source: "estimate", note: "ATP's estimate, not your broker's number: the broker's margin API is not used in practice mode." } },
    costs: { to_open: { stt: 5, exch: 4, sebi: 0, stamp: 0, brokerage: 40, tax: 8, total: 57, other: 12 }, round_trip: 114, brokerage_per_order: 20, orders: legs.length, as_of: "2026-10-27", note: "" },
    lot_sizes: legs.map(() => LOT), labels: legs.map(label), order: legs.map((l, i) => [l.side, i] as const).sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] === "BUY" ? -1 : 1)).map((x) => x[1]),
  };
}

function hedgeFirst(legs: Leg[]) {
  return legs.map((l, i) => ({ l, i })).sort((a, b) => (a.l.side === b.l.side ? a.i - b.i : a.l.side === "BUY" ? -1 : 1));
}

function preview(legs: Leg[]) {
  const ev = evaluation(legs);
  return {
    mode: "PAPER", practice: "Practice mode (PAPER)", product: "MIS", as_of: "2026-10-27T05:00:00+00:00", refusals: [], totals_errors: [],
    legs: hedgeFirst(legs).map(({ l, i }, k) => ({
      seq: k + 1, leg: i, role: l.side === "BUY" ? "hedge" : "short", mode: "PAPER", practice: "", contract: null, label: label(l), side: l.side, lots: l.lots,
      lot_size: LOT, units: l.lots * LOT, order_type: "MARKET", product: "MIS", price: l.price, price_source: "live", fill_price: l.price, fill_note: null,
      stop_loss: l.stop_loss, margin: null, max_loss: null, charges: null, risk: {}, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "" })),
    totals: { margin: ev.margin, extremes: ev.extremes, net_premium: ev.net_premium, costs: 57, breakevens: ev.breakevens },
    can_execute: true, confirm_label: `Place ${legs.length} PAPER orders, hedge legs first`,
  };
}

interface Seen { posts: MockCalls["posts"]; prefs: { items: Record<string, unknown>; version: number } }

async function open(page: Page, theme: "light" | "dark", opts: { partial?: boolean } = {}): Promise<Seen> {
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
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const calls = await mockApi(page, ON);
  const seen: Seen = { posts: calls.posts, prefs: { items: {}, version: 0 } };
  const sessions = { NSE: { is_open: true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/paper\/fno\/held/, (r) => r.fulfill({ json: [] }));
  await page.route(/\/api(?:\/v1)?\/paper\/fno\/basket\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/settings")) {
      return route.fulfill({ json: { source: null, width_steps: 2, lots: 1, stop_pct: 50,
        note: "You have no options settings yet, so a neutral start is used: wings 2 strikes apart, 1 lot, a sold leg's stop-loss 50% above its price. Edit any leg." } });
    }
    const body = request.postDataJSON() as { legs: Leg[] };
    seen.posts.push({ url: url.pathname, method: request.method(), body });
    if (url.pathname.endsWith("/evaluate")) return route.fulfill({ json: evaluation(body.legs) });
    if (url.pathname.endsWith("/preview")) return route.fulfill({ json: preview(body.legs) });
    if (url.pathname.endsWith("/execute")) {
      const ordered = hedgeFirst(body.legs);
      return route.fulfill({ json: {
        mode: "PAPER", status: opts.partial ? "PARTIAL" : "PLACED", reasons: [], placed: opts.partial ? ordered.length - 1 : ordered.length,
        next_steps: opts.partial ? ["1 leg filled and is open in practice mode. Keep them, or close them with Exit now on Positions or the chain.",
          "To place the legs that did not fill, fix what each one says and tap \"Place the remaining legs\". Nothing is retried by itself."] : [],
        legs: ordered.map(({ l, i }, k) => {
          const failed = opts.partial && k === ordered.length - 1;
          return { seq: k + 1, leg: i, label: label(l), side: l.side, lots: l.lots, status: failed ? "REFUSED" : "PLACED",
            reasons: failed ? ["You already hold 3 positions, your limit. Exit one before a new entry."] : [],
            filled: failed ? null : { price: l.price, quantity: l.lots * LOT, lots: l.lots }, order_id: failed ? null : 10 + k, trade_id: failed ? null : 20 + k };
        }) } });
    }
    return route.fulfill({ json: { available: false, why: "A pre-filled basket is available for Zerodha Kite only for now." } });
  });
  await page.route(/\/api(?:\/v1)?\/me\/prefs(\/fno_baskets)?(\?.*)?$/, async (route) => {
    const request = route.request();
    if (request.method() === "PUT") {
      const body = request.postDataJSON() as { items: Record<string, unknown> };
      seen.posts.push({ url: new URL(request.url()).pathname + new URL(request.url()).search, method: "PUT", body });
      seen.prefs = { items: body.items, version: seen.prefs.version + 1 };
      return route.fulfill({ json: { namespace: "fno_baskets", value: { items: body.items }, version: seen.prefs.version } });
    }
    return route.fulfill({ json: { prefs: { fno_baskets: { items: seen.prefs.items } }, versions: { fno_baskets: seen.prefs.version } } });
  });
  await page.goto("/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  return seen;
}

/** Opens the panel from the badge and builds a bull put spread around the ATM (24,500). */
async function bullPut(page: Page) {
  await page.locator("[data-strip='basket']").click();
  const panel = page.locator("[data-basket-panel]");
  await expect(panel).toBeVisible();
  await expect(panel.locator("[data-basket='empty']")).toBeVisible();
  await panel.locator("[data-strategy='Bull put spread']").click();
  await expect(panel.locator("[data-leg]")).toHaveCount(2);
  await expect(panel.locator("[data-basket='plain-words']")).toContainText("If NIFTY closes below");
  return panel;
}

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`Basket panel v2 at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    for (const theme of THEMES) {
      test(`the basket panel looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        const panel = await bullPut(page);
        await expect(panel.locator("[data-figure='margin']")).toContainText("₹11,400.00");
        await expect(panel).toHaveScreenshot(`basket-panel-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}

// 8b-j (F&O 8b slice 2): two baskets side by side - his basket and a ready strategy from his settings - information only.
// Baselines `fno8b2-basket-compare-v2-<360|390|1440>-<theme>.png` (the panel scrolled to the Compare section).
for (const [width, height] of [[1440, 900], [390, 844], [360, 800]] as const) {
  test.describe(`Basket compare v2 at ${width}`, () => {
    test.use(width === 1440 ? { viewport: { width, height } } : { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`two baskets side by side look the same (${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        const panel = await bullPut(page);
        await panel.locator("[data-tool='compare']").click();
        const compare = panel.locator("[data-basket='compare']");
        await compare.locator("[data-compare-pick='1']").selectOption({ label: "Bear call spread" });
        await expect(compare.locator("[data-figure-row='Breakevens'] td")).toHaveText(["24,452", "24,528"]);
        await expect(compare.locator("[data-figure-row='Probability of profit'] td")).toHaveText(["58.0%", "58.0%"]);
        // The engine was asked for the bear call's numbers; nothing was checked for placing or placed.
        await expect.poll(() => seen.posts.filter((p) => p.url.endsWith("/basket/evaluate"))
          .some((p) => (p.body as { legs: Leg[] }).legs.map((l) => `${l.side} ${l.strike} ${l.right}`).join(",") === "BUY 24700 CE,SELL 24500 CE")).toBe(true);
        expect(seen.posts.filter((p) => /\/basket\/(preview|execute)$|\/paper\/fno\/order/.test(p.url))).toEqual([]);
        await compare.evaluate((el) => el.scrollIntoView({ block: "start" }));
        await page.mouse.move(0, 0);
        await expect(panel).toHaveScreenshot(`fno8b2-basket-compare-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}

test.describe("Basket panel v2 flows", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("a ready strategy from the chain -> edit a leg -> confirm (total margin, max loss) -> PAPER, hedge first -> a partial fill shown per leg", async ({ page }) => {
    const seen = await open(page, "light", { partial: true });
    await expectPhoneLayout(page);
    const panel = await bullPut(page);
    await expect(panel.locator("[data-practice-frame]")).toContainText("Practice mode (PAPER)");
    await expect(panel.locator("[data-basket='settings-note']")).toContainText("neutral start");
    await expect(panel.locator("[data-leg='1'] [data-leg-label]")).toHaveText("NIFTY 03 NOV 24300 PE");
    await expect(panel.locator("[data-leg='2'] [data-leg-label]")).toHaveText("NIFTY 03 NOV 24500 PE");
    await expect(panel.locator("[data-leg='2'] [data-leg-stop]")).toHaveValue("255");
    await expect(panel.locator("[data-figure='max-loss']")).toContainText("−₹11,400.00");
    await expect(panel.locator("[data-figure='breakevens']")).toContainText("24,452");
    await panel.locator("[data-leg='1'] [data-leg-more]").tap();
    await expect(panel.locator("[data-leg='1'] [data-leg-lots]")).toHaveText("2");
    await expect.poll(() => (seen.posts.filter((p) => p.url.endsWith("/basket/evaluate")).at(-1)?.body as { legs: Leg[] } | undefined)?.legs[0].lots).toBe(2);
    expect(seen.posts.filter((p) => /\/basket\/(preview|execute)$/.test(p.url))).toEqual([]);          // nothing checked or placed yet

    await panel.locator("[data-basket='product'] [data-option='NRML']").tap();      // F1: no product saved - Execute stays disabled until one is picked
    await panel.locator("[data-basket='execute']").tap();
    const confirm = panel.locator("[data-basket='confirm']");
    await expect(confirm.locator("[data-figure='total-margin']")).toBeVisible();
    await expect(confirm.locator("[data-figure='total-max-loss']")).toContainText("₹");
    await expect(confirm.locator("[data-confirm-leg='1']")).toHaveAttribute("data-role", "hedge");
    expect(seen.posts.filter((p) => p.url.endsWith("/basket/execute"))).toEqual([]);                    // the confirm step places nothing
    await panel.locator("[data-basket='place']").tap();

    const result = panel.locator("[data-basket='result']");
    await expect(result).toHaveAttribute("data-status", "PARTIAL");
    await expect(result).toContainText("1 of 2 legs filled; 1 did not.");
    await expect(result.locator("[data-result-leg='1']")).toHaveAttribute("data-leg-status", "PLACED");
    await expect(result.locator("[data-result-leg='2']")).toHaveAttribute("data-leg-status", "REFUSED");
    await expect(result.locator("[data-result-leg='2']")).toContainText("You already hold 3 positions");
    await expect(result.locator("[data-basket='next-steps']")).toContainText("Exit now");
    const executed = seen.posts.filter((p) => p.url.endsWith("/basket/execute"));
    expect(executed).toHaveLength(1);                                                                   // never retried by itself
    expect(executed[0].body).not.toHaveProperty("mode");
    expect((executed[0].body as { legs: Leg[] }).legs.map((l) => [l.side, l.strike, l.lots])).toEqual([["BUY", 24300, 2], ["SELL", 24500, 1]]);

    await panel.locator("[data-basket='remaining']").tap();                                              // the user's own tap
    await expect(panel.locator("[data-basket='confirm']")).toBeVisible();
    const again = seen.posts.filter((p) => p.url.endsWith("/basket/preview")).at(-1)?.body as { legs: Leg[] };
    expect(again.legs.map((l) => [l.side, l.strike])).toEqual([["SELL", 24500]]);
  });

  test("Save and Load a basket (the user's own preferences); the broker-app link says why it is not available", async ({ page }) => {
    const seen = await open(page, "dark");
    const panel = await bullPut(page);
    await panel.locator("[data-tool='save']").tap();
    await panel.locator("[data-basket='save-name']").fill("Bull put 24500");
    await panel.locator("[data-basket='save-confirm']").tap();
    await expect(panel.locator("[data-basket-note='done']")).toHaveText("Saved \"Bull put 24500\".");
    const put = seen.posts.find((p) => p.url.includes("/me/prefs/fno_baskets"));
    expect(put?.url).toContain("version=0");
    const saved = Object.values((put?.body as { items: Record<string, { name: string; legs: { side: string; stop_loss?: number }[] }> }).items)[0];
    expect(saved.name).toBe("Bull put 24500");
    expect(saved.legs.map((l) => [l.side, l.stop_loss ?? null])).toEqual([["BUY", null], ["SELL", 255]]);
    page.once("dialog", (d) => d.accept());
    await panel.locator("[data-tool='clear']").tap();
    await expect(panel.locator("[data-leg]")).toHaveCount(0);
    await panel.locator("[data-tool='load']").tap();
    await panel.locator("[data-saved-load]").first().tap();
    await expect(panel.locator("[data-leg]")).toHaveCount(2);
    await panel.locator("[data-tool='broker-app']").tap();
    await expect(panel.locator("[data-basket-note='warn']")).toContainText("Zerodha Kite only");
  });
});
