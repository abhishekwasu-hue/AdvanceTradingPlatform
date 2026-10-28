import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import chartsSource from "../chart/ChartsPageV2.tsx?raw";
import journalSource from "../pages/JournalPage.tsx?raw";
import screenerSource from "../pages/ScreenerPage.tsx?raw";
import signalsSource from "../signals/SignalsV2.tsx?raw";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import { growthApi } from "./api";
import { DEMO_DISHES, EMPTY_SIGNALS } from "./dish.fixture";
import { DISH_REFRESH_MS, dishShown, readDishOpen, writeDishOpen } from "./model";
import PageDishV2, { PageDishView, dishState } from "./PageDishV2";
import dishSource from "./PageDishV2.tsx?raw";
import type { PageDish } from "./types";

/** MO4 slice 4: the page dishes on Signals, My positions, the Journal, Charts and Find setups. */

const html = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const text = (out: string) => out.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, "\"").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("the read", () => {
  it("GET /api/growth/dish/<page>, the chart's symbol and an exchange off NSE only", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await growthApi.dish("signals");
    await growthApi.dish("charts", "NIFTY 50", "NSE");
    await growthApi.dish("charts", "GOLD", "mcx");
    expect(String(spy.mock.calls[0][0])).toMatch(/\/growth\/dish\/signals$/);
    expect(String(spy.mock.calls[1][0])).toMatch(/\/growth\/dish\/charts\?symbol=NIFTY\+50$/);
    expect(String(spy.mock.calls[2][0])).toMatch(/\/growth\/dish\/charts\?symbol=GOLD&exchange=MCX$/);
    for (const call of spy.mock.calls) expect((call[1] as RequestInit | undefined)?.method ?? "GET").toBe("GET");
  });
});

describe("the dish", () => {
  it("each page: the title, the headline standing out, every section with its lines and the note (an older server)", () => {
    for (const typed of Object.values(DEMO_DISHES)) {
      const dish = { ...typed, sections: typed.sections.map(({ kind: _k, rows: _r, ...rest }) => rest) };
      const out = html(<PageDishView dish={dish} />);
      const shown = text(out);
      expect(out).toContain(`data-dish="${dish.page}"`);
      expect(shown).toContain(dish.title);
      if (dish.headline) expect(out).toContain("data-dish-headline");
      for (const s of dishShown(dish).sections) {
        expect(out).toContain(`data-rhythm-section="${s.id}"`);
        for (const line of s.lines) expect(shown).toContain(line);
      }
      expect(shown).toContain("it does not tell you what to trade");
    }
  });

  it("UX-11b C3: each typed section renders by its kind - tables, Signed numbers, chips, no sentences", () => {
    const want: Record<string, string[]> = {
      signals: ['data-rows="waiting"', 'data-chip="waiting"', 'data-rows="sources"', 'data-rows="record"', "58%"],
      positions: ['data-rows="risk"', 'data-meaning-bar="inside"', 'data-chip="near"', 'data-rows="premium"', 'data-chip="expiry"'],
      journal: ["data-discipline", 'data-rows="extremes"', 'data-chip="suggested-tags"'],
      charts: ['data-chip="trend-up"', 'data-chip="trend-sideways"', 'data-chip="event-bos-up"', 'data-chip="list"'],
      screener: ['data-rows="matches"', 'data-chip="active-scans"'],
    };
    for (const dish of Object.values(DEMO_DISHES)) {
      const out = html(<PageDishView dish={dish} />);
      for (const w of want[dish.page]) expect(out, `${dish.page}: ${w}`).toContain(w);
      expect(out).toContain(`data-details="dish.${dish.page}.note"`);
    }
    expect(text(html(<PageDishView dish={DEMO_DISHES.positions} />))).toMatch(/If each reached its invalidation ▼ −₹2,150/);
  });

  it("the headline is not repeated below; a section that only held it goes", () => {
    const shown = dishShown(DEMO_DISHES.signals);
    expect(shown.sections.find((s) => s.id === "waiting")!.lines).not.toContain(DEMO_DISHES.signals.headline);
    const only: PageDish = { ...DEMO_DISHES.screener, headline: "x matched.", sections: [{ id: "a", title: "A", lines: ["x matched."], empty: null, link: null }] };
    expect(dishShown(only).sections).toEqual([]);
    const out = html(<PageDishView dish={only} />);
    expect(out).not.toContain("data-dish-toggle");             // nothing to unfold
  });

  it("a line with a call word is left out, never rewritten; so is such a headline", () => {
    const bad: PageDish = { ...DEMO_DISHES.signals, headline: "Buy now", sections: [
      { id: "x", title: "X", lines: ["DEMOA: target reached.", "DEMOB: a buyer-side setup from your strategy."], empty: null, link: null }] };
    const shown = dishShown(bad);
    expect(shown.headline).toBeNull();
    expect(shown.sections[0].lines).toEqual(["DEMOB: a buyer-side setup from your strategy."]);
  });

  it("nothing yet: each section says so in plain words - never a sample figure", () => {
    const shown = text(html(<PageDishView dish={EMPTY_SIGNALS} />));
    for (const s of EMPTY_SIGNALS.sections) expect(shown).toContain(s.empty!);
  });

  it("the fixtures speak in structure words: no call word anywhere, no number in a setup line", () => {
    for (const dish of Object.values(DEMO_DISHES)) {
      for (const s of dish.sections) for (const line of s.lines) expect(hasCallWords(line), line).toBe(false);
      expect(hasCallWords(dish.title)).toBe(false);
    }
    const setups = DEMO_DISHES.signals.sections.find((s) => s.id === "waiting")!.lines.slice(1);
    for (const line of setups) expect(line.replace(/^[A-Z0-9&-]+: /, "")).not.toMatch(/\d/);
  });

  it("folded: the title and the headline only, with the button to unfold", () => {
    const out = html(<PageDishView dish={DEMO_DISHES.charts} folded />);
    expect(text(out)).toContain(DEMO_DISHES.charts.headline!);
    expect(out).not.toContain("data-rhythm-section");
    expect(out).toMatch(/aria-expanded="false"[^>]*data-dish-toggle/);
    expect(html(<PageDishView dish={DEMO_DISHES.charts} />)).toMatch(/aria-expanded="true"/);
  });

  it("his fold is remembered on this device only, and storage that is not there is no problem", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } });
    expect(readDishOpen("charts", false)).toBe(false);
    writeDishOpen("charts", true);
    expect(readDishOpen("charts", false)).toBe(true);
    writeDishOpen("signals", false);
    expect(readDishOpen("signals", true)).toBe(false);
    expect(readDishOpen("journal", true)).toBe(true);
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    expect(readDishOpen("charts", true)).toBe(true);
    expect(() => writeDishOpen("charts", false)).not.toThrow();
    vi.unstubAllGlobals();
  });

  it("404 (a flag off), 422 (no symbol) or the journal off: nothing shows; a failed read keeps what was shown", () => {
    expect(dishState({ error: new ApiError(404, "Not found") })).toEqual({ status: "off" });
    expect(dishState({ error: new ApiError(422, "Pick a symbol") })).toEqual({ status: "off" });
    expect(dishState({ error: new ApiError(500, "Oops") })).toEqual({ status: "error" });
    const ok = dishState({ data: DEMO_DISHES.signals });
    expect(ok).toEqual({ status: "ok", data: DEMO_DISHES.signals });
    expect(dishState({ error: new Error("offline") }, ok)).toBe(ok);
    expect(dishState({ data: { ...DEMO_DISHES.journal, available: false, sections: [] } })).toEqual({ status: "off" });
    expect(html(<PageDishV2 page="signals" load={async () => DEMO_DISHES.signals} />)).toContain('aria-label="Loading your summary"');
  });

  it("refreshes only the pages that change during the session", () => {
    expect(Object.keys(DISH_REFRESH_MS).sort()).toEqual(["positions", "screener", "signals"]);
  });
});

describe("where it is mounted", () => {
  it("each page mounts its dish only with growth_v2 (the v2 pages need ui_v2 already)", () => {
    expect(signalsSource).toMatch(/dishOn && <PageDishV2 page="signals" \/>/);
    expect(signalsSource).toContain('useFeatures().on("growth_v2")');
    expect(positionsSource).toMatch(/features\.on\("growth_v2"\) && <PageDishV2 page="positions" \/>/);
    expect(journalSource).toMatch(/shareCard && <PageDishV2 page="journal"/);
    expect(chartsSource).toMatch(/features\.on\("growth_v2"\) && !fs\.on && !hubView && tab === "chart"/);
    expect(chartsSource).toMatch(/<PageDishV2 page="charts" symbol=\{symbol\} exchange=\{exchange\} folded \/>/);
    expect(screenerSource).toContain('const dishOn = features.on("growth_v2") && features.on("ui_v2");');
  });

  it("the component places nothing and posts nothing", () => {
    expect(dishSource).not.toMatch(/method:\s*"POST"|approve|placeOrder|\/paper\//);
  });
});
