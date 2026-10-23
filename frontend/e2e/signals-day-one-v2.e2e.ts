import { expect, test, type Page } from "@playwright/test";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Signals day one (owner order 23 Oct): a new user's Signals page with `ui_v2` + `strategy_templates` on and nothing in
 * the inbox. The "Pick a ready strategy and a list to watch" card: pick, see every default setting, adopt on paper; the
 * page then shows his watch (a new setup, one waiting for its trigger, the rest folded). The adopt call carries only the
 * template and the list (no mode, no settings). Phone 390 and desktop 1440, light and dark; no sideways scroll and 44 px
 * targets on the phone. All data is made up. Screenshots: set SIG1_SHOTS=<folder> to save them (nothing is committed).
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, strategy_templates: true }, brokers: CONNECTED };

const SETTINGS = [
  { key: "ma_type", label: "Average type", value: "Exponential", help: "How both averages are calculated." },
  { key: "fast", label: "Pullback average", value: "20", help: "Bars in the fast average the pullback comes back to." },
  { key: "slow", label: "Trend average", value: "50", help: "Bars in the slow average that defines the trend." },
  { key: "slope_bars", label: "Trend slope over (bars)", value: "5", help: "The trend average must be higher or lower than before." },
];
const OFFERS = [
  { id: "A5", name: "Structure flip retest", idea: "After a level breaks, wait for the market to come back to it from the other side and reject it.",
    difficulty: "Beginner", family: "A", timeframe: "Daily", settings: SETTINGS.slice(0, 2), rules: [] },
  { id: "B1", name: "Range breakout", idea: "A signal on the first close beyond a range or a consolidation by a set distance.", difficulty: "Beginner",
    family: "B", timeframe: "Daily", settings: SETTINGS.slice(0, 2), rules: [] },
  { id: "D3", name: "Moving-average pullback in trend", idea: "In a trend, wait for the pullback to reach the fast average and for a reversal there.",
    difficulty: "Intermediate", family: "D", timeframe: "Daily", settings: SETTINGS, rules: ["The pullback average must be shorter than the trend average."] },
];
const LISTS = [
  { kind: "system", key: "index-nifty50", name: "NIFTY 50 stocks (50)", group: "Index stocks", count: 50, default: true },
  { kind: "system", key: "index-niftybank", name: "NIFTY BANK stocks (12)", group: "Index stocks", count: 12, default: false },
];
const row = (symbol: string, state: string, text: string, extra: Record<string, unknown> = {}) =>
  ({ symbol, exchange: "NSE", state, direction: null, text, area: null, setup_day: null, bar_day: "2026-10-07", ...extra });
const WATCH = {
  template: { id: "D3", name: OFFERS[2].name, idea: OFFERS[2].idea, timeframe: "Daily", settings: SETTINGS },
  strategy_id: "template:D3:1:1d:both:,,,,,,", list: { kind: "system", key: "index-nifty50", name: "NIFTY 50 stocks (50)" }, truncated: false,
  count: 50, max_watch: 60, mode: "PAPER", entries: "APPROVAL", adopted_at: "2026-10-08T05:00:00Z", evaluated_at: "2026-10-08T05:00:00Z",
  bar_day: "2026-10-07", note: null,
  rows: [
    row("DEMOALPHA", "new", "In a rising trend on the Daily chart the pullback reached the fast moving average and a bullish reversal formed there.",
        { direction: "LONG", area: "Buyer area: 1,412.5–1,436", setup_day: "2026-10-07" }),
    row("DEMOBETA", "armed", "In a falling trend on the Daily chart the rally reached the fast moving average; waiting for a bearish reversal there.",
        { direction: "SHORT", area: "Seller area: 812–826.4", setup_day: "2026-10-06" }),
    ...Array.from({ length: 47 }, (_, i) => row(`DEMO${String(i + 1).padStart(2, "0")}`, "watching",
      "No setup in place on the Daily chart; watching for a pullback to the fast moving average in a trend.")),
    row("DEMONEW", "no_data", "No Daily history for this symbol yet."),
  ],
  summary: { new: 1, armed: 1, recent: 0, watching: 47, warmup: 0, no_data: 1, not_read: 0 },
};

async function open(page: Page, theme: "dark" | "light") {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["signals"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, ON);
  const posts: { path: string; body: unknown }[] = [];
  let adopted: typeof WATCH | null = null;
  await page.route(/\/api(?:\/v1)?\/signals\/(feed|day-one)/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api(?:\/v1)?/, "");
    if (path === "/signals/feed") {
      return route.fulfill({ json: { tab: url.searchParams.get("tab") ?? "today", source: "all", symbol: null, items: [], next_cursor: null,
        counts: { waiting: 0, today: 0 }, as_of: "2026-10-08T05:00:00+00:00", since: null, sources: {} } });
    }
    const card = () => ({ offers: OFFERS, lists: LISTS, default_list: { kind: "system", key: "index-nifty50" }, can_adopt: true, max_watch: 60,
      adopted, outdated: false });
    if (path === "/signals/day-one" && request.method() === "GET") return route.fulfill({ json: card() });
    if (path === "/signals/day-one/adopt" && request.method() === "POST") {
      posts.push({ path, body: request.postDataJSON() });
      adopted = WATCH;
      return route.fulfill({ json: card() });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/signals");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return posts;
}

async function shot(page: Page, name: string) {
  const dir = process.env.SIG1_SHOTS;
  if (!dir) return;
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true, animations: "disabled" });

}

const SIZES = [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
for (const size of SIZES) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Signals day one ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("first visit: pick, see the settings, adopt on paper, then his watch", async ({ page }) => {
        const posts = await open(page, theme);
        const card = page.locator("[data-day-one='setup']");
        await expect(card.getByRole("heading", { name: "Pick a ready strategy and a list to watch" })).toBeVisible();
        await expect(card.getByLabel("List to watch")).toHaveValue("system:index-nifty50");
        if (size.phone && !process.env.SIG1_SHOTS) await expectPhoneLayout(page);   // a full-page capture resizes the page
        await shot(page, `day-one-${size.width}-${theme}-1-card`);

        await card.getByRole("radio", { name: /Moving-average pullback in trend/ }).check();
        await card.getByRole("button", { name: "See its settings" }).click();
        const confirm = page.locator("[data-day-one='confirm']");
        for (const s of SETTINGS) { await expect(confirm.getByText(s.label, { exact: true })).toBeVisible(); }
        await expect(confirm.getByText("Paper money only. Nothing is placed: each setup waits for you.")).toBeVisible();
        if (size.phone && !process.env.SIG1_SHOTS) await expectPhoneLayout(page);   // a full-page capture resizes the page
        await shot(page, `day-one-${size.width}-${theme}-2-settings`);

        await confirm.getByRole("button", { name: "Adopt on paper and read my list" }).click();
        const watch = page.locator("[data-day-one='watch']");
        await expect(watch.getByRole("heading", { name: "Your watch: Moving-average pullback in trend" })).toBeVisible();
        expect(posts).toEqual([{ path: "/signals/day-one/adopt", body: { template_id: "D3", list: { kind: "system", key: "index-nifty50" } } }]);
        await expect(watch.getByText("1 new setup, 1 waiting for its trigger on your list of 50.")).toBeVisible();
        await expect(watch.getByText("Buyer area: 1,412.5–1,436")).toBeVisible();
        await expect(watch.locator("[data-day-one-row='watching']")).toHaveCount(0);           // folded
        await expect(page.getByText("Your watch above shows where each symbol on your list stands.")).toBeVisible();
        if (size.phone && !process.env.SIG1_SHOTS) await expectPhoneLayout(page);   // a full-page capture resizes the page
        await shot(page, `day-one-${size.width}-${theme}-3-watch`);
        await watch.getByRole("button", { name: /Show the other 48 symbols/ }).click();
        await expect(watch.locator("[data-day-one-row='watching']")).toHaveCount(47);
        if (size.phone && !process.env.SIG1_SHOTS) await expectPhoneLayout(page);   // a full-page capture resizes the page
      });
    });
  }
}
