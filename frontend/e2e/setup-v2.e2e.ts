import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UI2-7 (UI v2 proposal §11, §12, §13 UI-8): the setup wizard at Settings → Setup and the dashboard's Setup chip, with
 * the `ui_v2` flag on. The progress is the server's (`GET/PUT /api/me/setup`), mocked here as a small server that keeps
 * what was saved and works out "done" from what the page did (a PAPER deployment, saved limits, a test alert).
 * - Baselines `setup-v2-step{1..4}-390-light.png`.
 * - Skip for now, resume after a reload, the chip's words and its close button, the first-sign-in opening, PAPER only,
 *   and the flag off (no setup page; today's dashboard keeps its go-live checklist).
 * Every reply is hand-written, made-up data (e2e/fixtures/setup_v2.json, dashboard_v2.json): no real account or id.
 * After an intended change: `npx playwright test --project=phone e2e/setup-v2.e2e.ts --update-snapshots`, check the new
 * images, commit.
 */

const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const KEYS = ["broker", "risk", "strategy", "alerts"] as const;
type Key = typeof KEYS[number];
type Stored = { steps: Record<Key, { skipped_at: string | null }>; opened_at: string | null; dismissed_at: string | null };

const SEEN = "2026-10-12T09:00:00Z";
const blank = (): Stored => ({ steps: { broker: { skipped_at: null }, risk: { skipped_at: null }, strategy: { skipped_at: null }, alerts: { skipped_at: null } },
  opened_at: SEEN, dismissed_at: null });

/** The mocked server's state, and what the page sent. */
interface Server { stored: Stored; version: number; done: Record<Key, boolean>; sent: { method: string; url: string; body: unknown }[] }

function answer(s: Server) {
  const steps = KEYS.map((key) => {
    const skippedAt = s.stored.steps[key].skipped_at;
    return { key, done: s.done[key], skipped: !s.done[key] && skippedAt !== null, skipped_at: skippedAt };
  });
  const open = steps.filter((x) => !x.done && !x.skipped);
  return { steps, done_count: steps.filter((x) => x.done).length, next: open[0]?.key ?? null, complete: !open.length, stored: s.stored, version: s.version };
}

interface Open {
  path?: string;
  flag?: boolean;
  done?: Partial<Record<Key, boolean>>;
  stored?: Partial<Stored>;
  /** Keep the same server across a reload (resume). */
  server?: Server;
}

async function open(page: Page, o: Open = {}): Promise<Server> {
  const server: Server = o.server ?? { stored: { ...blank(), ...o.stored }, version: o.stored ? 2 : 1, sent: [],
    done: { broker: false, risk: false, strategy: false, alerts: false, ...o.done } };
  await page.clock.install({ time: new Date("2026-10-21T10:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: o.flag === false ? undefined : (F.features as Record<string, boolean>), brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  const reply = (json: unknown) => (route: Route) => route.fulfill({ json });
  const sent = (route: Route) => {
    const r = route.request();
    let body: unknown = null;
    try { body = r.postDataJSON(); } catch { body = r.postData(); }
    server.sent.push({ method: r.method(), url: r.url().replace(/^https?:\/\/[^/]+\/api(?:\/v1)?/, ""), body });
    return body;
  };
  await page.route(api("/me/setup(\\?|$)"), async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: answer(server) });
    const body = sent(route) as Stored;
    const version = Number(new URL(route.request().url()).searchParams.get("version"));
    if (version !== server.version) {
      return route.fulfill({ status: 409, json: { detail: { message: "the preferences changed since this edit was made", version: server.version, current: server.stored } } });
    }
    server.stored = body;
    server.version += 1;
    return route.fulfill({ json: answer(server) });
  });
  await page.route(api("/strategies$"), reply(S.strategies));
  await page.route(api("/market-data/sources$"), reply(S.sources));
  await page.route(api("/market-data/candles$"), reply(S.candles));
  await page.route(api("/backtest$"), (route) => { sent(route); server.done.strategy = true; return route.fulfill({ json: S.backtest }); });
  await page.route(api("/deployments$"), (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: F.deployments });
    const body = sent(route) as Record<string, unknown>;
    server.done.strategy = true;
    return route.fulfill({ json: { id: 91, status: "ACTIVE", ...body } });
  });
  await page.route(api("/paper/risk$"), (route) => {
    if (route.request().method() !== "PUT") return route.fulfill({ json: S.paper_risk });
    sent(route);
    server.done.risk = true;
    return route.fulfill({ json: { ...(S.paper_risk as object), change: { applied: {}, deferred: {}, from: null } } });
  });
  await page.route(api("/paper/account$"), (route) => { sent(route); return route.fulfill({ json: {} }); });
  await page.route(api("/alert-channels$"), reply(S.alert_channels));
  await page.route(api("/alert-channels/deliveries"), reply([]));
  await page.route(api("/alert-channels/\\w+/test$"), (route) => { sent(route); server.done.alerts = true; return route.fulfill({ json: { ok: true, detail: "Test message sent via TELEGRAM" } }); });
  await page.route(api("/readiness"), reply(S.readiness));
  await page.goto(o.path ?? "/settings/setup");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return server;
}

const wizard = (page: Page) => page.locator("[data-block='wizard']");
const stepTitle = (page: Page) => wizard(page).locator("h2");

test.describe("setup wizard 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  const SHOTS: [number, Open, string][] = [
    [1, {}, "Connect your broker"],
    [2, { done: { broker: true } }, "Set your risk limits"],
    [3, { done: { broker: true, risk: true } }, "Pick your first strategy"],
    [4, { done: { broker: true, risk: true }, stored: { steps: { ...blank().steps, strategy: { skipped_at: SEEN } } } }, "Choose your alerts"],
  ];
  for (const [n, o, title] of SHOTS) {
    test(`step ${n} looks the same`, async ({ page }) => {
      await open(page, o);
      await expect(stepTitle(page)).toHaveText(title);
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
      await expect(page).toHaveScreenshot(`setup-v2-step${n}-390-light.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  for (const [n, o] of SHOTS) {
    test(`step ${n}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
      await open(page, o);
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
      await expectPhoneLayout(page);
    });
  }

  test("skip for now, then resume after a reload on the next open step; the chip follows", async ({ page }) => {
    const server = await open(page);
    await wizard(page).getByRole("button", { name: "I'll trade on paper first" }).click();
    await expect(stepTitle(page)).toHaveText("Set your risk limits");
    await wizard(page).getByRole("button", { name: "Skip for now" }).click();
    await expect(stepTitle(page)).toHaveText("Pick your first strategy");
    await expect.poll(() => server.version).toBe(3);
    const puts = server.sent.filter((c) => c.url.startsWith("/me/setup"));
    expect(puts.map((c) => c.url)).toEqual(["/me/setup?version=1", "/me/setup?version=2"]);
    expect((puts[1].body as Stored).steps).toMatchObject({ broker: { skipped_at: expect.any(String) }, risk: { skipped_at: expect.any(String) } });

    await page.reload();
    await expect(stepTitle(page)).toHaveText("Pick your first strategy");
    await expect(wizard(page).locator("[data-step-state='skipped']")).toHaveCount(2);
    await page.getByRole("link", { name: "Home" }).click();
    await expect(page.locator("[data-block='setup']")).toContainText("Setup 3 of 4");
    await expect(page.locator("[data-block='setup']")).toContainText("Next: pick your first strategy");
  });

  test("the risk step: the spec's defaults, the sentence, max trades under Advanced, saved through /paper/risk", async ({ page }) => {
    const server = await open(page, { done: { broker: true } });
    const form = wizard(page);
    await expect(form.locator("[data-field='capital']")).toHaveValue("1,00,000");
    await expect(form.locator("[data-field='riskPerTradePct']")).toHaveValue("1");
    await expect(form.locator("[data-field='dailyLossPct']")).toHaveValue("3");
    await expect(form.locator("[data-risk-sentence]")).toContainText("With these limits the most you can lose today is ₹3,000.");
    await expect(form.locator("[data-field='maxTradesPerDay']")).toBeHidden();
    await form.getByText("Advanced").click();
    await expect(form.locator("[data-field='maxTradesPerDay']")).toHaveValue("10");
    await form.locator("[data-field='dailyLossPct']").fill("40");
    await form.locator("[data-field='dailyLossPct']").blur();
    await expect(form).toContainText("Daily loss limit must be between 0.1 and 20.");
    await form.locator("[data-field='dailyLossPct']").fill("2");
    await expect(form.locator("[data-risk-sentence]")).toContainText("is ₹2,000.");
    // A focused field hides the bottom bar (PW2); leave the field first, as a finger closing the keyboard does.
    await form.locator("[data-field='dailyLossPct']").blur();
    await form.getByRole("button", { name: "Save limits" }).click();
    await expect(form).toContainText("Limits saved.");
    const put = server.sent.find((c) => c.method === "PUT" && c.url === "/paper/risk");
    expect(put?.body).toEqual({ risk_per_trade_pct: 1, daily_loss_pct: 2, max_trades_per_day: 10 });
    expect(server.sent.some((c) => c.url === "/paper/account")).toBe(false);          // the capital did not change
    await expect(wizard(page).locator("[data-step-state='done']")).toHaveCount(2);
    await wizard(page).getByRole("button", { name: "Next step" }).click();
    await expect(stepTitle(page)).toHaveText("Pick your first strategy");
  });

  test("the strategy step deploys in PAPER only and never offers LIVE", async ({ page }) => {
    const server = await open(page, { done: { broker: true, risk: true } });
    const cards = wizard(page).locator("[data-suggestion]");
    await expect(cards).toHaveCount(3);
    await expect(wizard(page)).not.toContainText(/\bLive\b|\bLIVE\b|real money/);
    await cards.first().getByRole("button", { name: "Test it" }).click();
    await expect(cards.first()).toContainText("12 trades · 58% won · +₹4,250 on the last 30 days of NIFTY 50 prices.");
    await cards.nth(2).getByRole("button", { name: "Start on Paper Autopilot" }).click();
    await expect(cards.nth(2)).toContainText("Started on Paper Autopilot.");
    const post = server.sent.find((c) => c.method === "POST" && c.url === "/deployments");
    expect(post?.body).toMatchObject({ strategy_id: "orb_15m_5m", mode: "PAPER", instrument_kind: "OPTION", option_position: "BUY" });
    expect(post?.body).not.toHaveProperty("broker_name");
    expect(server.sent.filter((c) => JSON.stringify(c.body ?? "").includes("LIVE"))).toEqual([]);
    await expect(wizard(page).locator("[data-step-state='done']")).toHaveCount(3);
  });

  test("the alerts step sends a test through the existing API and the step is done", async ({ page }) => {
    const server = await open(page, { done: { broker: true, risk: true, strategy: true } });
    const telegram = wizard(page).locator("[data-channel='TELEGRAM']");
    await telegram.getByRole("button", { name: "Send a test" }).click();
    await expect(telegram).toContainText("Test sent. Check that it arrived.");
    expect(server.sent.some((c) => c.method === "POST" && c.url === "/alert-channels/telegram/test")).toBe(true);
    await expect(wizard(page).locator("[data-step-state='done']")).toHaveCount(4);
    await wizard(page).getByRole("button", { name: "Finish" }).click();
    await expect(wizard(page)).toContainText("Setup is complete.");
  });

  test("Settings → Setup also holds the go-live checklist", async ({ page }) => {
    await open(page);
    await expect(page.locator("[data-block='go-live']")).toContainText("Go-live checklist");
    await expect(page.locator("[data-block='go-live']")).toContainText("Risk limits");
  });
});

test.describe("setup chip and first sign-in", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the dashboard chip reads the stored progress, opens the wizard on that step, and can be closed", async ({ page }) => {
    const server = await open(page, { path: "/", done: { broker: true } });
    const chip = page.locator("[data-block='setup']");
    await expect(chip).toContainText("Setup 2 of 4");
    await expect(chip).toContainText("Next: set your risk limits");
    await chip.getByRole("link").click();
    await expect(page).toHaveURL(/\/settings\/setup$/);
    await expect(stepTitle(page)).toHaveText("Set your risk limits");
    await page.getByRole("link", { name: "Home" }).click();
    await page.getByRole("button", { name: "Hide the setup reminder" }).click();
    await expect(page.locator("[data-block='setup']")).toHaveCount(0);
    await expect.poll(() => server.stored.dismissed_at).not.toBeNull();
    await page.reload();
    await expect(page.locator("[data-block='strip']")).toBeVisible();
    await expect(page.locator("[data-block='setup']")).toHaveCount(0);
    // Settings still lists Setup until it is complete
    await page.goto("/settings");
    await expect(page.locator("[data-block='setup']")).toContainText("Setup 2 of 4");
  });

  test("first sign-in under ui_v2: the dashboard opens the wizard once", async ({ page }) => {
    const server = await open(page, { path: "/", stored: { opened_at: null } });
    await expect(page).toHaveURL(/\/settings\/setup$/);
    await expect(stepTitle(page)).toHaveText("Connect your broker");
    await expect.poll(() => server.stored.opened_at).not.toBeNull();
    await page.getByRole("link", { name: "Home" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("[data-block='setup']")).toContainText("Setup 1 of 4");
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("no setup page and no wizard; today's dashboard keeps its go-live checklist", async ({ page }) => {
    const server = await open(page, { flag: false, path: "/settings/setup", stored: { opened_at: null } });
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-block='wizard'], [data-block='setup']")).toHaveCount(0);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Go-live checklist")).toBeVisible();
    expect(server.sent.filter((c) => c.url.startsWith("/me/setup"))).toEqual([]);
  });
});
