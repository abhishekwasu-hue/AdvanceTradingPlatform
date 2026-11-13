import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { DEMO_ANALYSES } from "../src/optionChain/analyses.fixture";
import { DEMO_ANALYTICS } from "../src/optionChain/chainAnalytics.fixture";
import { mockApi, type MockOptions } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";
import { warnOnManySolidBrand } from "./solidBrand";
import { expectComboValue, pickOption } from "./combobox";

// Chart + colour order Part 3 (CC-8): a warning annotation, never a failure, when a screen shows more than one solid
// brand button (the one main action per screen).
test.afterEach(async ({ page }, testInfo) => { await warnOnManySolidBrand(page, testInfo); });

/**
 * OC1: the automatic Option chain page with the `ui_v2` flag on, on a hand-made chain (e2e/fixtures/option_chain_v2.json,
 * round made-up numbers). The clock is 27 Oct 2026, an expiry day, so the page opens on the next expiry (3 Nov).
 * - Baselines `option-chain-v2-<width>-<theme>.png` at 1440 and 390, light and dark (the "after" pictures; the flag-off
 *   page's `option-chain-<width>-<theme>.png` in mobile.e2e.ts are the "before" page without its developer controls).
 * - Automatic symbol and expiry (no click), the ATM row in the middle, the phone layout and a row's details, the
 *   plain-word states, the broker gate, and no sample data anywhere.
 * Browser-dependent state is pinned in the init script (the tab shown, scroll bars that take no room), because CI's
 * Chromium differs from a developer's.
 * After an intended change: `npx playwright test --project=phone e2e/option-chain-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const V2: MockOptions = { features: { ui_v2: true }, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;

interface Calls { expiries: string[]; live: string[] }

type Leg = { oi: number | null; oi_change: number | null; ltp: number | null };
type Row = { strike: number; call: Leg; put: Leg };

/** M96 L1: the made-up chain's Trade "Net Diff" block as the server builds it (ATM ± 6 steps, NIFTY 50 / BANKNIFTY and
 * SENSEX 100; here the fixture's own OI change stands for the change since the day's first reading) and its PCR. */
function withNetDiff(body: Record<string, unknown>): Record<string, unknown> {
  const rows = body.rows as Row[];
  const summary = body.summary as Record<string, unknown>;
  const name = String(body.underlying);
  const step = name === "NIFTY" ? 50 : 100;
  const spot = Number(summary.spot);
  const atm = Math.round(spot / step) * step;
  const shown = rows.filter((r) => r.strike >= atm - 6 * step && r.strike <= atm + 6 * step).map((r) => {
    const ce = r.call.oi_change ?? 0;
    const pe = r.put.oi_change ?? 0;
    return { strike: r.strike, ce_chg: ce, ce_oi: r.call.oi ?? 0, ce_ltp: r.call.ltp, pe_ltp: r.put.ltp, pe_oi: r.put.oi ?? 0, pe_chg: pe,
      diff: pe - ce, dominance: pe - ce > 0 ? "Bulls" : pe - ce < 0 ? "Bears" : "Neutral" };
  });
  const call = shown.reduce((a, r) => a + r.ce_oi, 0);
  const put = shown.reduce((a, r) => a + r.pe_oi, 0);
  const pcr = call > 0 ? Math.round((put / call) * 100) / 100 : null;
  return { ...body, summary: { ...summary, pcr_oi_atm: pcr },
    net_diff: { status: "ready", since: "2026-10-27T03:50:00+00:00", step, atm, window: 6, pcr, rows: shown } };
}

/** M96 L1: made-up stored OI readings of the day (the banner and its History), as /banner and /history answer. */
function bannerRow(i: number) {
  const call = 9_000_000 + 60_000 * i;
  const put = 9_600_000 + 110_000 * i;
  const min = 25 - 5 * i;
  return { slot: `2026-10-27T10:${String(min).padStart(2, "0")}:00+05:30`, data_as_of: `2026-10-27T04:${String(min + 31).padStart(2, "0")}:10+00:00`,
    direction: "BULLISH", message: "", fact_message: "Put writing rising + Call short covering (resistance weakening) → NIFTY OI readings lean up",
    put_class: "Writing", call_class: "Short covering", signal: i === 5 ? "BEARISH (Weakening)" : "BULLISH (Strong)", diff: put - call, delta_diff: 50_000,
    total_call_oi: call, total_put_oi: put, pcr: Math.round((put / call) * 100) / 100, pcr_band: "MILD_BULLISH", pcr_label: "", underlying_price: 24480,
    atm_strike: 24500, strike_step: 50, max_pain: 24500, expiry: "2026-11-03", dte: 7, first_of_day: i === 5 };
}
const FRESH = { market_open: true, stale: false, age_minutes: 1, stale_after_minutes: 15 };

async function open(page: Page, theme: "light" | "dark", opts: {
  options?: MockOptions; marketOpen?: boolean; live?: (body: Record<string, unknown>) => Record<string, unknown>; path?: string;
  /** The chain read fails with this status (the server's own words in the body must never show). */
  failLive?: number;
} = {}): Promise<Calls> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["option-chain"]));
    } catch { /* storage unavailable */ }
    // Pinned browser state: the tab is shown (the chain reads only then), whatever the headless build reports.
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, opts.options ?? V2);
  const calls: Calls = { expiries: [], live: [] };
  const sessions = { NSE: { is_open: opts.marketOpen ?? true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    const m = /\/option-chain\/([^/]+)\/(expiries|live)/.exec(url.pathname)!;
    const symbol = decodeURIComponent(m[1]);
    if (m[2] === "expiries") {
      calls.expiries.push(symbol);
      return route.fulfill({ json: { ...DATA.expiries, underlying: symbol } });
    }
    calls.live.push(`${symbol}@${url.searchParams.get("expiry") ?? ""}`);
    if (opts.failLive) return route.fulfill({ status: opts.failLive, json: { detail: "RuntimeError: socket closed" } });
    const body = withNetDiff({ ...DATA.live, underlying: symbol, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: opts.marketOpen ?? true });
    return route.fulfill({ json: opts.live ? opts.live(body) : body });
  });
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(banner|history)/, (route) => {
    const m = /\/option-chain\/([^/]+)\/(banner|history)/.exec(new URL(route.request().url()).pathname)!;
    const underlying = decodeURIComponent(m[1]);
    const flags = { ...FRESH, market_open: opts.marketOpen ?? true };
    if (m[2] === "banner") return route.fulfill({ json: { underlying, date: "2026-10-27", banner: { ...bannerRow(0), underlying }, ...flags } });
    return route.fulfill({ json: { underlying, date: "2026-10-27", interval: 5, rows: [0, 1, 2, 3, 4, 5].map(bannerRow), ...flags } });
  });
  // OC4: the analyses below the chain (the OC2 fixture, renamed to the symbol on screen) and no saved analyses yet.
  await page.route(/\/api(?:\/v1)?\/option-chain\/analyses/, (r) => {
    const symbol = new URL(r.request().url()).searchParams.get("symbol") ?? "NIFTY";
    return r.fulfill({ json: { ...DEMO_ANALYSES, symbol, analytics: DEMO_ANALYTICS } });   // M96 N1: the chain analytics too
  });
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
    prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: "normal" } },
    versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 0 } } }));
  await page.goto(opts.path ?? "/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    // Pinned: scroll bars take no room (overlay or classic depends on the Chromium build).
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await page.waitForLoadState("networkidle");
  return calls;
}

async function chainReady(page: Page) {
  await expect(page.locator("[data-page='option-chain-v2']")).toBeVisible();
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
}

/** The ATM row's middle is within one row of the middle of the table's visible rows. */
async function expectAtmCentred(page: Page) {
  const m = await page.evaluate(() => {
    const box = document.querySelector("[data-chain-scroll]")!;
    const head = box.querySelector("thead")!.getBoundingClientRect();
    const view = box.getBoundingClientRect();
    const row = box.querySelector("tr[data-atm]")!.getBoundingClientRect();
    return { rowMid: row.top + row.height / 2, viewMid: (head.bottom + view.bottom) / 2, rowHeight: row.height, scrolled: box.scrollTop };
  });
  expect(m.scrolled, "the table scrolled to the ATM row").toBeGreaterThan(0);
  expect(Math.abs(m.rowMid - m.viewMid)).toBeLessThanOrEqual(m.rowHeight);
}

test.describe("Option chain v2 at 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await chainReady(page);
      await expect(page).toHaveScreenshot(`option-chain-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("opens on NIFTY and the next expiry (27 Oct is an expiry day) with no click, the ATM row in the middle", async ({ page }) => {
    const calls = await open(page, "light");
    await chainReady(page);
    expect(calls.expiries).toEqual(["NIFTY"]);
    expect(calls.live[0]).toBe("NIFTY@2026-11-03");
    await expect(page.getByLabel("Symbol", { exact: true })).toHaveValue("NIFTY");          // CT-1 C6: the one SymbolPicker
    await expectComboValue(page.getByLabel("Expiry", { exact: true }), "2026-11-03");
    await expectAtmCentred(page);
    // Desktop columns: OI, OI change, Volume, IV, LTP on each side, the strike in the centre; Greeks and bid / ask off.
    const heads = await page.locator("[data-chain-scroll] thead th").evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.textContent?.trim()));   // M63 I-7: a (?) mark may follow a jargon label
    expect(heads).toEqual(["Call OI", "Call OI change", "Call Volume", "Call IV", "Call LTP", "Strike", "Put LTP", "Put IV", "Put Volume", "Put OI change", "Put OI"]);
    await expect(page.locator("[data-max-oi='call']")).toHaveCount(1);
    await expect(page.locator("[data-max-oi='put']")).toHaveCount(1);
    await expect(page.locator("tr[data-strike='25000'] [data-max-oi='call']")).toBeVisible();
    await expect(page.locator("tr[data-strike='24100'] [data-max-oi='put']")).toBeVisible();
    // M96 (Trade AS IS): the PCR card is ATM ± 6 (the banner's and the OI-difference table's), the trading-days DTE card
    // gives way to the banner's calendar-days expiry line.
    for (const [card, value] of [["spot", "24,480.00"], ["futures", "24,531.50"], ["atm", "24,500"], ["pcr", "ATM ± 6"], ["max-pain", "24,500"],
      ["atm-iv", "12.0%"]]) {
      await expect(page.locator(`[data-summary='${card}']`)).toContainText(value);
    }
    await expect(page.locator("[data-summary='dte']")).toHaveCount(0);
  });

  test("Columns adds the Greeks and bid / ask; a new symbol reads its own expiries and chain", async ({ page }) => {
    const calls = await open(page, "light");
    await chainReady(page);
    await page.getByRole("button", { name: "Columns" }).click();
    await page.getByLabel("Greeks (delta, gamma, theta, vega)").check();
    await page.getByLabel("Bid and ask").check();
    await expect(page.locator("[data-chain-scroll] thead")).toContainText("Delta");
    await expect(page.locator("[data-chain-scroll] thead")).toContainText("Bid");
    await page.keyboard.press("Escape");
    // CT-1 C6: the one SymbolPicker over the symbols with options - focused, it suggests the indices; Down moves, Enter picks.
    await page.getByLabel("Symbol", { exact: true }).focus();
    await expect(page.getByRole("listbox", { name: "Suggested symbols" }).getByRole("option")).toHaveCount(4);
    await page.keyboard.press("ArrowDown");
    await expect(page.locator("[role='listbox'] [aria-selected='true'] span").first()).toHaveText("NIFTY");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Symbol", { exact: true })).toHaveValue("BANKNIFTY");
    // Typing searches only the symbols that have options (no server call): a share with options, never one without.
    await page.getByLabel("Symbol", { exact: true }).fill("relia");
    await expect(page.getByRole("listbox", { name: "Matching symbols" }).getByRole("option").first()).toContainText("RELIANCE");
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("Symbol", { exact: true })).toHaveValue("BANKNIFTY");
    await expect.poll(() => calls.live[calls.live.length - 1]).toBe("BANKNIFTY@2026-11-03");
    expect(calls.expiries).toEqual(["NIFTY", "BANKNIFTY"]);
    await chainReady(page);
    await pickOption(page, page.getByLabel("Expiry", { exact: true }), "2026-11-10");
    await expect.poll(() => calls.live[calls.live.length - 1]).toBe("BANKNIFTY@2026-11-10");
  });

  test("the chain refreshes on its own while the page is shown", async ({ page }) => {
    const calls = await open(page, "light");
    await chainReady(page);
    const before = calls.live.length;
    await page.clock.runFor(16_000);
    await expect.poll(() => calls.live.length).toBeGreaterThan(before);
  });
});

test.describe("Option chain v2 at 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await chainReady(page);
      await expect(page).toHaveScreenshot(`option-chain-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("phone: OI and LTP on each side, the strike in the centre, the ATM row in the middle, a row's details on tap", async ({ page }) => {
    await open(page, "dark");
    await chainReady(page);
    await expectPhoneLayout(page);
    const heads = await page.locator("[data-chain-scroll] thead th").evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.textContent?.trim()));   // M63 I-7: a (?) mark may follow a jargon label
    expect(heads).toEqual(["Call OI", "Call LTP", "Strike", "Put LTP", "Put OI"]);
    // The strike column sits in the middle of the screen.
    const strike = await page.locator("[data-chain-scroll] thead th", { hasText: "Strike" }).boundingBox();
    expect(Math.abs(strike!.x + strike!.width / 2 - 195)).toBeLessThanOrEqual(24);
    await expectAtmCentred(page);
    // UX3: the summary is two rows of two (the rest under More), never a sideways strip.
    const summary = page.locator("[data-block='chain-summary']");
    const strip = await summary.evaluate((e) => ({ sw: e.scrollWidth, cw: e.clientWidth }));
    expect(strip.sw).toBeLessThanOrEqual(strip.cw);
    await expect(summary.locator("[data-summary]:visible")).toHaveCount(4);
    await summary.getByRole("button", { name: /More: futures/ }).tap();
    await expect(summary.locator("[data-summary]:visible")).toHaveCount(8);
    await summary.getByRole("button", { name: "Show less" }).tap();
    await page.locator("tr[data-strike='24500']").tap();
    const sheet = page.locator("[data-row-details]");
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole("heading")).toContainText("NIFTY 24,500");
    // M63 I-7: a jargon row header also holds its (?) mark, so its name may end in "What is …?".
    for (const label of ["LTP", "OI change", "IV", "Bid", "Delta", "Vega"]) await expect(sheet.getByRole("rowheader", { name: new RegExp(`^${label}(?: What is ${label}\\?)?$`) })).toBeVisible();
    await sheet.getByRole("button", { name: "Close" }).tap();
    await expect(sheet).toHaveCount(0);
  });

  test("phone 360: the layout rules hold with every column on", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await page.addInitScript(() => { try { localStorage.setItem("atp_option_chain_columns", JSON.stringify({ greeks: true, quotes: true, more: true })); } catch { /* none */ } });
    await open(page, "light");
    await chainReady(page);
    await expectPhoneLayout(page);
    // Wider than the screen: the table opens with the strike in the centre and scrolls sideways inside its box; M96: the
    // strike then stays at the edge it reaches (never over the cells beside it), and a chevron marks the side with more.
    const box = page.locator("[data-chain-scroll]");
    expect(await box.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true);
    const head = page.locator("[data-chain-scroll] thead th", { hasText: "Strike" });
    const opened = await head.boundingBox();
    expect(Math.abs(opened!.x + opened!.width / 2 - 180)).toBeLessThanOrEqual(24);
    await expect(page.locator("[data-scroll-hint='right']")).toBeVisible();
    await box.evaluate((e) => { e.scrollLeft = e.scrollWidth; });
    const boxAt = await box.boundingBox();
    const strike = await head.boundingBox();
    expect(Math.abs(strike!.x - boxAt!.x)).toBeLessThanOrEqual(2);
    await expect(page.locator("[data-scroll-hint='right']")).toHaveCount(0);
    await expect(page.locator("[data-scroll-hint='left']")).toBeVisible();
  });

  test("M96 phone 390: Trade's banner and Net Diff table on top, 44 px targets, nothing cut, no sideways page", async ({ page }) => {
    await open(page, "light");
    await chainReady(page);
    await expectPhoneLayout(page);
    const y = async (sel: string) => (await page.locator(sel).first().boundingBox())!.y;
    expect(await y("[data-block='chain-summary']")).toBeLessThan(await y("[data-block='oi-banner']"));
    expect(await y("[data-block='oi-banner']")).toBeLessThan(await y("[data-block='oi-diff']"));
    expect(await y("[data-block='oi-diff']")).toBeLessThan(await y("[data-chain-scroll]"));
    // M96 (Trade AS IS): the banner - direction colour, the message, classes, PCR ATM ± 6 with its zone, calendar expiry
    const banner = page.locator("[data-block='oi-banner']");
    await expect(banner.locator("[data-banner-tone='up']")).toBeVisible();
    await expect(banner.locator("[data-banner-message]")).toHaveText(/NIFTY OI readings lean up$/);
    await expect(banner.locator("[data-banner-classes]")).toHaveText("Put: Writing · Call: Short covering");
    await expect(banner.locator("[data-banner-pcr]")).toContainText(/PCR \(ATM ± 6\): \d\.\d{2} - /);
    await expect(banner.locator("[data-banner-expiry]")).toHaveText("Expiry: 3 Nov 2026 (7 days left)");
    // Trade's Net Diff: ATM ± 6 steps of 50 (the fixture lists every 100: 24200 .. 24800), a TOTAL row, Dominance words
    const diff = page.locator("[data-block='oi-diff']");
    await expect(diff.locator("[data-oi-diff-row]")).toHaveCount(7);
    await expect(diff.locator("[data-oi-diff-total]")).toContainText("TOTAL");
    await expect(diff.locator("[data-dominance]").first()).toHaveText(/^(Bulls|Bears|Neutral)$/);
    await banner.locator("summary", { hasText: "OI history (Signal)" }).tap();
    await expect(banner.locator("[data-history-signal]").first()).toHaveText("BULLISH (Strong)");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    // tap targets: the OI-difference cells, the chain rows and the strikes chips
    for (const sel of ["[data-oi-diff-cell]", "[data-chain-scroll] tbody tr", "[data-strike-range] button", "[data-oi-history-trade] summary"]) {
      const small = await page.locator(sel).evaluateAll((els) => els.filter((e) => { const r = e.getBoundingClientRect(); return r.height > 0 && r.height < 43.5; }).length);
      expect(small, sel).toBe(0);
    }
    // nothing cut: no value or label in the banner or the OI-difference table is clipped
    const cut = await page.locator("[data-block='chain-summary'] *, [data-block='oi-banner'] [data-banner-tone] > div, [data-block='oi-diff'] td *, [data-block='oi-diff'] th").evaluateAll((els) =>
      els.filter((e) => { const h = e as HTMLElement; return h.children.length === 0 && (h.textContent ?? "").trim() && h.scrollWidth > h.clientWidth + 1; })
        .map((e) => e.textContent));
    expect(cut).toEqual([]);
    // OI in lakhs, two decimals; owner 13 Nov: the unit once above the table, no "L" in the cells
    await expect(page.locator("[data-chain-scroll] tbody td[data-col='call-oi']").first()).toHaveText(/^\d[\d,]*\.\d{2}$/);
    await expect(page.locator("[data-block='oi-diff'] h2 [data-oi-unit]")).toHaveText("OI in lakhs");
  });
});

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`Option chain v2 at ${width}: the analyses below the chain (OC4)`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of THEMES) {
      test(`${theme}: Analysis tabs, Ask AI and My analyses`, async ({ page }) => {
        await open(page, theme);
        await chainReady(page);
        const block = page.locator("[data-analysis-mount='option-chain']");
        await block.scrollIntoViewIfNeeded();
        await expect(block.locator("[data-oc-analyses='ok']")).toBeVisible();
        await expect(block.getByRole("tab", { name: /Support/ })).toBeVisible();
        await expect(block.getByRole("region", { name: "Ask AI about this option chain" })).toHaveCount(0);   // OC-2: in the header's panel
        // CT-1 C10: the one Ask AI in the top bar, then "Ask about this chain" (the page has no button of its own).
        await expect(page.locator("[data-oc-ask-open]")).toHaveCount(0);
        await page.locator("[data-ask-ai-open]").click();
        await expect(page.getByRole("dialog").locator("[data-ask-context]")).toContainText("Expiry 3 Nov");
        await page.getByRole("dialog").locator("[data-ask-option='chain']").click();
        await expect(page.getByRole("dialog").getByRole("region", { name: "Ask AI about this option chain" })).toBeVisible();
        await expect(page.getByRole("dialog").getByRole("button", { name: /highest call OI/ })).toBeVisible();   // 3 suggested questions
        await page.keyboard.press("Escape");
        await expect(block.getByText("Ask AI about this chain and choose Save as My analysis to keep it here.")).toBeVisible();
        await expect(block).not.toContainText(/sample|lookback|manual ltp/i);
        if (width === 390) await expectPhoneLayout(page);
        await expect(block).toHaveScreenshot(`option-chain-v2-analyses-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}

test.describe("Option chain v2 states", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("OI history still building", async ({ page }) => {
    await open(page, "light", { live: (b) => ({ ...b, oi_change: { status: "building", source: "first_reading", since: "2026-10-27T04:58:00+00:00" } }) });
    await chainReady(page);
    await expect(page.getByText("OI change will show after the first few minutes")).toBeVisible();
  });

  test("market closed: the last session's chain", async ({ page }) => {
    await open(page, "light", { marketOpen: false });
    await chainReady(page);
    await expect(page.getByText("Market closed — showing the last session's chain")).toBeVisible();
  });

  test("B2: a closed market is 'as of the last session close' and a spot from our own last close is labelled", async ({ page }) => {
    await open(page, "light", { marketOpen: false, live: (b) => ({ ...b, data_as_of: "2026-10-23T15:30:00+05:30",
      summary: { ...(b.summary as Record<string, unknown>), spot_source: "last_close" } }) });
    await chainReady(page);
    if (process.env.B2_SHOT) await page.screenshot({ path: process.env.B2_SHOT });
    await expect(page.getByText("Market closed — as of the last session close, 23 Oct 2026, 15:30 IST")).toBeVisible();
    await expect(page.getByText("Market closed — showing the last session's chain")).toHaveCount(0);
    await expect(page.locator("[data-summary='spot']")).toContainText("Spot: last close");
    await expectPhoneLayout(page);
  });

  test("no broker (M86): one connect line, no live chain read, the end-of-day chain or its plain line, never sample data", async ({ page }) => {
    const calls = await open(page, "light", { options: { features: { ui_v2: true }, brokers: [] } });
    await expect(page.locator("[data-no-broker='page'] [data-connect-live]")).toHaveCount(1);
    await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
    // No end-of-day options file in this made-up world: one plain line where the chain goes.
    await expect(page.locator("[data-eod-chain='none']")).toBeVisible();
    expect(calls.live).toEqual([]);
    await expect(page.getByText(/sample/i)).toHaveCount(0);
  });

  test("a chain that cannot be read says so in plain words; no developer text anywhere", async ({ page }) => {
    await open(page, "light", { failLive: 502 });
    await expect(page.getByText("We could not load the option chain. We will try again in a moment.")).toBeVisible();
    const words = await page.locator("main").innerText();
    expect(words).not.toMatch(/sample|lookback|cached|Underlying LTP|tilt|RuntimeError|Insufficient data|Stale/i);
  });
});

// ---------------------------------------------------------------- OC5: the Charts view
// The made-up answer of GET /api/option-chain/charts (e2e/fixtures/option_chain_charts.json: the backend's own output for
// the chain above plus 8 made-up stored readings; e2e/fixtures/make_option_chain_charts.py writes it). New baselines
// `option-chain-charts-v2-<width>-<theme>.png`; the chain's own baselines above are unchanged.
const CHARTS = JSON.parse(readFileSync(new URL("./fixtures/option_chain_charts.json", import.meta.url), "utf8")) as Record<string, unknown> & {
  charts: { key: string; title: string; caption: string; x_kind: string }[];
};

async function openCharts(page: Page, theme: "light" | "dark", body: (b: typeof CHARTS) => Record<string, unknown> = (b) => b) {
  // Each chain answer carries its own read time, as the server's does (the charts read again when it changes).
  let reads = 0;
  const calls = await open(page, theme, { live: (b) => ({ ...b, fetched_at: new Date(Date.parse(String(b.fetched_at)) + 1000 * reads++).toISOString() }) });
  const asked: URLSearchParams[] = [];
  await page.route(/\/api(?:\/v1)?\/option-chain\/charts\?/, (route) => {
    asked.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: body(CHARTS) });
  });
  await chainReady(page);
  await page.getByRole("button", { name: "Charts", exact: true }).click();
  await expect(page.locator("[data-oc-charts]")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  return { calls, asked };
}

test.describe("Option chain v2 Charts at 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`Charts looks the same (${theme})`, async ({ page }) => {
      await openCharts(page, theme);
      await expect(page.locator("[data-chart-card]")).toHaveCount(10);
      await expect(page).toHaveScreenshot(`option-chain-charts-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("Charts reads the shared expiry, ATM ± 10 and 5-minute readings; the range and the view are the trader's; it refreshes with the chain", async ({ page }) => {
    const { asked, calls } = await openCharts(page, "light");
    await expect.poll(() => asked.length).toBeGreaterThan(0);
    expect(Object.fromEntries(asked[0])).toEqual({ symbol: "NIFTY", strikes: "10", interval: "5", expiry: "2026-11-03" });
    await expect(page.locator("[data-block='chain']")).toHaveCount(0);
    for (const c of CHARTS.charts) {
      const card = page.locator(`[data-chart-card='${c.key}']`);
      await expect(card.getByRole("heading", { name: c.title })).toBeVisible();
      await expect(card).toContainText(c.caption);
    }
    await expect(page.locator("[data-charts-facts]")).toContainText("7 days to expiry.");
    await pickOption(page, page.getByLabel("Strikes around ATM"), "15");
    await expect.poll(() => asked[asked.length - 1].get("strikes")).toBe("15");
    await page.getByRole("button", { name: "10 min" }).click();
    await expect.poll(() => asked[asked.length - 1].get("interval")).toBe("10");
    const before = asked.length;
    const chainReads = calls.live.length;
    await page.clock.runFor(16_000);
    await expect.poll(() => calls.live.length).toBeGreaterThan(chainReads);
    await expect.poll(() => asked.length).toBeGreaterThan(before);
    // Back to the chain: the table again, the ATM row in the middle.
    await page.getByRole("button", { name: "Chain", exact: true }).click();
    await chainReady(page);
    const words = await page.locator("main").innerText();
    expect(words).not.toMatch(/sample|lookback|RuntimeError|\bbuy\b|\bsell\b/i);
  });

  test("early in the session a chart says it builds through the session, never a number", async ({ page }) => {
    await openCharts(page, "light", (b) => ({ ...b, session: { readings: 0, since: null, note: null },
      charts: b.charts.map((c) => c.x_kind === "time" ? { ...c, state: "building", series: [],
        message: "Builds through the session: a reading of the chain is stored every 5 minutes while the market is open, so this chart fills in as the day goes on." } : c) }));
    await expect(page.locator("[data-chart-building]").first()).toContainText("Builds through the session");
    await expect(page.locator("[data-chart-card='oi_by_strike'] svg")).toBeVisible();
  });
});

test.describe("Option chain v2 Charts at 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`Charts looks the same (${theme})`, async ({ page }) => {
      await openCharts(page, theme);
      // The first chart in view (the controls and the facts sit above it).
      await page.locator("[data-charts-pager]").evaluate((e) => window.scrollTo(0, e.getBoundingClientRect().top + window.scrollY - 120));
      await expect(page).toHaveScreenshot(`option-chain-charts-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("phone: one chart at a time, swiped sideways or moved with the arrows; the layout rules hold", async ({ page }) => {
    await openCharts(page, "dark");
    const deck = page.locator("[data-charts-deck]");
    const pager = page.locator("[data-charts-pager]");
    await expect(pager).toContainText("OI by strike · 1 of 10");
    // Exactly one card is in view.
    const inView = () => page.locator("[data-chart-card]").evaluateAll((cards) => cards.filter((c) => {
      const r = c.getBoundingClientRect();
      return r.left >= -1 && r.right <= window.innerWidth + 1;
    }).length);
    expect(await inView()).toBe(1);
    await page.getByRole("button", { name: "Next chart" }).tap();
    await expect(pager).toContainText("Change in OI by strike · 2 of 10");
    // A swipe: the deck scrolls sideways and snaps to the next card.
    await deck.evaluate((e) => { const cards = e.children as HTMLCollectionOf<HTMLElement>; e.scrollTo({ left: cards[2].offsetLeft - cards[0].offsetLeft }); });
    await expect(pager).toContainText("3 of 10");
    // Every card is exactly the deck's width, so a snapped deck shows one chart at a time.
    const widths = await deck.evaluate((e) => [...e.children].map((c) => Math.round(c.getBoundingClientRect().width) - e.clientWidth));
    expect(widths.every((d) => d === 0)).toBe(true);
    await expectPhoneLayout(page);
  });

  test("phone: the chain view's toggle is a 44 px target in the gap above the chain", async ({ page }) => {
    await open(page, "light");
    await chainReady(page);
    const toggle = await page.locator("[data-view-toggle]").boundingBox();
    const table = await page.locator("[data-chain-scroll]").boundingBox();
    expect(toggle!.height).toBeGreaterThanOrEqual(44);
    expect(toggle!.y + toggle!.height).toBeLessThanOrEqual(table!.y + 1);
    await expectPhoneLayout(page);
  });
});

/** UX-11 (owner, 27 Oct): numbers first - every Option chain card passes the text budget (one short visible line,
 * the rest in Details); the visible-word count is printed for the comparison page. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 option chain text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, "light");
      console.log(`[words] Option chain ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}

// M96 L1 proof pictures (owner: Trade AS IS): the top of the chain view - the summary cards, Trade's banner with its
// History open, and the Net Diff table - at 390 and 1440, light and dark, into docs/design/m96/. Off unless M96_CAPTURE
// is set (CI never writes into docs): `M96_CAPTURE=1 npx playwright test --project=phone e2e/option-chain-v2.e2e.ts -g "M96 capture"`.
for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`M96 capture ${width}`, () => {
    test.skip(!process.env.M96_CAPTURE, "proof pictures only on request (M96_CAPTURE)");
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of THEMES) {
      test(`M96 capture ${width} ${theme}`, async ({ page }) => {
        await open(page, theme);
        await chainReady(page);
        await page.locator("[data-oi-history-trade] summary").click();
        await expect(page.locator("[data-history-signal]").first()).toBeVisible();
        const top = (await page.locator("[data-block='chain-summary']").boundingBox())!;
        const end = (await page.locator("[data-block='oi-diff']").boundingBox())!;
        await page.setViewportSize({ width, height: Math.ceil(end.y + end.height + 24) });
        const t2 = (await page.locator("[data-block='chain-summary']").boundingBox())!;
        const e2 = (await page.locator("[data-block='oi-diff']").boundingBox())!;
        const x = width === 390 ? 0 : t2.x - 16;
        await page.screenshot({ path: `../docs/design/m96/after-trade-port-${width}-${theme}.png`, animations: "disabled",
          clip: { x, y: t2.y - 12, width: width - x, height: e2.y + e2.height - t2.y + 24 } });
        expect(top.y).toBeGreaterThan(0);
      });
    }
  });
}
