import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Strategy templates step 2 (docs/design/STRATEGY_TEMPLATES_IMPL.md): with `ui_v2` and `strategy_templates` on, the v2
 * Paper bot form lists the templates; picking one asks the server for the pre-fill ("Adopt as a paper strategy") and
 * "Start on paper" sends the same PAPER request with the template's strategy id and candles. Nothing starts before that.
 * - Baselines `strategy-templates-v2-{1440,390,360}-{light,dark}.png` (the form with a template picked) and, section 5,
 *   `strategy-templates-exits-v2-{1440,390,360}-{light,dark}.png` (its exits, a multiple of the risk chosen).
 * Every reply is made-up data (e2e/fixtures/setup_v2.json strategies, the templates below): no real account or id.
 * After an intended change: `npx playwright test --project=phone e2e/strategy-templates-v2.e2e.ts --update-snapshots`.
 */

const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const NOW = "2026-10-21T10:00:00+05:30";
const TF = [{ value: "5m", label: "5-minute" }, { value: "15m", label: "15-minute" }, { value: "1h", label: "1-hour" }, { value: "1d", label: "Daily" }];
const TEMPLATES = [
  { id: "D2", version: 1, name: "Moving-average crossover", family: "D", family_name: "Trend following",
    idea: "A signal when a fast moving average crosses a slow one on a closed bar; optionally only when the close is on the same side of a slower trend average.",
    difficulty: "Beginner", style: "Both", timeframes: TF, default_timeframe: "5m", sides_default: "both",
    params: [{ key: "fast", label: "Fast average", help: "Bars in the fast average.", type: "int", default: 9, min: 2, max: 200, step: 1 }],
    rules: [], available: true },
  { id: "B1", version: 1, name: "Range breakout", family: "B", family_name: "Breakout",
    idea: "A signal on the first close beyond a range or a consolidation by a set distance; the idea has ended when the close is back inside the range.",
    difficulty: "Beginner", style: "Both", timeframes: TF, default_timeframe: "5m", sides_default: "both", params: [], rules: [], available: true },
  { id: "B3", version: 1, name: "Channel breakout", family: "B", family_name: "Breakout", idea: "Not switched on here.",
    difficulty: "Intermediate", style: "Both", timeframes: TF, default_timeframe: "5m", sides_default: "both", params: [], rules: [], available: false },
];
const LABEL: Record<string, string> = { "5m": "5min", "15m": "15min", "1h": "60min", "1d": "day" };
const BOT = { id: 61, strategy_id: "template:D2:1:15m:both:,5,,,,,", symbol: "RELIANCE", exchange: "NSE", timeframe: "15min", mode: "PAPER",
  broker_name: "upstox", status: "ACTIVE", pause_reason: null, last_evaluated_at: "2026-10-21T04:28:00Z", last_signal_at: null, last_error: null,
  consecutive_failures: 0, open_positions: 0, created_by: 7, created_at: "2026-10-12T04:00:00Z", updated_at: "2026-10-19T04:51:00Z",
  instrument_kind: "UNDERLYING", option_position: null, expiry_rule: null, strike_rule: null, strike_offset: 0, premium_stop_pct: null,
  max_lots: null, contract_rules: "", holding: "INTRADAY" };

// Section 5 (exits as settings): what the server offers for a template with an exit signal (made-up, as the adopt route answers)
const LEVEL_TARGETS = ["NONE", "R_MULTIPLE", "TEMPLATE_EXIT"];
const EXITS = {
  precedence: {
    UNDERLYING: { stops: ["TEMPLATE_INVALIDATION"], targets: LEVEL_TARGETS }, FUTURE: { stops: ["TEMPLATE_INVALIDATION"], targets: LEVEL_TARGETS },
    OPTION_SINGLE: { stops: ["TEMPLATE_INVALIDATION", "PREMIUM_PCT"], targets: LEVEL_TARGETS },
    OPTION_STRUCTURE: { stops: ["CREDIT_PCT"], targets: ["CREDIT_PCT", "TEMPLATE_EXIT"] },
  },
  exit_signal: true, wave_projection: false, base_waves: [], target_r: { min: 0.1, max: 20 }, ratio: { min: 0, max: 10 },
};

interface Server { sent: { method: string; url: string; body: unknown }[] }

async function open(page: Page, o: { theme?: "light" | "dark"; path?: string; templatesOff?: boolean; exits?: boolean } = {}): Promise<Server> {
  const server: Server = { sent: [] };
  await page.clock.install({ time: new Date(NOW) });
  await page.addInitScript((theme) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const features = { ...(F.features as Record<string, boolean>), strategy_templates: !o.templatesOff };
  await mockApi(page, { features, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  const record = (route: Route) => {
    const r = route.request();
    let body: unknown = null;
    try { body = r.postDataJSON(); } catch { body = r.postData(); }
    server.sent.push({ method: r.method(), url: r.url().replace(/^https?:\/\/[^/]+\/api(?:\/v1)?/, ""), body });
    return body;
  };
  await page.route(api("/strategies$"), (route) => route.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/broker/credentials$"), (route) => route.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/strategy-templates$"), (route) => o.templatesOff
    ? route.fulfill({ status: 404, json: { detail: "Not found" } }) : route.fulfill({ json: { templates: TEMPLATES } }));
  await page.route(api("/strategy-templates/\\w+/adopt$"), (route) => {
    const body = record(route) as { timeframe?: string };
    const id = /strategy-templates\/(\w+)\/adopt/.exec(route.request().url())![1];
    const tf = body.timeframe ?? "5m";
    const sid = `template:${id}:1:${tf}:both:,,,,,,`;
    return route.fulfill({ json: { template_id: id, name: TEMPLATES.find((t) => t.id === id)!.name, strategy_id: sid,
      deployment: { strategy_id: sid, timeframe: LABEL[tf], holding: tf === "1d" ? "SWING" : "INTRADAY", mode: "PAPER" },
      settings: { timeframe: tf, sides: "both", params: {} },
      notes: ["Signals only: what to trade, how much and when to get out stay your own choices.", "Paper money only. Nothing starts until you confirm it."],
      ...(o.exits ? { exits: EXITS } : {}) } });
  });
  await page.route(api("/deployments(\\?.*)?$"), (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: [BOT] });
    const body = record(route) as Record<string, unknown>;
    return route.fulfill({ json: { ...BOT, ...body, id: 70 } });
  });
  await page.route(api("/worker/status$"), (route) => route.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.goto(o.path ?? "/deployments");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return server;
}

const form = (page: Page) => page.locator("[data-block='start']");
const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true },
  { name: "360", width: 360, height: 780, mobile: true }]) {
  test.describe(`Strategy templates ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the form with a template picked looks the same (${theme})`, async ({ page }) => {
        await open(page, { theme, path: "/deployments?template=D2" });
        await expect(form(page).locator("[data-template-notes]")).toContainText("Starts with the template's usual settings.");
        await expect(page).toHaveScreenshot(`strategy-templates-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    for (const theme of ["light", "dark"] as const) {
      test(`the exits for this bot, pre-filled with the template's defaults (${theme})`, async ({ page }) => {
        await open(page, { theme, path: "/deployments?template=D2", exits: true });
        await expect(form(page).locator("[data-template-exits]")).toContainText("Exits for this bot");
        await expect(form(page).getByLabel("Stop")).toHaveValue("TEMPLATE_INVALIDATION");          // NIFTY 50: its option
        await form(page).getByLabel("Take profit").selectOption({ label: "At a multiple of my risk" });
        await form(page).getByLabel("Times my risk").fill("2");
        await expect(page).toHaveScreenshot(`strategy-templates-exits-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    if (size.mobile) {
      test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
        await open(page, { path: "/deployments?template=D2" });
        await expectPhoneLayout(page);
      });

      test("with the exits shown: no sideways scroll, 44 px targets", async ({ page }) => {
        await open(page, { path: "/deployments?template=D2", exits: true });
        await form(page).getByLabel("Take profit").selectOption({ label: "At a multiple of my risk" });
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("Strategy templates behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("pick a template: the server's pre-fill, then Start on paper sends it as PAPER; nothing starts before", async ({ page }) => {
    const server = await open(page);
    const strategy = form(page).getByLabel("Pick a strategy");
    await expect(strategy.locator("optgroup[label='Strategy templates'] option")).toHaveText(["Moving-average crossover", "Range breakout"]);
    await strategy.selectOption({ label: "Range breakout" });
    await expect(form(page).getByLabel("Chart timeframe")).toHaveValue("15m");
    await expect(form(page)).toContainText("A signal on the first close beyond a range");
    await expect(form(page).locator("[data-template-notes]")).toContainText("Nothing starts until you confirm it.");
    expect(server.sent.filter((c) => c.url === "/deployments")).toHaveLength(0);
    expect(server.sent.at(-1)).toEqual({ method: "POST", url: "/strategy-templates/B1/adopt", body: { timeframe: "15m" } });
    await form(page).getByLabel("Chart timeframe").selectOption("1d");
    await expect(form(page)).toContainText("held for days");
    await form(page).getByLabel("Pick a symbol").selectOption("RELIANCE");
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Started on paper: RELIANCE.");
    const post = server.sent.find((c) => c.url === "/deployments");
    expect(post?.body).toEqual({ strategy_id: "template:B1:1:1d:both:,,,,,,", symbol: "RELIANCE", exchange: "NSE", timeframe: "day",
      mode: "PAPER", holding: "SWING", instrument_kind: "UNDERLYING" });
  });

  test("opened from an idea's Adopt link: its timeframe and settings are the pre-fill the server checks; nothing starts", async ({ page }) => {
    const server = await open(page, { path: "/deployments?template=D2&timeframe=1h&set.fast=5&set.unknown=1&set.slow=x" });
    await expect(form(page).getByLabel("Pick a strategy")).toHaveValue("template-pick:D2");
    await expect(form(page).getByLabel("Chart timeframe")).toHaveValue("1h");
    await expect(form(page).locator("[data-template-notes]")).toContainText("Starts with the settings from the idea.");
    // only the template's own settings, typed as it declares them, reach the adopt request
    expect(server.sent.filter((c) => c.url.endsWith("/adopt")).at(-1)).toEqual(
      { method: "POST", url: "/strategy-templates/D2/adopt", body: { timeframe: "1h", params: { fast: 5 } } });
    expect(server.sent.filter((c) => c.url === "/deployments")).toHaveLength(0);
    await form(page).getByLabel("Pick a strategy").selectOption({ label: "Range breakout" });
    await expect(form(page).locator("[data-template-notes]")).toContainText("Starts with the template's usual settings.");
    expect(server.sent.at(-1)?.body).toEqual({ timeframe: "1h" });
  });

  test("a bot on a template shows the template's name; no ids or internal words on the page", async ({ page }) => {
    await open(page);
    await expect(page.locator("[data-block='bots']")).toContainText("Moving-average crossover");
    const body = await page.locator("main").innerText();
    for (const word of ["template:", "template-pick", "strategy_templates", "deployment", "worker", "Not found"]) expect(body).not.toContain(word);
  });

  test("the exits: pre-filled with the template's defaults (nothing extra sent); a choice is sent with the same PAPER request", async ({ page }) => {
    const server = await open(page, { path: "/deployments?template=D2", exits: true });
    const exits = form(page).locator("[data-template-exits]");
    await expect(exits.getByLabel("Take profit")).toHaveValue("NONE");
    await expect(exits.getByLabel("Take profit").locator("option")).toHaveText(
      ["No fixed target", "At a multiple of my risk", "When the template says the idea has ended"]);
    // NIFTY 50 trades its option: the stop may be the template's level or only the premium stop
    await expect(exits.getByLabel("Stop").locator("option")).toHaveText(
      ["Where the idea is wrong", "Only my premium stop"]);
    await expect(exits).toContainText("Your premium stop always stays as a safety net.");
    // a share: one fixed stop, shown as words
    await form(page).getByLabel("Pick a symbol").selectOption("RELIANCE");
    await expect(exits.locator("[data-exit-stop]")).toContainText("Where the idea is wrong");
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Started on paper: RELIANCE.");
    expect((server.sent.find((c) => c.url === "/deployments")?.body as Record<string, unknown>).template_exits).toBeUndefined();
    // a multiple of the risk needs its number first; then it is sent
    await exits.getByLabel("Take profit").selectOption({ label: "At a multiple of my risk" });
    await expect(exits).toContainText("Your minimum reward to risk in your risk settings still applies.");
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(exits.getByRole("alert")).toHaveText("Type how many times your risk to take profit at (0.1 to 20).");
    expect(server.sent.filter((c) => c.url === "/deployments")).toHaveLength(1);
    await exits.getByLabel("Times my risk").fill("2");
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => server.sent.filter((c) => c.url === "/deployments").length).toBe(2);
    expect((server.sent.filter((c) => c.url === "/deployments").at(-1)?.body as Record<string, unknown>).template_exits)
      .toEqual({ target_rule: "R_MULTIPLE", target_r: 2 });
    // the exit signal, on the option of an index, with only the premium stop
    await form(page).getByLabel("Pick a symbol").selectOption("NIFTY 50");
    await exits.getByLabel("Stop").selectOption({ label: "Only my premium stop" });
    await exits.getByLabel("Take profit").selectOption({ label: "When the template says the idea has ended" });
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => server.sent.filter((c) => c.url === "/deployments").length).toBe(3);
    const last = server.sent.filter((c) => c.url === "/deployments").at(-1)?.body as Record<string, unknown>;
    expect(last.template_exits).toEqual({ stop_source: "PREMIUM_PCT", target_rule: "TEMPLATE_EXIT" });
    expect(last.mode).toBe("PAPER");
    const body = await page.locator("main").innerText();
    for (const word of ["TEMPLATE_", "PREMIUM_PCT", "R_MULTIPLE", "stop_source", "target_rule"]) expect(body).not.toContain(word);
  });

  test("an older server without exit choices: no exits block, the request as before", async ({ page }) => {
    await open(page, { path: "/deployments?template=D2" });
    await expect(form(page).locator("[data-template-notes]")).toContainText("Starts with the template's usual settings.");
    await expect(form(page).locator("[data-template-exits]")).toHaveCount(0);
  });

  test("with the flag off: no template group, the form as before", async ({ page }) => {
    await open(page, { templatesOff: true, path: "/deployments?template=D2" });
    await expect(form(page).locator("optgroup[label='Strategy templates']")).toHaveCount(0);
    await expect(form(page).getByLabel("Chart timeframe")).toHaveCount(0);
  });
});

/** UX-11 PR 5 (owner, 27 Oct): numbers first - every Strategy templates card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 Strategy templates text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, { theme: "light" });
      console.log(`[words] Strategy templates ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
