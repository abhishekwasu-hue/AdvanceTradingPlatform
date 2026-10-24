import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * TT-8 (owner, 24 Oct) on My positions, behind `ui_v2` with the PAPER account on: the legs of one basket under their group
 * with "Exit this leg" and "Exit the whole group"; exiting the hedge before its sold leg shows a plain warning and asks
 * again (never a block); the whole group closes in one call. Every reply is made-up data (no real account, price or id).
 * Screenshots only when TT8_SHOTS names a folder (the PR's before / after evidence); no baseline is kept here.
 */

const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const SHORT_CE = "NIFTY 24900 CE 27 OCT 26";
const LONG_CE = "NIFTY 25000 CE 27 OCT 26";
const leg = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75, stop_loss: null,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-10-27",
});
const POSITIONS = [leg(301, SHORT_CE, "SHORT", 80), leg(302, LONG_CE, "LONG", 45)];
const GROUPS = { groups: [{ key: "basket:made-up", kind: "basket", name: "Basket", trade_ids: [301, 302], hedge_ids: [302] }] };
const WARNING = `${LONG_CE} protects your sold leg ${SHORT_CE}, which is still open. If you exit it first, that sold leg has no protection and the possible loss is no longer capped. You can still exit it - confirm to go ahead, or use "Exit the whole group" to close the sold legs first.`;

async function open(page: Page) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "positions"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), paper_account: true }, brokers: T.token_status as unknown[] });
  await mockTrading(page, { paperAccount: true });
  const posts: { url: string; body: unknown }[] = [];
  await page.route(api("/positions$"), (r) => r.fulfill({ json: POSITIONS }));
  await page.route(api("/paper/fno/contracts$"), (r) => r.fulfill({ json: { items: [] } }));
  await page.route(api("/paper/fno/positions/groups$"), (r) => r.fulfill({ json: GROUPS }));
  await page.route(api("/paper/fno/positions/\\d+/exit-(leg|group)$"), (r) => {
    const url = r.request().url().replace(/^.*\/api(?:\/v1)?/, "");
    let body: unknown = null;
    try { body = r.request().postDataJSON(); } catch { body = null; }
    posts.push({ url, body });
    if (url.endsWith("/exit-group")) {
      return r.fulfill({ json: { status: "CLOSED", legs: [
        { trade_id: 301, symbol: SHORT_CE, closed: true, exit_price: 72.5, pnl: 562.5, warnings: [] },
        { trade_id: 302, symbol: LONG_CE, closed: true, exit_price: 41, pnl: -300, warnings: [] }] } });
    }
    return r.fulfill({ json: { status: "CLOSED", warning: WARNING, legs: [{ trade_id: 302, symbol: LONG_CE, closed: true, exit_price: 41, pnl: -300, warnings: [] }] } });
  });
  await page.goto("/positions");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-group='basket']")).toBeVisible();
  return posts;
}

const SIZES = [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
for (const size of SIZES) {
  test.describe(`TT-8 basket group ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });

    test("Exit this leg warns on the hedge and asks again; Exit the whole group closes all legs in one call", async ({ page }) => {
      const posts = await open(page);
      const shots = process.env.TT8_SHOTS;
      const group = page.locator("[data-group='basket']");
      await expect(group.getByTestId("exit-group")).toBeEnabled();
      await expect(group.getByTestId("exit-leg")).toHaveCount(2);
      await expect(group.getByTestId("exit-now")).toHaveCount(0);
      if (shots) await page.screenshot({ path: `${shots}/tt8-group-${size.width}.png`, fullPage: false });

      await page.locator("[data-position='302']").getByTestId("exit-leg").click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText("Exit this leg?");
      await expect(dialog.locator("[data-hedge-warning]")).toContainText("You can still exit it");
      await expect(dialog.getByTestId("group-exit-confirm")).toHaveText("Exit this leg anyway");
      if (size.phone) await expectPhoneLayout(page);
      if (shots) await page.screenshot({ path: `${shots}/tt8-hedge-warning-${size.width}.png`, fullPage: false });
      await dialog.getByTestId("group-exit-confirm").click();
      await expect.poll(() => posts).toEqual([{ url: "/paper/fno/positions/302/exit-leg", body: { confirm: true } }]);
      await expect(page.getByText(`${LONG_CE} closed`)).toBeVisible();

      await group.getByTestId("exit-group").click();
      await expect(page.getByRole("dialog")).toContainText("the sold legs first");
      await page.getByTestId("group-exit-confirm").click();
      await expect.poll(() => posts.map((p) => p.url)).toEqual(["/paper/fno/positions/302/exit-leg", "/paper/fno/positions/301/exit-group"]);
      await expect(page.getByText("2 legs closed")).toBeVisible();
    });
  });
}
