import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { resolveChartTheme } from "./chartTheme";
import { ChartPrefsStore, DEFAULT_CHART_PREFS, applyPatch, chartPrefsStore, cleanPrefs, conflictOf, type ChartPrefs } from "./useChartPrefs";

type Call = { path: string; init?: RequestInit };

/** A fake `request`: answers from the handler, records each call. */
function server(handler: (call: Call, n: number) => unknown) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    const call = { path, init };
    calls.push(call);
    const out = handler(call, calls.length);
    if (out instanceof Error) throw out;
    return out;
  }) as unknown as ConstructorParameters<typeof ChartPrefsStore>[1];
  return { calls, fetcher, body: (i: number) => JSON.parse(String(calls[i].init?.body)) as ChartPrefs };
}

function memoryStorage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); }, m };
}

const conflict = (version: number, current: Partial<ChartPrefs>) =>
  new ApiError(409, "the preferences changed", "", JSON.stringify({ detail: { message: "the preferences changed since this edit was made", version, current } }));

afterEach(() => { vi.unstubAllGlobals(); });

describe("UI2-4b1 chart prefs", () => {
  it("defaults: the app theme (which follows the app), no grid, the theme's candle colours, volume and RSI on", () => {
    expect(DEFAULT_CHART_PREFS).toEqual({ theme: "app", grid: false, candle_colors: null, indicators: { volume: true, rsi: true }, reader_overlay: false });
    expect(resolveChartTheme(DEFAULT_CHART_PREFS.theme, "light")).toBe("light");
    expect(resolveChartTheme(DEFAULT_CHART_PREFS.theme, "dark")).toBe("dark");
  });

  it("anything malformed falls back field by field", () => {
    expect(cleanPrefs(null)).toEqual(DEFAULT_CHART_PREFS);
    expect(cleanPrefs({ theme: "blue", grid: "yes", candle_colors: { up: "#ffffff", down: "#FFFFFF" }, indicators: { rsi: false } }))
      .toEqual({ ...DEFAULT_CHART_PREFS, indicators: { volume: true, rsi: false } });
    expect(applyPatch(DEFAULT_CHART_PREFS, { indicators: { volume: false } }).indicators).toEqual({ volume: false, rsi: true });
  });

  it("loads the server's value and version, then saves a change with that version", async () => {
    const s = server((c) => (c.path === "/me/prefs"
      ? { prefs: { chart: { ...DEFAULT_CHART_PREFS, theme: "dark" } }, versions: { chart: 4 } }
      : { namespace: "chart", value: {}, version: 5 }));
    const store = new ChartPrefsStore(1, s.fetcher);
    await store.loadOnce();
    await store.loadOnce();                                                        // read once
    expect(store.get()).toMatchObject({ loaded: true, error: null, prefs: { theme: "dark" } });
    await store.update({ grid: true });
    expect(s.calls.map((c) => c.path)).toEqual(["/me/prefs", "/me/prefs/chart?version=4"]);
    expect(s.calls[1].init?.method).toBe("PUT");
    expect(s.body(1)).toMatchObject({ theme: "dark", grid: true });
    await store.update({ theme: "light" });
    expect(s.calls[2].path).toBe("/me/prefs/chart?version=5");
  });

  it("a change made before the server answers is kept and saved on the version read", async () => {
    let release = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const calls: string[] = [];
    const fetcher = (async (path: string) => {
      calls.push(path);
      if (path === "/me/prefs") { await gate; return { prefs: { chart: { ...DEFAULT_CHART_PREFS, theme: "light" } }, versions: { chart: 2 } }; }
      return { version: 3 };
    }) as unknown as ConstructorParameters<typeof ChartPrefsStore>[1];
    const store = new ChartPrefsStore(2, fetcher);
    const loading = store.loadOnce();
    const saving = store.update({ grid: true });
    expect(store.get().prefs.grid).toBe(true);                                     // at once
    release();
    await loading;
    await saving;
    expect(calls).toEqual(["/me/prefs", "/me/prefs/chart?version=2"]);
    expect(store.get().prefs).toMatchObject({ theme: "light", grid: true });        // the server's theme, this change
  });

  it("409: takes the other device's value, applies this change on top once more and saves on the new version", async () => {
    const s = server((c, n) => {
      if (c.path === "/me/prefs") return { prefs: { chart: DEFAULT_CHART_PREFS }, versions: { chart: 1 } };
      return n === 2 ? conflict(3, { ...DEFAULT_CHART_PREFS, theme: "light" }) : { version: 4 };
    });
    const store = new ChartPrefsStore(3, s.fetcher);
    await store.loadOnce();
    await store.update({ grid: true });
    expect(s.calls.map((c) => c.path)).toEqual(["/me/prefs", "/me/prefs/chart?version=1", "/me/prefs/chart?version=3"]);
    expect(s.body(2)).toMatchObject({ theme: "light", grid: true });               // neither change is lost
    expect(store.get()).toMatchObject({ error: null, prefs: { theme: "light", grid: true } });
  });

  it("409 twice: keeps the server's value and says so; a network failure keeps the change on this device", async () => {
    const s = server((c) => (c.path === "/me/prefs" ? { prefs: {}, versions: {} } : conflict(9, { ...DEFAULT_CHART_PREFS, theme: "dark" })));
    const store = new ChartPrefsStore(4, s.fetcher);
    await store.loadOnce();
    await store.update({ grid: true });
    expect(s.calls).toHaveLength(3);
    expect(store.get().prefs).toMatchObject({ theme: "dark", grid: false });
    expect(store.get().error).toMatch(/another device/);

    const down = server(() => new ApiError(0, "offline"));
    const offline = new ChartPrefsStore(5, down.fetcher);
    await offline.loadOnce();
    expect(offline.get().error).toMatch(/could not be loaded/);
    await offline.update({ theme: "light" });
    expect(down.calls.map((c) => c.path)).toEqual(["/me/prefs", "/me/prefs"]);  // never read: read again, never a blind write
    expect(offline.get().prefs.theme).toBe("light");
    expect(offline.get().error).toMatch(/not saved/);
  });

  it("after a failed first read, a save reads the version first and writes on it", async () => {
    let n = 0;
    const s = server((c) => {
      n += 1;
      if (c.path === "/me/prefs") return n === 1 ? new ApiError(0, "offline") : { prefs: { chart: { ...DEFAULT_CHART_PREFS, grid: true } }, versions: { chart: 7 } };
      return { version: 8 };
    });
    const store = new ChartPrefsStore(6, s.fetcher);
    await store.loadOnce();
    await store.update({ theme: "dark" });
    expect(s.calls.map((c) => c.path)).toEqual(["/me/prefs", "/me/prefs", "/me/prefs/chart?version=7"]);
    expect(s.body(2)).toMatchObject({ theme: "dark", grid: true });             // the other device's grid is kept
  });

  it("conflictOf reads only a 409 with a version", () => {
    expect(conflictOf(conflict(2, { grid: true }))).toEqual({ version: 2, current: { ...DEFAULT_CHART_PREFS, grid: true } });
    expect(conflictOf(new ApiError(422, "bad"))).toBeNull();
    expect(conflictOf(new ApiError(409, "x", "", "not json"))).toBeNull();
    expect(conflictOf(new Error("x"))).toBeNull();
  });

  it("keeps a per-user copy in localStorage for the first paint, and works when storage throws", async () => {
    const storage = memoryStorage();
    vi.stubGlobal("localStorage", storage);
    const s = server((c) => (c.path === "/me/prefs" ? { prefs: { chart: { ...DEFAULT_CHART_PREFS, theme: "light" } }, versions: { chart: 1 } } : { version: 2 }));
    await new ChartPrefsStore(6, s.fetcher).loadOnce();
    expect(JSON.parse(storage.m.get("atp_chart_prefs:6") ?? "{}")).toMatchObject({ theme: "light" });
    expect(new ChartPrefsStore(6, s.fetcher).get().prefs.theme).toBe("light");     // before any request
    expect(new ChartPrefsStore(7, s.fetcher).get().prefs.theme).toBe("app");       // another user: not theirs

    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    const blocked = new ChartPrefsStore(8, s.fetcher);
    expect(blocked.get().prefs).toEqual(DEFAULT_CHART_PREFS);
    await blocked.update({ grid: true });
    expect(blocked.get().prefs.grid).toBe(true);
  });

  it("signed out: no store, nothing is read or sent", () => {
    expect(chartPrefsStore(null)).toBeNull();
    expect(chartPrefsStore(11)).toBe(chartPrefsStore(11));                         // one store per user
  });
});
