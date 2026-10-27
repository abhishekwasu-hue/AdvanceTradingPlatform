import { expect, test, type Page } from "@playwright/test";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * TM Part 1 (docs/design/DATA_HUB.md): the data hub in the browser, with `ui_v2` + `data_hub` on.
 * - The one notice above every page: "reconnecting" while the broker connection reconnects, and "Your <Broker> login
 *   has ended" with ONE "Log in again" button (the same renew panel as the Broker page) when the login ended.
 * - Live ticks on the existing live stream move the watchlist's price, and the page tells the server which symbols it
 *   shows (`PUT /api/live/ticks`).
 * - With the flag off: no notice and nothing new is asked.
 * Every reply is made-up data: no real account, price or id. Screenshots for the PR: set TM1_SHOTS to a folder.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const LISTS = [{ id: 1, name: "Indices", items: [{ symbol: "NIFTY 50", exchange: "NSE" }, { symbol: "NIFTY BANK", exchange: "NSE" }] }];
const SHOTS = process.env.TM1_SHOTS;

type State = "live" | "reconnecting" | "renew_needed";
const MESSAGES: Record<State, string> = {
  live: "Live prices from Upstox.",
  reconnecting: "Live prices from Upstox are reconnecting. Prices may be a few seconds old until then.",
  renew_needed: "Your Upstox login has ended. Log in again to resume live prices.",
};
const status = (state: State) => ({ state, broker: "upstox", broker_name: "Upstox", account_label: "primary", message: MESSAGES[state],
  since: "2026-10-19T05:30:00+00:00", retry_seconds: state === "reconnecting" ? 2 : null });

async function open(page: Page, theme: "light" | "dark", { state = "live", hub = true, brokers = CONNECTED }: { state?: State; hub?: boolean; brokers?: unknown[] } = {}) {
  const puts: unknown[] = [];
  let streams = 0;
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_workspace: true, watchlists: true, ...(hub ? { data_hub: true } : {}) }, brokers });
  await mockCharts(page, { watchlists: LISTS });
  await page.route(/\/live\/hub$/, (r) => r.fulfill({ json: { status: status(state) } }));
  await page.route(/\/live\/ticks$/, (r) => { puts.push(r.request().postDataJSON()); return r.fulfill({ json: { accepted: 2, streamed: 2, status: status(state) } }); });
  await page.route(/\/live\/stream/, (r) => {
    streams++;
    const events = [
      "event: ready\ndata: {\"retry_ms\":2000}\n\n",
      `event: hub\ndata: ${JSON.stringify(status(state))}\n\n`,
      state === "renew_needed" ? "" : `event: ticks\ndata: ${JSON.stringify({ items: [{ symbol: "NIFTY 50", exchange: "NSE", ltp: 46350.25, ts: "2026-10-19T05:30:01+00:00" }] })}\n\n`,
    ].join("");
    return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: events });
  });
  await page.goto("/charts");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; animation: none !important; }" });
  return { puts, streams: () => streams };
}

const rows = (page: Page) => page.getByRole("list", { name: /^Symbols in / }).locator("[data-row]");

for (const width of [1440, 390]) {
  test.describe(`live prices ${width}`, () => {
    test.use({ viewport: { width, height: width === 390 ? 844 : 900 } });
    for (const theme of ["light", "dark"] as const) {
      test(`reconnecting notice and live ticks (${theme})`, async ({ page }) => {
        const { puts } = await open(page, theme, { state: "reconnecting" });
        const notice = page.locator("[data-live-notice='reconnecting']");
        await expect(notice).toBeVisible();
        await expect(notice).toHaveText(MESSAGES.reconnecting);
        if (width === 1440) {
          await expect(rows(page).first()).toContainText("46,350.25");      // the tick moved the watchlist's price
          await expect.poll(() => puts.length).toBeGreaterThan(0);
          expect(JSON.stringify(puts)).toContain("NIFTY 50");
        }
        await page.mouse.move(0, 0);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/tm1-reconnecting-${width}-${theme}.png` });
      });

      test(`renew notice with one button that opens the renew panel (${theme})`, async ({ page }) => {
        await open(page, theme, { state: "renew_needed" });
        const notice = page.locator("[data-live-notice='renew']");
        await expect(notice).toBeVisible();
        await expect(notice).toContainText(MESSAGES.renew_needed);
        await expect(notice.getByRole("button")).toHaveCount(1);
        await expect(notice).not.toContainText(/token|stream|websocket/i);
        await page.mouse.move(0, 0);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/tm1-renew-${width}-${theme}.png` });
        await notice.getByRole("button", { name: "Log in again" }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/tm1-renew-panel-${width}-${theme}.png` });
      });
    }
  });
}

test.describe("live prices: states without a notice", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("live: no notice", async ({ page }) => {
    await open(page, "light", { state: "live" });
    await expect(rows(page).first()).toContainText("46,350.25");
    await expect(page.locator("[data-live-notice]")).toHaveCount(0);
    // UX-7: the header's price-feed dot names the state in words (the tint on a changed price: useTickFlash.test.ts).
    await expect(page.locator("header [data-feed]")).toHaveCount(1);
    await expect(page.locator("header [data-feed]")).toHaveAttribute("aria-label", /^Prices: (Live|Market closed)$/);
  });

  test("UX-7: the header dot says when the broker login has ended", async ({ page }) => {
    await open(page, "light", { state: "renew_needed" });
    await expect(page.locator("header [data-feed='renew']")).toHaveAttribute("aria-label", "Prices: Broker login needed");
  });

  test("one renew prompt: the page's own broker card asks, the notice does not ask again", async ({ page }) => {
    const expired = [{ ...CONNECTED[0], token_status: "EXPIRED", needs_login: true }];
    await open(page, "light", { state: "renew_needed", brokers: expired });
    await expect(page.locator("[data-broker-gate='login-needed']")).toBeVisible();
    await expect(page.locator("[data-live-notice]")).toHaveCount(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/tm1-renew-gated-page-1440-light.png` });
  });

  test("flag off: no notice and no live-ticks registration", async ({ page }) => {
    const { puts } = await open(page, "light", { state: "renew_needed", hub: false });
    await expect(rows(page)).toHaveCount(2);
    await expect(page.locator("[data-live-notice]")).toHaveCount(0);
    await expect(page.locator("header [data-feed]")).toHaveCount(0);                 // UX-7: no dot without live prices
    expect(puts).toEqual([]);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/tm1-before-flag-off-1440-light.png` });
  });
});
