import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import contract from "./builderTexts.json";
import { newStage, type Registry, type Stage } from "./model";
import { builderOf, clearStore, snapshot, syncDeviceHistory, VERSIONS_KEY, type VersionStore } from "./versions";

/** LAB-18 (AMW-7): the device history goes to the server once per scan on the first load with the feature, then the
 * device copy is cleared; what the server could not take yet stays. Made-up data. */
const reg = contract.registry as unknown as Registry;
const t0 = new Date("2026-10-20T08:35:00Z");
const rsi = (v: number): Stage[] => [newStage(reg, "RSI", { kind: "value", value: v }, ">")];
const store = (): VersionStore => ({
  7: [snapshot(reg, "A", "1d", rsi(50), t0), snapshot(reg, "A", "1d", rsi(60), new Date(t0.getTime() + 60_000))],
  12: [snapshot(reg, "B", "1h", rsi(40), t0)],
  3: [],
});

describe("the first sync of the device history (LAB-18)", () => {
  it("uploads each scan once, oldest id first, and leaves nothing on the device", async () => {
    const calls: [number, unknown[]][] = [];
    const api = { importVersions: async (id: number, list: unknown[]) => { calls.push([id, list]); return { imported: list.length, skipped: 0, already: false }; } };
    const res = await syncDeviceHistory(store(), api);
    expect(calls.map(([id]) => id)).toEqual([7, 12]);                  // the empty scan 3 is dropped without a call
    expect((calls[0][1] as { name: string; texts: string[] }[]).map((v) => v.texts.length)).toEqual([1, 1]);
    expect(res).toEqual({ left: {}, uploaded: 3, stopped: false });
  });

  it("a repeat (the server already has it) still clears the device copy", async () => {
    const api = { importVersions: async () => ({ imported: 0, skipped: 0, already: true }) };
    expect((await syncDeviceHistory(store(), api)).left).toEqual({});
  });

  it("stops and keeps everything left while the server feature is off (503)", async () => {
    let n = 0;
    const api = { importVersions: async () => { n += 1; throw new ApiError(503, "off"); } };
    const res = await syncDeviceHistory(store(), api);
    expect(n).toBe(1);
    expect(res.stopped).toBe(true);
    expect(Object.keys(res.left).sort()).toEqual(["12", "7"]);
  });

  it("drops a scan the server says is not this account's; keeps one for a retry after a network or server error", async () => {
    const api = { importVersions: async (id: number) => {
      if (id === 7) throw new ApiError(404, "Screen not found");
      throw new TypeError("Failed to fetch");
    } };
    const res = await syncDeviceHistory(store(), api);
    expect(Object.keys(res.left)).toEqual(["12"]);
    const forbidden = await syncDeviceHistory({ 12: store()[12] }, { importVersions: async () => { throw new ApiError(403, "no"); } });
    expect(Object.keys(forbidden.left)).toEqual(["12"]);           // a viewer: kept, harmless
  });

  it("sends at most the device cap per scan and never a junk id", async () => {
    const many = Array.from({ length: 25 }, (_, i) => snapshot(reg, "C", "1d", rsi(i), new Date(t0.getTime() + i * 1000)));
    const sizes: number[] = [];
    const api = { importVersions: async (_id: number, list: unknown[]) => { sizes.push(list.length); return { imported: list.length, skipped: 0, already: false }; } };
    const res = await syncDeviceHistory({ 5: many, abc: many, "-1": many }, api);
    expect(sizes).toEqual([20]);
    expect(res.left).toEqual({});
  });

  it("the builder blocks a save sends, and clearing the device copy never throws", () => {
    const v = snapshot(reg, "A", "1d", rsi(50), t0);
    expect(builderOf(v)).toEqual({ stages: v.stages, texts: v.texts, summaries: v.summaries });
    let removed = "";
    clearStore({ removeItem: (k) => { removed = k; } });
    expect(removed).toBe(VERSIONS_KEY);
    expect(() => clearStore({ removeItem: () => { throw new Error("blocked"); } })).not.toThrow();
    expect(() => clearStore(null)).not.toThrow();
  });
});
