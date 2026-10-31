import { expect, test, type Page, type Route } from "@playwright/test";
import { choose } from "./combobox";
import { expectPhoneLayout } from "./phoneLayout";
import { openSettings } from "./settingsMocks";

/**
 * M64 (owner order 30 Oct + amendment): Settings › Risk limits at /settings/risk-limits (behind `ui_v2`), on made-up
 * replies. The limit form moved here from the Risk page (/risk-management, which keeps today's numbers); the same rules are
 * tested where they now live: one primary action, a value above the admin's ceiling is marked and nothing is sent, a limit
 * that is off stays blank (not set), halving the size beyond the pause step is refused, and when the losses-in-a-row pause
 * ends is saved with the limits. Every check is a switch, off by default; only the checks changed are sent.
 */

const f = (key: string, label: string, unit: string, min: number, max: number, value: number | null, o: { whole?: boolean; optional?: boolean; ceiling?: number } = {}) =>
  ({ key, label, unit, min, max, whole: o.whole ?? false, optional: o.optional ?? false, value, ceiling: o.ceiling ?? null });
const check = (id: string, label: string, group: "main" | "more", fields: unknown[], enabled = false, saved = false) =>
  ({ id, label, help: `${label}: what it does.`, group, enabled, saved, fields });

export const LIMITS = {
  checks: [
    check("max_lots_per_order", "Max lots per order", "main", [f("max_lots_per_order", "Lots", "lots", 1, 100000, 4, { whole: true })], true, true),
    check("max_open_positions", "Max open positions", "main", [f("max_open_positions", "Positions", "positions", 1, 200, null, { whole: true })]),
    check("daily_loss", "Daily loss limit", "main", [f("max_daily_loss_pct", "% of account", "%", 0.1, 5, null, { optional: true, ceiling: 5 }),
      f("daily_loss_rupees", "Rupees", "₹", 1, 1e9, null, { optional: true })]),
    check("max_entries_per_day", "Max new entries per day", "main", [f("max_trades_per_day", "Entries", "entries", 1, 500, null, { whole: true })]),
    check("loss_cooldown", "Cool-down after losses in a row", "main", [f("max_consecutive_losses", "Losses in a row", "losses", 1, 50, null, { whole: true }),
      f("loss_cooldown_minutes", "Pause", "minutes", 1, 1440, null, { whole: true, optional: true })]),
    check("min_reward_risk", "Minimum reward:risk", "main", [f("min_risk_reward", "Reward for 1 risk", "x", 0.1, 10, null)]),
    check("margin_safety", "Margin safety", "main", [f("margin_safety_pct", "Of available margin", "%", 1, 100, null)]),
    check("risk_sizing", "Risk-based sizing", "main", [f("risk_per_trade_pct", "% of account", "%", 0.01, 2, 0.5, { optional: true, ceiling: 2 }),
      f("risk_per_trade_rupees", "Rupees", "₹", 1, 1e9, null, { optional: true })], false, true),
    check("portfolio_risk", "Risk across open positions", "more", [f("max_portfolio_risk_pct", "% of account", "%", 0.1, 10, null, { ceiling: 10 })]),
    check("stop_cooldown", "Cool-down after a stop-out", "more", [f("stop_cooldown_minutes", "Wait", "minutes", 1, 1440, null, { whole: true })]),
    check("drawdown", "Drawdown steps", "more", [f("dd_level_1_pct", "Halve size at", "%", 0.1, 100, null, { optional: true }),
      f("dd_level_2_pct", "Pause at", "%", 0.1, 25, null, { optional: true, ceiling: 25 })]),
    check("event_rules", "Market event days", "more", [f("event_size_cut_pct", "Size cut when an event names none", "%", 1, 100, null, { optional: true })]),
  ],
  capital: null, loss_streak_pause: "next_session", on: 1, exits: "Exits are never checked: stops, targets and Exit now always work.",
  page: "/settings/risk-limits", paper: {},
};

interface Put { body: Record<string, unknown> }

async function open(page: Page): Promise<Put[]> {
  const puts: Put[] = [];
  await openSettings(page, "/settings/risk-limits", { routes: async (p) => {
    await p.route(/\/api(?:\/v1)?\/risk-settings\/limits$/, async (route: Route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        puts.push({ body });
        return route.fulfill({ json: { ...LIMITS, on: 2, note: null } });
      }
      return route.fulfill({ json: LIMITS });
    });
  } });
  await expect(page.locator("[data-testid='risk-limits-main']")).toBeVisible();
  return puts;
}

const row = (page: Page, id: string) => page.locator(`[data-check='${id}']`);
const toggle = (page: Page, id: string) => row(page, id).getByRole("switch");
const note = (page: Page) => page.locator("[data-note='risk-limits-note']");

test.describe("Settings › Risk limits 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("every check is a switch, off unless saved; one primary action sends only what changed", async ({ page }) => {
    const puts = await open(page);
    await expect(page.locator("[data-testid='risk-limits-count']")).toHaveText("1 of 12 on");
    await expect(toggle(page, "max_lots_per_order")).toHaveAttribute("aria-checked", "true");
    await expect(toggle(page, "daily_loss")).toHaveAttribute("aria-checked", "false");
    await expect(row(page, "risk_sizing")).toContainText("Saved: 0.5%");                     // switched off, value kept
    const primaries = page.locator("main .bg-brand:is(a, button):not([data-subtab])");
    await expect(primaries).toHaveCount(1);
    await expect(primaries).toHaveText("Save risk limits");
    await toggle(page, "daily_loss").click();
    await row(page, "daily_loss").locator("[data-field='max_daily_loss_pct']").fill("2.5");
    await primaries.click();
    await expect(note(page)).toContainText("Saved.");
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({ checks: { daily_loss: { enabled: true, values: { max_daily_loss_pct: 2.5, daily_loss_rupees: null } } },
      capital: null, loss_streak_pause: "next_session" });
  });

  test("a value above the admin's ceiling is marked and nothing is sent", async ({ page }) => {
    const puts = await open(page);
    await toggle(page, "risk_sizing").click();
    await row(page, "risk_sizing").locator("[data-field='risk_per_trade_pct']").fill("3");
    await page.getByRole("button", { name: "Save risk limits" }).click();
    await expect(note(page)).toHaveText("Risk-based sizing: the most allowed is 2 (your admin's ceiling). Ask your admin to change it.");
    expect(puts).toHaveLength(0);
  });

  test("a limit that is off stays blank (not set); switching it on needs a value, else nothing is sent", async ({ page }) => {
    const puts = await open(page);
    await expect(row(page, "max_entries_per_day").locator("input")).toHaveCount(0);           // off: no value shown, nothing set
    await toggle(page, "max_entries_per_day").click();
    const field = row(page, "max_entries_per_day").locator("[data-field='max_trades_per_day']");
    await expect(field).toHaveValue("");
    await expect(field).toHaveAttribute("placeholder", "Value");
    await page.getByRole("button", { name: "Save risk limits" }).click();
    await expect(note(page)).toHaveText("Max new entries per day: enter a value to switch it on.");
    expect(puts).toHaveLength(0);
    await field.fill("10");
    await page.getByRole("button", { name: "Save risk limits" }).click();
    await expect(note(page)).toContainText("Saved.");
    expect(puts[0].body.checks).toEqual({ max_entries_per_day: { enabled: true, values: { max_trades_per_day: 10 } } });
  });

  test("halving the size at a larger drawdown than the pause is marked and nothing is sent; with no pause step it saves", async ({ page }) => {
    const puts = await open(page);
    await toggle(page, "drawdown").click();
    await row(page, "drawdown").locator("[data-field='dd_level_1_pct']").fill("12");
    await row(page, "drawdown").locator("[data-field='dd_level_2_pct']").fill("10");
    await page.getByRole("button", { name: "Save risk limits" }).click();
    await expect(note(page)).toHaveText("Drawdown steps: halve the size at a drawdown no larger than the one that pauses new entries (10%).");
    expect(puts).toHaveLength(0);
    await row(page, "drawdown").locator("[data-field='dd_level_2_pct']").fill("");
    await page.getByRole("button", { name: "Save risk limits" }).click();
    await expect(note(page)).toContainText("Saved.");
    expect(puts[0].body.checks).toEqual({ drawdown: { enabled: true, values: { dd_level_1_pct: 12, dd_level_2_pct: null } } });
  });

  test("when the losses-in-a-row pause ends is saved with the limits; the default is the next session", async ({ page }) => {
    const puts = await open(page);
    await toggle(page, "loss_cooldown").click();
    await row(page, "loss_cooldown").locator("[data-field='max_consecutive_losses']").fill("3");
    const pause = row(page, "loss_cooldown").locator("[data-combobox]");
    await expect(pause).toHaveAttribute("data-combobox", "next_session");
    await choose(page, pause, "until_resume");
    await page.getByRole("button", { name: "Save risk limits" }).click();
    await expect(note(page)).toContainText("Saved.");
    expect(puts[0].body).toMatchObject({ loss_streak_pause: "until_resume",
      checks: { loss_cooldown: { enabled: true, values: { max_consecutive_losses: 3, loss_cooldown_minutes: null } } } });
  });
});

test.describe("Settings › Risk limits 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
    await open(page);
    await expectPhoneLayout(page);
  });
});
