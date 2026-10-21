import { chromium, devices, expect, test, type BrowserContext } from "@playwright/test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mockApi } from "./mockApi";

/**
 * D3: the installable app on the production build (`vite preview`). Checks what Chrome needs before it offers
 * "Install app": a valid manifest (name, start_url, scope, display, colours, 192 / 512 icons with a maskable one, id), a
 * service worker registered for scope `/` with a fetch handler, and Chromium's own verdict
 * (`Page.getInstallabilityErrors` empty).
 *
 * The page is served on 127.0.0.1, a secure context like the https production domain, so the worker may register.
 * The rest of the suite blocks workers (playwright.config.ts) and uses incognito-like contexts, in which Chromium never
 * calls a page installable; this spec starts its own persistent (normal) profile with workers allowed.
 * A real Android phone is checked by hand: docs/deploy/HOSTINGER_RUNBOOK.md, "PWA install on Android".
 */

const { defaultBrowserType: _ignored, ...pixel } = devices["Pixel 7"];

type Icon = { src: string; sizes: string; type?: string; purpose?: string };

/** Width and height from a PNG's IHDR chunk. */
function pngSize(bytes: Buffer): [number, number] {
  expect(bytes.subarray(1, 4).toString("latin1")).toBe("PNG");
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

test.describe("PWA installability (D3)", () => {
  let context: BrowserContext;
  let profile = "";
  let ownTrace = false;

  test.beforeEach(async ({}, info) => {
    // One run is enough (it starts its own Chromium); the desktop project owns it.
    test.skip(info.project.name !== "desktop", "desktop project only");
    profile = mkdtempSync(join(tmpdir(), "atp-pwa-"));
    context = await chromium.launchPersistentContext(profile, {
      ...pixel,                                                    // an Android Chrome phone
      baseURL: info.project.use.baseURL,
      serviceWorkers: "allow",
      headless: true,
      ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}),
    });
    // A trace for CI when a test fails. With the config's `trace` option on, Playwright Test already traces every
    // context this worker opens, this hand-made one included (and attaches it on failure); start our own only when it
    // does not, so a failure always leaves trace.zip in the test's output folder.
    ownTrace = await context.tracing.start({ screenshots: true, snapshots: true, sources: true }).then(() => true, () => false);
  });

  test.afterEach(async ({}, info) => {
    if (context) {
      if (ownTrace) {
        const failed = info.status !== info.expectedStatus;
        await context.tracing.stop(failed ? { path: info.outputPath("trace.zip") } : undefined).catch(() => undefined);
      }
      await context.close();
    }
    if (profile) rmSync(profile, { recursive: true, force: true });
  });

  test("the manifest is served and complete", async () => {
    const page = context.pages()[0] ?? await context.newPage();
    const res = await page.request.get("/manifest.webmanifest");
    expect(res.status()).toBe(200);
    const manifest = await res.json() as Record<string, unknown> & { icons: Icon[] };
    expect(manifest).toMatchObject({
      id: "/", name: "Advance Trading Platform", short_name: "ATP", scope: "/", display: "standalone",
      background_color: expect.stringMatching(/^#[0-9a-f]{6}$/i), theme_color: expect.stringMatching(/^#[0-9a-f]{6}$/i),
    });
    expect(String(manifest.start_url)).toMatch(/^\/(\?|$)/);       // inside the scope
    for (const size of ["192x192", "512x512"]) {
      const png = manifest.icons.filter((i) => i.sizes === size && i.type === "image/png");
      expect(png.some((i) => (i.purpose ?? "any").split(" ").includes("any")), `${size} any`).toBe(true);
      expect(png.some((i) => (i.purpose ?? "").split(" ").includes("maskable")), `${size} maskable`).toBe(true);
    }
    for (const icon of manifest.icons.filter((i) => i.type === "image/png")) {
      const got = await page.request.get(icon.src);
      expect(got.status(), icon.src).toBe(200);
      const [w, h] = pngSize(await got.body());
      expect(`${w}x${h}`, icon.src).toBe(icon.sizes);
    }
  });

  test("the service worker registers for scope / and becomes active; Chromium calls the page installable", async () => {
    const page = context.pages()[0] ?? await context.newPage();
    await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ } });
    await mockApi(page);
    await page.goto("/");

    expect(await page.evaluate(() => window.isSecureContext)).toBe(true);
    const worker = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      const active = reg.active!;
      if (active.state !== "activated") {
        await new Promise<void>((done) => active.addEventListener("statechange", () => { if (active.state === "activated") done(); }));
      }
      return { scope: reg.scope, script: active.scriptURL, state: active.state };
    });
    const origin = new URL(page.url()).origin;
    expect(worker).toEqual({ scope: `${origin}/`, script: `${origin}/sw.js`, state: "activated" });

    const sw = await (await page.request.get("/sw.js")).text();
    expect(sw).toContain(`addEventListener("fetch"`);
    expect(sw).not.toContain("__ATP_BUILD__");                     // stamped by the build

    const cdp = await context.newCDPSession(page);
    const manifest = await cdp.send("Page.getAppManifest") as { url: string; errors: { message: string }[] };
    expect(manifest.url).toBe(`${origin}/manifest.webmanifest`);
    expect(manifest.errors).toEqual([]);
    await expect.poll(async () => {
      const { installabilityErrors } = await cdp.send("Page.getInstallabilityErrors");
      return installabilityErrors.map((e) => e.errorId);
    }, { timeout: 15_000 }).toEqual([]);

    // No "needs a domain" notice any more (D3).
    await expect(page.locator("[data-install='unavailable']")).toHaveCount(0);
    expect(await page.locator("body").innerText()).not.toMatch(/set up the domain/i);
  });
});
