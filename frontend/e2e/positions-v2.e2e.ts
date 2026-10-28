import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading, type TradingMocks, type Writes } from "./tradingMocks";
import { warnOnManySolidBrand } from "./solidBrand";

// Chart + colour order Part 3 (CC-8): a warning annotation, never a failure, when a screen shows more than one solid
// brand button (the one main action per screen).
test.afterEach(async ({ page }, testInfo) => { await warnOnManySolidBrand(page, testInfo); });

/**
 * UI2-9a (UI v2 proposal §2 row 14, §8, §9, §13 UI-11): My positions with the `ui_v2` flag on.
 * - Baselines `positions-v2-<width>-<theme>.png` at 1440, 390 and 360. The old `positions-*` baselines (mobile.e2e.ts)
 *   are untouched: with the flag off the old page renders as before.
 * - Exits (ADR-0004): "Exit now" keeps today's confirm and endpoint, is never disabled, and stays reachable while the
 *   broker needs a login or is not connected at all.
 * Every reply is hand-written, made-up data (e2e/fixtures/trading_v2.json). After an intended change:
 * `npx playwright test --project=phone e2e/positions-v2.e2e.ts --update-snapshots`, check the new images, commit.
 */

interface Setup extends TradingMocks {
  flag?: boolean;
  broker?: "connected" | "login" | "none";
  /** /broker/token-status never answers (the broker check hangs). */
  hangBroker?: boolean;
  /** M45 MB-7: `multi_account` on, and /positions labels two paper rows with the trader's own simulated accounts. */
  accounts?: boolean;
}

async function open(page: Page, theme: "dark" | "light", setup: Setup = {}): Promise<Writes> {
  const { flag = true, broker = "connected", hangBroker = false, accounts = false, ...mocks } = setup;
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const brokers = broker === "none" ? [] : broker === "login" ? T.token_status_login : T.token_status;
  const features = { ...(T.features as Record<string, boolean>), ...(accounts ? { multi_account: true } : {}) };
  await mockApi(page, { features: flag ? features : undefined, brokers: brokers as unknown[] });
  const writes = await mockTrading(page, mocks);
  if (accounts) {
    const labelled = (T.positions as { id: number }[]).map((p) => ({ ...p, account_label: p.id === 201 ? "Family" : p.id === 202 ? "Main" : null }));
    await page.route(/\/api(?:\/v1)?\/positions$/, (r) => (r.request().method() === "GET" ? r.fulfill({ json: labelled }) : r.fallback()));
  }
  if (hangBroker) await page.route(/\/api(?:\/v1)?\/broker\/token-status$/, () => new Promise(() => undefined));
  await page.goto("/positions");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  if (hangBroker) {
    await expect(page.locator("main")).toBeVisible();
    return writes;                                          // the gate stays "checking" (aria-busy) for good
  }
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return writes;
}

const THEMES = ["dark", "light"] as const;
const PHONES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];

for (const size of PHONES) {
  test.describe(`v2 positions ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-page='positions-v2']")).toBeVisible();
        await expect(page).toHaveScreenshot(`positions-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    test("before the broker login: the gate, the exits and the paper account look the same", async ({ page }) => {
      test.skip(size.width !== 390, "one baseline of the gated state");
      await open(page, "light", { broker: "login", paperAccount: true });
      await expect(page.locator("[data-block='exits']")).toBeVisible();
      await expect(page.getByTestId("paper-account")).toBeVisible();
      await expect(page).toHaveScreenshot("positions-v2-login-390-light.png", { animations: "disabled", fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });

    for (const state of [{ name: "connected" }, { name: "empty", empty: true }, { name: "login", broker: "login" as const, paperAccount: true }]) {
      test(`${state.name}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
        await open(page, "dark", state);
        await expect(page.locator("[data-page='positions-v2']")).toBeVisible();
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("v2 positions 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='positions-v2']")).toBeVisible();
      await expect(page).toHaveScreenshot(`positions-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("the title, the tiles and every open position with its levels, price and P&L", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("[data-shell='v2'] header").first()).toContainText("My trades");      // UX3-9: My trades › Positions
    await expect(page.getByRole("heading", { level: 1, name: "My positions" })).toBeVisible();
    const tiles = page.locator("[data-block='summary']");
    await expect(tiles.locator("[data-tile='open']")).toContainText("3");
    await expect(tiles.locator("[data-tile='open']")).toContainText("2 paper · 1 live");
    await expect(tiles.locator("[data-tile='open-pnl']")).toContainText("+₹3,720");
    await expect(tiles.locator("[data-tile='booked']")).toContainText("+₹660");
    await expect(tiles.locator("[data-tile='risk']")).toContainText("₹5,970");
    await expect(tiles.locator("[data-tile='risk']")).toContainText("1.2% of capital");

    const rows = page.locator("[data-block='positions'] [data-position]");
    await expect(rows).toHaveCount(3);
    const option = page.locator("[data-position='201']");
    // UX-15 (owner, 28 Oct): the broker standard - Qty / Avg. / LTP, the levels as chips.
    await expect(option.locator("[data-qty-avg]")).toContainText("Qty +150 (2 lots)");
    await expect(option.locator("[data-qty-avg]")).toContainText("Avg. 121.80");
    await expect(option.locator("[data-qty-avg]")).toContainText("LTP 142.60");
    await expect(option).not.toContainText("Buy ·");
    for (const l of ["On NIFTY 50 (Long)", "Stop 24,700.00", "Target 24,950.00", "Premium floor 100.00"]) {
      await expect(option.locator("[data-levels]")).toContainText(l);
    }
    await expect(option).toContainText("+₹3,120");
    await expect(option.getByRole("link", { name: "Chart" })).toHaveAttribute("href", "/charts/NIFTY%2050");
    const short = page.locator("[data-position='202']");
    await expect(short).toContainText("Short");
    await expect(short).toContainText("+₹1,000");
    const live = page.locator("[data-position='203']");
    await expect(live).toContainText("Live");
    await expect(live).toContainText("−₹400");
    await expect(live).toContainText("Close a live position in your broker's app.");

    const closed = page.locator("[data-block='history'] [data-closed]");
    await expect(closed).toHaveCount(3);
    await expect(closed.first()).toContainText("HDFCBANK");
    await expect(page.locator("[data-closed='191']")).toContainText("Charges ₹52.10 (from your contract note)");
    await expect(page.locator("[data-closed='191']")).toContainText("Entered before the level held.");
  });

  test("Exit now: neutral, enabled, only on paper positions; today's confirm first, then today's endpoint", async ({ page }) => {
    const writes = await open(page, "light");
    const exits = page.getByTestId("exit-now");
    await expect(exits).toHaveCount(2);
    for (const b of await exits.all()) {
      await expect(b).toBeEnabled();
      await expect(b).not.toHaveClass(/(^|\s)(bg-brand|text-down|bg-down)(\s|$)/);
    }
    await expect(page.locator("[data-position='203']").getByTestId("exit-now")).toHaveCount(0);

    // Cancel: nothing is sent.
    let asked = "";
    page.once("dialog", (d) => { asked = d.message(); void d.dismiss(); });
    await page.locator("[data-position='201']").getByTestId("exit-now").click();
    await expect.poll(() => asked).toBe("Exit LONG NIFTY 24800 CE x150 (PAPER) now, at the current price?");
    expect(writes.calls.filter((c) => c.url.includes("exit-now"))).toHaveLength(0);

    // Confirm: today's endpoint, and the result in plain words.
    page.once("dialog", (d) => void d.accept());
    await page.locator("[data-position='201']").getByTestId("exit-now").click();
    await expect(page.locator("[data-note='action']")).toHaveText("NIFTY 24800 CE: Closed (Exit now) at 142.60: +₹3,120.00.");
    expect(writes.calls.filter((c) => c.method === "POST" && c.url === "/positions/201/exit-now")).toHaveLength(1);
  });

  test("one card style, no primary button of its own, and a glossary '?'", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("main .bg-brand:is(a, button):not([data-subtab])")).toHaveCount(0);
    await page.getByRole("button", { name: "What is Risk at stops?" }).click();
    await expect(page.getByRole("note")).toContainText("What you would lose if every open position reached its stop now.");
    const main = page.locator("main");
    for (const gone of ["Sample", "registry", "adapter", "Total Trades", "Log in from the Account tab", "recommended"]) {
      await expect(main.getByText(gone, { exact: false }), gone).toHaveCount(0);
    }
  });

  test("nothing open: the friendly empty state with one action", async ({ page }) => {
    await open(page, "light", { empty: true });
    const block = page.locator("[data-block='positions']");
    await expect(block).toContainText("All positions are closed. Find a setup or let Autopilot trade for you.");
    await expect(block.getByRole("link", { name: "Find setups" })).toHaveAttribute("href", "/screener");
    await expect(page.locator("[data-block='history']")).toContainText("Closed trades show here.");
  });

  test("before the day's broker login: 'Log in to Upstox', and the paper exits still work", async ({ page }) => {
    const writes = await open(page, "light", { broker: "login" });
    const gate = page.locator("[data-broker-gate='login-needed']");
    await expect(gate.getByRole("button", { name: "Log in to Upstox" })).toBeVisible();
    await expect(gate).toContainText("Your Upstox session has ended. Log in to see live prices.");
    await expect(gate).not.toContainText("your positions");                     // they are listed right below
    const card = page.locator("[data-block='exits']");
    await expect(card.locator("[data-chip='no-price']").first()).toContainText("Live prices: connect your broker");   // UX-15: a chip, not a sentence
    await expect(card.locator("[data-position]")).toHaveCount(3);
    await expect(card).not.toContainText("+₹3,120");                          // no live price, so no P&L
    const exit = card.locator("[data-position='202']").getByTestId("exit-now");
    await expect(exit).toBeEnabled();
    page.once("dialog", (d) => void d.accept());
    await exit.click();
    await expect.poll(() => writes.calls.filter((c) => c.url === "/positions/202/exit-now").length).toBe(1);
  });

  test("no broker stored: 'Connect your broker', the paper exits and 'Pause new PAPER entries' still work", async ({ page }) => {
    const writes = await open(page, "light", { broker: "none", paperAccount: true });
    await expect(page.locator("[data-broker-gate='no-broker']")).toBeVisible();
    await expect(page.locator("[data-block='exits'] [data-testid='exit-now']")).toHaveCount(2);
    await expect(page.locator("[data-block='summary']")).toHaveCount(0);
    await expect(page.locator("[data-block='exits'] [data-col]")).toHaveCount(0);             // no price or P&L columns
    const pause = page.getByRole("button", { name: "Pause new PAPER entries" });
    await expect(pause).toBeEnabled();
    await pause.click();
    await expect.poll(() => writes.calls.filter((c) => c.method === "POST" && c.url === "/paper/pause").length).toBe(1);
  });

  test("before the day's broker login: 'Pause new PAPER entries' is there, and no price or P&L is shown", async ({ page }) => {
    await open(page, "light", { broker: "login", paperAccount: true });
    await expect(page.getByTestId("paper-account")).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause new PAPER entries" })).toBeEnabled();
    const card = page.locator("[data-block='exits']");
    await expect(card.locator("[data-col]")).toHaveCount(0);
    await expect(card).not.toContainText("P&L");
  });

  test("the broker check never answers: the exits show as soon as the positions load", async ({ page }) => {
    await open(page, "light", { hangBroker: true });
    await expect(page.locator("[data-broker-gate='loading']")).toBeVisible();
    await expect(page.locator("[data-block='exits'] [data-position]")).toHaveCount(3);
    await expect(page.locator("[data-block='exits'] [data-testid='exit-now']").first()).toBeEnabled();
  });

  test("positions cannot be read before the broker login: the error is shown, not an empty list", async ({ page }) => {
    await open(page, "light", { broker: "login", positionsFail: true });
    const card = page.locator("[data-block='exits']");
    await expect(card).toContainText("We could not load your positions. We will try again in a moment.");
    await expect(card).not.toContainText("All positions are closed.");
  });

  test("live prices cannot be read: every row and its Exit now stay, with no price made up", async ({ page }) => {
    await open(page, "light", { exposureFail: true });
    await expect(page.locator("[data-block='positions'] [data-position]")).toHaveCount(3);
    await expect(page.getByTestId("exit-now")).toHaveCount(2);
    for (const b of await page.getByTestId("exit-now").all()) await expect(b).toBeEnabled();
    const row = page.locator("[data-position='201']");
    await expect(row.locator("[data-col='pnl'] [data-chip='no-price']")).toContainText("Waiting for a live price");   // UX-15: one chip, no dashes
    await expect(row.locator("[data-qty-avg]")).not.toContainText("LTP");
    await expect(row).not.toContainText("+₹3,120");
    await expect(page.locator("[data-tile='open-pnl']")).toContainText("–");
  });
});

test.describe("M45 MB-7 accounts (multi_account on)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("each labelled row shows its account; the filter narrows the list and every Exit now stays", async ({ page }) => {
    await open(page, "light", { accounts: true });
    const rows = page.locator("[data-block='positions'] [data-position]");
    await expect(rows).toHaveCount(3);
    await expect(page.locator("[data-position='201'] [data-testid='account-badge']")).toHaveText("Family");
    await expect(page.locator("[data-position='202'] [data-testid='account-badge']")).toHaveText("Main");
    await expect(page.locator("[data-position='203'] [data-testid='account-badge']")).toHaveCount(0);
    const filter = page.getByTestId("account-filter").getByLabel("Account", { exact: true });
    await expect(filter).toHaveValue("");
    expect((await filter.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await filter.selectOption("Main");
    await expect(rows).toHaveCount(1);
    await expect(page.locator("[data-position='202'] [data-testid='exit-now']")).toBeEnabled();
    await filter.selectOption("");
    await expect(rows).toHaveCount(3);
  });

  test("no labelled row (today's answer): no filter, no badge", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("[data-block='positions'] [data-position]")).toHaveCount(3);
    await expect(page.getByTestId("account-filter")).toHaveCount(0);
    await expect(page.getByTestId("account-badge")).toHaveCount(0);
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("today's positions page, exactly as before", async ({ page }) => {
    await open(page, "light", { flag: false });
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-page='positions-v2']")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Positions", exact: true })).toBeVisible();
  });
});
