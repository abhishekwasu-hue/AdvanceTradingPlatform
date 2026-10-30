import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PAGE_FLAGS, closedFlag } from "../shell/pageFlags";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { NAV_SECTIONS_V2, searchPages, visibleSections } from "../shell/navModel";
import { flagOffFallback, isGatedPath, shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import { DEMO_LIST, DEMO_OVERVIEW } from "./markets.fixture";
import { ListDetailView, MarketsBody, type Load, type View } from "./MarketsPageV2";
import pageSource from "./MarketsPageV2.tsx?raw";
import modelSource from "./model.ts?raw";
import {
  advanceShare, asOfText, breadthText, dishFacts, dishLines, fetchList, fetchOverview, heatClass, leadersAndLaggards, moveText, rankGroups, relativeText,
  toneOf, type MarketList, type MarketsOverview, type Period,
} from "./model";

/** MO4 slice 2: the Sector & index view - its rules, its plain states, the shell wiring and the flag. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
// The platform's advice words (backend app/portfolio/risk_text.py ADVICE_WORDS and app/screener/taxonomy.py SCREENER_WORDS).
const ADVICE = /\b(recommend\w*|advice|advise|should|must|buy|buying|sell|selling|reduce|cut your|add more|hedge now|guarantee\w*|best|safe bet|target\w*|top pick|bullish|bearish)\b/i;
const noop = () => undefined;
const ok = <T,>(data: T): Load<T> => ({ status: "ok", data });
const body = (state: Load<MarketsOverview>, view: View = "sectors", period: Period = "day") =>
  text(renderToStaticMarkup(<MemoryRouter><MarketsBody state={state} view={view} period={period} onRetry={noop} /></MemoryRouter>));
const bodyHtml = (view: View = "sectors", period: Period = "day") =>
  renderToStaticMarkup(<MemoryRouter><MarketsBody state={ok(DEMO_OVERVIEW)} view={view} period={period} onRetry={noop} /></MemoryRouter>);
const detail = (state: Load<MarketList>, period: Period = "day") =>
  renderToStaticMarkup(<MemoryRouter><ListDetailView state={state} view="sectors" period={period} onBack={noop} onRetry={noop} /></MemoryRouter>);

afterEach(() => { vi.restoreAllMocks(); });

describe("the reads", () => {
  it("reads GET /api/markets/overview and /api/markets/lists/<key> - reads only", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(DEMO_OVERVIEW), { status: 200 }));
    await fetchOverview();
    spy.mockResolvedValue(new Response(JSON.stringify(DEMO_LIST), { status: 200 }));
    await fetchList("sector-it");
    expect(String(spy.mock.calls[0][0])).toMatch(/\/markets\/overview$/);
    expect(String(spy.mock.calls[1][0])).toMatch(/\/markets\/lists\/sector-it$/);
    for (const [, init] of spy.mock.calls) expect(init?.method ?? "GET").toBe("GET");
  });
});

describe("the rules", () => {
  it("a missing move is 'I don't know', never 0", () => {
    expect(moveText(1.2)).toBe("+1.20%");
    expect(moveText(-0.5)).toBe("−0.50%");
    expect(moveText(null)).toBe("I don't know");
    expect(toneOf(null)).toBe("unknown");
    expect(toneOf(0.001)).toBe("flat");
    expect(relativeText(0.4, "NIFTY 50")).toBe("+0.40 pts vs NIFTY 50");
    expect(relativeText(null, "NIFTY 50")).toBeNull();
  });

  it("the heat-map tint grows with the move, scaled per period; flat and unknown are plain", () => {
    expect(heatClass(0.3, "day")).toBe("bg-up/10");
    expect(heatClass(1.6, "day")).toBe("bg-up/40");
    expect(heatClass(1.6, "week")).toBe("bg-up/20");
    expect(heatClass(-9, "month")).toBe("bg-down/40");
    expect(heatClass(null, "day")).toBe("bg-surface-2");
    expect(heatClass(0, "day")).toBe("bg-surface-2");
  });

  it("breadth in words and as a share", () => {
    const b = { advances: 8, declines: 3, unchanged: 1, counted: 12, missing: 2 };
    expect(breadthText(b)).toBe("8 rose · 3 fell · 1 unchanged");
    expect(advanceShare(b)).toBe(67);
    expect(breadthText({ ...b, advances: 0, declines: 0, unchanged: 0, counted: 0 })).toBeNull();
  });

  it("ranks by the move, unknown last, and never names one group as leader and laggard", () => {
    expect(rankGroups(DEMO_OVERVIEW.sectors, "day").map((s) => s.name)).toEqual(["Banking", "Pharma", "IT", "Realty"]);
    const { leaders, laggards } = leadersAndLaggards(DEMO_OVERVIEW.sectors, "day");
    expect(leaders.map((s) => s.name)).toEqual(["Banking"]);           // three with a move: one each way, never "leading" IT
    expect(laggards.map((s) => s.name)).toEqual(["IT"]);
    const many = Array.from({ length: 8 }, (_, i) => ({ name: `S${i}`, change: { day: 8 - i, week: null, month: null } }));
    const eight = leadersAndLaggards(many, "day");
    expect(eight.leaders.map((s) => s.name)).toEqual(["S0", "S1", "S2"]);
    expect(eight.laggards.map((s) => s.name)).toEqual(["S7", "S6", "S5"]);
    const two = leadersAndLaggards(DEMO_OVERVIEW.sectors, "day", 1);
    expect([two.leaders[0].name, two.laggards[0].name]).toEqual(["Banking", "IT"]);
  });

  it("the dish facts (UX-11b C4) match its words", () => {
    expect(dishFacts(DEMO_OVERVIEW, "day")).toEqual({ bench: { name: "NIFTY 50", change: 0.45 }, strongest: { name: "Banking", change: 0.9 },
      weakest: { name: "IT", change: -1.3 }, up: 2, known: 3 });
  });

  it("the dish: the market's move, the strongest and weakest sector, how many rose", () => {
    expect(dishLines(DEMO_OVERVIEW, "day")).toEqual([
      "NIFTY 50 rose on the day (+0.45%).",
      "Strongest sector: Banking (+0.90%). Weakest: IT (−1.30%).",
      "2 of 3 sectors rose on the day.",
    ]);
    expect(dishLines(DEMO_OVERVIEW, "month")[0]).toBe("NIFTY 50 fell over the month (−0.80%).");
    const unknown = { ...DEMO_OVERVIEW, benchmark: { name: "NIFTY 50", change: { day: null, week: null, month: null } } };
    expect(dishLines(unknown, "day")[0]).toBe("I don't know how NIFTY 50 moved on the day: its close is not stored.");
    expect(asOfText("2026-10-21", "day")).toBe("At the close of 21 Oct 2026");
    expect(asOfText("2026-10-21", "week")).toBe("Over the week to 21 Oct 2026");
  });
});

describe("the page", () => {
  it("opens ready: the dish, leaders and laggards, a heat map (desktop) and a list (phone) of every sector", () => {
    const html = bodyHtml();
    const words = text(html);
    expect(words).toContain("At the close of 21 Oct 2026");
    // UX-11b C4: the dish as numbers - leader / laggard chips, Signed moves, the sectors-up bar.
    expect(html).toMatch(/data-chip="leader"[^>]*>.*Banking/);
    expect(html).toMatch(/data-chip="laggard"[^>]*>.*IT/);
    expect(html).toContain('data-progress="Sectors that rose"');
    expect(words).toContain("2 of 3");
    expect(html).toMatch(/data-signed="up"[^>]*>.*\+0\.90%/);
    expect(html).toContain('data-block="leaders"');
    expect(html).toMatch(/data-heatmap[^>]*class="hidden[^"]*md:grid/);
    expect(html).toMatch(/data-list[^>]*class="[^"]*md:hidden/);
    for (const name of ["Banking", "IT", "Pharma", "Realty"]) {
      expect(html).toContain(`data-tile="${name}"`);
      expect(html).toContain(`data-row="${name}"`);
    }
    expect(words).toContain("28 rose · 11 fell · 1 unchanged");
    // UX-11b C4: a tile's strongest / weakest stock with Signed moves (the words stay in its title).
    expect(words).toContain("KAPPA ▲ +4.20% SIGMA ▼ −2.10%");
    expect(html).toContain('title="Strongest KAPPA +4.20% · Weakest SIGMA −2.10%"');
    // Realty has no stored move: it says so.
    expect(words).toMatch(/Realty I don't know/);
    // A tile opens the sector's stocks, keeping the view and the period.
    expect(html).toContain('href="/markets?view=sectors&amp;period=day&amp;list=sector-banking"');
    expect(words).toContain("for your own reading");                      // UI-28 U1.10: said affirmatively
  });

  it("indices are grouped by family; an index without a stocks list opens its chart", () => {
    const html = bodyHtml("indices", "week");
    const words = text(html);
    expect(html).toContain('data-family="BROAD"');
    expect(html).toContain('data-family="BSE"');
    expect(words).toContain("Over the week to 21 Oct 2026");
    expect(html).toContain('href="/markets?view=indices&amp;period=week&amp;list=index-nifty50"');
    expect(html).toContain('href="/charts/NIFTY%20IT"');
    expect(html).toContain('href="/charts/SENSEX?exchange=BSE"');
  });

  it("plain states: loading, could not load (with Try again), no lists, no data - never numbers", () => {
    expect(renderToStaticMarkup(<MemoryRouter><MarketsBody state={{ status: "loading" }} view="sectors" period="day" onRetry={noop} /></MemoryRouter>)).toContain('aria-busy="true"');
    const error = body({ status: "error" });
    expect(error).toContain("We could not load the market view.");
    expect(error).toContain("Try again");
    const noLists = body(ok({ ...DEMO_OVERVIEW, state: "no_lists", message: "The index and sector lists are not ready yet.", sectors: [], indices: [] }));
    expect(noLists).toContain("The index and sector lists are not ready yet.");
    expect(noLists).not.toMatch(/\d+\.\d+%/);
  });

  it("a sector's stocks: moves per period, strongest / weakest, each stock opens its chart, unknown says so", () => {
    const html = detail(ok(DEMO_LIST));
    const words = text(html);
    expect(words).toContain("All sectors");
    expect(words).toContain("3 stocks. The average move of the sector's stocks (each stock counts the same).");
    expect(html).toContain('data-member="THETA"');
    expect(html).toContain('href="/charts/THETA"');
    expect(words).toContain("−5.05 pts vs NIFTY 50");
    expect(words).toMatch(/NODATA No Data Ltd – I don't know/);
    expect(html).toContain('data-block="movers"');
    expect(text(detail({ status: "error" }))).toContain("We could not load these stocks.");
  });

  it("an index's stocks offer its chart", () => {
    const index: MarketList = { ...DEMO_LIST, kind: "index", name: "NIFTY BANK", symbol: "NIFTY BANK", list_key: "index-niftybank" };
    expect(detail(ok(index))).toContain('href="/charts/NIFTY%20BANK"');
  });

  it("no advice wording on the page or in its rules", () => {
    for (const html of [bodyHtml(), bodyHtml("indices", "month"), detail(ok(DEMO_LIST))]) expect(text(html)).not.toMatch(ADVICE);
    const strip = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    expect(strip(pageSource)).not.toMatch(ADVICE);
    expect(strip(modelSource)).not.toMatch(ADVICE);
  });
});

describe("the shell", () => {
  it("/markets is the v2 page behind `markets_view`, in the Find section (M60), found by search", () => {
    expect(shellKeyFromPath("/markets")).toBe("markets");
    expect(shellTitle("markets")).toBe("Sectors & indices");
    expect(PAGE_COMPONENTS_V2.markets).toBeDefined();
    expect(PAGE_FLAGS.markets).toBe("markets_view");
    expect(closedFlag("markets", { answered: true, on: () => false })).toBe("markets_view");
    expect(closedFlag("markets", { answered: true, on: (f) => f === "markets_view" })).toBeNull();
    const off = { on: () => false, role: "OWNER" };
    const on = { on: (f: string) => f === "markets_view", role: "OWNER" };
    const find = (ctx: { on: (f: string) => boolean; role: string }) => visibleSections(ctx).find((sec) => sec.id === "find")!.items.map((i) => i.key);
    expect(find(off)).not.toContain("markets");
    expect(find(on)).toEqual(["signals", "markets"]);
    expect(NAV_SECTIONS_V2.find((sec) => sec.id === "find")!.items.find((i) => i.key === "markets")?.path).toBe("/markets");
    expect(searchPages("sector", on)[0].key).toBe("markets");
    expect(searchPages("heat map", off).map((i) => i.key)).not.toContain("markets");
  });

  it("works without a broker (shared end-of-day data) and goes home while ui_v2 is off", () => {
    expect(isGatedPath("/markets")).toBe(false);
    expect(flagOffFallback("/markets")).toBe("/");
  });
});
