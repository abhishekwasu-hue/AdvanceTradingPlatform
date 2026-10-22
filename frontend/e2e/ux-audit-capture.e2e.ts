import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { mockTrading } from "./tradingMocks";

/**
 * Master Order 3, part 2 (docs/design/UX_AUDIT_FULL.md): a CAPTURE-ONLY spec. It renders every page the audit lists
 * that has no committed baseline yet, in the v2 shell (`ui_v2` on), with the existing e2e mocks (made-up data only), at
 * 1440 x 900 and 390 x 844, and writes plain PNGs to docs/design/ux-audit/ with page.screenshot({ path }) - never
 * toHaveScreenshot, so nothing here becomes a baseline. It asserts nothing about the pages and never runs in CI.
 * Run locally, against a built preview:
 *   UX_AUDIT_CAPTURE=1 npx playwright test --project=phone e2e/ux-audit-capture.e2e.ts
 * AUDIT_TEXT_DIR=<dir> also writes each page's visible text (the audit's word scan reads it); not committed.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the UX audit, never in CI");
// Opt-in locally too, so a plain `--project=phone` run never rewrites the audit's pictures.
test.skip(!process.env.UX_AUDIT_CAPTURE, "set UX_AUDIT_CAPTURE=1 to capture the UX audit pictures");

const OUT = new URL("../../docs/design/ux-audit/", import.meta.url);
const TEXT_DIR = process.env.AUDIT_TEXT_DIR;
const ME = JSON.parse(readFileSync(new URL("./fixtures/me.json", import.meta.url), "utf8")) as Record<string, unknown>;
const CONNECTED = F.token_status as unknown[];
const UPSTOX = [{ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected",
  message: "Connected for market data, charts, the scanner, the option chain and paper trading.", data_only: true, renew: false }];
// The flags that are ON by default on a fresh install (backend DEFAULT_ON_FLAGS), plus ui_v2 (the audited shell) and
// discipline_engine (so the Journal page can be seen).
const FLAGS: Record<string, boolean> = { ui_v2: true, ai_copilot: true, marketplace: true, public_api: true, backtest_optimizer: true,
  live_trading: true, options_builder: true, self_signup: true, discipline_engine: true };
const INTROS = ["dashboard", "signals", "option-chain", "charts", "positions", "portfolio", "risk-management", "screener", "copilot",
  "backtest", "deployments", "strategies", "strategy-builder", "settings", "broker"];

interface Shot { name: string; path: string; textOnly?: boolean; role?: string; signedOut?: boolean; flagsOff?: boolean; then?: (page: Page) => Promise<void> }

const SHOTS: Shot[] = [
  { name: "sign-in", path: "/account", signedOut: true },
  { name: "sign-in-old-shell", path: "/account", signedOut: true, flagsOff: true },
  { name: "setup", path: "/settings/setup" },
  { name: "screener", path: "/screener" },
  { name: "custom-scan", path: "/screener/custom" },
  { name: "strategies", path: "/strategies" },
  { name: "strategy-builder", path: "/strategy-builder" },
  { name: "backtest", path: "/backtest" },
  { name: "copilot", path: "/copilot" },
  { name: "autopilot", path: "/deployments" },
  { name: "orders", path: "/orders" },
  { name: "analytics", path: "/analytics" },
  { name: "discipline", path: "/journal" },   // the file name avoids "journal" (the public-repo guard reads it as real data)
  { name: "coach", path: "/journal/coach" },
  { name: "news-events", path: "/news-events" },
  { name: "fundamentals", path: "/fundamentals" },
  { name: "options-builder", path: "/option-chain/plan" },
  { name: "settings", path: "/settings" },
  { name: "settings-advanced", path: "/settings#advanced" },
  { name: "settings-account", path: "/settings/account" },
  { name: "team", path: "/team" },
  { name: "notifications", path: "/notifications" },
  { name: "factor-lab", path: "/quant" },
  { name: "contract-specs", path: "/instruments" },
  { name: "marketplace", path: "/marketplace" },
  { name: "activity-log", path: "/system-logs" },
  { name: "admin", path: "/admin", role: "SUPER_ADMIN" },
  { name: "admin-as-user", path: "/admin" },
  { name: "not-found", path: "/no-such-page" },
  { name: "chart-window-old", path: "/?chart=NIFTY%2050&tf=15min" },
  { name: "broker-connect-panel", path: "/broker", then: async (page) => {
    const connect = page.getByRole("button", { name: /Zerodha/ }).first();
    if (await connect.count()) await connect.click();
    await page.waitForTimeout(400);
  } },
  // Pages with committed baselines (frontend/e2e/__screenshots__): text only, for the word scan (AUDIT_TEXT_DIR).
  ...["/", "/broker", "/charts", "/signals", "/positions", "/portfolio", "/risk-management", "/option-chain"].map((path) =>
    ({ name: `text${path === "/" ? "-dashboard" : path.replace("/", "-")}`, path, textOnly: true })),
  { name: "command-bar", path: "/", then: async (page) => { await page.keyboard.press("Control+k"); await page.waitForTimeout(400); } },
];

async function open(page: Page, s: Shot) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((intros) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(intros));
      localStorage.setItem("atp_sign_in", JSON.stringify({ userId: 2, id: "e2e-sign-in", isNew: false }));
      localStorage.setItem("atp_broker_prompt_later:2", "e2e-sign-in");
    } catch { /* storage unavailable */ }
    try { Object.defineProperty(Notification, "permission", { get: () => "denied" }); } catch { /* no Notification API */ }
  }, INTROS);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: s.flagsOff ? undefined : FLAGS, brokers: CONNECTED, connections: UPSTOX });
  await mockDashboard(page, { me: false });
  await mockTrading(page);
  await mockCharts(page);
  await page.route(/\/api(?:\/v1)?\/auth\/me$/, (r) => (s.signedOut
    ? r.fulfill({ status: 401, json: { detail: "Not authenticated" } })
    : r.fulfill({ json: { ...ME, ...(s.role ? { role: s.role } : {}) } })));
  if (s.signedOut) await page.route(/\/api(?:\/v1)?\/auth\/refresh$/, (r) => r.fulfill({ status: 401, json: { detail: "No session" } }));
  await page.goto(s.path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("body")).toBeVisible();
  await page.locator("[aria-busy='true']").first().waitFor({ state: "detached", timeout: 8_000 }).catch(() => undefined);
  await page.waitForTimeout(300);
  await s.then?.(page);
}

for (const [label, size] of [["1440", { width: 1440, height: 900 }], ["390", { width: 390, height: 844 }]] as const) {
  test.describe(`UX audit capture ${label}`, () => {
    test.use(label === "390" ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size, deviceScaleFactor: 1 });
    for (const s of SHOTS) {
      if (s.textOnly && !TEXT_DIR) continue;
      test(`${s.name} (${label})`, async ({ page }) => {
        await open(page, s);
        mkdirSync(OUT, { recursive: true });
        // Settings -> AI lists the provider's internal model names: masked, so no image in the repo carries them.
        if (!s.textOnly) await page.screenshot({ path: new URL(`${s.name}-${label}.png`, OUT).pathname, animations: "disabled", mask: [page.locator("#ai")] });
        if (TEXT_DIR) {
          mkdirSync(TEXT_DIR, { recursive: true });
          writeFileSync(`${TEXT_DIR}/${s.name}-${label}.txt`, await page.locator("body").innerText());
        }
      });
    }
  });
}
