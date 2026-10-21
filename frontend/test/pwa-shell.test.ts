import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { fileURLToPath } from "node:url";
import { atpServiceWorker } from "../vite.config";

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

  it("uses the dark surface token for its colours", () => {
    const tokens = readFileSync(join(HERE, "../src/styles/tokens.css"), "utf8");
    const surface = /--surface: (\d+) (\d+) (\d+);/.exec(tokens)!.slice(1).map(Number);
    const hex = `#${surface.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
    expect(manifest.background_color).toBe(hex);
    expect(manifest.theme_color).toBe(hex);
  });
});

