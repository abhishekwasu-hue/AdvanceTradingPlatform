import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * LAB-18 (AMW-7, flag `screener_copilot`, behind `ui_v2`): the saved scan's versions live on the server.
 * - First load with the feature: the history this device kept (versions.ts) is uploaded once per scan, then the device
 *   copy is cleared.
 * - The Versions dialog lists the server's versions newest first with what changed; "Open this version" loads its
 *   blocks into the builder and saves nothing; the next save is a new version.
 * - Baselines `screener-versions-v2-<width>-<theme>.png` (390 / 1440, light / dark): the dialog. Made-up data.
 * After an intended change: `npx playwright test --project=phone e2e/screener-versions-v2.e2e.ts --update-snapshots`.
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const KEY = "atp_screener_versions:1";
const RSI = { id: "smvki4md11", kind: "indicator", left: { fn: "RSI", params: { n: 14 }, tf: null }, op: ">", right: { kind: "value", value: 60 }, enabled: true };
const EMA = { id: "smvki4md12", kind: "indicator", left: { fn: "close", params: {}, tf: null }, op: ">",
  right: { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 20 }, tf: null } }, enabled: true };
// What #412 kept on this device for scan 41: two saves.
const DEVICE = {
  41: [
    { at: "2026-10-19T08:35:00.000Z", name: "Momentum", base_tf: "1d", stages: [RSI], texts: ["RSI(14) > 60"], summaries: ["RSI(14) above 60"] },
    { at: "2026-10-20T08:35:00.000Z", name: "Momentum", base_tf: "1d", stages: [RSI, EMA], texts: ["RSI(14) > 60", "close > EMA(close, 20)"],
      summaries: ["RSI(14) above 60", "close above EMA(close, 20)"] },
  ],
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, ai_copilot: true, screener_v2: true, screener_copilot: true }, brokers: CONNECTED };
const OFF: MockOptions = { features: { ui_v2: true, ai_copilot: true, screener_v2: true }, brokers: CONNECTED };
const THEMES = ["dark", "light"] as const;

interface Version { version: number; number: number; latest: boolean; name: string; base_tf: string; conditions: number; change: string[];
  origin: string; created_at: string; builder: unknown }

interface Calls { imports: { url: string; body: { versions: unknown[] } }[]; saves: { method: string; body: Record<string, unknown> }[]; reads: string[] }

async function open(page: Page, theme: "dark" | "light", options: MockOptions = ON, device: unknown = DEVICE): Promise<Calls> {
  const calls: Calls = { imports: [], saves: [], reads: [] };
  const server: Version[] = [];
  const add = (v: Omit<Version, "number" | "latest" | "version">) => {
    server.push({ ...v, version: server.length + 1, number: 0, latest: false });
    server.forEach((x, i) => { x.number = i + 1; x.latest = i === server.length - 1; });
  };
  await page.addInitScript(([t, d]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener"]));
      // only on the first load: a reload must see what the page left behind
      if (d && !sessionStorage.getItem("seeded")) { localStorage.setItem("atp_screener_versions:1", JSON.stringify(d)); sessionStorage.setItem("seeded", "1"); }
    } catch { /* storage unavailable */ }
  }, [theme, device] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/screens\/41\/versions\/import$/.test(url)) {
      const body = route.request().postDataJSON() as { versions: { at: string; name: string; base_tf: string; texts: string[]; stages: unknown[]; summaries: string[] }[] };
      calls.imports.push({ url, body });
      body.versions.forEach((v, i) => add({ name: v.name, base_tf: v.base_tf, conditions: v.texts.length, origin: "device", created_at: v.at,
        change: i === 0 ? ["First saved version."] : [`Added: ${v.summaries[v.summaries.length - 1]}`], builder: { stages: v.stages, texts: v.texts, summaries: v.summaries } }));
      return route.fulfill({ json: { imported: body.versions.length, skipped: 0, already: false } });
    }
    if (/\/screens\/41\/versions$/.test(url)) {
      return route.fulfill({ json: { screen_id: 41, imported: true, max: 50, versions: server.map(({ builder: _b, ...v }) => v) } });
    }
    const one = /\/screens\/41\/versions\/(\d+)$/.exec(url);
    if (one) {
      calls.reads.push(url);
      const v = server.find((x) => x.version === Number(one[1]));
      return v ? route.fulfill({ json: { ...v, text: "", params: {}, ast_version: "screenql/1" } }) : route.fulfill({ status: 404, json: { detail: "Version not found" } });
    }
    if (/\/screens(\/41)?$/.test(url) && (method === "POST" || method === "PUT")) {
      const body = route.request().postDataJSON() as { name: string; source: string; base_tf: string; builder?: { texts: string[] } };
      calls.saves.push({ method, body });
      const prev = server[server.length - 1];
      add({ name: body.name, base_tf: body.base_tf, conditions: body.builder?.texts.length ?? 1, origin: "save",
        created_at: `2026-10-23T10:0${server.length}:00Z`, builder: body.builder,
        change: prev && prev.name !== body.name ? [`Name: “${prev.name}” to “${body.name}”`] : ["Removed: close above EMA(close, 20)"] });
      return route.fulfill({ status: method === "POST" ? 201 : 200, json: { id: 41, name: body.name, text: body.source, base_tf: body.base_tf } });
    }
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_gallery" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
  return calls;
}

const save = (page: Page) => page.getByRole("button", { name: "Save" }).filter({ visible: true }).click();

/** The Momentum starter saved under a new name: the device's two versions plus this save. */
async function saveScan(page: Page, calls: Calls) {
  await expect.poll(() => calls.imports.length).toBe(1);
  await page.getByRole("button", { name: "Momentum" }).click();
  await page.getByLabel("Scan name").fill("My momentum scan");
  await save(page);
  await expect(page.getByTestId("scan-versions-open")).toHaveText("Versions (3)");
}

test.describe("Scan versions on the server, 1440", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("the device history is uploaded once, then the device copy is cleared", async ({ page }) => {
    const calls = await open(page, "light");
    await expect.poll(() => calls.imports.length).toBe(1);
    expect(calls.imports[0].url).toMatch(/\/screener\/screens\/41\/versions\/import$/);
    expect(calls.imports[0].body.versions).toEqual(DEVICE[41]);
    await expect.poll(() => page.evaluate((k) => localStorage.getItem(k), KEY)).toBeNull();
    await page.reload();
    await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(calls.imports).toHaveLength(1);                             // nothing left to upload
  });

  test("a save sends its blocks and lists the server's versions; opening an older one saves nothing; the next save is a new version", async ({ page }) => {
    const calls = await open(page, "light");
    await saveScan(page, calls);
    expect(calls.saves).toHaveLength(1);
    expect(calls.saves[0].body.builder).toMatchObject({ texts: ["RSI(14) > 60", "close > EMA(close, 20)"], summaries: ["RSI(14) above 60", "close above EMA(close, 20)"] });
    await page.getByTestId("scan-versions-open").click();
    const dialog = page.getByRole("dialog", { name: "Versions of this scan" });
    await expect(dialog.getByTestId("scan-versions").locator("li[data-testid^='scan-version-']")).toHaveCount(3);
    await expect(dialog.getByTestId("scan-version-3")).toContainText("Version 3(latest)");
    await expect(dialog.getByTestId("scan-version-3")).toContainText("Name: “Momentum” to “My momentum scan”");
    await expect(dialog.getByTestId("scan-version-2")).toContainText("Added: close above EMA(close, 20)");
    await expect(dialog.getByTestId("scan-version-1")).toContainText("Saved 19 Oct, 14:05 · 1 condition · 1d");
    await expect(dialog.getByTestId("scan-version-open-3")).toHaveCount(0);
    await expect(dialog).toContainText("Kept with your account, the newest 50 for each scan.");
    await expect(dialog).not.toContainText("this device");
    await dialog.getByTestId("scan-version-open-1").click();
    await expect(dialog).toBeHidden();
    expect(calls.reads.at(-1)).toMatch(/\/versions\/1$/);
    await expect(page.getByLabel("Scan name")).toHaveValue("Momentum");
    await expect(page.getByText("Version 1 is in the builder. Save to keep it as the newest version.")).toBeVisible();
    expect(calls.saves).toHaveLength(1);                                // opening saved nothing
    await save(page);
    await expect(page.getByTestId("scan-versions-open")).toHaveText("Versions (4)");
    expect(calls.saves[1].method).toBe("PUT");
    expect(calls.saves[1].body.builder).toMatchObject({ texts: ["RSI(14) > 60"] });
  });

  test("flag off: no versions button and nothing uploaded", async ({ page }) => {
    const calls = await open(page, "light", OFF);
    await page.waitForLoadState("networkidle");
    expect(calls.imports).toHaveLength(0);
    expect(await page.evaluate((k) => localStorage.getItem(k), KEY)).not.toBeNull();   // kept for when it turns on
    await page.getByRole("button", { name: "Momentum" }).click();
    await save(page);
    await expect(page.getByTestId("scan-versions-open")).toHaveCount(0);
    expect(calls.saves[0].body.builder).toBeUndefined();
  });

  for (const theme of THEMES) {
    test(`the Versions dialog looks the same (${theme})`, async ({ page }) => {
      const calls = await open(page, theme);
      await saveScan(page, calls);
      await page.getByTestId("scan-versions-open").click();
      await expect(page.getByRole("dialog", { name: "Versions of this scan" }).getByTestId("scan-version-1")).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveScreenshot(`screener-versions-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }
});

test.describe("Scan versions on the server, 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the phone layout rules hold with the dialog's page", async ({ page }) => {
    const calls = await open(page, "light");
    await saveScan(page, calls);
    await expectPhoneLayout(page);
  });

  for (const theme of THEMES) {
    test(`the Versions dialog looks the same (${theme})`, async ({ page }) => {
      const calls = await open(page, theme);
      await saveScan(page, calls);
      await page.getByTestId("scan-versions-open").click();
      await expect(page.getByRole("dialog", { name: "Versions of this scan" }).getByTestId("scan-version-1")).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveScreenshot(`screener-versions-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }
});
