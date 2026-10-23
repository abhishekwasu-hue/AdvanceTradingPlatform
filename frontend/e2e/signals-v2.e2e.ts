import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts, type ChartsCalls } from "./chartsMocks";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UI2-3 (UI v2 proposal §7, §3 M6 / M7; MOBILE_APP_PLAN §2.1): the Signals inbox with the `ui_v2` flag on.
 * - Baselines `signals-v2-<width>-<theme>.png` at 1440 (full page), 390 and 360. The old `signals-*` baselines are not
 *   touched: with the flag off the old page renders as before (mobile.e2e.ts keeps proving it).
 * - Approve, skip and expired paths at 360 px on made-up fixtures (e2e/fixtures/signals_v2.json): every decision goes
 *   to the existing /api/ai/actions/{id}/approve|reject|snooze and the list follows.
 * - The phone layout rules, the desktop list + detail (from 1440 px, every column whole; 1280 / 1366 get the stacked
 *   cards), `?proposal=<id>`, M6 (Library drawer) and M7 (Watchtower link).
 * - UI2-4e: the desktop detail's chart is the v2 chart (made-up candles, e2e/chartsMocks.ts) with the levels marked,
 *   "Open chart" carrying its timeframe, and the "Live" / "Price delayed" chip; the 1440 baselines show it.
 * After an intended change: `npx playwright test --project=phone e2e/signals-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */

type Row = { id: string; status: string; action_id: number | null; symbol: string; source: { kind: string }; outcome: string | null; snoozed_until: string | null } & Record<string, unknown>;
type Proposal = { id: number } & Record<string, unknown>;
const ME = JSON.parse(readFileSync(new URL("./fixtures/me.json", import.meta.url), "utf8")) as Record<string, unknown>;
const DATA = JSON.parse(readFileSync(new URL("./fixtures/signals_v2.json", import.meta.url), "utf8")) as { today: Row[]; earlier: Row[]; proposals: Proposal[] };

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const V2: MockOptions = { features: { ui_v2: true, ai_copilot: true }, brokers: CONNECTED };
const STRATEGIES = [{ id: "demo_orb", name: "Demo opening range", description: "A made-up strategy for the test.", category: "indicator_based",
  timeframes: ["5min"], default_params: {} }];

/** Another member's waiting entry proposal (made up): not in this user's Signals, so it stays on the Watchtower. */
const THEIRS: Proposal = { ...DATA.proposals[0], id: 950, owner_user_id: 99, reason: "Made-up entry proposal: LONG DEMO OTHER", evidence: { ...(DATA.proposals[0].evidence as object), symbol: "DEMO OTHER" } };

interface Inbox { decisions: { id: number; kind: string; body: unknown }[]; charts?: ChartsCalls }

/** The inbox's server side, from the fixture: GET /signals/feed per tab and filter, GET /ai/actions?status=PROPOSED (the
 * risk pre-check) and the three decision endpoints, as the backend answers them. `expireOnApprove`: that proposal
 * expires just before the trader approves it (the server's 409). Registered after mockApi, so it answers first. */
async function mockInbox(page: Page, opts: { expireOnApprove?: number; others?: Proposal[]; edit?: (today: Row[]) => void } = {}): Promise<Inbox> {
  const today = DATA.today.map((r) => ({ ...r }));
  opts.edit?.(today);
  const earlier = DATA.earlier.map((r) => ({ ...r }));
  const all = () => [...today, ...earlier];
  const inbox: Inbox = { decisions: [] };
  await page.route(/\/api(?:\/v1)?\/(signals\/feed|ai\/actions|strategies$)/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api(?:\/v1)?/, "");
    const method = request.method();
    if (path === "/strategies" && method === "GET") return route.fulfill({ json: STRATEGIES });
    if (path === "/signals/feed" && method === "GET") {
      const tab = url.searchParams.get("tab") ?? "today";
      const source = url.searchParams.get("source") ?? "all";
      const symbol = url.searchParams.get("symbol");
      const keep = (r: Row) => (source === "all" || r.source.kind === source) && (!symbol || r.symbol === symbol);
      const waiting = today.filter((r) => r.status === "waiting" && keep(r));
      const rows = tab === "waiting" ? waiting : tab === "today" ? today.filter(keep) : all().filter(keep);
      return route.fulfill({ json: { tab, source, symbol, items: rows, next_cursor: null, counts: { waiting: waiting.length, today: today.filter(keep).length },
        as_of: "2026-10-08T05:00:00+00:00", since: null, sources: {} } });
    }
    if (path === "/ai/actions" && method === "GET") {
      const open = new Set(today.filter((r) => r.status === "waiting").map((r) => r.action_id));
      return route.fulfill({ json: [...DATA.proposals.filter((p) => open.has(p.id)), ...(opts.others ?? [])] });
    }
    const decide = path.match(/^\/ai\/actions\/(\d+)\/(approve|reject|snooze)$/);
    if (decide && method === "POST") {
      const id = Number(decide[1]);
      inbox.decisions.push({ id, kind: decide[2], body: request.postDataJSON() });
      const row = today.find((r) => r.action_id === id);
      if (!row) return route.fulfill({ status: 404, json: { detail: "No such proposal" } });
      if (opts.expireOnApprove === id) row.status = "expired";
      if (row.status !== "waiting") return route.fulfill({ status: 409, json: { detail: `Action is already ${row.status.toUpperCase()}` } });
      if (decide[2] === "approve") {
        row.status = "taken";
        row.outcome = "PAPER order #78 placed at the re-quoted price 24790";
        return route.fulfill({ json: { id, status: "EXECUTED", result: row.outcome, snoozed_until: null } });
      }
      if (decide[2] === "reject") {
        row.status = "skipped";
        return route.fulfill({ json: { id, status: "REJECTED", result: null, snoozed_until: null } });
      }
      row.status = "snoozed";
      row.snoozed_until = "2026-10-08T05:15:00+00:00";
      return route.fulfill({ json: { id, status: "PROPOSED", result: null, snoozed_until: row.snoozed_until } });
    }
    return route.fallback();
  });
  return inbox;
}

async function open(page: Page, path: string, theme: "dark" | "light", options: MockOptions = V2,
  opts: { expireOnApprove?: number; intro?: boolean; others?: Proposal[]; role?: string; edit?: (today: Row[]) => void } = {}) {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript(([t, intro]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      if (!intro) localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["signals"]));
    } catch { /* storage unavailable */ }
  }, [theme, !!opts.intro] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  // UI2-4e: the detail's chart reads made-up candles, last price, chart settings and market sessions (e2e/chartsMocks.ts).
  const charts = await mockCharts(page);
  const inbox = await mockInbox(page, opts);
  inbox.charts = charts;
  if (opts.role) {
    const me = JSON.parse(readFileSync(new URL("./fixtures/me.json", import.meta.url), "utf8")) as Record<string, unknown>;
    await page.route(/\/api(?:\/v1)?\/auth\/me$/, (route) => route.fulfill({ json: { ...me, role: opts.role } }));
  }
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return inbox;
}

/** Every cell of the desktop list lies inside the list's box (nothing cut off), each price fits its cell, and the page
 * does not scroll sideways (Phase A: no sideways tables). */
async function expectWholeTable(page: Page) {
  const m = await page.evaluate(() => {
    const table = document.querySelector("table[aria-label='Signals list']");
    if (!table) return null;
    const box = table.parentElement!.getBoundingClientRect();
    const cells = [...table.querySelectorAll("th, td")];
    return {
      over: Math.max(...cells.map((c) => c.getBoundingClientRect().right - box.right)),
      clipped: cells.filter((c) => c.scrollWidth > c.clientWidth + 1).length,
      sideways: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(m, "the desktop list is shown").not.toBeNull();
  expect(m!.over, "a column runs past the list's edge").toBeLessThanOrEqual(0.5);
  expect(m!.clipped, "a cell's content is cut off").toBe(0);
  expect(m!.sideways, "the page scrolls sideways").toBeLessThanOrEqual(0);
}

const THEMES = ["dark", "light"] as const;

/** UI2-4e: the detail's v2 chart is drawn (the library's canvas, the bars in). */
async function chartReady(page: Page) {
  await expect(page.locator("[data-signal-chart] .tv-lightweight-charts canvas").first()).toBeVisible();
  await expect(page.locator("[data-signal-chart]").getByText("Loading prices…")).toHaveCount(0);
}
const PHONES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];

for (const size of PHONES) {
  test.describe(`Signals v2 ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`Signals looks the same (${theme})`, async ({ page }) => {
        await open(page, "/signals", theme);
        await expect(page.locator("[data-page='signals-v2']")).toBeVisible();
        await expect(page.locator("[data-signal-card='action:901']").getByRole("button", { name: "Approve" })).toBeVisible();
        await expect(page).toHaveScreenshot(`signals-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar - Waiting and Today", async ({ page }) => {
      await open(page, "/signals", "dark");
      await expectPhoneLayout(page);
      await page.getByRole("tab", { name: /Today/ }).click();
      await expect(page.locator("[data-signal-card='action:860']")).toBeVisible();
      await expectPhoneLayout(page);
      await page.getByRole("button", { name: "Filter" }).click();
      await expect(page.getByRole("radiogroup", { name: "Source" })).toBeVisible();
      await expectPhoneLayout(page);
    });
  });
}

test.describe("Signals v2 decisions at 360 px", () => {
  test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("phone default: Waiting for you, the first card open with Approve / Skip / chart / More", async ({ page }) => {
    await open(page, "/signals", "light");
    await expect(page.getByRole("tab", { name: "Waiting · 2" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tab", { name: "Today · 7" })).toBeVisible();
    const first = page.locator("[data-signal-card='action:901']");
    await expect(first.getByText("Waiting · 8 min")).toBeVisible();
    await expect(first.getByText("Fits your risk limits · most you can lose ₹5,250")).toBeVisible();
    for (const name of ["Approve", "Skip", "Open chart for NIFTY 50", "More for NIFTY 50"]) await expect(first.getByRole(name.startsWith("Open") ? "link" : "button", { name })).toBeVisible();
    await expect(page.locator("[data-signal-card='action:902']").getByRole("button", { name: "Review" })).toBeVisible();
    await expect(page.locator("[data-signal-chart]")).toHaveCount(0);                      // UI2-4e: the chart is the desktop panel's
    await expect(page.getByText("That's all for now. New signals also arrive as a notification.")).toBeVisible();
  });

  test("approve: the existing approve endpoint, a plain message, and the signal moves to Taken", async ({ page }) => {
    const inbox = await open(page, "/signals", "light");
    await page.locator("[data-signal-card='action:901']").getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("status")).toHaveText("Approved. A paper trade for NIFTY 50 was placed at the price then.");
    expect(inbox.decisions).toEqual([{ id: 901, kind: "approve", body: {} }]);
    await expect(page.locator("[data-signal-card='action:901']")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Waiting · 1" })).toBeVisible();
    await page.getByRole("tab", { name: /Today/ }).click();
    const card = page.locator("[data-signal-card='action:901']");
    await expect(card.locator("[data-status='taken']")).toHaveText("Taken");
    await expect(card.getByRole("button", { name: "Approve" })).toHaveCount(0);
  });

  test("skip: the existing reject endpoint; nothing is placed and the signal moves to Skipped", async ({ page }) => {
    const inbox = await open(page, "/signals", "light");
    const second = page.locator("[data-signal-card='action:902']");
    await second.getByRole("button", { name: "Review" }).click();
    await second.getByRole("button", { name: "Skip" }).click();
    await expect(page.getByRole("status")).toHaveText("Skipped BANK NIFTY. Nothing was placed.");
    expect(inbox.decisions).toEqual([{ id: 902, kind: "reject", body: {} }]);
    await expect(second).toHaveCount(0);
    await page.getByRole("tab", { name: /Today/ }).click();
    await expect(page.locator("[data-signal-card='action:902'] [data-status='skipped']")).toHaveText("Skipped");
  });

  test("expired while open: the server's 409 is a plain sentence, nothing is placed, the list shows it Expired", async ({ page }) => {
    const inbox = await open(page, "/signals", "light", V2, { expireOnApprove: 901 });
    await page.locator("[data-signal-card='action:901']").getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("alert")).toHaveText("This signal was already decided or has expired. Nothing was placed.");
    expect(inbox.decisions.map((d) => d.kind)).toEqual(["approve"]);
    await expect(page.locator("[data-signal-card='action:901']")).toHaveCount(0);
    await page.getByRole("tab", { name: /Today/ }).click();
    const card = page.locator("[data-signal-card='action:901']");
    await expect(card.locator("[data-status='expired']")).toHaveText("Expired");
    await card.getByRole("button", { name: "Details" }).click();
    await expect(card.getByText("This signal expired before anyone decided. Nothing was placed.")).toBeVisible();
    await expect(card.getByRole("button", { name: "Approve" })).toHaveCount(0);
  });

  test("expired on screen: once the time runs out the buttons go, without a request", async ({ page }) => {
    const inbox = await open(page, "/signals", "light");
    const first = page.locator("[data-signal-card='action:901']");
    await expect(first.getByRole("button", { name: "Approve" })).toBeVisible();
    await page.clock.fastForward("09:00");
    await expect(first.locator("[data-status='expired']")).toHaveText("Expired");
    await expect(first.getByRole("button", { name: "Approve" })).toHaveCount(0);
    expect(inbox.decisions).toEqual([]);
  });

  test("an expired signal from earlier today has no decision buttons", async ({ page }) => {
    await open(page, "/signals", "light");
    await page.getByRole("tab", { name: /Today/ }).click();
    const card = page.locator("[data-signal-card='action:860']");
    await expect(card.locator("[data-status='expired']")).toHaveText("Expired");
    await card.getByRole("button", { name: "Details" }).click();
    await expect(card.getByText("This signal expired before anyone decided. Nothing was placed.")).toBeVisible();
    await expect(card.getByRole("button", { name: /Approve|Skip/ })).toHaveCount(0);
  });

  test("snooze sits under More and uses the existing snooze endpoint", async ({ page }) => {
    const inbox = await open(page, "/signals", "light");
    const first = page.locator("[data-signal-card='action:901']");
    await first.getByRole("button", { name: "More for NIFTY 50" }).click();
    await first.getByRole("button", { name: /It expires/ }).first().click();
    await expect(page.getByRole("status")).toContainText("Snoozed NIFTY 50");
    expect(inbox.decisions[0].kind).toBe("snooze");
  });
});

test.describe("Signals v2 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`Signals looks the same (${theme})`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 1330 });             // the whole detail panel (UI2-4e: with its chart)
      await open(page, "/signals", theme);
      await expect(page.locator("[data-signal-detail='action:901']")).toBeVisible();
      await chartReady(page);
      await expect(page).toHaveScreenshot(`signals-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("desktop default: Today with the first waiting signal selected; picking a row shows its detail", async ({ page }) => {
    await open(page, "/signals", "light");
    await expect(page.getByRole("tab", { name: /Today/ })).toHaveAttribute("aria-selected", "true");
    const table = page.getByRole("table", { name: "Signals list" });
    await expect(table.locator("tbody tr")).toHaveCount(7);
    await expect(table.getByRole("columnheader")).toHaveText(["Time", "Symbol", "Setup", "Status"]);      // UX3: no price columns
    const detail = page.locator("[data-signal-detail]");
    await expect(detail).toHaveAttribute("data-signal-detail", "action:901");
    for (const label of ["Size", "Max loss", "Reward : risk"]) await expect(detail.locator("[data-key-figures]")).toContainText(label);
    // UX3 fix 10: the user's own levels are one tap away, under neutral names.
    const levels = detail.locator("details[data-your-levels]");
    await expect(levels.getByText("Exit if wrong")).toBeHidden();
    await levels.getByText("Your levels").click();
    for (const label of ["Entry", "Exit if wrong", "Exit at profit"]) await expect(levels.getByText(label, { exact: true })).toBeVisible();
    await expect(detail).not.toContainText(/\b(?:Stop|Target)\b/);
    await expect(detail.getByText("1 lot (75)")).toBeVisible();
    await expect(detail.getByRole("button", { name: "Approve paper trade" })).toBeVisible();
    await expect(detail.getByRole("link", { name: "Open chart" })).toHaveAttribute("href", "/charts/NIFTY%2050?tf=5min");
    await table.getByRole("button", { name: "AXISBANK" }).click();
    await expect(detail).toHaveAttribute("data-signal-detail", "action:860");
    await expect(detail.getByText("This signal expired before anyone decided. Nothing was placed.")).toBeVisible();
    await expect(detail.getByRole("button", { name: /Approve/ })).toHaveCount(0);
  });

  test("UI2-4e: the detail's chart is the v2 chart of the signal, with the trader's settings; Open chart keeps its timeframe", async ({ page }) => {
    const { charts } = await open(page, "/signals", "light");
    const chart = page.locator("[data-signal-chart='NIFTY 50']");
    await chartReady(page);
    await expect(page.locator("[data-levels-chart]")).toHaveCount(0);                      // the chart marks the levels now
    expect(charts!.candles.filter((c) => !c.before).map((c) => `${c.symbol} ${c.timeframe}`)).toEqual(["NIFTY 50 5min"]);
    const tfs = chart.getByRole("group", { name: "Timeframe" });
    await expect(tfs.getByRole("button")).toHaveText(["1m", "3m", "5m", "15m", "30m", "1H", "D"]);
    await expect(chart.getByRole("button", { name: "Chart settings" })).toBeVisible();
    await tfs.getByRole("button", { name: "15m", exact: true }).click();
    await expect.poll(() => charts!.candles.some((c) => c.symbol === "NIFTY 50" && c.timeframe === "15min")).toBe(true);
    // One link to the Chart tab (the detail's "Open chart"), carrying the chart's timeframe.
    await expect(chart.getByRole("link", { name: "Open in Chart tab" })).toHaveCount(0);
    const openChart = page.locator("[data-signal-detail]").getByRole("link", { name: "Open chart" });
    await expect(openChart).toHaveAttribute("href", "/charts/NIFTY%2050?tf=15min");
    await openChart.click();
    await expect(page).toHaveURL(/\/charts\/NIFTY%2050$/);
    await expect(page.locator("[data-page='charts-v2']")).toBeVisible();
    await expect(page.locator("[data-page='charts-v2']").getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15m", exact: true }))
      .toHaveAttribute("aria-pressed", "true");
  });

  test("UI2-4e: the detail's chart says Live, then Price delayed with its age when the price stops coming", async ({ page }) => {
    await open(page, "/signals", "light");
    await chartReady(page);
    const chip = page.locator("[data-signal-chart] [data-live-chip]");
    await expect(chip).toHaveText("Live");
    await page.route(/\/api(?:\/v1)?\/market-data\/ltp/, (r) => r.fulfill({ status: 503, json: { detail: "unavailable" } }));
    await page.clock.runFor(190_000);
    await expect(chip).toHaveText("Price delayed · 3 min");
  });

  test("every column is fully visible at 1440 - Today, History, and a row with long prices", async ({ page }) => {
    await open(page, "/signals", "light", V2, { edit: (today) => Object.assign(today[0], { entry: 135000, stop: 134250.5, target: 136500 }) });
    await expect(page.locator("[data-row='action:901']")).toBeVisible();
    await expect(page.locator("[data-row='action:901']")).not.toContainText("1,35,000");                 // UX3: no price in the list
    await expectWholeTable(page);
    await page.getByRole("tab", { name: "History" }).click();
    await expect(page.getByRole("table", { name: "Signals list" }).locator("tbody tr")).toHaveCount(9);
    await expectWholeTable(page);
  });

  test("at 1440 with an always-visible scrollbar (17 px) a row of six-digit prices still fits", async ({ page }) => {
    // UI2-3 review note: headless Chromium hides scrollbars, so 17 px of padding stands in for a classic one.
    await open(page, "/signals", "light", V2, { edit: (today) => Object.assign(today[0], { entry: 135000, stop: 134250.5, target: 136500 }) });
    await page.addStyleTag({ content: "html { padding-right: 17px !important; }" });
    await expect(page.locator("[data-row='action:901']")).not.toContainText("1,36,500");
    await expectWholeTable(page);
  });

  test("the source filter and the symbol filter ask the feed again", async ({ page }) => {
    await open(page, "/signals", "light");
    await page.getByRole("radio", { name: "Scans" }).click();
    const table = page.getByRole("table", { name: "Signals list" });
    await expect(table.locator("tbody tr")).toHaveCount(3);
    await page.getByRole("radio", { name: "All sources" }).click();
    await page.getByRole("button", { name: "Symbol" }).click();
    await page.getByLabel("Symbol").fill("infy");
    await page.getByRole("button", { name: "Show" }).click();
    await expect(table.locator("tbody tr")).toHaveCount(1);
    await page.getByRole("button", { name: "Clear symbol" }).click();
    await expect(table.locator("tbody tr")).toHaveCount(7);
  });

  test("a ?proposal=<id> link that is not waiting says so, and names another member's", async ({ page }) => {
    await open(page, "/signals?proposal=999", "light", V2, { others: [THEIRS] });
    await expect(page.getByRole("tab", { name: /Waiting for you/ })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("[data-missing-proposal]")).toHaveText(
      "Signal #999 is not waiting for you any more: it was decided, snoozed or has expired. Find it under Today or History.");
    await page.goto("/signals?proposal=950");
    const note = page.locator("[data-missing-proposal]");
    await expect(note).toContainText("Signal #950 is another member's, so it is not in your Signals. It stays on the Watchtower.");
    await expect(note.getByRole("link", { name: "Open the Watchtower" })).toHaveAttribute("href", "/copilot/watchtower?action=950");
  });

  test("a viewer sees the signals but cannot approve, skip or snooze", async ({ page }) => {
    await open(page, "/signals", "light", V2, { role: "VIEWER" });
    const detail = page.locator("[data-signal-detail='action:901']");
    await expect(detail).toBeVisible();
    await expect(detail.locator("[data-view-only]")).toHaveText("Your role can view signals but not approve or skip them. Ask your organisation owner.");
    await expect(detail.getByRole("button", { name: /Approve|Skip|More for/ })).toHaveCount(0);
    await expect(detail.getByRole("link", { name: "Open chart" })).toBeVisible();
  });

  test("a ?proposal=<id> link opens that proposal; History reaches older days", async ({ page }) => {
    await open(page, "/signals?proposal=902", "light");
    await expect(page.locator("[data-signal-detail]")).toHaveAttribute("data-signal-detail", "action:902");
    await page.getByRole("tab", { name: "History" }).click();
    await expect(page.getByRole("table", { name: "Signals list" }).locator("tbody tr")).toHaveCount(9);
  });

  test("the page intro shows on a first visit: one line, no box title (item 5)", async ({ page }) => {
    await open(page, "/signals", "light", V2, { intro: true });
    await expect(page.locator("[data-page-intro='open']")).toBeVisible();
    await expect(page.getByRole("heading", { name: "What is this page?" })).toHaveCount(0);
  });

  test("M6: the Library's strategy drawer has 'Try it on a symbol', sample data only under Advanced", async ({ page }) => {
    await open(page, "/strategies", "light");
    await page.getByRole("button", { name: "Try it on a symbol" }).click();
    let drawer = page.getByRole("dialog", { name: "Demo opening range" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByLabel("Symbol")).toHaveValue("NIFTY 50");
    // UX3 fix 7: a trader has no sample data at all; only the platform admin keeps it, under Advanced.
    await expect(drawer.getByText("Advanced")).toHaveCount(0);
    await expect(drawer.getByText(/sample/i)).toHaveCount(0);
    await page.route(/\/api(?:\/v1)?\/auth\/me$/, (r) => r.fulfill({ json: { ...ME, role: "SUPER_ADMIN" } }));
    await page.reload();
    await page.getByRole("button", { name: "Try it on a symbol" }).click();
    drawer = page.getByRole("dialog", { name: "Demo opening range" });
    await expect(drawer.getByText("Sample data seed")).toHaveCount(0);
    await drawer.getByText("Advanced").click();
    await drawer.getByLabel("Use sample data instead of your broker's candles").check();
    await expect(drawer.getByLabel("Sample data seed")).toBeVisible();
  });

  test("M7: another member's entry proposal stays on the Watchtower for the organisation owner", async ({ page }) => {
    await open(page, "/copilot/watchtower", "light", V2, { others: [THEIRS] });
    await expect(page.getByTestId("watch-signals-link")).toContainText("(2 waiting)", { timeout: 15_000 });   // the Signals count
    await expect(page.getByTestId("watch-proposal")).toHaveCount(1);
    await expect(page.getByTestId("watch-proposal")).toContainText("DEMO OTHER");
    await expect(page.getByTestId("tab-watchtower")).toContainText("1");
  });

  test("M7: the Watchtower keeps its other proposals and links entry approvals to Signals", async ({ page }) => {
    await open(page, "/copilot/watchtower", "light");
    const link = page.getByTestId("watch-signals-link");
    await expect(link).toContainText("Trades waiting for your approval are in Signals now (2 waiting).", { timeout: 15_000 });
    await expect(page.getByTestId("watch-proposal")).toHaveCount(0);           // the fixture's waiting proposals are entries
    await expect(page.getByTestId("tab-watchtower")).not.toContainText("2");    // the tab badge does not count them either
    await link.click();
    await expect(page).toHaveURL(/\/signals\?proposal=901$/);
    await expect(page.locator("[data-signal-detail]")).toHaveAttribute("data-signal-detail", "action:901");
  });
});

for (const width of [1280, 1366]) {
  test.describe(`Signals v2 at ${width} (laptop)`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("the list is stacked cards, not a cut-off table: every signal whole, no sideways scroll", async ({ page }) => {
      await open(page, "/signals", "light");
      for (const tab of ["Waiting for you", "Today", "History"]) {
        await page.getByRole("tab", { name: new RegExp(tab) }).click();
        await expect(page.locator("[data-signal-card]").first()).toBeVisible();
        await expect(page.getByRole("table", { name: "Signals list" })).toHaveCount(0);
        const m = await page.evaluate(() => {
          const main = document.querySelector("[data-page='signals-v2']")!.getBoundingClientRect();
          const cards = [...document.querySelectorAll("[data-signal-card]")];
          return { over: Math.max(...cards.map((c) => c.getBoundingClientRect().right - main.right)),
            clipped: cards.filter((c) => c.scrollWidth > c.clientWidth + 1).length, sideways: document.documentElement.scrollWidth - window.innerWidth };
        });
        expect(m.over, tab).toBeLessThanOrEqual(0.5);
        expect(m.clipped, tab).toBe(0);
        expect(m.sideways, tab).toBeLessThanOrEqual(0);
      }
      const first = page.locator("[data-signal-card='action:901']");
      await page.getByRole("tab", { name: /Waiting for you/ }).click();
      await expect(first.getByRole("button", { name: "Approve" })).toBeVisible();     // decisions work at this width too
    });
  });
}

test.describe("Signals with the flag off", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Signals is today's page with the generator, and no inbox", async ({ page }) => {
    const inbox = await open(page, "/signals", "dark", { brokers: CONNECTED });
    await expect(page.getByLabel("Sample data seed")).toBeVisible();
    await expect(page.locator("[data-page='signals-v2']")).toHaveCount(0);
    await expect(page.locator("[data-embedded-chart], [data-live-chip]")).toHaveCount(0);   // UI2-4e: no v2 chart either
    expect(inbox.decisions).toEqual([]);
  });

  test("the Library has no drawer button and the Watchtower no Signals link", async ({ page }) => {
    await open(page, "/strategies", "dark", { brokers: CONNECTED });
    await expect(page.getByText("Demo opening range")).toBeVisible();
    await expect(page.getByRole("button", { name: "Try it on a symbol" })).toHaveCount(0);
    await page.goto("/copilot/watchtower");
    await expect(page.getByTestId("tab-panel-watchtower")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("watch-proposal").first()).toBeVisible();          // entry proposals stay here
    await expect(page.getByTestId("watch-signals-link")).toHaveCount(0);
  });
});
