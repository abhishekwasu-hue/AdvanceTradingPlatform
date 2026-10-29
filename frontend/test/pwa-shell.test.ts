import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { fileURLToPath } from "node:url";
import { PRELOAD_FONT, atpFontPreload, atpServiceWorker } from "../vite.config";

/* PW1 shell checks that need Node (files, a sandboxed worker). They live outside src/ because the app's tsconfig
 * has no Node types; vitest runs them with the rest. */
const HERE = fileURLToPath(new URL(".", import.meta.url));
const PUBLIC = join(HERE, "../public");
const ORIGIN = "https://atp.example";

/** Loads public/sw.js in a sandbox with a fake `self`, Cache Storage and network, and returns its event handlers. */
function loadWorker(network: (url: string) => Promise<Response>) {
  const handlers: Record<string, (event: unknown) => void> = {};
  const store = new Map<string, Map<string, Response>>();
  const caches = {
    open: async (name: string) => {
      if (!store.has(name)) store.set(name, new Map());
      const c = store.get(name)!;
      return {
        addAll: async (paths: string[]) => { for (const p of paths) c.set(new URL(p, ORIGIN).href, new Response(p)); },
        put: async (req: Request, res: Response) => { c.set(req.url, res); },
      };
    },
    match: async (req: Request | string) => {
      const key = typeof req === "string" ? new URL(req, ORIGIN).href : req.url;
      for (const c of store.values()) if (c.has(key)) return c.get(key);
      return undefined;
    },
    keys: async () => [...store.keys()],
    delete: async (name: string) => store.delete(name),
  };
  const self = {
    location: new URL(ORIGIN),
    addEventListener: (type: string, fn: (event: unknown) => void) => { handlers[type] = fn; },
    skipWaiting: vi.fn(),
    clients: { claim: async () => undefined },
  };
  const source = readFileSync(join(PUBLIC, "sw.js"), "utf8");
  runInNewContext(source, { self, caches, fetch: network, URL, Response, Promise, console });
  return { handlers, store, self };
}

/** Dispatches a fake fetch event; returns the response the worker chose, or null when it left the request alone. */
async function dispatchFetch(handlers: Record<string, (event: unknown) => void>, path: string, init: { method?: string; mode?: string } = {}) {
  let responded = null as Promise<Response | undefined> | null;
  const waits: Promise<unknown>[] = [];
  handlers.fetch({
    request: { url: new URL(path, ORIGIN).href, method: init.method ?? "GET", mode: init.mode ?? "no-cors" },
    respondWith: (p: Promise<Response | undefined>) => { responded = p; },
    waitUntil: (p: Promise<unknown>) => { waits.push(p); },
  });
  const res = responded ? await responded : null;
  await Promise.all(waits);
  return res;
}

async function installed(network: (url: string) => Promise<Response>) {
  const w = loadWorker(network);
  const waits: Promise<unknown>[] = [];
  w.handlers.install({ waitUntil: (p: Promise<unknown>) => waits.push(p) });
  await Promise.all(waits);
  return w;
}

describe("service worker (PW1)", () => {
  it("never touches API calls, other origins or non-GET requests", async () => {
    const network = vi.fn(async () => new Response("live"));
    const { handlers, store } = await installed(network);
    expect(await dispatchFetch(handlers, "/api/market/quote?symbol=NIFTY")).toBeNull();
    expect(await dispatchFetch(handlers, "/api/v1/positions")).toBeNull();
    expect(await dispatchFetch(handlers, "https://fonts.googleapis.com/css2?family=Inter")).toBeNull();
    expect(await dispatchFetch(handlers, "/assets/index-abc.js", { method: "POST" })).toBeNull();
    const cached = [...store.values()].flatMap((c) => [...c.keys()]);
    expect(cached.some((k) => k.includes("/api/"))).toBe(false);
  });

  it("caches hashed build files after the first fetch and serves them from the cache", async () => {
    const network = vi.fn(async () => new Response("chunk", { status: 200 }));
    const { handlers } = await installed(network);
    // Response.type is "default" outside a browser; the worker stores only "basic" (same-origin) responses.
    network.mockImplementation(async () => Object.defineProperty(new Response("chunk"), "type", { value: "basic" }));
    await dispatchFetch(handlers, "/assets/OptionChainPage-1a2b.js");
    await dispatchFetch(handlers, "/assets/OptionChainPage-1a2b.js");
    expect(network).toHaveBeenCalledTimes(1);
  });

  it("does not cache anything outside /assets, /icons and the precache list", async () => {
    const network = vi.fn(async () => Object.defineProperty(new Response("x"), "type", { value: "basic" }));
    const { handlers } = await installed(network);
    expect(await dispatchFetch(handlers, "/version-unknown.json")).toBeNull();
    expect(await dispatchFetch(handlers, "/sw.js")).toBeNull();
  });

  it("loads pages from the network and shows the offline page without a connection", async () => {
    const online = await installed(async () => new Response("<html>app</html>"));
    expect(await (await dispatchFetch(online.handlers, "/option-chain", { mode: "navigate" }))!.text()).toBe("<html>app</html>");

    const offline = await installed(async (url: string) => {
      if (url.endsWith("offline.html") || url.includes("/icons/") || url.endsWith(".svg") || url.endsWith(".webmanifest")) return new Response(url);
      throw new TypeError("Failed to fetch");
    });
    const res = await dispatchFetch(offline.handlers, "/positions", { mode: "navigate" });
    expect(await res!.text()).toBe("/offline.html");
  });

  it("removes old shell caches on activate and switches only when the page asks", async () => {
    const w = await installed(async () => new Response(""));
    w.store.set("atp-shell-old", new Map());
    w.store.set("other-cache", new Map());
    const waits: Promise<unknown>[] = [];
    w.handlers.activate({ waitUntil: (p: Promise<unknown>) => waits.push(p) });
    await Promise.all(waits);
    expect([...w.store.keys()].sort()).toEqual(["atp-shell-__ATP_BUILD__", "other-cache"]);
    expect(w.self.skipWaiting).not.toHaveBeenCalled();
    w.handlers.message({ data: { type: "SKIP_WAITING" } });
    expect(w.self.skipWaiting).toHaveBeenCalledTimes(1);
  });
});

describe("build stamping", () => {
  it("replaces the version placeholder with a hash of the emitted file names", () => {
    const dir = mkdtempSync(join(tmpdir(), "atp-sw-"));
    writeFileSync(join(dir, "sw.js"), readFileSync(join(PUBLIC, "sw.js")));
    const plugin = atpServiceWorker() as unknown as {
      configResolved: (c: { build: { outDir: string } }) => void;
      generateBundle: (o: unknown, b: Record<string, unknown>) => void;
      writeBundle: () => void;
    };
    plugin.configResolved({ build: { outDir: dir } });
    plugin.generateBundle({}, { "assets/index-1.js": {}, "assets/index-1.css": {} });
    plugin.writeBundle();
    const stamped = readFileSync(join(dir, "sw.js"), "utf8");
    expect(stamped).not.toContain("__ATP_BUILD__");
    expect(stamped).toMatch(/const VERSION = "[0-9a-f]{12}";/);
  });
});

describe("web app manifest", () => {
  const manifest = JSON.parse(readFileSync(join(PUBLIC, "manifest.webmanifest"), "utf8"));

  it("has what browsers need to offer installation", () => {
    expect(manifest.name).toBe("AiAlgoDesk");
    expect(manifest.short_name).toBe("AiAlgoDesk");
    expect(manifest.short_name.length).toBeLessThanOrEqual(12);
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url.startsWith("/")).toBe(true);
    const sizes = (purpose: string) => manifest.icons.filter((i: { purpose: string }) => i.purpose === purpose).map((i: { sizes: string }) => i.sizes);
    expect(sizes("any")).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    expect(sizes("maskable")).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  });

  it("points every icon at a file that exists, and the shortcuts at the four daily pages", () => {
    for (const icon of manifest.icons) expect(() => readFileSync(join(PUBLIC, icon.src))).not.toThrow();
    const paths = manifest.shortcuts.map((s: { url: string }) => new URL(s.url, ORIGIN).pathname);
    expect(paths).toEqual(["/", "/screener", "/option-chain", "/notifications"]);
  });

  /** A PNG's width and height, from its IHDR chunk. */
  const pngSize = (src: string) => {
    const png = readFileSync(join(PUBLIC, src));
    expect(png.subarray(1, 4).toString("latin1")).toBe("PNG");
    return `${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`;
  };

  it("AUD-G2.3: every PNG icon, maskable ones included, is the size it declares", () => {
    for (const icon of manifest.icons.filter((i: { type: string }) => i.type === "image/png")) expect(pngSize(icon.src)).toBe(icon.sizes);
  });

  it("AUD-G2.3: a phone and a desktop screenshot for the richer install prompt, each the size it declares", () => {
    const shots = manifest.screenshots as { src: string; sizes: string; type: string; form_factor: string; label: string }[];
    expect(shots.map((s) => s.form_factor).sort()).toEqual(["narrow", "wide"]);
    for (const shot of shots) {
      expect(shot.type).toBe("image/png");
      expect(shot.label.length).toBeGreaterThan(0);
      expect(pngSize(shot.src)).toBe(shot.sizes);
      const [w, h] = shot.sizes.split("x").map(Number);
      // Chromium's rules: each side 320-3840 px, the long side at most 2.3 times the short one, the form factor's shape.
      for (const side of [w, h]) { expect(side).toBeGreaterThanOrEqual(320); expect(side).toBeLessThanOrEqual(3840); }
      expect(Math.max(w, h) / Math.min(w, h)).toBeLessThanOrEqual(2.3);
      expect(shot.form_factor === "wide" ? w > h : h > w).toBe(true);
    }
  });

  it("uses the dark surface token for its colours", () => {
    const tokens = readFileSync(join(HERE, "../src/styles/tokens.css"), "utf8");
    const surface = /--surface: (\d+) (\d+) (\d+);/.exec(tokens)!.slice(1).map(Number);
    const hex = `#${surface.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
    expect(manifest.background_color).toBe(hex);
    expect(manifest.theme_color).toBe(hex);
  });
});

describe("AUD-G2.3 font preload", () => {
  type Handler = (html: string, ctx: { bundle?: Record<string, unknown> }) => { tag: string; attrs: Record<string, unknown> }[];
  const plugin = () => {
    const p = atpFontPreload() as unknown as { configResolved: (c: { base: string }) => void; transformIndexHtml: { order: string; handler: Handler } };
    p.configResolved({ base: "/" });
    return p.transformIndexHtml;
  };

  it("preloads the Inter Latin file the build emitted, same-origin and with crossorigin (fonts are CORS requests)", () => {
    const bundle = { "assets/inter-latin-ext-wght-normal-AAA.woff2": {}, "assets/inter-latin-wght-normal-Dx4kX_Al.woff2": {}, "assets/index-1.js": {} };
    expect(plugin().order).toBe("post");
    expect(plugin().handler("", { bundle })).toEqual([{ tag: "link", injectTo: "head",
      attrs: { rel: "preload", href: "/assets/inter-latin-wght-normal-Dx4kX_Al.woff2", as: "font", type: "font/woff2", crossorigin: true } }]);
    expect(PRELOAD_FONT.test("assets/inter-latin-ext-wght-normal-AAA.woff2")).toBe(false);
  });

  it("stops the build when the file is missing (never a silent loss of the preload)", () => {
    expect(() => plugin().handler("", { bundle: { "assets/index-1.js": {} } })).toThrow(/Inter Latin/);
  });
});
