import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * CX6 (owner, 24 Oct) + CX-F1 / CX-F2 (25 Oct), behind `ui_v2` with the PAPER account on, on made-up answers only (no real
 * account, price or id):
 * - My positions: "Set combined stop-loss" -> the sheet (a tick box per leg, ₹ / spot / premium, the plain preview
 *   sentence) -> confirm sets it once; the chip shows the distance to trigger and the "stale price" mark; Edit and Remove.
 * - The CRITICAL notice's link /positions?exit=<id>: "Exit short now?" with one confirm, through the leg's own exit.
 * - Terminal: the same chip in the status bar.
 * CX_SHOTS=<dir>: also writes the phone (390) and desktop (1440) screenshots for the owner; no baseline is kept.
 */

const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const SHOTS = process.env.CX_SHOTS;
const SHORT_CE = "NIFTY 25200 CE 27 OCT 26";
const LONG_CE = "NIFTY 25400 CE 27 OCT 26";
const SHORT_PE = "NIFTY 24800 PE 27 OCT 26";
const leg = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75, stop_loss: null,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-10-27", mark_price: entry,
});
const POSITIONS = [leg(301, SHORT_CE, "SHORT", 80), leg(302, LONG_CE, "LONG", 35), leg(303, SHORT_PE, "SHORT", 70)];
const WORDS = "If combined loss reaches ₹2,500, these 3 legs exit - sold legs first.";
const EXIT = {
  id: 7, mode: "PAPER", origin: "CX", scope: "SELECTED", measure: "MTM_INR", sl_value: 2500, target_value: null, sl_side: null, target_side: null,
  spot_symbol: null, check_mode: "TOUCH", timeframe_minutes: null, status: "ARMED", fired_kind: null, last_value: -400,
  distance: { sl: 2100, target: null }, version: 1, words: WORDS, note: null, trail: [],
  legs: [{ trade_id: 301, symbol: SHORT_CE, role: "SHORT" }, { trade_id: 302, symbol: LONG_CE, role: "LONG" }, { trade_id: 303, symbol: SHORT_PE, role: "SHORT" }],
  stale: [{ trade_id: 302, symbol: LONG_CE, price: 35, seen_at: "2026-10-19T05:20:00Z" }],
};

interface Seen { posts: { url: string; body: unknown }[] }

async function routes(page: Page, seen: Seen, state: { exits: unknown[] }) {
  await page.route(api("/paper/fno/contracts$"), (r) => r.fulfill({ json: { items: [] } }));
  await page.route(api("/paper/fno/positions/groups$"), (r) => r.fulfill({ json: { groups: [] } }));
  await page.route(api("/paper/fno/group-exits(/.*)?$"), (r) => {
    const req = r.request();
    const url = req.url().replace(/^.*\/api(?:\/v1)?/, "");
    if (req.method() === "GET") return r.fulfill({ json: state.exits });
    let body: unknown = null;
    try { body = req.postDataJSON(); } catch { body = null; }
    seen.posts.push({ url, body });
    if (url.endsWith("/preview")) return r.fulfill({ json: { ok: true, reasons: [], words: WORDS, value_now: -400, distance: { sl: 2100, target: null }, legs: EXIT.legs } });
    if (url.endsWith("/cancel")) { state.exits = []; return r.fulfill({ json: { ...EXIT, status: "CANCELLED" } }); }
    if (url.endsWith("/edit")) {
      const b = body as { sl_value: number };
      const changed = { ...EXIT, sl_value: b.sl_value, version: 2, distance: { sl: b.sl_value - 400, target: null }, stale: [],
        words: `If combined loss reaches ₹${b.sl_value.toLocaleString("en-IN")}, these 3 legs exit - sold legs first.` };
      state.exits = [changed];
      return r.fulfill({ json: changed });
    }
    state.exits = [EXIT];
    return r.fulfill({ json: EXIT });
  });
  await page.route(api("/paper/fno/positions/\\d+/exit-leg$"), (r) => {
    const url = r.request().url().replace(/^.*\/api(?:\/v1)?/, "");
    seen.posts.push({ url, body: r.request().postDataJSON() });
    return r.fulfill({ json: { status: "CLOSED", legs: [{ trade_id: 301, symbol: SHORT_CE, closed: true, exit_price: 92, pnl: -900, warnings: [] }] } });
  });
}

async function openPositions(page: Page, path = "/positions", exits: unknown[] = []) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "positions", "terminal"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), paper_account: true }, brokers: T.token_status as unknown[] });
  await mockTrading(page, { paperAccount: true });
  const seen: Seen = { posts: [] };
  const state = { exits };
  await page.route(api("/positions$"), (r) => r.fulfill({ json: POSITIONS }));
  await routes(page, seen, state);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  return { seen, state };
}

const SIZES = [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
for (const size of SIZES) {
  test.describe(`CX6 combined stop-loss ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });

    test("set it from the sheet, see the chip with the distance and the stale mark, edit and remove it", async ({ page }) => {
      const { seen } = await openPositions(page);
      await page.getByTestId("combined-open").click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText("Set combined stop-loss");
      await expect(dialog.locator("[data-leg]")).toHaveCount(3);
      // M68 PR-3 (TS1-031): nothing is pre-chosen (no saved Exits value) - he picks the ₹ loss here.
      await expect(dialog.locator("[data-measure] input:checked")).toHaveCount(0);
      await dialog.locator("[data-measure='MTM_INR']").click();
      await dialog.getByTestId("combined-sl").fill("2500");
      await expect(dialog.locator("[data-preview-words]")).toHaveText(WORDS);
      await expect(dialog.locator("[data-preview-now]")).toContainText("−₹400");
      if (size.phone) await expectPhoneLayout(page);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/cx-sheet-${size.width}.png`, fullPage: false });
      await dialog.getByTestId("combined-confirm").click();
      await expect(dialog).toHaveCount(0);
      const set = seen.posts.filter((p) => p.url === "/paper/fno/group-exits");
      expect(set).toHaveLength(1);
      expect(set[0].body).toMatchObject({ scope: "SELECTED", trade_ids: [301, 302, 303], measure: "MTM_INR", sl_value: 2500, target_value: null });
      expect(set[0].body).not.toHaveProperty("check_mode");           // M68 PR-3: a ₹ exit's only check is every price (server)

      const chip = page.locator("[data-combined-exit='7']");
      await expect(chip).toContainText("Combined stop-loss · 3 legs");
      await expect(chip.locator("[data-distance]")).toHaveText("stop ₹2,100 to go");
      await expect(chip.locator("[data-stale]")).toHaveText("Stale price");
      await expect(page.getByText(`Combined exit set: ${WORDS}`)).toBeVisible();
      if (SHOTS) {
        await chip.scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SHOTS}/cx-chip-${size.width}.png`, fullPage: false });
      }

      await chip.getByTestId("combined-edit").click();
      await expect(page.getByRole("dialog")).toContainText("Change combined exit");
      await page.getByTestId("combined-sl").fill("3000");
      await page.getByTestId("combined-confirm").click();
      await expect.poll(() => seen.posts.at(-1)).toEqual({ url: "/paper/fno/group-exits/7/edit", body: { version: 1, sl_value: 3000, target_value: null, trailing: null } });   // CX2: the sheet sends its trailing (none here)
      await expect(chip.locator("[data-distance]")).toHaveText("stop ₹2,600 to go");
      await expect(chip.locator("[data-stale]")).toHaveCount(0);

      await chip.getByTestId("combined-remove").click();
      await page.getByRole("dialog", { name: "Remove this combined exit?" }).locator("[data-confirm]").click();   // UI-28 U3.3
      await expect.poll(() => seen.posts.at(-1)?.url).toBe("/paper/fno/group-exits/7/cancel");
      await expect(page.locator("[data-combined-exit]")).toHaveCount(0);
      await expect(page.getByText("Combined exit removed. The positions stay open.")).toBeVisible();
    });

    test("CX-F2: the notice's link asks 'Exit short now?' once and closes that leg through its own exit", async ({ page }) => {
      const { seen } = await openPositions(page, "/positions?exit=301");
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText("Exit short now?");
      await expect(dialog).toContainText(`${SHORT_CE} closes at the current price.`);
      const confirm = dialog.getByTestId("exit-short-confirm");
      await expect(confirm).toBeEnabled();
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/cx-exit-short-${size.width}.png`, fullPage: false });
      await confirm.click();
      await expect.poll(() => seen.posts).toEqual([{ url: "/paper/fno/positions/301/exit-leg", body: { confirm: false } }]);
      await expect(page.getByText(`${SHORT_CE} closed`)).toBeVisible();
    });
  });
}

// --- Terminal: the chip in the status bar --------------------------------------------------------------------------------

const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as { expiries: unknown; live: Record<string, unknown> };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const STATUS = {
  today: { mtm: 1650, booked: 1900, note: "Booked today plus your open positions' profit or loss since entry, at their last price." },
  open: { count: 3, priced: 3, mtm: -400, as_of: "2026-10-27T05:00:00Z" },
  funds: { balance: 1_001_900, available: 993_300, source: "practice", note: "Your practice balance minus the margin estimate." },
  margin: { used: 8600, estimated: true, not_estimated: 0, note: "ATP's estimate, not your broker's number." },
  mode: { mode: "PAPER", live_allowed: false, message: "Practice mode: no real order is sent and no money moves." },
  feed: { state: "live", message: "Live prices from Upstox.", broker_name: "Upstox" },
  kill_switch: { engaged: false, scope: null, message: "New orders are allowed." },
  as_of: "2026-10-27T05:00:00Z",
};

for (const size of SIZES) {
  test(`CX6 Terminal ${size.width}: the active combined exit in the status bar and on the positions panel`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
    await page.addInitScript(() => {
      try {
        localStorage.setItem("atp_install_dismissed", "1");
        localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["terminal"]));
      } catch { /* storage unavailable */ }
    });
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
    await mockApi(page, { features: { ui_v2: true, terminal: true, paper_account: true }, brokers: CONNECTED });
    await mockCharts(page);
    await page.route(api("/me/prefs(/[a-z_]+)?(\\?.*)?$"), (r) => (r.request().method() === "PUT"
      ? r.fulfill({ json: { namespace: "terminal", value: r.request().postDataJSON(), version: 1 } })
      : r.fulfill({ json: { prefs: { chart: {}, terminal: { layouts: {}, active: null } }, versions: { chart: 1, terminal: 0 } } })));
    await page.route(api("/terminal/status$"), (r) => r.fulfill({ json: STATUS }));
    await page.route(api("/positions\\?mine=true$"), (r) => r.fulfill({ json: POSITIONS }));
    await page.route(api("/orders\\?mine=true"), (r) => r.fulfill({ json: [] }));
    await page.route(api("/option-chain/([^/]+)/(expiries|live)"), (route) => (new URL(route.request().url()).pathname.endsWith("/expiries")
      ? route.fulfill({ json: DATA.expiries }) : route.fulfill({ json: { ...DATA.live, market_open: true } })));
    await page.route(api("/portfolio/exposure"), (r) => r.fulfill({ json: { price_source: "ltp", currency: "INR", open_positions: 3, unrealised_pnl: -400,
      realised_today: 1900, priced_symbols: POSITIONS.map((p) => p.symbol), by_symbol: [] } }));
    await page.route(api("/trading/incomplete$"), (r) => r.fulfill({ json: { items: [], as_of: "2026-10-27T05:00:00Z" } }));
    await page.route(api("/paper/fno/(held|risk-budget|event-risk|basket/settings)"), (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
    await routes(page, { posts: [] }, { exits: [EXIT] });
    await page.goto("/terminal");
    await page.waitForLoadState("networkidle");

    const bar = page.locator("[data-block='terminal-status'] [data-status='combined-exit']");
    await expect(bar).toContainText("Combined exit: ₹2,100 to go · stale price");
    if (size.phone) {
      await page.locator("[data-tab='positions']").click();
    }
    await expect(page.locator("[data-combined-exit='7']")).toContainText("stop ₹2,100 to go");
    const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
    expect(r.scroll).toBeLessThanOrEqual(r.width);
    if (SHOTS) {
      await page.screenshot({ path: `${SHOTS}/cx-terminal-${size.width}.png`, fullPage: false });
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await page.screenshot({ path: `${SHOTS}/cx-statusbar-${size.width}.png`, fullPage: false });
    }
  });
}
