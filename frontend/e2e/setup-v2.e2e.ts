import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UI2-7 (UI v2 proposal §11, §12, §13 UI-8): the setup wizard at Settings → Setup and the Setup chip in Settings, with
 * the `ui_v2` flag on. P3: the dashboard shows the Get started card instead of the chip and never opens the wizard; the
 * go-live checklist moved to Settings → Advanced ("Prepare for LIVE", e2e/front-page.e2e.ts). The progress is the server's (`GET/PUT /api/me/setup`), mocked here as a small server that keeps
 * what was saved and works out "done" from what the page did (a PAPER deployment, saved limits, a test alert).
 * - Baselines `setup-v2-step{1..4}-390-light.png`.
 * - Skip for now, resume after a reload, the chip's words in Settings, no automatic opening from the dashboard, PAPER
 *   only, and the flag off (no setup page; today's dashboard shows the Get started card, not the go-live checklist).
 * Every reply is hand-written, made-up data (e2e/fixtures/setup_v2.json, dashboard_v2.json): no real account or id.
 * After an intended change: `npx playwright test --project=phone e2e/setup-v2.e2e.ts --update-snapshots`, check the new
 * images, commit.
 */

const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const KEYS = ["broker", "risk", "strategy", "alerts"] as const;
type Key = typeof KEYS[number];
type Stored = { steps: Record<Key, { skipped_at: string | null }>; opened_at: string | null; dismissed_at: string | null;
  get_started_hidden_at: string | null; get_started_shown_at: string | null };

const SEEN = "2026-10-12T09:00:00Z";
const blank = (): Stored => ({ steps: { broker: { skipped_at: null }, risk: { skipped_at: null }, strategy: { skipped_at: null }, alerts: { skipped_at: null } },
  opened_at: SEEN, dismissed_at: null, get_started_hidden_at: null, get_started_shown_at: null });

/** The mocked server's state, and what the page sent. */
interface Server { stored: Stored; version: number; done: Record<Key, boolean>; sent: { method: string; url: string; body: unknown }[];
  /** UX2 fix 5: GET /alerts/telegram/connect. */
  telegram?: { available: boolean; connected: boolean; own_bot: boolean; working: boolean } }

function answer(s: Server) {
  const steps = KEYS.map((key) => {
    const skippedAt = s.stored.steps[key].skipped_at;
    return { key, done: s.done[key], skipped: !s.done[key] && skippedAt !== null, skipped_at: skippedAt };
  });
  // As the backend: a skip only changes the order; a skipped broker step counts as done (app/me/setup.py view()).
  const open = steps.filter((x) => !(x.done || (x.key === "broker" && x.skipped)));
  const next = (open.find((x) => !x.skipped) ?? open[0])?.key ?? null;
  // P3: the Get started card's three steps (its paper bot and Telegram steps read as the strategy and alerts steps here).
  const card = [{ key: "broker", done: s.done.broker }, { key: "paper_bot", done: s.done.strategy }, { key: "telegram", done: s.done.alerts }];
  const cardDone = card.every((c) => c.done);
  const get_started = { steps: card, done_count: card.filter((c) => c.done).length, complete: cardDone, hidden_at: s.stored.get_started_hidden_at,
    shown_at: s.stored.get_started_shown_at, visible: s.stored.get_started_hidden_at === null && (!cardDone || s.stored.get_started_shown_at !== null) };
  return { steps, done_count: steps.filter((x) => x.done).length, next, complete: !open.length, get_started, stored: s.stored, version: s.version };
}

interface Open {
  path?: string;
  flag?: boolean;
  done?: Partial<Record<Key, boolean>>;
  stored?: Partial<Stored>;
  /** Keep the same server across a reload (resume). */
  server?: Server;
  /** Every PUT /me/setup fails (the server is down for saves). */
  failSaves?: boolean;
  /** The signed-in user's role (default the fixture's OWNER). */
  role?: string;
  /** GET /paper/account answers 404 (the paper account is off). */
  noPaperAccount?: boolean;
  /** POST /deployments answers 409 (already deployed in the organisation). */
  deployConflict?: boolean;
  /** UX2 fix 5: no Telegram channel yet, and this server has the platform's bot (Connect Telegram in one tap). */
  telegramConnect?: boolean;
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
    // The alerts step shows "Turn on here" only where the browser has the Push API and has not decided on notifications
    // (src/alerts/push.ts pushReadiness). Chromium builds differ (CI's headless build answered otherwise, moving the
    // whole step), so every run gets the same browser: push supported, permission not asked yet. Nothing here is asked
    // or subscribed: the tests never press "Turn on here".
    const w = window as unknown as Record<string, unknown>;
    if (!("PushManager" in window)) w.PushManager = function PushManager() { /* stub */ };
    if (!("serviceWorker" in navigator)) {
      Object.defineProperty(Navigator.prototype, "serviceWorker", { configurable: true, get: () => ({ register: () => Promise.reject(new Error("not in tests")) }) });
    }
    w.Notification = class { static permission = "default"; static requestPermission() { return Promise.resolve("default"); } };
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: o.flag === false ? undefined : (F.features as Record<string, boolean>), brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  if (o.role) await page.route(api("/auth/me$"), (route) => route.fulfill({ json: { ...(F.me as object), role: o.role } }));
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
    if (o.failSaves) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
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
    if (o.deployConflict) return route.fulfill({ status: 409, json: { detail: "Deployment already exists" } });
    server.done.strategy = true;
    return route.fulfill({ json: { id: 91, status: "ACTIVE", ...body } });
  });
  await page.route(api("/paper/risk$"), (route) => {
    if (route.request().method() !== "PUT") return route.fulfill({ json: S.paper_risk });
    sent(route);
    server.done.risk = true;
    return route.fulfill({ json: { ...(S.paper_risk as object), change: { applied: {}, deferred: {}, from: null } } });
  });
  await page.route(api("/paper/account$"), (route) => {
    if (o.noPaperAccount) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    if (route.request().method() === "GET") return route.fulfill({ json: { starting_capital: 100000 } });
    const body = sent(route) as { starting_capital: number };
    return route.fulfill({ json: { starting_capital: body.starting_capital } });
  });
  // UX2 fix 5: the fixture's channel is the organisation's own bot; `telegramConnect` starts with none and the platform bot.
  server.telegram ??= o.telegramConnect ? { available: true, connected: false, own_bot: false, working: false }
    : { available: true, connected: false, own_bot: true, working: false };
  await page.route(api("/alert-channels$"), (route) => route.fulfill({ json: server.telegram?.own_bot ? S.alert_channels
    : server.telegram?.connected ? [{ ...(S.alert_channels as object[])[0], config: { chat_id: "masked", platform_bot: true } }] : [] }));
  await page.route(api("/alerts/telegram/connect$"), (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: server.telegram });
    sent(route);
    return route.fulfill({ json: { url: "https://t.me/ExampleAlertsBot?start=AbCdEfGhIjKlMnOp", expires_at: "2026-10-21T04:45:00Z" } });
  });
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
      if (n === 4) await expect(wizard(page).locator("[data-channel='PUSH']").getByRole("button", { name: "Turn on here" })).toBeVisible();
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

  test("skip for now, then resume after a reload on the next open step; the Settings chip follows", async ({ page }) => {
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
    // P3: the dashboard shows the Get started card, not the Setup chip
    await expect(page.locator("[data-block='get-started']")).toBeVisible();
    await expect(page.locator("[data-block='setup']")).toHaveCount(0);
    // a skip only changes the order: the skipped risk step stays open; the skipped broker step counts (paper first)
    // Settings redesign B: the Settings index's "Set up your account" row carries the chip
    await page.goto("/settings");
    await expect(page.locator("[data-row='setup'] [data-chip]")).toHaveText("Setup 2 of 4");
  });

  test("the risk step: the spec's defaults, the sentence, max trades under Advanced, saved through /paper/risk", async ({ page }) => {
    const server = await open(page, { done: { broker: true } });
    const form = wizard(page);
    await expect(form.getByLabel("Starting capital")).toHaveValue("1,00,000");                // from GET /paper/account
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
    // a changed starting capital is saved after the limits
    await form.locator("[data-field='capital']").fill("2,00,000");
    await form.locator("[data-field='capital']").blur();
    await expect(form.locator("[data-risk-sentence]")).toContainText("is ₹4,000.");
    await form.getByRole("button", { name: /Save/ }).click();
    await expect.poll(() => server.sent.filter((c) => c.method === "PUT" && /^\/paper\/(risk|account)$/.test(c.url)).map((c) => c.url))
      .toEqual(["/paper/risk", "/paper/risk", "/paper/account"]);
    expect(server.sent.find((c) => c.url === "/paper/account")?.body).toEqual({ starting_capital: 200000 });
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
    await cards.nth(2).getByRole("button", { name: "Start a paper bot" }).click();
    await expect(cards.nth(2)).toContainText("Started as a paper bot.");
    const post = server.sent.find((c) => c.method === "POST" && c.url === "/deployments");
    expect(post?.body).toMatchObject({ strategy_id: "orb_15m_5m", mode: "PAPER", instrument_kind: "OPTION", option_position: "BUY" });
    expect(post?.body).not.toHaveProperty("broker_name");
    expect(server.sent.filter((c) => JSON.stringify(c.body ?? "").includes("LIVE"))).toEqual([]);
    await expect(wizard(page).locator("[data-step-state='done']")).toHaveCount(3);
  });

  test("the risk step without a paper account: no capital field, nothing sent for it", async ({ page }) => {
    const server = await open(page, { done: { broker: true }, noPaperAccount: true });
    const form = wizard(page);
    await expect(form.locator("[data-field='dailyLossPct']")).toHaveValue("3");
    await expect(form.locator("[data-field='capital']")).toHaveCount(0);
    await expect(form.locator("[data-risk-sentence]")).toContainText("is ₹3,000.");            // of the server's capital basis
    await form.getByRole("button", { name: "Save limits" }).click();
    await expect(form).toContainText("Limits saved.");
    expect(server.sent.filter((c) => c.url === "/paper/account")).toEqual([]);
  });

  test("the strategy step: already deployed in the organisation (409) says nothing new started and is not done", async ({ page }) => {
    await open(page, { done: { broker: true, risk: true }, deployConflict: true });
    const first = wizard(page).locator("[data-suggestion]").first();
    await first.getByRole("button", { name: "Start a paper bot" }).click();
    await expect(first).toContainText("Nothing new was started: this strategy already runs on paper for NIFTY 50 in your organisation. Use Test it");
    await expect(first).not.toContainText("Started as a paper bot");
    await expect(wizard(page).locator("[data-step-state='done']")).toHaveCount(2);
  });

  test("the alerts step sends a test through the existing API and the step is done", async ({ page }) => {
    const server = await open(page, { done: { broker: true, risk: true, strategy: true } });
    const telegram = wizard(page).locator("[data-channel='TELEGRAM']");
    await expect(telegram).toContainText("Your organisation's own Telegram bot sends your alerts.");
    await expect(telegram.getByRole("button", { name: "Link my Telegram" })).toBeVisible();
    await telegram.getByRole("button", { name: "Send a test" }).click();
    await expect(telegram).toContainText("Test sent. Check your Telegram chat.");
    expect(server.sent.some((c) => c.method === "POST" && c.url === "/alert-channels/telegram/test")).toBe(true);
    await expect(wizard(page).locator("[data-step-state='done']")).toHaveCount(4);
    await wizard(page).getByRole("button", { name: "Finish" }).click();
    await expect(wizard(page)).toContainText("Setup is complete.");
  });

  test("UX2 fix 5: Connect Telegram in one tap - the link opens, Start in Telegram, the row turns Connected with Send a test", async ({ page }) => {
    await page.context().route(/t\.me\//, (route) => route.fulfill({ body: "<title>Telegram</title>", contentType: "text/html" }));
    const server = await open(page, { done: { broker: true, risk: true, strategy: true }, telegramConnect: true });
    const telegram = wizard(page).locator("[data-channel='TELEGRAM']");
    await expect(telegram).toContainText("Get alerts on your phone in Telegram. One tap, then press Start.");
    await expect(telegram).not.toContainText(/token|chat id|BotFather/i);
    const popup = page.waitForEvent("popup");
    await telegram.getByRole("button", { name: "Connect Telegram" }).click();
    await expect.poll(async () => (await popup).url()).toContain("https://t.me/ExampleAlertsBot?start=");
    await expect(telegram).toContainText("Press Start there, then come back.");
    await expect(telegram.getByRole("link", { name: "Open Telegram" })).toHaveAttribute("href", "https://t.me/ExampleAlertsBot?start=AbCdEfGhIjKlMnOp");
    expect(server.sent.some((c) => c.method === "POST" && c.url === "/alerts/telegram/connect")).toBe(true);
    server.telegram = { available: true, connected: true, own_bot: false, working: true };       // Start was pressed in Telegram
    await page.clock.runFor(3500);
    await expect(telegram).toContainText("Connected");
    await expect(telegram).toContainText("Telegram is connected. A welcome message is in your chat.");
    await expect(telegram.getByRole("button", { name: "Send a test" })).toBeVisible();
    await expect(telegram.getByRole("button", { name: "Link my Telegram" })).toHaveCount(0);
    await telegram.evaluate((el) => el.scrollIntoView({ block: "center" }));        // clear of the bottom navigation
    await expect(telegram).toHaveScreenshot("setup-v2-telegram-connected-390-light.png", { animations: "disabled", maxDiffPixelRatio: 0.02 });
  });

  test("Settings → Setup links to the go-live checklist, now under Settings → Advanced", async ({ page }) => {
    // Settings redesign B3: only while phase0_paper_only is off (these flags leave it off); v2 address.
    await open(page);
    await expect(page.locator("[data-block='go-live']")).toHaveCount(0);
    await page.locator("[data-block='prepare-live']").click();
    await expect(page).toHaveURL(/\/settings\/advanced#go-live$/);
    await expect(page.locator("[data-block='go-live']")).toContainText("Go-live checklist");
    await expect(page.locator("[data-block='go-live'] [data-row='risk']")).toContainText("Risk limits set");
  });
});

test.describe("the dashboard and the wizard (P3)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the dashboard shows the Get started card, not the Setup chip; Settings keeps the chip, which opens the wizard", async ({ page }) => {
    await open(page, { path: "/", done: { broker: true } });
    const card = page.locator("[data-block='get-started']");
    await expect(card).toContainText("1 of 3 done");
    await expect(page.locator("[data-block='setup']")).toHaveCount(0);
    await page.goto("/settings");
    const chip = page.locator("[data-row='setup']");
    await expect(chip.locator("[data-chip]")).toHaveText("Setup 2 of 4");
    await chip.getByRole("link").click();
    await expect(page).toHaveURL(/\/settings\/setup$/);
    await expect(stepTitle(page)).toHaveText("Set your risk limits");
  });

  test("the dashboard never opens the wizard, not even on the first sign-in", async ({ page }) => {
    const server = await open(page, { path: "/", stored: { opened_at: null } });
    await expect(page.locator("[data-block='strip']")).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("[data-block='get-started']")).toContainText("0 of 3 done");
    await expect(page.locator("[data-block='wizard']")).toHaveCount(0);
    expect(server.stored.opened_at).toBeNull();
    // the wizard's own address still works, and records its first opening there
    await page.goto("/settings/setup");
    await expect(stepTitle(page)).toHaveText("Connect your broker");
    await expect.poll(() => server.stored.opened_at).not.toBeNull();
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("no setup page and no wizard; today's dashboard shows the Get started card, not the go-live checklist", async ({ page }) => {
    const server = await open(page, { flag: false, path: "/settings/setup", stored: { opened_at: null } });
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-block='wizard'], [data-block='setup']")).toHaveCount(0);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[data-block='get-started']")).toContainText("0 of 3 done");
    await expect(page.getByText("Go-live checklist")).toHaveCount(0);
    expect(server.sent.filter((c) => c.url.startsWith("/me/setup"))).toEqual([]);         // nothing saved; the wizard never opened
  });
});
