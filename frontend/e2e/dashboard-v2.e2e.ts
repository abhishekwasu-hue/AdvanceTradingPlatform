import { expect, test, type Page } from "@playwright/test";
import { F, api, mockDashboard, setupAnswer } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { warnOnManySolidBrand } from "./solidBrand";

// Chart + colour order Part 3 (CC-8): a warning annotation, never a failure, when a screen shows more than one solid
// brand button (the one main action per screen).
test.afterEach(async ({ page }, testInfo) => { await warnOnManySolidBrand(page, testInfo); });

/**
 * UI2-2 (UI v2 proposal §5, §12; MOBILE_APP_PLAN Phase A): the v2 dashboard, "Today", with the `ui_v2` flag on.
 * - Baselines `dashboard-v2-<width>-<theme>.png` at 1440, 390 and 360. The old dashboard's baselines
 *   (`dashboard-<width>-<theme>.png`, mobile.e2e.ts) are untouched: with the flag off it renders as before.
 * - On a phone: the PW2 layout rules (no sideways scroll, 44 px targets, nothing behind the bottom bar), one column in
 *   the §5 order, and the same in the "log in" and empty states.
 * - The blocks' data, the broker states, the friendly empty states, one primary button, and the removals (UI2-4e: no
 *   chart on this page; the v2 chart is on Charts and in the Signals detail).
 * Every reply is hand-written, made-up data (e2e/fixtures/dashboard_v2.json): no real account, price or id.
 * After an intended change: `npx playwright test --project=phone e2e/dashboard-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */

interface Setup {
  /** "connected" (default), "login" (stored, not logged in today), "none" (no broker stored), "empty" (connected, nothing yet). */
  state?: "connected" | "login" | "none" | "empty";
  /** IST wall clock, default Monday 19 Oct 2026 11:00 (market open). */
  time?: string;
  flag?: boolean;
  /** Replaces the strip's reply. */
  strip?: unknown;
  /** Replaces the setup progress reply (GET /api/me/setup). */
  setup?: unknown;
}

async function open(page: Page, theme: "dark" | "light", setup: Setup = {}) {
  const { state = "connected", time = "2026-10-19T11:00:00+05:30", flag = true, strip, setup: setupReply } = setup;
  await page.clock.install({ time: new Date(time) });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const brokers = state === "none" ? [] : state === "login" ? F.token_status_login : F.token_status;
  await mockApi(page, { features: flag ? (F.features as Record<string, boolean>) : undefined, brokers: brokers as unknown[] });
  await mockDashboard(page, { state, strip, me: true });
  if (setupReply) await page.route(api("/me/setup(\\?|$)"), (route) => route.fulfill({ json: setupReply }));
  await page.goto("/");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

const THEMES = ["dark", "light"] as const;
const PHONES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];
const ORDER = ["strip", "get-started", "attention", "positions", "signals", "today"];

for (const size of PHONES) {
  test.describe(`v2 dashboard ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
        await expect(page).toHaveScreenshot(`dashboard-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    for (const state of ["connected", "login", "empty"] as const) {
      test(`${state}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
        await open(page, "dark", { state });
        await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
        await expectPhoneLayout(page);
        // <main> scrolls on its own, so also check that every block sits inside the 16 px gutters and that no figure
        // spills out of its box (overlapping its neighbour).
        const spill = await page.evaluate(() => {
          const width = window.innerWidth;
          const out: string[] = [];
          for (const b of document.querySelectorAll("[data-block]")) {
            const r = b.getBoundingClientRect();
            if (r.left < 15 || r.right > width - 15) out.push(`${b.getAttribute("data-block")}: ${Math.round(r.left)}-${Math.round(r.right)}`);
          }
          for (const e of document.querySelectorAll("[data-block] *")) {
            // The glossary "?" has an invisible 44 px touch ring that overhangs its box on purpose.
            if (e.closest("[aria-label^='What is']") || e.querySelector("[aria-label^='What is']")) continue;
            if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === "visible" && e.clientWidth > 0) {
              out.push(`${e.tagName}:${(e.textContent ?? "").trim().slice(0, 20)}`);
            }
          }
          return out;
        });
        expect(spill).toEqual([]);
      });
    }

    test("one column, in the §5 order, under the title Today", async ({ page }) => {
      await open(page, "light");
      await expect(page.locator("[data-shell='v2'] header").first()).toContainText("Today");
      const blocks = await page.locator("[data-block]").evaluateAll((els) => els.map((e) => ({
        name: e.getAttribute("data-block"), top: e.getBoundingClientRect().top, left: e.getBoundingClientRect().left,
      })));
      expect(blocks.map((b) => b.name)).toEqual(ORDER);
      for (let i = 1; i < blocks.length; i += 1) expect(blocks[i].top).toBeGreaterThan(blocks[i - 1].top);
      expect(new Set(blocks.map((b) => Math.round(b.left))).size).toBe(1);
    });
  });
}

test.describe("v2 dashboard 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
      await expect(page).toHaveScreenshot(`dashboard-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("the strip runs full width; Needs attention and positions on the left, signals and Today on the right", async ({ page }) => {
    await open(page, "light");
    const box = async (name: string) => (await page.locator(`[data-block='${name}']`).boundingBox())!;
    const [strip, attention, positions, signals, today] = await Promise.all(["strip", "attention", "positions", "signals", "today"].map(box));
    expect(attention.x).toBeCloseTo(strip.x, 0);
    expect(positions.x).toBeCloseTo(attention.x, 0);
    expect(signals.x).toBeGreaterThan(attention.x + attention.width);
    expect(today.x).toBeCloseTo(signals.x, 0);
    expect(attention.width).toBeGreaterThan(signals.width * 1.8);
    expect(strip.width).toBeCloseTo(signals.x + signals.width - strip.x, 0);
  });

  test("every block shows its data", async ({ page }) => {
    await open(page, "light");
    const strip = page.locator("[data-block='strip']");
    await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("24,812.35");
    await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("+118.40");
    await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("(+0.48%)");
    await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("Put OI above call OI (PCR 1.12)");
    await expect(strip.locator("[data-index='NIFTY BANK']")).toContainText("BANK NIFTY");
    await expect(strip.locator("[data-index='NIFTY BANK']")).toContainText("−212.55");
    await expect(strip.locator("[data-index='INDIA VIX']")).toContainText("Normal");
    await expect(strip).toContainText("Market open · closes 15:30");
    await expect(strip).toContainText("Upstox connected");
    await expect(strip).toContainText("+₹6,847");
    await expect(strip).toContainText("+₹2,540 booked · +₹4,307 open");

    const attention = page.locator("[data-block='attention']");
    await expect(attention.locator("[data-attention]")).toHaveCount(3);
    await expect(attention.locator("[data-attention]").first()).toContainText("2 trades are waiting for your approval");
    await expect(attention.locator("[data-attention]").first()).toContainText("first one expires in 8 min");
    await expect(attention.locator("[data-attention='paused']")).toContainText("It hit your rule of 3 losses in a row at 10:21.");
    await expect(attention.locator("[data-attention='alerts']")).toContainText("Your alert: NIFTY 50 crossed 24,800");

    const positions = page.locator("[data-block='positions']");
    await expect(positions.locator("li")).toHaveCount(3);
    await expect(positions.locator("li").nth(0)).toContainText("Buy · 2 lots (150) · avg 121.80");
    await expect(positions.locator("li").nth(0)).toContainText("+₹3,120");
    await expect(positions.locator("li").nth(1)).toContainText("Sell · 1 lot (35) · avg 54,000.00");
    await expect(positions.locator("li").nth(2)).toContainText("LIVE");                  // CT-1 C8: a LIVE row keeps its LIVE badge
    await expect(positions.locator("li").nth(2)).toContainText("−₹700");

    const signals = page.locator("[data-block='signals']");
    await expect(signals.locator("li")).toHaveCount(3);
    await expect(signals.locator("li").first()).toContainText("10:42");
    await expect(signals.locator("li").first()).toContainText("Waiting for you");
    await expect(signals.locator("li").first()).not.toContainText(/24,790|Entry|Stop|Target/);            // UX3 fix 10: structure words only
    await expect(signals.locator("li").nth(2)).toContainText("Taken");

    const today = page.locator("[data-block='today']");
    await expect(today).toContainText("+₹2,540");
    await expect(today).toContainText("58%");
    await expect(today).toContainText("41 closed trades in 30 days");
    await expect(today).toContainText("39%");
    await expect(today).toContainText("2 waiting · 1 taken");
    // P3: the Get started card (the same as today's dashboard) replaced the Setup chip
    const card = page.locator("[data-block='get-started']");
    await expect(card).toContainText("2 of 3 done");
    await expect(card.locator("[data-done='true']")).toHaveCount(2);
    await expect(card.getByRole("link", { name: "Set up Telegram" })).toHaveAttribute("href", "/settings/alerts");
    await expect(page.locator("[data-block='setup']")).toHaveCount(0);
  });

  test("one primary button: the first Needs attention action", async ({ page }) => {
    await open(page, "light");
    const primaries = page.locator("main .bg-brand:is(a, button):not([data-subtab])");
    await expect(primaries).toHaveCount(1);
    await expect(primaries).toHaveText("Review 2 trades");
    await expect(page.locator("[data-attention='approvals']").getByRole("link", { name: "Review 2 trades" })).toHaveAttribute("href", "/copilot/watchtower");
  });

  test("today's blocks that moved away are gone, and no developer wording is shown", async ({ page }) => {
    await open(page, "light");
    const main = page.locator("main");
    for (const gone of ["Backend online", "Backend offline", "Autopilot trading", "beat", "Engines behind this console", "Deploy a strategy",
      "Market pulse", "Option-chain OI", "P&L by strategy", "Strategy mix", "Exposure by symbol", "Running deployments", "Latest alerts",
      "Go-live", "Broker funds", "Net P&L", "registry", "adapter", "flag", "503", "sample"]) {
      await expect(main.getByText(gone, { exact: false }), gone).toHaveCount(0);
    }
  });

  test("UI2-4e: no chart here - Market pulse lives on Charts, so nothing draws a chart or reads candles", async ({ page }) => {
    const candles: string[] = [];
    page.on("request", (r) => { if (/\/market-data\/candles/.test(r.url())) candles.push(r.url()); });
    await open(page, "light");
    await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
    await expect(page.locator(".tv-lightweight-charts, [data-embedded-chart], [data-live-chip]")).toHaveCount(0);
    expect(candles).toEqual([]);
  });

  test("a glossary '?' explains a term", async ({ page }) => {
    await open(page, "light");
    await page.getByRole("button", { name: "What is Win rate?" }).click();
    await expect(page.getByRole("note")).toContainText("The share of your closed trades that made a profit.");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("note")).toHaveCount(0);
  });

  test("before the day's broker login: the strip from market memory and 'Log in to Upstox' first", async ({ page }) => {
    await open(page, "light", { state: "login", time: "2026-10-19T09:05:00+05:30" });
    const strip = page.locator("[data-block='strip']");
    await expect(strip).toContainText("Pre-open · opens 09:15");
    await expect(strip).toContainText("Upstox · log in");
    await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("24,693.95");
    await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("As of 16 Oct");
    await expect(strip.locator("[data-index='INDIA VIX']")).toContainText("No price yet");
    const first = page.locator("[data-attention]").first();
    await expect(first).toContainText("Log in to Upstox");
    await expect(first).toContainText("we never see or store your broker password");
    await expect(first.getByRole("button", { name: "Log in" })).toHaveClass(/bg-brand/);
  });

  test("no broker stored: the Get started card and nothing else, 'Connect your broker' first", async ({ page }) => {
    await open(page, "light", { state: "none" });
    const card = page.locator("[data-block='get-started']");
    await expect(card).toContainText("0 of 3 done");
    await expect(page.locator("[data-block]")).toHaveCount(1);
    await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
    const connect = card.getByRole("link", { name: "Connect your broker" });
    await expect(connect).toHaveClass(/bg-brand/);                                      // the one primary button here
    await expect(connect).toHaveAttribute("href", /^\/(broker|settings)$/);       // /broker once the Broker page exists
  });

  test("no broker stored and Get started hidden: the broker gate's 'Connect your broker' and nothing else", async ({ page }) => {
    const a = setupAnswer("none");
    await open(page, "light", { state: "none", setup: { ...a, stored: { ...a.stored, get_started_hidden_at: "2026-10-18T09:00:00Z" } } });
    const gate = page.locator("[data-broker-gate='no-broker']");
    await expect(gate).toBeVisible();
    await expect(gate.getByRole("heading", { name: "Connect your broker" })).toBeVisible();
    await expect(page.locator("[data-block]")).toHaveCount(0);
    await gate.getByRole("link", { name: "Connect a broker" }).click();
    await expect(page).toHaveURL(/\/broker$/);                             // P2b: the Broker page
  });

  test("nothing yet: the friendly empty states, each with one action", async ({ page }) => {
    await open(page, "light", { state: "empty" });
    await expect(page.locator("[data-block='attention']")).toHaveText("All clear. All caught up.");
    const positions = page.locator("[data-block='positions']");
    await expect(positions).toContainText("All positions are closed.");
    await expect(positions.getByRole("link", { name: "Find setups" })).toHaveAttribute("href", "/screener");
    const signals = page.locator("[data-block='signals']");
    await expect(signals).toContainText("Signals show here as they come today.");
    await expect(signals.getByRole("link", { name: "Set up a scan" })).toHaveAttribute("href", "/screener/custom");
    await expect(page.locator("main .bg-brand:is(a, button):not([data-subtab])")).toHaveCount(0);
  });

  test("market closed: the last close with when it opens", async ({ page }) => {
    const closed = { ...(F.strip as object), refresh_seconds: 300, market: { exchange: "NSE", phase: "closed", is_open: false, opens_at: "09:15",
      closes_at: "15:30", next_open: "2026-10-20T09:15:00+05:30", holiday: null, reason: "after-close" } };
    await open(page, "light", { time: "2026-10-19T16:10:00+05:30", strip: closed });
    await expect(page.locator("[data-block='strip']")).toContainText("Closed · opens Tue 09:15");
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("today's dashboard, exactly as before", async ({ page }) => {
    await open(page, "light", { flag: false });
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-page='dashboard-v2']")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Deploy a strategy" })).toBeVisible();
  });
});

// Settings redesign D ("Setup health", docs/design/SETTINGS_REDESIGN_REVIEW.md R8): the setup-health rows in Needs
// attention, from `GET /api/me/setup` -> `health` (made-up states), with the Get started card hidden. Baselines
// `dashboard-v2-health-<width>-<theme>.png` (the Needs attention block) at 360, 390 and 1440, on an account with nothing
// else waiting, so the block shows the four rows.
function healthAnswer() {
  const base = setupAnswer("connected");
  return {
    ...base,
    get_started: { ...base.get_started, hidden_at: "2026-10-12T09:00:00Z", visible: false },
    stored: { ...base.stored, get_started_hidden_at: "2026-10-12T09:00:00Z" },
    health: [
      { key: "analytics_token", state: "expiring" },
      { key: "ai_key", state: "no_credit", message: "This key has no credit left." },
      { key: "telegram", state: "not_connected" },
      { key: "data_feed", state: "delayed" },
    ],
  };
}

test.describe("v2 dashboard setup health", () => {
  for (const size of [{ width: 360, height: 800, phone: true }, { width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
    test.describe(`${size.width}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });
      for (const theme of THEMES) {
        test(`the rows with one fix each (${theme})`, async ({ page }) => {
          await open(page, theme, { state: "empty", setup: healthAnswer() });
          const attention = page.locator("[data-block='attention']");
          await expect(attention.locator("[data-attention='renew-token']")).toContainText("Renew");
          if (size.phone && theme === "dark") await expectPhoneLayout(page);
          await attention.scrollIntoViewIfNeeded();
          await expect(attention).toHaveScreenshot(`dashboard-v2-health-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
        });
      }
    });
  }

  test("each row opens its fix; a hidden row stays hidden today; Telegram waits for the Get started card", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const telegramGets: string[] = [];
    page.on("request", (r) => { if (r.url().includes("/alerts/telegram/connect")) telegramGets.push(r.url()); });
    await open(page, "light", { state: "empty", setup: healthAnswer() });
    const attention = page.locator("[data-block='attention']");
    await expect(attention.locator("[data-attention='ai-key'] a")).toHaveAttribute("href", "/settings#ai");
    await expect(attention.locator("[data-attention='telegram'] a")).toHaveAttribute("href", "/settings#alerts");
    await expect(attention.locator("[data-attention='renew-token'] a")).toHaveAttribute("href", "/broker");
    await expect(attention.locator("[data-attention='data-delayed']")).toContainText("Live prices are delayed");
    await attention.locator("[data-hide='ai-key']").click();
    await expect(attention.locator("[data-attention='ai-key']")).toHaveCount(0);
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[data-attention='renew-token']")).toBeVisible();
    await expect(page.locator("[data-attention='ai-key']")).toHaveCount(0);
    expect(telegramGets).toEqual([]);
  });

  test("the Get started card on the page: no Telegram row; nothing to fix: no health rows", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, "light", { setup: { ...setupAnswer("connected"), health: [{ key: "telegram", state: "not_connected" }] } });
    await expect(page.locator("[data-block='get-started']")).toBeVisible();
    await expect(page.locator("[data-attention='telegram']")).toHaveCount(0);
    await open(page, "light", { state: "empty", setup: { ...healthAnswer(), health: [] } });
    await expect(page.locator("[data-block='attention']")).toContainText("All clear.");
    for (const id of ["renew-token", "ai-key", "telegram", "data-delayed"]) await expect(page.locator(`[data-attention='${id}']`)).toHaveCount(0);
  });
});
