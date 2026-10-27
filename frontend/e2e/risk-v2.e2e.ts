import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading, type TradingMocks, type Writes } from "./tradingMocks";

/**
 * UI2-9a (UI v2 proposal §2 row 17, §8, §9, §13 UI-11): Risk limits with the `ui_v2` flag on.
 * - Baselines `risk-v2-<width>-<theme>.png` at 1440, 390 and 360. Today's Risk Management page has no baseline of its
 *   own, and with the flag off it renders as before.
 * - Today's limit in rupees and the guardian's state; the one primary action, "Save limits", sending today's request;
 *   a value above the platform ceiling stopped next to its field; removing an event behind a confirm that names what
 *   changes.
 * Every reply is hand-written, made-up data (e2e/fixtures/trading_v2.json). After an intended change:
 * `npx playwright test --project=phone e2e/risk-v2.e2e.ts --update-snapshots`, check the new images, commit.
 */

async function open(page: Page, theme: "dark" | "light", setup: TradingMocks & { flag?: boolean } = {}): Promise<Writes> {
  const { flag = true, ...mocks } = setup;
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: flag ? (T.features as Record<string, boolean>) : undefined, brokers: T.token_status as unknown[] });
  const writes = await mockTrading(page, mocks);
  await page.goto("/risk-management");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return writes;
}

const THEMES = ["dark", "light"] as const;
const PHONES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];

for (const size of PHONES) {
  test.describe(`v2 risk ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-page='risk-v2']")).toBeVisible();
        await expect(page).toHaveScreenshot(`risk-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
      await open(page, "dark");
      await expect(page.locator("[data-page='risk-v2']")).toBeVisible();
      await expectPhoneLayout(page);
    });
  });
}

test.describe("v2 risk 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='risk-v2']")).toBeVisible();
      await expect(page).toHaveScreenshot(`risk-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("today: the daily loss limit in rupees, the paper limit in use and the guardian's state", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("[data-shell='v2'] header").first()).toContainText("Risk limits");
    const today = page.locator("[data-block='today']");
    await expect(today.locator("[data-tile='daily-limit']")).toContainText("₹15,000");
    await expect(today.locator("[data-tile='daily-limit']")).toContainText("3.0% of ₹5,00,000 capital");
    await expect(today.locator("[data-tile='per-trade']")).toContainText("₹5,000");
    await expect(today.locator("[data-tile='paper-used']")).toContainText("39%");
    await expect(today.locator("[data-tile='paper-used']")).toContainText("₹5,775 of ₹15,000 today");
    await expect(today.locator("[data-mode='PAPER']")).toContainText("Normal size");
    await expect(today.locator("[data-mode='PAPER']")).toContainText("Waiting after a stop-out: HDFCBANK (12 min left)");
    await expect(today.locator("[data-mode='LIVE']")).toContainText("Size cut to 50%");
    await expect(today).toContainText("Exits are never blocked.");
  });

  test("one primary action: 'Save limits' sends today's request with the changed value", async ({ page }) => {
    const writes = await open(page, "light");
    const primaries = page.locator("main .bg-brand:is(a, button):not([data-subtab])");
    await expect(primaries).toHaveCount(1);
    await expect(primaries).toHaveText("Save limits");
    await page.getByLabel("Daily loss limit (% of capital)").fill("2.5");
    await primaries.click();
    await expect(page.locator("[data-note='save']")).toHaveText("Saved. Every paper and live entry now uses these limits and guardian rules.");
    const put = writes.calls.find((c) => c.method === "PUT" && c.url === "/risk-settings");
    expect(put?.body).toEqual({ ...(T.risk_settings as object), max_daily_loss_pct: 2.5, loss_streak_pause: "next_session" });
  });

  test("a value above the platform ceiling is marked next to its field and nothing is sent", async ({ page }) => {
    const writes = await open(page, "light");
    const field = page.getByLabel("Risk per trade (% of capital)");
    await field.fill("3");
    await field.blur();
    await expect(page.locator("[data-field='risk_per_trade_pct'] [role='alert']")).toHaveText("The most allowed is 2.");
    await page.getByRole("button", { name: "Save limits" }).click();
    await expect(page.locator("[data-note='save']")).toContainText("Nothing was saved.");
    expect(writes.calls.filter((c) => c.url === "/risk-settings")).toHaveLength(0);
  });

  test("halving the size at a larger drawdown than the pause is marked and nothing is sent", async ({ page }) => {
    const writes = await open(page, "light");
    await page.getByLabel("Halve the size at a drawdown of (%)").fill("12");
    await page.getByRole("button", { name: "Save limits" }).click();
    await expect(page.locator("[data-field='dd_level_1_pct'] [role='alert']"))
      .toHaveText("Halve the size at a drawdown no larger than the one that pauses new entries (10%).");
    expect(writes.calls.filter((c) => c.url === "/risk-settings")).toHaveLength(0);
  });

  test("with the pause turned off (0%), any halving level is fine: a lower daily loss limit saves", async ({ page }) => {
    const writes = await open(page, "light", { riskSettings: { dd_level_1_pct: 5, dd_level_2_pct: 0 } });
    await page.getByLabel("Daily loss limit (% of capital)").fill("2");
    await page.getByRole("button", { name: "Save limits" }).click();
    await expect(page.locator("[data-note='save']")).toHaveText("Saved. Every paper and live entry now uses these limits and guardian rules.");
    await expect(page.locator("[data-field='dd_level_1_pct'] [role='alert']")).toHaveCount(0);
    const put = writes.calls.find((c) => c.method === "PUT" && c.url === "/risk-settings");
    expect(put?.body).toEqual({ ...(T.risk_settings as object), dd_level_1_pct: 5, dd_level_2_pct: 0, max_daily_loss_pct: 2, loss_streak_pause: "next_session" });
  });

  test("removing an event: a neutral button, then a confirm that names what changes", async ({ page }) => {
    const writes = await open(page, "light");
    const row = page.locator("[data-event='32']");
    await expect(row).toContainText("Fri 23 Oct 2026");
    await expect(row).toContainText("No new entries");
    await expect(row).toContainText("RELIANCE · 09:15 to 11:00 · Quarterly results");
    await expect(page.locator("[data-event='31']")).toContainText("Size cut by 50%");
    const remove = row.getByRole("button", { name: "Remove" });
    await expect(remove).not.toHaveClass(/(^|\s)(bg-brand|text-down|bg-down)(\s|$)/);
    await remove.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Remove this market event?");
    await expect(dialog).toContainText("New entries in RELIANCE will no longer be blocked on 23 Oct 2026.");
    await expect(dialog).toContainText("You can add it again later.");
    const described = await dialog.getAttribute("aria-describedby");
    await expect(page.locator(`[id="${described}"]`)).toContainText("will no longer be blocked");     // read with the title
    await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(writes.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
    await remove.click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove event" }).click();
    await expect(page.locator("[data-event='32']")).toHaveCount(0);
    expect(writes.calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual(["/risk-guardian/events/32"]);
  });

  test("organisation limits and recent checks in plain words", async ({ page }) => {
    await open(page, "light");
    const limits = page.locator("[data-block='org-limits']");
    await expect(limits.locator("[data-limit='41']")).toContainText("Daily loss₹15,000");
    await expect(limits.locator("[data-limit='42']")).toContainText("One symbol: RELIANCE");
    await expect(limits.locator("[data-limit='43']")).toContainText("Platform");
    await expect(limits.locator("[data-limit='43']").getByRole("button", { name: "Remove" })).toHaveCount(0);
    const checks = page.locator("[data-block='checks']");
    await expect(checks.locator("[data-check='502']")).toContainText("Warning");
    await expect(checks.locator("[data-check='501']")).toContainText("Passed");
    for (const gone of ["Risk Guardian status", "Save risk settings", "(R4)", "(P2)", "Phase", "Sample", "recommended"]) {
      await expect(page.locator("main").getByText(gone, { exact: false }), gone).toHaveCount(0);
    }
  });
});

// CP6-15: when the losses-in-a-row pause ends - the setting, the paused note and "Resume after losses in a row".
const PAUSED = { mode: "until_resume", mode_label: "Pause until I resume", limit: 3, losses_in_a_row: 3, paused: true, can_resume: true,
                 ends: "when you resume", resumed_at: "2026-10-16T05:00:00+00:00" };

test.describe("v2 risk: the losses-in-a-row pause (CP6-15)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the choice is saved with the other limits; the default is the next session", async ({ page }) => {
    const writes = await open(page, "light");
    const choice = page.locator("[data-field='loss_streak_pause']");
    await expect(choice).toContainText("After the losses-in-a-row limit");
    await expect(choice.getByLabel("Pause until the next session")).toBeChecked();
    await expect(choice).toContainText("A backtest cannot resume, so there the pause lasts to the end of the test.");
    await expect(page.locator("[data-note='loss-streak']")).toHaveCount(0);                // nothing paused: no note
    await choice.getByLabel("Pause until I resume").check();
    await page.getByRole("button", { name: "Save limits" }).click();
    await expect(page.locator("[data-note='save']")).toContainText("Saved.");
    const put = writes.calls.find((c) => c.method === "PUT" && c.url === "/risk-settings");
    expect(put?.body).toEqual({ ...(T.risk_settings as object), loss_streak_pause: "until_resume" });
  });

  test("while paused: the note says when it ends and that exits keep working; Resume is a neutral button", async ({ page }) => {
    const writes = await open(page, "light", { riskSettings: { loss_streak_pause: "until_resume" }, lossStreak: PAUSED });
    const note = page.locator("[data-note='loss-streak']");
    await expect(note).toContainText("New entries are paused after 3 losses in a row (your limit is 3). They stay paused until you resume them. Exits keep working.");
    await expect(note).toContainText("Last resumed 16 Oct 2026, 10:30");
    const resume = page.getByRole("button", { name: "Resume after losses in a row" });
    await expect(resume).not.toHaveClass(/(^|\s)(bg-brand|text-down|bg-down)(\s|$)/);
    await expect(page.locator("main .bg-brand:is(a, button):not([data-subtab])")).toHaveCount(1);                // still one primary action
    await resume.click();
    await expect(page.locator("[data-note='save']")).toHaveText("Resumed. New entries are allowed again; only losses from now on count towards your limit.");
    await expect(note).toHaveCount(0);
    const risk = writes.calls.filter((c) => c.url.startsWith("/risk-settings"));
    expect(risk.map((c) => `${c.method} ${c.url}`)).toEqual(["POST /risk-settings/loss-streak/resume"]);   // resuming changes no limit
  });

  test("paused until the next session: the note, and no resume button", async ({ page }) => {
    await open(page, "light", { lossStreak: { ...PAUSED, mode: "next_session", mode_label: "Pause until the next session", can_resume: false,
                                              ends: "at the next session", resumed_at: null } });
    await expect(page.locator("[data-note='loss-streak']")).toContainText("They start again at the next session. Exits keep working.");
    await expect(page.getByRole("button", { name: "Resume after losses in a row" })).toHaveCount(0);
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("today's risk page, exactly as before", async ({ page }) => {
    await open(page, "light", { flag: false });
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-page='risk-v2']")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Risk Management", exact: true })).toBeVisible();
  });
});

/** UX-11 (owner, 27 Oct): numbers first - every <Page> card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`risk text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, "light");
      console.log(`[words] risk ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
