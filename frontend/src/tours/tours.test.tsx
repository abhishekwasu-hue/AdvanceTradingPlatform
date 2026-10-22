import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import shellSource from "../shell/AppShellV2.tsx?raw";
import topBarSource from "../shell/TopBarV2.tsx?raw";
import pagesV2Source from "../shell/pagesV2.ts?raw";
import settingsSource from "../pages/SettingsPage.tsx?raw";
import introSource from "../ui/PageIntro.tsx?raw";
import { PAGE_TOURS, WELCOME, WELCOME_CTA } from "./content";
import TourEngine, { findTarget, placeCard } from "./TourEngine";
import hostSource from "./TourHost.tsx?raw";
import { TOUR_IDS, tourIdFor } from "./pages";
import {
  NO_MARKS, ToursStore, autoTour, cleanMarks, closeTour, markSeen, pageTourDue, showAllTipsAgain, skipAllTips, toursConflict, welcomeDue,
  type TourMarks,
} from "./state";
import { stepsFor, targetSelector, type TourLayout } from "./types";

/** MO2 part C: first-time guidance - the marks and when a tour opens (first visit, replay, reset, skip all), the store's
 * server sync, the content files (3-5 steps per layout, stable ids, plain words) and the engine's placement. The CI check
 * that every step's element exists on the real page, desktop and phone, is e2e/tours-v2.e2e.ts. */

const T = "2026-10-22T09:00:00Z";
const LAYOUTS: TourLayout[] = ["desktop", "phone"];

type Call = { path: string; init?: RequestInit };

function memoryStorage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); }, m };
}

const conflict = (version: number, current: TourMarks) =>
  new ApiError(409, "the preferences changed", "", JSON.stringify({ detail: { message: "the preferences changed since this edit was made", version, current } }));

/** A fake `/api/me/prefs` server with compare-and-set on the `tours` namespace. `tours: undefined` = an older server. */
function server(start: { marks?: TourMarks | undefined; version?: number; fail?: (c: Call) => Error | null } = {}) {
  const state = { marks: "marks" in start ? start.marks : NO_MARKS, version: start.version ?? 0 };
  const calls: Call[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    const call = { path, init };
    calls.push(call);
    const failure = start.fail?.(call);
    if (failure) throw failure;
    if (path === "/me/prefs") return { prefs: { chart: {}, ...(state.marks ? { tours: state.marks } : {}) }, versions: { tours: state.version } };
    const m = /^\/me\/prefs\/tours\?version=(\d+)$/.exec(path);
    if (m && init?.method === "PUT") {
      if (Number(m[1]) !== state.version) throw conflict(state.version, state.marks ?? NO_MARKS);
      state.marks = JSON.parse(String(init.body)) as TourMarks;
      state.version += 1;
      return { namespace: "tours", value: state.marks, version: state.version };
    }
    throw new ApiError(404, "not found");
  }) as unknown as ConstructorParameters<typeof ToursStore>[1];
  return { state, calls, fetcher };
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("MO2 tours: when a tour opens", () => {
  const fresh = (marks: Partial<TourMarks> = {}) => ({ marks: { ...NO_MARKS, ...marks }, fresh: true });

  it("first sign-in: the welcome first, then each page's tips on its first visit", () => {
    expect(autoTour(fresh(), "OWNER", "dashboard")).toEqual({ kind: "welcome" });
    expect(autoTour(fresh({ welcome_done_at: T }), "OWNER", "dashboard")).toEqual({ kind: "page", id: "dashboard" });
    expect(autoTour(fresh({ welcome_done_at: T, seen: { dashboard: T } }), "OWNER", "dashboard")).toBeNull();
    expect(autoTour(fresh({ welcome_done_at: T, seen: { dashboard: T } }), "OWNER", "signals")).toEqual({ kind: "page", id: "signals" });
    expect(autoTour(fresh({ welcome_done_at: T }), "OWNER", null)).toBeNull();                 // a page without tips
  });

  it("nothing opens on the cache or an unknown server (never on a guess)", () => {
    expect(autoTour({ marks: NO_MARKS, fresh: false }, "OWNER", "dashboard")).toBeNull();
    expect(pageTourDue({ marks: NO_MARKS, fresh: false }, "dashboard", "OWNER")).toBe(false);
  });

  it("skip all: neither the welcome nor any page's tips open by themselves", () => {
    for (const id of TOUR_IDS) expect(autoTour(fresh({ skip_all_at: T }), "OWNER", id)).toBeNull();
  });

  it("a VIEWER gets no welcome (no broker or paper bot to set up) but the page tips", () => {
    expect(welcomeDue(fresh(), "VIEWER")).toBe(false);
    expect(autoTour(fresh(), "VIEWER", "signals")).toEqual({ kind: "page", id: "signals" });
  });

  it("closing: a page is marked seen (replay too), the welcome done; its button opens the Broker page; skip all", () => {
    const page = closeTour({ kind: "page", id: "charts" }, "skip", T);
    expect(page.change(NO_MARKS).seen).toEqual({ charts: T }) ;
    expect(page.hold).toBe(false);
    const cta = closeTour({ kind: "welcome" }, "cta", T);
    expect(cta.go).toBe("/broker");
    expect(cta.change(NO_MARKS).welcome_done_at).toBe(T);
    const later = closeTour({ kind: "welcome" }, "done", T);
    expect(later.go).toBeNull();
    expect(later.hold).toBe(true);                                                         // not straight into the page's tips
    const all = closeTour({ kind: "page", id: "risk" }, "skip-all", T);
    expect(all.change(NO_MARKS)).toMatchObject({ skip_all_at: T, welcome_done_at: T });
    expect(WELCOME_CTA.to).toBe("/broker");
  });

  it("show all tips again clears every mark, so the welcome and each page's tips open once more", () => {
    const marks = { welcome_done_at: T, skip_all_at: T, seen: { dashboard: T, risk: T } };
    expect(showAllTipsAgain(marks)).toEqual({ welcome_done_at: null, skip_all_at: null, seen: {} });
    expect(autoTour({ marks: showAllTipsAgain(marks), fresh: true }, "OWNER", "risk")).toEqual({ kind: "welcome" });
  });

  it("a mark keeps the first time it was set", () => {
    expect(markSeen("signals", "2026-10-23T00:00:00Z")({ ...NO_MARKS, seen: { signals: T } }).seen.signals).toBe(T);
  });

  it("cleans what the server or the cache holds", () => {
    expect(cleanMarks(null)).toBeNull();
    expect(cleanMarks([])).toBeNull();
    expect(cleanMarks({ seen: { dashboard: T, admin: T, risk: "soon" }, skip_all_at: 3 })).toEqual({ welcome_done_at: null, skip_all_at: null, seen: { dashboard: T } });
    expect(toursConflict(conflict(4, { ...NO_MARKS, skip_all_at: T }))).toEqual({ version: 4, current: { ...NO_MARKS, skip_all_at: T } });
    expect(toursConflict(new ApiError(500, "x"))).toBeNull();
  });
});

describe("MO2 tours: the marks live on the server", () => {
  it("reads the server's marks; only then is it fresh; one read per user", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server({ marks: { ...NO_MARKS, welcome_done_at: T }, version: 3 });
    const store = new ToursStore(1, s.fetcher);
    expect(store.get()).toMatchObject({ fresh: false, known: false });
    await store.loadOnce();
    await store.loadOnce();
    expect(s.calls.map((c) => c.path)).toEqual(["/me/prefs"]);
    expect(store.get()).toMatchObject({ fresh: true, known: true, marks: { welcome_done_at: T } });
  });

  it("an older server without the namespace, or none at all: not fresh, so nothing opens", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const old = new ToursStore(2, server({ marks: undefined }).fetcher);
    await old.loadOnce();
    expect(old.get().fresh).toBe(false);
    const offline = new ToursStore(3, server({ fail: () => new ApiError(0, "offline") }).fetcher);
    await offline.loadOnce();
    expect(offline.get().fresh).toBe(false);
  });

  it("a seen page is saved as a compare-and-set on the version, and cached for the next paint", async () => {
    const storage = memoryStorage();
    vi.stubGlobal("localStorage", storage);
    const s = server({ version: 1 });
    const store = new ToursStore(4, s.fetcher);
    await store.loadOnce();
    await store.update(markSeen("dashboard", T));
    const put = s.calls[1];
    expect(put.path).toBe("/me/prefs/tours?version=1");
    expect(JSON.parse(String(put.init?.body))).toEqual({ ...NO_MARKS, seen: { dashboard: T } });
    expect(s.state.version).toBe(2);
    expect(JSON.parse(storage.m.get("atp_tours:4") ?? "{}").seen).toEqual({ dashboard: T });
    // The next sign-in on this device paints from the cache (the "?" shows) but still waits for the server to start one.
    const again = new ToursStore(4, s.fetcher);
    expect(again.get()).toMatchObject({ known: true, fresh: false, marks: { seen: { dashboard: T } } });
  });

  it("another device saved first (409): the change is applied again on its marks, nothing lost", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server({ version: 0 });
    const store = new ToursStore(5, s.fetcher);
    await store.loadOnce();
    s.state.marks = { ...NO_MARKS, seen: { risk: T } };                                  // the phone saved meanwhile
    s.state.version = 1;
    await store.update(markSeen("charts", T));
    expect(s.state.marks?.seen).toEqual({ risk: T, charts: T });
    expect(store.get().error).toBeNull();
  });

  it("skip all and show all again reach the server; a save before the first read reads first", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server({ marks: { ...NO_MARKS, seen: { signals: T } }, version: 2 });
    const store = new ToursStore(6, s.fetcher);
    await store.update(skipAllTips(T));
    expect(s.calls.map((c) => c.path)).toEqual(["/me/prefs", "/me/prefs/tours?version=2"]);
    expect(s.state.marks).toMatchObject({ skip_all_at: T, seen: { signals: T } });
    await store.update(showAllTipsAgain);
    expect(s.state.marks).toEqual({ welcome_done_at: null, skip_all_at: null, seen: {} });
  });

  it("a failed save says so in plain words and keeps the change on this device", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = server({ fail: (c) => (c.init?.method === "PUT" ? new ApiError(0, "offline") : null) });
    const store = new ToursStore(7, s.fetcher);
    await store.loadOnce();
    await store.update(markSeen("broker", T));
    expect(store.get().marks.seen.broker).toBe(T);
    expect(store.get().error).toMatch(/not saved/);
  });

  it("a replay request reaches the shell's tour host", () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const store = new ToursStore(8, server().fetcher);
    const got: unknown[] = [];
    const off = store.onRequest((r) => got.push(r));
    store.request({ kind: "page", id: "portfolio" });
    off();
    store.request({ kind: "page", id: "risk" });
    expect(got).toEqual([{ kind: "page", id: "portfolio" }]);
  });
});

describe("MO2 tours: one content file per page", () => {
  it("every tour page has its content, with 3 to 5 steps on the desktop and on the phone", () => {
    expect(Object.keys(PAGE_TOURS).sort()).toEqual([...TOUR_IDS].sort());
    for (const id of TOUR_IDS) {
      const tour = PAGE_TOURS[id];
      expect(tour.id).toBe(id);
      for (const layout of LAYOUTS) {
        const n = stepsFor(tour, layout).length;
        expect(n, `${id} on ${layout}`).toBeGreaterThanOrEqual(3);
        expect(n, `${id} on ${layout}`).toBeLessThanOrEqual(5);
      }
      expect(new Set(tour.steps.map((s) => s.id)).size).toBe(tour.steps.length);
    }
  });

  it("the welcome has four cards in the ordered order and ends on the Broker page", () => {
    expect(WELCOME.map((c) => c.title)).toEqual(["Welcome to AiAlgoDesk", "Connect your broker", "Your paper bot", "Get alerts on your phone"]);
  });

  it("targets are stable data-tour ids; the phone gets its own step where the layout differs", () => {
    for (const tour of Object.values(PAGE_TOURS)) {
      for (const s of tour.steps) if (s.target) expect(s.target).toMatch(/^[a-z][a-z0-9-]+$/);
    }
    expect(stepsFor(PAGE_TOURS.dashboard, "phone").map((s) => s.target)).toContain("nav-bottom");
    expect(stepsFor(PAGE_TOURS.dashboard, "desktop").map((s) => s.target)).toContain("nav-sidebar");
    expect(targetSelector("oc-chain")).toBe('[data-tour="oc-chain"]');
  });

  it("plain English, short, and no trading advice", () => {
    const texts = [...Object.values(PAGE_TOURS).flatMap((t) => t.steps.flatMap((s) => [s.title, s.body])), ...WELCOME.flatMap((c) => [c.title, c.body])];
    for (const text of texts) {
      expect(text).toMatch(/^[\x20-\x7e]+$/);                                                // English, no other script
      expect(text.length).toBeLessThanOrEqual(200);
      expect(text).not.toMatch(/\b(buy|sell|stop[- ]?loss|target price|guaranteed|sure profit)\b/i);
    }
  });

  it("the shell maps its pages to tours; the Copilot has no v2 page, so no tour", () => {
    expect(tourIdFor("risk-management")).toBe("risk");
    expect(tourIdFor("settings")).toBe("settings");
    expect(tourIdFor("copilot")).toBeNull();
    expect(tourIdFor(null)).toBeNull();
    expect(pagesV2Source).not.toMatch(/copilot:\s*lazy/);
  });
});

describe("MO2 tours: the engine", () => {
  it("places the card under the element, else above it, else docked; always inside the screen", () => {
    const view = { width: 1440, height: 900 };
    const card = { width: 360, height: 200 };
    expect(placeCard({ top: 100, left: 300, width: 200, height: 40 }, card, view, "desktop")).toMatchObject({ top: 158, left: 300 });
    expect(placeCard({ top: 800, left: 1400, width: 30, height: 40 }, card, view, "desktop")).toMatchObject({ top: 582, left: 1064 });
    expect(placeCard({ top: 0, left: 0, width: 1440, height: 900 }, card, view, "desktop")).toMatchObject({ top: 684 });
    expect(placeCard(null, card, view, "desktop")).toMatchObject({ top: 350, left: 540 });
    expect(placeCard({ top: 100, left: 300, width: 200, height: 40 }, card, { width: 390, height: 844 }, "phone")).toMatchObject({ left: 16, width: 358 });
  });

  it("an element counts only when it takes room on this layout", () => {
    const at = (w: number, h: number) => ({ querySelector: () => ({ getBoundingClientRect: () => ({ width: w, height: h }) }) }) as unknown as ParentNode;
    expect(findTarget({ id: "a", target: "x", title: "", body: "" }, at(10, 10))).not.toBeNull();
    expect(findTarget({ id: "a", target: "x", title: "", body: "" }, at(0, 0))).toBeNull();
    expect(findTarget({ id: "a", title: "", body: "" }, at(10, 10))).toBeNull();
  });

  it("the welcome card: its title, Next and Skip, and Skip all tips; never a modal", () => {
    const out = renderToStaticMarkup(<TourEngine tour={{ kind: "welcome" }} auto onClose={() => undefined} onUnavailable={() => undefined} />);
    expect(out).toContain("Welcome to AiAlgoDesk");
    expect(out).toContain(">Next<");
    expect(out).toContain(">Skip<");
    expect(out).toContain("Skip all tips");
    expect(out).toContain('aria-modal="false"');
    expect(out).not.toMatch(/text-\[\d+px\]/);
  });

  it("is never blocking and respects reduced motion (source checks)", async () => {
    const engine = (await import("./TourEngine.tsx?raw")).default;
    expect(engine).toContain('"Escape"');
    expect(engine).toContain("pointer-events-none");
    expect(engine).toContain("motionReduced()");
    expect(engine).toContain("duration-base");                                             // 0 ms under reduced motion
    expect(engine).not.toMatch(/FocusTrap|focus-trap|aria-modal="true"/);
  });
});

describe("MO2 tours: wiring", () => {
  it("the engine is its own lazy chunk; the shell mounts the host and the ? button", () => {
    expect(hostSource).toContain('lazy(() => import("./TourEngine"))');
    expect(shellSource).toContain("<TourHost");
    expect(shellSource).not.toMatch(/import TourEngine|from "\.\.\/tours\/TourEngine"/);
    expect(shellSource).toContain("onTour={replayTour}");
    expect(topBarSource).toContain('data-tour="tour-replay"');
  });

  it("Help has Show all tips again and Skip all tips; Skip all hides the What is this page? cards", () => {
    expect(settingsSource).toContain("<TipsButtonsV2");
    expect(introSource).toContain("tips.marks.skip_all_at");
  });
});
