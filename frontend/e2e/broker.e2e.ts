import { expect, test, type Page } from "@playwright/test";
import { mockApi, type MockCalls, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * P2b: the Broker page, in the shell the owner uses (today's shell, `ui_v2` off) and in the v2 shell.
 * - Baselines `broker-<width>-<theme>.png` at 1440 and 390, light and dark, and the sign-in prompt at both widths.
 * - The page passes the PW2 phone rules (no sideways scroll, 44 px targets, nothing behind the bottom bar).
 * - Broker is the first navigation entry with its status dot; the sign-in prompt and "Later"; a new account lands on
 *   the Broker page; the Connect panel shows only the catalog's fields; Renew; Remove asks first; the Dashboard banner.
 * Every /api call is mocked (e2e/mockApi.ts, P2a's catalog in fixtures/broker_catalog.json). Browser capabilities that
 * differ between runners (install, notifications) are pinned in the init script.
 * After an intended change: `npx playwright test --project=phone e2e/broker.e2e.ts --update-snapshots`.
 */

type Conn = { broker: string; account_label: string; name: string; status: string; message: string; data_only: boolean; renew: boolean };
const UPSTOX_DATA: Conn = { broker: "upstox", account_label: "primary", name: "Upstox", status: "connected",
  message: "Connected for market data, charts, the scanner, the option chain and paper trading.", data_only: true, renew: false };
const ZERODHA_LOGIN: Conn = { broker: "zerodha", account_label: "primary", name: "Zerodha (Kite)", status: "needs_login",
  message: "Zerodha (Kite) is not logged in today; press Log in, sign in and paste the address you land on.", data_only: false, renew: true };
const DHAN_BROKEN: Conn = { broker: "dhan", account_label: "primary", name: "Dhan", status: "not_working",
  message: "Dhan did not accept this token; generate a new one and paste it here.", data_only: false, renew: true };
const UPSTOX_LOGIN: Conn = { broker: "upstox", account_label: "primary", name: "Upstox", status: "needs_login",
  message: "Today's Upstox token has ended; paste your Analytics Token to stay connected for a year.", data_only: false, renew: true };

const THEMES = ["light", "dark"] as const;
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

interface OpenOptions {
  theme?: "light" | "dark";
  mocks?: MockOptions;
  /** localStorage set before the app first starts (e.g. a sign-in already marked "Later"); kept as the app changes it. */
  session?: Record<string, string>;
  /** Extra routes, registered after mockApi so they answer first. */
  before?: (page: Page) => Promise<void>;
}

async function open(page: Page, path: string, { theme = "light", mocks = { connections: [UPSTOX_DATA, ZERODHA_LOGIN] }, session, before }: OpenOptions = {}): Promise<MockCalls> {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript(([t, s]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");          // PW1's install offer: not part of these shots
      localStorage.setItem("atp_push_dismissed", "1");
      for (const [k, v] of Object.entries(s ?? {})) if (localStorage.getItem(k) === null) localStorage.setItem(k, v);
    } catch { /* storage unavailable */ }
    // Pin what differs between Chromium builds (CI vs local): no install prompt, notifications neither granted nor asked.
    try { Object.defineProperty(Notification, "permission", { get: () => "denied" }); } catch { /* no Notification API */ }
  }, [theme, session ?? null] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const calls = await mockApi(page, mocks);
  await before?.(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return calls;
}

/** "Later" already chosen for this sign-in, so the prompt stays away (the page's own shots and checks). */
const LATER = { atp_sign_in: JSON.stringify({ userId: 2, id: "e2e-sign-in", isNew: false }), "atp_broker_prompt_later:2": "e2e-sign-in" };
const shot = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

for (const [label, size] of [["1440", DESKTOP], ["390", PHONE]] as const) {
  test.describe(`Broker page ${label}`, () => {
    test.use(size === PHONE ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await open(page, "/broker", { theme, session: LATER });
        await expect(page.locator("[data-broker-page]")).toBeVisible();
        await expect(page.locator("[data-broker-tile]")).toHaveCount(7);
        await expect(page).toHaveScreenshot(`broker-${label}-${theme}.png`, shot);
      });
    }

    test(`the sign-in prompt looks the same (${label})`, async ({ page }) => {
      await open(page, "/", { mocks: { connections: [] } });
      const prompt = page.locator("[data-broker-dialog='prompt']");
      await expect(prompt).toBeVisible();
      await expect(prompt.locator("[data-broker-logo]").first()).toBeVisible();
      await expect(page).toHaveScreenshot(`broker-prompt-${label}-light.png`, shot);
      const box = await prompt.boundingBox();
      if (size === PHONE) expect(Math.round(box!.y + box!.height)).toBe(PHONE.height);   // a bottom sheet on the phone
      else expect(box!.width).toBeLessThan(500);                                         // a small modal on the desktop
    });
  });
}

test.describe("phone 390", () => {
  test.use({ viewport: PHONE, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the Broker page: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
    await open(page, "/broker", { theme: "dark", session: LATER });
    await expectPhoneLayout(page);
  });

  test("Broker is the first entry of More; a broker that needs attention puts the dot on More", async ({ page }) => {
    await open(page, "/", { session: LATER });
    const more = page.getByRole("button", { name: /More pages/ });
    await expect(more).toHaveAccessibleName("More pages, broker needs attention");
    await expect(more.locator("[data-broker-dot='needs_login']")).toBeVisible();
    await more.click();
    const drawer = page.locator("#app-navigation");
    await expect(drawer.getByRole("link").first()).toContainText("Broker");
    await drawer.getByRole("link", { name: /^Broker/ }).click();
    await expect(page).toHaveURL(/\/broker$/);
  });

  test("all connected: no dot on More", async ({ page }) => {
    await open(page, "/", { mocks: { connections: [UPSTOX_DATA] } });
    await expect(page.getByRole("button", { name: "More pages", exact: true })).toBeVisible();
    await expect(page.locator(".bottom-nav [data-broker-dot]")).toHaveCount(0);
    await expect(page.locator("[data-broker-dialog='prompt']")).toHaveCount(0);   // connected: no prompt either
  });

  test("tapping a tile opens a sheet with only that broker's fields; Connect answers Connected ✓", async ({ page }) => {
    const sent: unknown[] = [];
    await open(page, "/broker", { session: LATER, before: async (p) => {
      await p.route(/\/api(?:\/v1)?\/broker\/dhan\/connect$/, (r) => { sent.push(r.request().postDataJSON()); return r.fulfill({ json: { status: "connected", message: "Connected." } }); });
    } });
    await page.locator("[data-broker-tile='dhan']").click();
    const sheet = page.locator("[data-broker-dialog='connect']");
    await expect(sheet).toBeVisible();
    await expect(sheet.locator("input")).toHaveCount(2);
    await expect(sheet.getByLabel("Client ID")).toBeVisible();
    await expect(sheet.getByLabel("Access token")).toHaveAttribute("type", "password");
    await sheet.getByText("Where do I get this?").click();
    await expect(sheet.locator("ol li")).toHaveCount(3);
    const connect = sheet.getByRole("button", { name: "Connect", exact: true });
    await expect(connect).toBeDisabled();
    await sheet.getByLabel("Client ID").fill("1000000001");
    await sheet.getByLabel("Access token").fill("e2e-made-up-value");
    await connect.click();
    await expect(sheet.getByText("Connected ✓")).toBeVisible();
    expect(sent).toEqual([{ fields: { client_id: "1000000001", access_token: "e2e-made-up-value" } }]);
  });
});

test.describe("desktop 1440", () => {
  test.use({ viewport: DESKTOP, deviceScaleFactor: 1 });

  test("Broker is the first sidebar entry, with the dot: green, amber, red", async ({ page }) => {
    for (const [rows, dot] of [[[UPSTOX_DATA], "connected"], [[UPSTOX_DATA, ZERODHA_LOGIN], "needs_login"], [[UPSTOX_DATA, DHAN_BROKEN], "not_working"]] as const) {
      await page.unrouteAll({ behavior: "ignoreErrors" });
      await open(page, "/settings", { session: LATER, mocks: { connections: [...rows] } });
      const nav = page.locator("#app-navigation nav");
      const first = nav.getByRole("link").first();
      await expect(first).toContainText("Broker");
      await expect(first.locator(`[data-broker-dot='${dot}']`)).toBeVisible();
    }
  });

  test("the sign-in prompt: Later hides it for this sign-in; the next sign-in shows it again", async ({ page }) => {
    await open(page, "/", { mocks: { connections: [] } });
    const prompt = page.locator("[data-broker-dialog='prompt']");
    await expect(prompt.getByRole("heading", { name: "Connect your broker to see live charts and start paper trading" })).toBeVisible();
    await expect(prompt.getByRole("button", { name: "Connect" })).toBeVisible();
    await prompt.getByRole("button", { name: "Later" }).click();
    await expect(prompt).toHaveCount(0);
    await page.goto("/positions");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[data-broker-dialog='prompt']")).toHaveCount(0);   // the rest of this sign-in
    expect(await page.evaluate(() => localStorage.getItem("atp_broker_prompt_later:2"))).toBeTruthy();
    // A new tab of the same sign-in (the session cookie is shared by every tab): still no prompt (review M1).
    const tab = await page.context().newPage();
    await tab.goto("/");
    await tab.waitForLoadState("networkidle");
    await expect(tab.locator("main")).toBeVisible();
    await expect(tab.locator("[data-broker-dialog='prompt']")).toHaveCount(0);
    await tab.close();
    // A new sign-in (AuthContext writes a new id; here the record is replaced by hand): the prompt is back.
    await page.evaluate(() => localStorage.setItem("atp_sign_in", JSON.stringify({ userId: 2, id: "next-sign-in", isNew: false })));
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[data-broker-dialog='prompt']")).toBeVisible();
    await page.locator("[data-broker-dialog='prompt']").getByRole("button", { name: "Connect" }).click();
    await expect(page).toHaveURL(/\/broker$/);
  });

  test("today's login needed: '<Broker> needs today's login' - Renew opens the panel right there", async ({ page }) => {
    await open(page, "/", { mocks: { connections: [UPSTOX_LOGIN] } });
    const prompt = page.locator("[data-broker-dialog='prompt']");
    await expect(prompt.getByRole("heading", { name: "Upstox needs today's login" })).toBeVisible();
    await prompt.getByRole("button", { name: "Renew" }).click();
    const panel = page.locator("[data-broker-dialog='connect']");
    await expect(panel.getByRole("heading", { name: "Renew Upstox" })).toBeVisible();
    await expect(panel.locator("input")).toHaveCount(1);
    await expect(panel.getByLabel("Analytics Token")).toBeVisible();
  });

  test("a brand-new account lands on the Broker page first, with no prompt on top", async ({ page }) => {
    await open(page, "/", { mocks: { connections: [] }, session: { atp_sign_in: JSON.stringify({ userId: 2, id: "new", isNew: true }) } });
    await expect(page).toHaveURL(/\/broker$/);
    await expect(page.locator("[data-broker-page]")).toBeVisible();
    await expect(page.locator("[data-broker-dialog='prompt']")).toHaveCount(0);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveURL(/\/$/);                                          // only the first time
    await expect(page.locator("[data-broker-dialog='prompt']")).toHaveCount(0);   // that was this sign-in's prompt
  });

  test("Zerodha: Connect saves the key, then Log in and one box for the address; Renew sends request_token", async ({ page }) => {
    const renewed: unknown[] = [];
    await open(page, "/broker", { session: LATER, before: async (p) => {
      await p.route(/\/api(?:\/v1)?\/broker\/zerodha\/connect$/, (r) => r.fulfill({ json: { status: "needs_login", message: "Saved; now press Log in, sign in to Zerodha (Kite) and paste the address you land on." } }));
      await p.route(/\/api(?:\/v1)?\/broker\/zerodha\/login-url/, (r) => r.fulfill({ json: { broker_name: "zerodha", authorization_url: "https://kite.example/connect/login", code_param: "request_token", instructions: "" } }));
      await p.route(/\/api(?:\/v1)?\/broker\/zerodha\/renew$/, (r) => { renewed.push(r.request().postDataJSON()); return r.fulfill({ json: { status: "connected", message: "Connected." } }); });
      await p.route("https://kite.example/**", (r) => r.fulfill({ body: "login" }));
    } });
    await page.locator("[data-broker-tile='zerodha']").click();
    const panel = page.locator("[data-broker-dialog='connect']");
    await expect(panel.locator("input")).toHaveCount(2);
    await panel.getByLabel("API key").fill("made-up-key");
    await panel.getByLabel("API secret").fill("made-up-secret");
    await panel.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(panel.locator("[data-note]")).toContainText("now press Log in");
    await expect(panel.locator("input")).toHaveCount(1);
    const popup = page.waitForEvent("popup");
    await panel.getByRole("button", { name: "Log in" }).click();
    await (await popup).close();
    await panel.getByLabel("Address after login").fill("https://example.com/?request_token=abc&status=success");
    await panel.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(panel.getByText("Connected ✓")).toBeVisible();
    expect(renewed).toEqual([{ fields: { request_token: "https://example.com/?request_token=abc&status=success" } }]);
  });

  test("Remove asks first, names what happens, then deletes that account's details", async ({ page }) => {
    const deleted: string[] = [];
    await open(page, "/broker", { session: LATER, before: async (p) => {
      await p.route(/\/api(?:\/v1)?\/broker\/zerodha\/credentials/, (r) => { deleted.push(`${r.request().method()} ${r.request().url()}`); return r.fulfill({ status: 204, body: "" }); });
    } });
    await page.locator("[data-connection='zerodha']").getByRole("button", { name: "Remove" }).click();
    const dialog = page.locator("[data-broker-dialog='remove']");
    await expect(dialog.getByRole("heading", { name: "Remove Zerodha (Kite)?" })).toBeVisible();
    await expect(dialog).toContainText("are deleted");
    await dialog.getByRole("button", { name: "Remove" }).click();
    await expect(dialog).toHaveCount(0);
    expect(deleted).toHaveLength(1);
    expect(deleted[0]).toMatch(/^DELETE .*\/broker\/zerodha\/credentials\?account_label=primary$/);
  });

  test("the Dashboard banner: one Renew that opens the same panel; analytics-only Upstox gets none", async ({ page }) => {
    await open(page, "/", { session: LATER });
    const banner = page.locator("[data-renew-banner='zerodha']");
    await expect(banner).toContainText("Zerodha (Kite) needs today's login");
    await banner.getByRole("button", { name: "Renew" }).click();
    const panel = page.locator("[data-broker-dialog='connect']");
    await expect(panel.getByRole("heading", { name: "Renew Zerodha (Kite)" })).toBeVisible();
    await expect(panel.locator("input")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await open(page, "/", { session: LATER, mocks: { connections: [UPSTOX_DATA] } });
    await expect(page.locator("[data-renew-banner]")).toHaveCount(0);
  });

  test("Advanced is collapsed; it holds the static IP and the Upstox address", async ({ page }) => {
    await open(page, "/broker", { session: LATER });
    const advanced = page.locator("[data-broker-advanced]");
    await expect(advanced).not.toHaveAttribute("open", "");
    await expect(page.getByText("Static IP (SEBI)")).toHaveCount(0);
    await advanced.locator("summary").click();
    await expect(page.getByText("Static IP (SEBI)")).toBeVisible();
    await expect(advanced.getByRole("heading", { name: "Upstox OAuth app" })).toBeVisible();
  });

  test("the main screen has no technical words (only Advanced may)", async ({ page }) => {
    await open(page, "/broker", { session: LATER });
    const text = await page.locator("[data-broker-page]").evaluate((el) => {
      const clone = el.cloneNode(true) as HTMLElement;
      clone.querySelector("[data-broker-advanced]")?.remove();
      return clone.innerText;
    });
    expect(text).not.toMatch(/token expir|oauth|\bjwt\b|\btotp\b|redirect|request token|valid until/i);
  });

  test("Settings keeps only a link to the Broker page; the webhook is under Integrations, Vision under AI", async ({ page }) => {
    await open(page, "/settings", { session: LATER });
    await expect(page.locator("[data-broker-link]")).toHaveAttribute("href", "/broker");
    await expect(page.getByText("Add / update broker credentials")).toHaveCount(0);
    await expect(page.getByText("Broker session health")).toHaveCount(0);
    await expect(page.locator("#integrations").getByText("TradingView webhook")).toBeVisible();
    await expect(page.locator("#ai").getByText("Vision with your own AI key", { exact: true })).toBeVisible();
  });

  test("a member without brokers:write: no prompt, the actions are off, one line to ask the owner", async ({ page }) => {
    await open(page, "/broker", { mocks: { connections: [ZERODHA_LOGIN] }, before: async (p) => {
      await p.route(/\/api(?:\/v1)?\/auth\/me$/, (r) => r.fulfill({ json: { id: 3, email: "viewer@example.com", tenant_id: 2, role: "VIEWER", email_verified: true, scopes: ["analytics:read"] } }));
    } });
    await expect(page.locator("[data-broker-dialog='prompt']")).toHaveCount(0);
    await expect(page.locator("[data-ask-owner]")).toHaveText("Ask the owner of your organisation to connect a broker.");
    await expect(page.locator("[data-broker-tile='dhan']")).toBeDisabled();
    const row = page.locator("[data-connection='zerodha']");
    await expect(row.getByRole("button", { name: "Renew" })).toBeDisabled();
    await expect(row.getByRole("button", { name: "Remove" })).toBeDisabled();
  });

  test("Advanced: Connect another account opens the panel for that account; Log in with Upstox", async ({ page }) => {
    const sent: unknown[] = [];
    let oauthLabel = "";
    await open(page, "/broker", { session: LATER, mocks: {
      connections: [UPSTOX_DATA],
      brokers: [{ broker_name: "upstox", account_label: "primary", token_status: "EXPIRED", token_expires_at: null, last_verified_at: null,
        needs_login: true, oauth_supported: true, oauth_callback_url: "https://atp.example.com/api/broker/upstox/oauth/callback" }],
    }, before: async (p) => {
      await p.route(/\/api(?:\/v1)?\/broker\/dhan\/connect$/, (r) => { sent.push(r.request().postDataJSON()); return r.fulfill({ json: { status: "connected", message: "Connected." } }); });
      await p.route(/\/api(?:\/v1)?\/broker\/upstox\/oauth\/start/, (r) => {
        oauthLabel = new URL(r.request().url()).searchParams.get("account_label") ?? "";
        return r.fulfill({ json: { authorization_url: "https://upstox.example/login" } });
      });
      await p.route("https://upstox.example/**", (r) => r.fulfill({ body: "upstox login" }));
      await p.route(/\/api(?:\/v1)?\/broker\/upstox\/redirect-url$/, (r) => r.fulfill({ json: {
        redirect_url: "https://atp.example.com/api/broker/upstox/oauth/callback", redirect_uri_mismatch: false, accounts: [] } }));
    } });
    await page.locator("[data-broker-advanced] summary").click();
    const another = page.locator("[data-another-account]");
    await another.getByLabel("Broker").selectOption("dhan");
    await another.getByLabel("Account name").fill("family");
    await another.getByRole("button", { name: "Connect" }).click();
    const panel = page.locator("[data-broker-dialog='connect']");
    await panel.getByLabel("Client ID").fill("1000000002");
    await panel.getByLabel("Access token").fill("e2e-made-up-value");
    await panel.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(panel.getByText("Connected ✓")).toBeVisible();
    expect(sent).toEqual([{ fields: { client_id: "1000000002", access_token: "e2e-made-up-value" }, account_label: "family" }]);
    await panel.getByRole("button", { name: "Done" }).click();
    await expect(page.locator("[data-broker-advanced]").getByRole("textbox", { name: "Redirect URL" }))
      .toHaveValue("https://atp.example.com/api/broker/upstox/oauth/callback");
    await page.locator("[data-upstox-oauth='primary']").click();
    await expect(page).toHaveURL("https://upstox.example/login");
    expect(oauthLabel).toBe("primary");
  });

  test("the v2 shell shows the same Broker page, with Broker just above Settings in its sidebar (day-one fix 9)", async ({ page }) => {
    await open(page, "/broker", { session: LATER, mocks: { features: { ui_v2: true }, connections: [UPSTOX_DATA, ZERODHA_LOGIN] } });
    await expect(page.locator("[data-shell='v2']")).toBeVisible();
    await expect(page.locator("[data-broker-page]")).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Pages" });
    await expect(nav.getByRole("link").first()).toContainText("Today");
    await expect(nav.getByRole("link").last()).toContainText("Settings");
    await expect(nav.getByRole("link").nth(-2)).toContainText("Broker");
    await expect(nav.locator("[data-broker-dot='needs_login']")).toBeVisible();
  });
});
