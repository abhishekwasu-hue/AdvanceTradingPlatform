import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { cleanStored, dismissChip, setupChip, shouldAutoOpen, skipStep, type SetupStored } from "./model";
import { SetupStore, setupConflict } from "./useSetup";

/** UI2-7: the setup progress store - the server's answer, the per-user cache, skip / dismiss saves with versions, 409. */

type Call = { path: string; init?: RequestInit };
const T = "2026-10-21T09:30:00Z";
const EMPTY = cleanStored(null);

function answer(stored: SetupStored, version: number, done: string[] = []) {
  return { steps: ["broker", "risk", "strategy", "alerts"].map((key) => ({ key, done: done.includes(key), skipped: false, skipped_at: null })),
    done_count: done.length, next: null, complete: false, stored, version };
}

function server(handler: (call: Call, n: number) => unknown) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    const call = { path, init };
    calls.push(call);
    const out = handler(call, calls.length);
    if (out instanceof Error) throw out;
    return out;
  }) as unknown as ConstructorParameters<typeof SetupStore>[1];
  return { calls, fetcher, body: (i: number) => JSON.parse(String(calls[i].init?.body)) as SetupStored };
}

function memoryStorage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); }, m };
}

const conflict = (version: number, current: SetupStored) =>
  new ApiError(409, "the preferences changed", "", JSON.stringify({ detail: { message: "the preferences changed since this edit was made", version, current } }));

afterEach(() => { vi.unstubAllGlobals(); });

describe("UI2-7 setup progress store", () => {
  it("reads the server's progress; only then is it fresh (the automatic opening waits for it)", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server(() => answer(EMPTY, 0, ["broker"]));
    const store = new SetupStore(1, s.fetcher);
    expect(store.get()).toMatchObject({ view: null, loaded: false, fresh: false });
    await store.loadOnce();
    await store.loadOnce();
    expect(s.calls.map((c) => c.path)).toEqual(["/me/setup"]);
    expect(store.get().fresh).toBe(true);
    expect(setupChip(store.get().view)?.label).toBe("Setup 2 of 4");
    expect(shouldAutoOpen(store.get().view)).toBe(true);
  });

  it("skip for now: shown at once, saved with the version read, the server's answer taken", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server((c) => (c.init?.method === "PUT" ? answer(JSON.parse(String(c.init.body)), 5) : answer(EMPTY, 4)));
    const store = new SetupStore(1, s.fetcher);
    await store.loadOnce();
    const saving = store.update((st) => skipStep(st, "broker", T));
    expect(store.get().view?.steps[0].skipped).toBe(true);                       // before the server answers
    await saving;
    expect(s.calls[1].path).toBe("/me/setup?version=4");
    expect(s.body(1).steps.broker.skipped_at).toBe(T);
    expect(store.get().view?.version).toBe(5);
    expect(setupChip(store.get().view)).toMatchObject({ label: "Setup 2 of 4", next: "Next: set your risk limits" });
  });

  it("resumes on reload from the cache, then the server's answer replaces it", async () => {
    const storage = memoryStorage();
    vi.stubGlobal("localStorage", storage);
    const first = new SetupStore(9, server(() => answer(skipStep(EMPTY, "broker", T), 2)).fetcher);
    await first.loadOnce();
    expect(storage.m.has("atp_setup:9")).toBe(true);
    const later = new SetupStore(9, server(() => new ApiError(0, "offline")).fetcher);
    expect(later.get().view?.steps[0].skipped).toBe(true);                       // painted from the cache
    await later.loadOnce();
    expect(later.get()).toMatchObject({ loaded: true, fresh: false });          // a failed read keeps the cache, opens nothing
    expect(new SetupStore(10, server(() => answer(EMPTY, 0)).fetcher).get().view).toBeNull();     // another user: nothing
  });

  it("a 409 (another device saved first): the change is applied again on the server's marks once", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const elsewhere = skipStep(EMPTY, "risk", T);
    const s = server((c, n) => {
      if (c.init?.method !== "PUT") return answer(EMPTY, 1);
      return n === 2 ? conflict(2, elsewhere) : answer(JSON.parse(String(c.init.body)), 3);
    });
    const store = new SetupStore(1, s.fetcher);
    await store.loadOnce();
    await store.update((st) => dismissChip(st, T));
    expect(s.calls.map((c) => c.path)).toEqual(["/me/setup", "/me/setup?version=1", "/me/setup?version=2"]);
    expect(s.body(2)).toMatchObject({ dismissed_at: T, steps: { risk: { skipped_at: T } } });   // neither change lost
    expect(store.get().error).toBeNull();
  });

  it("a second conflict keeps the server's marks and says so; a failed save says what to do", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server((c) => (c.init?.method === "PUT" ? conflict(7, EMPTY) : answer(EMPTY, 1)));
    const store = new SetupStore(1, s.fetcher);
    await store.loadOnce();
    await store.update((st) => skipStep(st, "alerts", T));
    expect(store.get().error).toBe("Your setup was changed on another device. That progress is shown now.");
    expect(store.get().view?.steps[3].skipped).toBe(false);
    const down = new SetupStore(2, server((c) => (c.init?.method === "PUT" ? new ApiError(0, "offline") : answer(EMPTY, 1))).fetcher);
    await down.loadOnce();
    await down.update((st) => skipStep(st, "alerts", T));
    expect(down.get().error).toBe("Your setup progress was not saved. Check your connection and try again.");
  });

  it("a save before any good read reads first, so another device's marks are never replaced blindly", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server((c) => (c.init?.method === "PUT" ? answer(JSON.parse(String(c.init.body)), 4) : answer(skipStep(EMPTY, "broker", T), 3)));
    const store = new SetupStore(1, s.fetcher);
    await store.update((st) => dismissChip(st, T));
    expect(s.calls.map((c) => c.path)).toEqual(["/me/setup", "/me/setup?version=3"]);
    expect(s.body(1)).toMatchObject({ dismissed_at: T, steps: { broker: { skipped_at: T } } });
  });

  it("reads a 409's marks; anything else is not a conflict", () => {
    expect(setupConflict(conflict(2, EMPTY))).toEqual({ version: 2, current: EMPTY });
    expect(setupConflict(new ApiError(409, "x", "", "not json"))).toBeNull();
    expect(setupConflict(new Error("x"))).toBeNull();
  });
});
