import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * P3 (the owner's "simple front page"): today's Dashboard (the default shell, `ui_v2` off) with the Get started card at
 * the top, and the Go-live checklist under Settings → Advanced, reached from "Prepare for LIVE".
 * - Baselines `front-page-{1440,390}-{light,dark}.png` (the first screen of `/`, one step of three done) and
 *   `go-live-{390-light,390-dark,1440-light}.png` (the checklist block, for LIVE).
 * - The card in states 0-3 and hidden, Hide, "for good", Help → Show Get started, no LIVE on the front page in PAPER,
 *   and the checklist's folds.
 * The setup progress is the server's (`GET/PUT /api/me/setup`), mocked as a small server that keeps what was saved.
 * Every reply is hand-written, made-up data (e2e/fixtures/front_page.json, dashboard_v2.json): no real account or id.
 * After an intended change: `npx playwright test --project=phone e2e/front-page.e2e.ts --update-snapshots=all`, check
 * the new images, commit.
 */

const P = JSON.parse(readFileSync(new URL("./fixtures/front_page.json", import.meta.url), "utf8")) as Record<string, unknown>;
const KEYS = ["broker", "paper_bot", "telegram"] as const;
type Key = typeof KEYS[number];
type Stored = Record<string, unknown> & { get_started_hidden_at: string | null; get_started_shown_at: string | null };

const blank = (): Stored => ({
  steps: { broker: { skipped_at: null }, risk: { skipped_at: null }, strategy: { skipped_at: null }, alerts: { skipped_at: null } },
  opened_at: "2026-10-12T09:00:00Z", dismissed_at: null, get_started_hidden_at: null, get_started_shown_at: null,
});

interface Server { stored: Stored; version: number; done: Record<Key, boolean>; sent: { method: string; url: string; body: Stored }[] }

/** As the backend answers (app/me/setup.py view, get_started_view). */
function answer(s: Server) {
  const wizard = { broker: s.done.broker, risk: true, strategy: s.done.paper_bot, alerts: s.done.telegram } as Record<string, boolean>;
  const steps = Object.keys(wizard).map((key) => ({ key, done: wizard[key], skipped: false, skipped_at: null }));
  const gs = KEYS.map((key) => ({ key, done: s.done[key] }));
  const complete = gs.every((g) => g.done);
  return {
    steps, done_count: steps.filter((x) => x.done).length, next: steps.find((x) => !x.done)?.key ?? null, complete: steps.every((x) => x.done),
    get_started: { steps: gs, done_count: gs.filter((g) => g.done).length, complete, hidden_at: s.stored.get_started_hidden_at,
      shown_at: s.stored.get_started_shown_at, visible: s.stored.get_started_hidden_at === null && (!complete || s.stored.get_started_shown_at !== null) },
    stored: s.stored, version: s.version,
  };
}

interface Open {
  path?: string; theme?: "light" | "dark"; done?: Key[]; stored?: Partial<Stored>; role?: string;
  /** The user's e-mail is not verified and LIVE is paused on a broker: both top-bar notices would show. */
  liveNotices?: boolean;
  /** GET /deployments answers with a LIVE deployment, or fails. */
  deployments?: "live" | "fail";
}

async function open(page: Page, o: Open = {}): Promise<Server> {
  const server: Server = { stored: { ...blank(), ...o.stored }, version: 1, sent: [],
    done: { broker: false, paper_bot: false, telegram: false, ...Object.fromEntries((o.done ?? []).map((k) => [k, true])) } };
  await page.clock.install({ time: new Date("2026-10-21T10:30:00+05:30") });
  await page.addInitScript((theme) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme }));
      localStorage.setItem("atp_install_dismissed", "1");              // the install offers are PW1's; not part of these shots
      localStorage.setItem("atp_install_domain_dismissed", "1");
    } catch { /* storage unavailable */ }
    // Browser capabilities differ between Chromium builds (CI's headless build answered otherwise for push before), so
    // every run gets the same browser: the Push API present, notifications not decided (the playwright config blocks
    // service workers). Nothing is asked or subscribed here.
    const w = window as unknown as Record<string, unknown>;
    if (!("PushManager" in window)) w.PushManager = function PushManager() { /* stub */ };
    if (!("serviceWorker" in navigator)) {
      Object.defineProperty(Navigator.prototype, "serviceWorker", { configurable: true, get: () => ({ register: () => Promise.reject(new Error("not in tests")) }) });
    }
    w.Notification = class { static permission = "default"; static requestPermission() { return Promise.resolve("default"); } };
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page);                                                  // no flags: today's shell (ui_v2 off)
  await mockDashboard(page, { me: true });
  const reply = (json: unknown) => (route: Route) => route.fulfill({ json });
  const paperOnly = (rows: unknown) => (rows as { mode: string }[]).filter((r) => r.mode !== "LIVE");
  if (o.role || o.liveNotices) {
    await page.route(api("/auth/me$"), reply({ ...(F.me as object), ...(o.role ? { role: o.role } : {}), ...(o.liveNotices ? { email_verified: false } : {}) }));
  }
  if (o.liveNotices) {
    await page.route(api("/system/status$"), reply({ maintenance_mode: false, maintenance_message: null, disabled_brokers: ["zerodha"] }));
  }
  await page.route(api("/positions$"), reply(paperOnly(F.positions)));
  // As the backend: stopped deployments only when `include_stopped=true` is asked. The "live" case has one LIVE deployment,
  // and it is stopped, so only a read that includes stopped ones can see it.
  await page.route(api("/deployments(\\?|$)"), (route) => {
    if (o.deployments === "fail") return route.fulfill({ status: 503, json: { detail: "unavailable" } });
    const rows = o.deployments === "live" ? [...paperOnly(F.deployments), { ...(F.deployments as object[])[0], id: 99, mode: "LIVE", status: "STOPPED" }]
      : paperOnly(F.deployments);
    const stopped = new URL(route.request().url()).searchParams.get("include_stopped") === "true";
    return route.fulfill({ json: stopped ? rows : (rows as { status: string }[]).filter((d) => d.status !== "STOPPED") });
  });
  await page.route(api("/strategies$"), reply(P.strategies));
  await page.route(api("/broker/available$"), reply(P.brokers));
  await page.route(api("/system/health$"), reply(P.health));
  await page.route(api("/system/worker-status$"), reply(P.worker));
  await page.route(api("/accounts$"), reply(P.accounts));
  await page.route(api("/readiness"), (route) => route.fulfill({
    json: new URL(route.request().url()).searchParams.get("target") === "PAPER" ? P.readiness_paper : P.readiness_live }));
  await page.route(api("/me/setup(\\?|$)"), async (route) => {
    const r = route.request();
    if (r.method() === "GET") return route.fulfill({ json: answer(server) });
    const body = r.postDataJSON() as Stored;
    server.sent.push({ method: r.method(), url: r.url().replace(/^https?:\/\/[^/]+\/api(?:\/v1)?/, ""), body });
    server.stored = body;
    server.version += 1;
    return route.fulfill({ json: answer(server) });
  });
  await page.goto(o.path ?? "/");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return server;
}

const card = (page: Page) => page.locator("[data-block='get-started']");
/** "Connect your broker": the Broker page (/broker) once it exists, today's broker settings before (src/setup/getStarted.ts). */
const BROKER_HREF = /^\/(broker|settings)$/;
const SHOT = { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 } as const;
const THEMES = ["light", "dark"] as const;
const SIZES = [{ name: "1440", viewport: { width: 1440, height: 900 }, phone: false }, { name: "390", viewport: { width: 390, height: 844 }, phone: true }];

for (const size of SIZES) {
  test.describe(`front page ${size.name}`, () => {
    test.use(size.phone ? { viewport: size.viewport, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size.viewport });

    for (const theme of THEMES) {
      test(`the Dashboard looks the same (${theme})`, async ({ page }) => {
        await open(page, { theme, done: ["broker"] });
        await expect(card(page)).toContainText("1 of 3 done");
        await expect(page).toHaveScreenshot(`front-page-${size.name}-${theme}.png`, SHOT);
      });
    }

    test("the Get started card is the first thing under the greeting, and the go-live checklist is gone", async ({ page }) => {
      await open(page, { done: ["broker"] });
      const header = await page.locator("main header").first().boundingBox();
      const top = await card(page).boundingBox();
      expect(top!.y).toBeGreaterThan(header!.y);
      const others = await page.locator("main .shadow-card").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().top));
      expect(Math.min(...others)).toBeGreaterThanOrEqual(top!.y - 1);
      await expect(page.locator("main")).not.toContainText("Go-live checklist");
    });
  });
}

test.describe("Get started card 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("0, 1 and 2 steps done: the card, a tick per done step, one button per step", async ({ page }) => {
    const done: Key[][] = [[], ["broker"], ["broker", "paper_bot"]];
    for (const [n, keys] of done.entries()) {
      const p = n === 0 ? page : await page.context().newPage();
      await open(p, { done: keys });
      await expect(card(p)).toContainText(`${n} of 3 done`);
      await expect(card(p).locator("[data-done='true']")).toHaveCount(n);
      await expect(card(p).locator("li")).toHaveCount(3);
      for (const li of await card(p).locator("li").all()) await expect(li.locator("a")).toHaveCount(1);
      await expect(card(p).locator("li").nth(0)).toContainText("Connect your broker");
      await expect(card(p).locator("li").nth(1)).toContainText("Start your paper bot");
      await expect(card(p).locator("li").nth(2)).toContainText("Get alerts on your phone (Telegram)");
      // nothing on the front page opens the four-step wizard
      await expect(p).toHaveURL(/\/$/);
      await expect(p.locator("[data-block='wizard'], a[href='/settings/setup']")).toHaveCount(0);
    }
  });

  test("the buttons: Connect your broker leads to /broker (the broker settings until that page exists); Telegram to Settings → Alerts", async ({ page }) => {
    await open(page);
    await expect(card(page).getByRole("link", { name: "Connect your broker" })).toHaveAttribute("href", BROKER_HREF);
    await expect(card(page).getByRole("link", { name: "Start paper bot" })).toHaveAttribute("href", "/deployments");
    // Settings redesign B: the link is the v2 address; with ui_v2 off it goes back to today's /settings#alerts.
    await expect(card(page).getByRole("link", { name: "Set up Telegram" })).toHaveAttribute("href", "/settings/alerts");
    await card(page).getByRole("link", { name: "Set up Telegram" }).click();
    await expect(page).toHaveURL(/\/settings#alerts$/);
    await expect(page.locator("#alerts")).toBeInViewport({ timeout: 5_000 });
  });

  test("all three done: no card, and it is hidden for good (stored once)", async ({ page }) => {
    const server = await open(page, { done: ["broker", "paper_bot", "telegram"] });
    await expect(card(page)).toHaveCount(0);
    await expect.poll(() => server.stored.get_started_hidden_at).not.toBeNull();
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0].url).toBe("/me/setup?version=1");
    expect(server.sent[0].body).toMatchObject({ opened_at: "2026-10-12T09:00:00Z", get_started_shown_at: null });
    // a step opens again later (the broker removed): still gone
    server.done.broker = false;
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator("main")).toBeVisible();
    await expect(card(page)).toHaveCount(0);
  });

  test("Hide: the card goes and stays gone; Help in Settings brings it back", async ({ page }) => {
    const server = await open(page, { done: ["broker"] });
    await card(page).getByRole("button", { name: "Hide Get started" }).click();
    await expect(card(page)).toHaveCount(0);
    await expect.poll(() => server.stored.get_started_hidden_at).not.toBeNull();
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator("main")).toBeVisible();
    await expect(card(page)).toHaveCount(0);
    await page.goto("/settings");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Show Get started" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(card(page)).toContainText("1 of 3 done");
    expect(server.stored).toMatchObject({ get_started_hidden_at: null, get_started_shown_at: null });
  });

  test("Help with every step done shows the three ticks until hidden again", async ({ page }) => {
    const server = await open(page, { path: "/settings", done: ["broker", "paper_bot", "telegram"], stored: { get_started_hidden_at: "2026-10-20T09:00:00Z" } });
    await page.getByRole("button", { name: "Show Get started" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(card(page)).toContainText("All set.");
    await expect(card(page).locator("[data-done='true']")).toHaveCount(3);
    expect(server.stored.get_started_shown_at).not.toBeNull();
    await card(page).getByRole("button", { name: "Hide Get started" }).click();
    await expect(card(page)).toHaveCount(0);
    await expect.poll(() => server.stored.get_started_shown_at).toBeNull();
  });

  test("no card for a VIEWER", async ({ page }) => {
    await open(page, { role: "VIEWER" });
    await expect(page.locator("main header").first()).toBeVisible();
    await expect(card(page)).toHaveCount(0);
  });

  test("phone layout: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
    await open(page, { theme: "dark", done: ["broker"] });
    await expectPhoneLayout(page);
  });

  test("nothing about LIVE on the front page in PAPER: no MFA, e-mail, SEBI algo id or risk-default rows", async ({ page }) => {
    await open(page, { done: ["broker"] });
    const main = page.locator("main");
    await expect(main).toContainText("Get started");
    for (const gone of [/\bLIVE\b/, /MFA/, /SEBI/, /algo id/i, /Verify your email/i, /Email verified/i, /Platform defaults/i, /Go-live/i, /Prepare for LIVE/]) {
      await expect(main, String(gone)).not.toContainText(gone);
    }
  });
});

test.describe("top bar notices on the front page (P3 review)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const header = (page: Page) => page.locator("header").first();

  test("in PAPER: no 'LIVE paused' on the front page; other pages keep it; no 'Verify your email' chip anywhere (item 1)", async ({ page }) => {
    await open(page, { liveNotices: true });
    await expect(card(page)).toBeVisible();
    await expect(header(page)).not.toContainText("Verify your email");
    await expect(header(page)).not.toContainText("LIVE paused");
    await page.getByRole("link", { name: /My trades|Positions/ }).first().click();
    await expect(page).toHaveURL(/\/positions$/);
    await expect(header(page)).not.toContainText("Verify your email");
    await expect(header(page)).toContainText("LIVE paused on zerodha");
  });

  test("with a LIVE deployment (even a stopped one), or when the deployments cannot be read, the front page keeps LIVE paused", async ({ page }) => {
    for (const deployments of ["live", "fail"] as const) {
      const p = deployments === "live" ? page : await page.context().newPage();
      await open(p, { liveNotices: true, deployments });
      await expect(header(p), deployments).not.toContainText("Verify your email");
      await expect(header(p), deployments).toContainText("LIVE paused on zerodha");
    }
  });
});

test.describe("Settings → Advanced: the Go-live checklist", () => {
  const SHOTS = [
    { name: "390-light", theme: "light" as const, viewport: { width: 390, height: 844 }, phone: true },
    { name: "390-dark", theme: "dark" as const, viewport: { width: 390, height: 844 }, phone: true },
    { name: "1440-light", theme: "light" as const, viewport: { width: 1440, height: 900 }, phone: false },
  ];
  for (const s of SHOTS) {
    test.describe(s.name, () => {
      test.use(s.phone ? { viewport: s.viewport, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: s.viewport });
      test(`looks the same (${s.name})`, async ({ page }) => {
        await open(page, { path: "/settings#go-live", theme: s.theme });
        const block = page.locator("[data-block='go-live']");
        await expect(block.locator("[data-headline]")).toHaveText("3 blockers and 1 warning");
        await expect(block).not.toHaveAttribute("aria-busy", "true");
        await expect(block).toHaveScreenshot(`go-live-${s.name}.png`, SHOT);
      });
    });
  }

  test.describe("behaviour", () => {
    test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    test("'Prepare for LIVE' opens it; OK rows fold into 'N checks OK'; only blockers and warnings are open", async ({ page }) => {
      await open(page, { path: "/settings" });
      await expect(page.locator("[data-block='go-live']")).toHaveCount(0);           // folded under Advanced, not loaded
      await page.getByRole("link", { name: /Prepare for LIVE/ }).click();
      await expect(page).toHaveURL(/\/settings#go-live$/);
      const block = page.locator("[data-block='go-live']");
      await expect(block).toBeInViewport({ timeout: 5_000 });
      await expect(block.locator("[data-row]")).toHaveCount(4);
      await expect(block.locator("[data-row][data-status='todo']")).toHaveCount(3);
      await expect(block.locator("[data-row][data-status='warn']")).toHaveCount(1);
      for (const row of await block.locator("[data-row]").all()) await expect(row.locator("a, button")).toHaveCount(1);
      await expect(block.locator("[data-row='broker_session']").getByRole("link", { name: "Log in to your broker" })).toHaveAttribute("href", BROKER_HREF);
      const ok = block.locator("[data-fold='ok']");
      await expect(ok.locator("summary")).toHaveText("9 checks OK");
      await expect(ok.locator("li").first()).toBeHidden();
      await ok.locator("summary").click();
      await expect(ok.locator("li")).toHaveCount(9);
      await expect(ok.locator("li").first()).toBeVisible();
      await expect(block.locator("[data-fold='optional'] summary")).toHaveText("1 optional");
      // no developer footer, no internal numbers for passing items, no server wording
      for (const gone of ["Computed from", "heartbeat", "rows", "48211", "OAuth", "Settings > Brokers", "todo", "401"]) {
        await expect(block, gone).not.toContainText(gone);
      }
    });

    test("Paper: the LIVE-only items are optional, so one blocker and one warning", async ({ page }) => {
      await open(page, { path: "/settings#go-live" });
      const block = page.locator("[data-block='go-live']");
      await block.getByRole("button", { name: "Paper" }).click();
      await expect(block.locator("[data-headline]")).toHaveText("1 blocker and 1 warning");
      await expect(block.locator("[data-fold='optional'] summary")).toHaveText("3 optional");
    });
  });
});
