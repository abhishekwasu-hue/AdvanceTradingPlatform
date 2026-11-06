import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EMBED_HINT_KEY, EMBED_HINT_TEXT, EMBED_IDLE_MS, EmbedActivation, TAP_SLOP_PX, type EmbedState } from "./embedActivation";

/** P1-4 (2.3): an embedded chart is activated by a tap (like a map embed); idle and a tap outside end it. */

let store: Map<string, string>;
beforeEach(() => {
  store = new Map();
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } });
  vi.useFakeTimers();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function make(touch = true) {
  const seen: EmbedState[] = [];
  const m = new EmbedActivation((s) => seen.push(s), touch);
  const tap = (x = 50, y = 50, type = "touch", id = 1) => { m.pointerDown(x, y, type, id); m.pointerUp(x, y, id); };
  return { m, seen, tap };
}

describe("P1-4 2.3: embedded chart activation", () => {
  it("shows the hint the first time; the first tap hides it and it is remembered on this device", () => {
    const a = make();
    expect(a.m.current).toEqual({ active: false, hint: true });
    a.tap();
    expect(a.m.current).toEqual({ active: true, hint: false });
    expect(store.get(EMBED_HINT_KEY)).toBe("seen");
    expect(make().m.current.hint).toBe(false);                    // the next chart / visit: no hint
  });

  it("no hint on a device without touch, and a mouse click never activates", () => {
    const a = make(false);
    expect(a.m.current.hint).toBe(false);
    a.tap(50, 50, "mouse");
    expect(a.m.current.active).toBe(false);
  });

  it("a broken store shows the hint and never throws", () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    const a = make();
    expect(a.m.current.hint).toBe(true);
    expect(() => a.tap()).not.toThrow();
    expect(a.m.current.active).toBe(true);
  });

  it("a drag, a page scroll (cancelled) or a pinch is not a tap", () => {
    const a = make();
    a.m.pointerDown(50, 50, "touch", 1); a.m.pointerMove(50 + TAP_SLOP_PX + 1, 50, 1); a.m.pointerUp(50 + TAP_SLOP_PX + 1, 50, 1);
    expect(a.m.current.active).toBe(false);
    a.m.pointerDown(50, 50, "touch", 1); a.m.pointerUp(50, 52, 1, true);
    expect(a.m.current.active).toBe(false);
    a.m.pointerDown(50, 50, "touch", 1); a.m.pointerDown(80, 80, "touch", 2); a.m.pointerUp(50, 50, 1); a.m.pointerUp(80, 80, 2);
    expect(a.m.current.active).toBe(false);
    expect(a.m.current.hint).toBe(true);
  });

  it("deactivates after the idle time with no touch; a touch on it restarts the wait", () => {
    const a = make();
    a.tap();
    vi.advanceTimersByTime(EMBED_IDLE_MS - 100);
    expect(a.m.current.active).toBe(true);
    a.m.pointerDown(40, 40, "touch", 3);                           // a finger held on the chart: never idle under it
    vi.advanceTimersByTime(EMBED_IDLE_MS * 2);
    expect(a.m.current.active).toBe(true);
    a.m.pointerMove(140, 40, 3); a.m.pointerUp(140, 40, 3);        // a pan; the wait starts again on lift
    vi.advanceTimersByTime(EMBED_IDLE_MS - 1);
    expect(a.m.current.active).toBe(true);
    vi.advanceTimersByTime(1);
    expect(a.m.current.active).toBe(false);
  });

  it("a tap outside deactivates it at once; a later tap activates it again", () => {
    const a = make();
    a.tap();
    a.m.outside();
    expect(a.m.current.active).toBe(false);
    vi.advanceTimersByTime(EMBED_IDLE_MS);
    a.tap();
    expect(a.m.current.active).toBe(true);
    expect(a.seen.map((s) => s.active)).toEqual([true, false, true]);
  });

  it("EmbeddedChartV2 wires it: captureTouch follows the activation, the outline is a token ring, the hint text is English", () => {
    const src = readFileSync(new URL("./EmbeddedChartV2.tsx", import.meta.url), "utf8");
    expect(src).toContain("captureTouch={activation.active}");
    expect(src).toContain("ring-brand");
    expect(src).toContain("{...activation.handlers}");
    expect(EMBED_HINT_TEXT).toBe("Tap to move the chart");
  });
});
