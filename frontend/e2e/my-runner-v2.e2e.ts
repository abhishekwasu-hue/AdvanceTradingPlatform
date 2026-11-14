import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Runner S7: "My runner" at /runner with `ui_v2` and `user_runner` on. Empty: the six set-up steps, the install line
 * with Copy, "Create pairing code" (shown once, a 10-minute countdown) and "We set it up for you". Paired: the runner's
 * card (online / offline, last seen, version, broker login, HIS kill switch - read-only with the command before S4's
 * relay), the link settings, the read-only copies "From your runner", and Remove with a confirm. Flag off: no entry and
 * "Coming soon". Every reply is made up (no real account, IP or price). RUNNER_PROOF_DIR (optional): screenshots there.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const NOW = "2026-11-14T16:00:00+05:30";
const minsAgo = (m: number) => new Date(Date.parse(NOW) - m * 60_000).toISOString();
const SETTINGS = { mirror_upload: true, offline_after_minutes: 3, offline_telegram: true };

const DEVICE = {
  device_id: "a1b2c3d4", name: "My VPS", status: "ONLINE", version: "0.1.0", paired_at: minsAgo(300), last_heartbeat_at: minsAgo(1),
  revoked_at: null, run_state: "running", kill_switch_on: false, open_positions: 1, broker_session: "valid", egress_ip: "203.0.113.7",
};
const OFFLINE = { ...DEVICE, device_id: "e5f6a7b8", name: "Spare VPS", status: "OFFLINE", last_heartbeat_at: minsAgo(45), broker_session: "expired" };
const MIRROR = {
  device: DEVICE,
  positions: [{ ref: "p1", deployment_ref: null, mode: "PAPER", symbol: "MADEUP", exchange: "NSE", status: "OPEN", quantity: 50,
    average_price: 101.5, last_price: 102, unrealised_pnl: 25, realised_pnl: null, exit_reason: null, opened_at: minsAgo(60), closed_at: null,
    updated_at: minsAgo(1) }],
  orders: [{ ref: "o1", deployment_ref: null, mode: "PAPER", symbol: "MADEUP", exchange: "NSE", side: "BUY", order_type: "LIMIT", quantity: 50,
    price: 101.5, trigger_price: null, status: "COMPLETE", filled_quantity: 50, average_price: 101.5, reason: null, placed_at: minsAgo(60),
    updated_at: minsAgo(60) }],
  audit: [],
};

interface Calls { posts: string[] }

async function open(page: Page, opts: { flag?: boolean; devices?: unknown[]; path?: string } = {}): Promise<Calls> {
  const { flag = true, path = "/runner" } = opts;
  let devices = opts.devices ?? [];
  const calls: Calls = { posts: [] };
  await page.clock.install({ time: new Date(NOW) });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* intentional: storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ...(flag ? { user_runner: true } : {}) }, brokers: CONNECTED });
  await page.route(/\/api(\/v1)?\/(runner|me\/prefs\/runner|support\/contact)/, async (route) => {
    const req = route.request();
    const url = req.url().replace(/^https?:\/\/[^/]+\/api(?:\/v1)?/, "");
    if (req.method() !== "GET") calls.posts.push(`${req.method()} ${url}`);
    if (url === "/support/contact") return route.fulfill({ json: { configured: false } });
    if (url === "/runner/devices") return route.fulfill({ json: { devices, settings: SETTINGS } });
    if (url === "/runner/pairing-codes") {
      return route.fulfill({ status: 201, json: { code: "QWER7KPM", expires_at: new Date(Date.parse(NOW) + 10 * 60_000).toISOString(), expires_in_minutes: 10 } });
    }
    const revoke = url.match(/^\/runner\/devices\/([a-z0-9]+)\/revoke$/);
    if (revoke) {
      devices = devices.map((d) => ((d as { device_id: string }).device_id === revoke[1] ? { ...(d as object), status: "REVOKED", revoked_at: NOW } : d));
      return route.fulfill({ json: devices.find((d) => (d as { device_id: string }).device_id === revoke[1]) });
    }
    if (/^\/runner\/devices\/[a-z0-9]+\/mirror$/.test(url)) return route.fulfill({ json: MIRROR });
    if (url === "/me/prefs/runner") return route.fulfill({ json: { namespace: "runner", value: req.postDataJSON(), version: 1 } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  return calls;
}

async function proof(page: Page, name: string) {
  const dir = process.env.RUNNER_PROOF_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

const sidebar = (page: Page) => page.getByRole("navigation", { name: "Pages" });

test.describe("My runner 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("empty: the six steps, the install line, a pairing code with its countdown, and the setup service", async ({ page }) => {
    await open(page, { path: "/" });
    await sidebar(page).getByRole("link", { name: "My runner" }).click();
    await expect(page).toHaveURL(/\/runner$/);
    await expect(page.getByRole("heading", { level: 1, name: "My runner" })).toBeVisible();
    const steps = page.locator("[data-step]");
    await expect(steps).toHaveCount(6);
    await expect(page.locator("[data-command='install line'] code")).toContainText("releases/latest/download/install.sh");
    await expect(page.getByRole("button", { name: "Copy: install line" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "We set it up for you" })).toBeVisible();
    await proof(page, "1440-empty");
    await page.getByRole("button", { name: "Create pairing code" }).click();
    await expect(page.locator("[data-pairing-code]")).toContainText("QWER7KPM");
    await expect(page.locator("[data-countdown]")).toHaveText(/^Expires in (10:00|9:[0-5]\d)$/);
    await page.clock.runFor(65_000);
    await expect(page.locator("[data-countdown]")).toHaveText(/^Expires in 8:[0-5]\d$/);
    await proof(page, "1440-pairing-code");
    await page.clock.runFor(10 * 60_000);
    await expect(page.locator("[data-code-expired]")).toBeVisible();
    await expect(page.locator("[data-pairing-code]")).toHaveCount(0);
  });

  test("paired: online and offline cards, his kill switch (read-only before S4), copies from his runner, Remove", async ({ page }) => {
    const calls = await open(page, { devices: [DEVICE, OFFLINE] });
    const online = page.locator("[data-card='runner-a1b2c3d4']");
    await expect(online.locator("[data-online='online']")).toHaveText("Online");
    await expect(online).toContainText("Last seen1 min ago");
    await expect(online).toContainText("Broker login: active");
    await expect(online.locator("[data-kill-state]")).toHaveText("OFF");
    await expect(online.locator("[data-kill-toggle]")).toHaveCount(0);
    await expect(online.locator("[data-kill-fallback] code")).toHaveText("sudo docker exec atp-runner python -m app.runner kill");
    const offline = page.locator("[data-card='runner-e5f6a7b8']");
    await expect(offline.locator("[data-online='offline']")).toHaveText("Offline");
    await expect(offline).toContainText("Broker login: expired");
    const mirror = page.locator("[data-card='runner-mirror']").first();
    await expect(mirror).toContainText("From your runner");
    await expect(mirror.locator("[data-mirror-positions]")).toContainText("MADEUP");
    await expect(page.getByRole("switch", { name: "Send copies to ATP" })).toHaveAttribute("aria-checked", "true");
    await proof(page, "1440-paired");
    await online.getByRole("button", { name: "Remove runner" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Remove this runner?");
    await dialog.getByRole("button", { name: "Remove runner" }).click();
    await expect.poll(() => calls.posts).toContain("POST /runner/devices/a1b2c3d4/revoke");
    await expect(page.getByText("My VPS was removed from your account.")).toBeVisible();
    await expect(page.locator("[data-card='runner-a1b2c3d4']")).toHaveCount(0);
  });

  test("with `user_runner` off: no entry, and the address says Coming soon", async ({ page }) => {
    await open(page, { flag: false });
    await expect(sidebar(page).getByRole("link", { name: "My runner" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Coming soon" })).toBeVisible();
  });
});

for (const width of [390, 360]) {
  test.describe(`My runner ${width} phone`, () => {
    test.use({ viewport: { width, height: 844 }, hasTouch: true, isMobile: true });

    test("empty and paired: no sideways scroll, 44 px targets", async ({ page }) => {
      await open(page);
      await expect(page.locator("[data-step]")).toHaveCount(6);
      await expectPhoneLayout(page);
      await proof(page, `${width}-empty`);
    });

    test("paired", async ({ page }) => {
      await open(page, { devices: [DEVICE] });
      await expect(page.locator("[data-card='runner-a1b2c3d4'] [data-online='online']")).toHaveText("Online");
      await expect(page.locator("[data-mirror-orders]")).toContainText("MADEUP");
      await expectPhoneLayout(page);
      await proof(page, `${width}-paired`);
    });
  });
}
