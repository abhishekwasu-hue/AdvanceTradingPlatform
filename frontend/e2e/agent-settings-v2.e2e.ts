import { expect, test, type Page } from "@playwright/test";
import { openAdmin, type Theme } from "./adminMocks";
import { installDiagnostics } from "./diagnostics";

/**
 * SRV2 P2 + P4 ("Pending job purn kr" 21 Nov): the owner's "My trading agent: settings and positions" card in Admin -> Controls
 * -> Safety. Editing one field enables "Save NIFTY (1)" and sends only that field; the positions list is read-only. Another admin
 * (403) sees no card.
 * - Baselines (NEW): `agent-settings-v2-{1440,390}-{light,dark}.png` (the card alone).
 * Every reply is made up (generic field names, not the bot's). After an intended change:
 * `npx playwright test --project=phone e2e/agent-settings-v2.e2e.ts --update-snapshots`.
 */
installDiagnostics();
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

const FIELDS = [
  { key: "f_on", label: "Entries on for this symbol", group: "entry", type: "bool", value: true, saved: true },
  { key: "f_depth", label: "Strike depth", group: "entry", type: "int", unit: "strikes", strikes: true, value: 2, saved: false, help: "How far in the money the sold leg is." },
  { key: "f_mode", label: "Entry mode", group: "entry", type: "choice", choices: ["Mode A", "Mode B"], value: "Mode A", saved: false },
  { key: "f_exit", label: "Exit check every (seconds)", group: "exit", type: "int", value: 15, saved: false },
];
const POSITIONS = [
  { id: 1, symbol: "NIFTY", direction: "bullish", status: "OPEN", opened_at: "2026-11-21T09:45:00+05:30", closed_at: null, pnl: 1250, exit_reason: null, mode: "PAPER",
    legs: [{ strike: 24000, option_type: "PE", side: "SELL", qty: 75, entry_price: 80, exit_price: null },
      { strike: 23900, option_type: "PE", side: "BUY", qty: 75, entry_price: 50, exit_price: null }] },
  { id: 2, symbol: "BANKNIFTY", direction: "bearish", status: "CLOSED", opened_at: "2026-11-20T10:00:00+05:30", closed_at: "2026-11-20T11:00:00+05:30", pnl: -800,
    exit_reason: "Stop", mode: "PAPER", legs: [{ strike: 52000, option_type: "CE", side: "SELL", qty: 35, entry_price: 120, exit_price: 145 }] },
];

async function withSettings(page: Page, theme: Theme, owner = true) {
  const saved: unknown[] = [];
  await openAdmin(page, "/admin?section=controls&group=safety", {
    theme,
    routes: async (p) => {
      await p.route(/\/api(?:\/v1)?\/admin\/controls\/srv2-agent-mode$/, (r) => r.fulfill({ status: 403, json: { detail: "Only the platform owner." } }));
      await p.route(/\/api(?:\/v1)?\/admin\/controls\/srv2-settings\/[A-Z]+$/, async (r) => {
        if (!owner) return r.fulfill({ status: 403, json: { detail: "Only the platform owner can see or change the trading agent's settings." } });
        if (r.request().method() === "PUT") saved.push(r.request().postDataJSON());
        return r.fulfill({ json: { installed: true, symbol: "NIFTY", symbols: ["NIFTY", "BANKNIFTY", "SENSEX"], signal_symbols: ["NIFTY", "BANKNIFTY"],
          fields: FIELDS, has_fields: true } });
      });
      await p.route(/\/api(?:\/v1)?\/admin\/controls\/srv2-positions$/, (r) =>
        owner ? r.fulfill({ json: { installed: true, positions: POSITIONS } }) : r.fulfill({ status: 403, json: { detail: "no" } }));
    },
  });
  return saved;
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Agent settings ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as Theme[]) {
      test(`settings and positions; one change sends one field (${theme})`, async ({ page }) => {
        const saved = await withSettings(page, theme);
        const card = page.locator("[data-console-card='agent-settings']");
        await expect(card.locator("[data-agent-settings='NIFTY']")).toBeVisible();
        await expect(card.locator("[data-agent-position='OPEN']")).toContainText("−24000PE +23900PE");
        const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
        expect(r.scroll).toBeLessThanOrEqual(r.width);
        await card.evaluate((el) => el.scrollIntoView({ block: "start" }));
        await expect(card).toHaveScreenshot(`agent-settings-v2-${size.name}-${theme}.png`, SHOT);
        await card.locator("[data-agent-field='f_depth'] input").fill("3");
        await card.getByRole("button", { name: "Save NIFTY (1)" }).click();
        await expect.poll(() => saved.length).toBe(1);
        expect(saved[0]).toEqual({ values: { f_depth: 3 } });
      });
    }

    test("another admin sees no settings card", async ({ page }) => {
      await withSettings(page, "light", false);
      await expect(page.locator("[data-controls-groups='safety']")).toBeVisible();
      await expect(page.locator("[data-console-card='agent-settings']")).toHaveCount(0);
    });
  });
}
