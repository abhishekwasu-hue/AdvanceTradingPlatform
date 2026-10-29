import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { STATE_LABEL, headline, listRef, listValue, rowArea, rowText, type DayOne, type DayOneOffer, type DayOneRow, type DayOneWatch } from "./dayOne";
import dayOneSource from "./dayOne.ts?raw";
import { SetupView, WatchView, listGroups, listOptions } from "./DayOneV2";
import viewSource from "./DayOneV2.tsx?raw";
import { WITHHELD, callWords, check } from "./language";
import pageSource from "./SignalsV2.tsx?raw";

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const inRouter = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const primaries = (html: string) => html.match(/(?<![:\w-])bg-brand(?![\w-/])/g)?.length ?? 0;

const D3: DayOneOffer = {
  id: "D3", name: "Moving-average pullback in trend", idea: "In a trend, wait for the pullback to reach the fast average.", difficulty: "Intermediate",
  family: "D", timeframe: "Daily", rules: ["The pullback average must be shorter than the trend average."],
  settings: [{ key: "ma_type", label: "Average type", value: "Exponential", help: "How both averages are calculated." },
             { key: "fast", label: "Pullback average", value: "20", help: "Bars in the fast average." }],
};
const CARD_DATA: DayOne = {
  offers: [D3, { ...D3, id: "B1", name: "Range breakout", difficulty: "Beginner" }],
  lists: [{ kind: "system", key: "index-nifty50", name: "NIFTY 50 stocks (50)", group: "Index stocks", count: 50, default: true },
          { kind: "system", key: "index-nifty500", name: "NIFTY 500 stocks (500)", group: "Index stocks", count: 500, default: false },
          { kind: "own", id: 7, name: "My demo list", group: "My lists", count: 3, default: false }],
  default_list: { kind: "system", key: "index-nifty50" }, can_adopt: true, max_watch: 60, adopted: null, outdated: false,
};
const row = (over: Partial<DayOneRow>): DayOneRow => ({
  symbol: "DEMO1", exchange: "NSE", state: "watching", direction: null, text: "No setup in place on the Daily chart right now.", area: null,
  setup_day: null, bar_day: "2026-10-22", ...over,
});
const WATCH: DayOneWatch = {
  template: { id: "D3", name: D3.name, idea: D3.idea, timeframe: "Daily", settings: [...D3.settings] },
  strategy_id: "template:D3:1:1d:both:,,,,,,", list: { kind: "system", key: "index-nifty50", name: "NIFTY 50 stocks (50)" }, truncated: false,
  count: 4, max_watch: 60, mode: "PAPER", entries: "APPROVAL", adopted_at: "2026-10-23T05:00:00Z", evaluated_at: "2026-10-23T05:00:00Z",
  bar_day: "2026-10-22", note: null,
  rows: [row({ symbol: "DEMO2", state: "new", direction: "LONG", text: "The close broke out above the range on the Daily chart.", area: "Buyer area: 1,234.5–1,250" }),
         row({ symbol: "DEMO3", state: "armed", direction: "SHORT", text: "In a falling trend on the Daily chart the rally reached the fast moving average; waiting for a bearish reversal there.", area: "Seller area: 98–101" }),
         row({ symbol: "DEMO1" }), row({ symbol: "DEMO4", state: "no_data", text: "No Daily history for this symbol yet." })],
  summary: { new: 1, armed: 1, recent: 0, watching: 1, warmup: 0, no_data: 1, not_read: 0 },
};

describe("Signals day one: the first-visit card", () => {
  it("offers the ready strategies and the lists, NIFTY 50 picked, one primary action", () => {
    const html = inRouter(<SetupView data={CARD_DATA} onAdopt={noop} />);
    const words = text(html);
    expect(words).toContain("Pick a ready strategy and a list to watch");
    expect(words).toContain("Moving-average pullback in trend");
    expect(words).toContain("Range breakout");
    expect(html).toContain('data-combobox="system:index-nifty50"');                // NIFTY 50 picked
    const rows = listOptions(listGroups(CARD_DATA.lists));
    expect(rows.find((r) => r.value === "system:index-nifty50")?.label).toBe("NIFTY 50 stocks (50)");
    expect(rows.some((r) => r.group === "My lists")).toBe(true);
    expect(words).toContain("See its settings");
    expect(words).toContain("Paper money only");
    expect(primaries(html)).toBe(1);
  });

  it("shows every default setting before he confirms, PAPER and approval in words", () => {
    const html = inRouter(<SetupView data={CARD_DATA} onAdopt={noop} initialStep="confirm" />);
    const words = text(html);
    expect(words).toContain("Its settings (the strategy's own defaults)");
    for (const s of D3.settings) { expect(words).toContain(s.label); expect(words).toContain(s.value); }
    expect(words).toContain("The pullback average must be shorter than the trend average.");
    expect(words).toContain("Paper money only. Nothing is placed: each setup waits for you.");
    expect(words).toContain("Adopt on paper and read my list");
    expect(words).not.toMatch(/\bLIVE\b|live money/i);
    expect(primaries(html)).toBe(1);
  });

  it("says in plain words why he cannot pick yet - never sample data", () => {
    expect(text(inRouter(<SetupView data={{ ...CARD_DATA, offers: [] }} onAdopt={noop} />))).toContain("Ready strategies are not switched on for your account yet.");
    expect(text(inRouter(<SetupView data={{ ...CARD_DATA, lists: [], default_list: null }} onAdopt={noop} />))).toContain("The index and sector lists are not ready yet.");
    const viewer = text(inRouter(<SetupView data={{ ...CARD_DATA, can_adopt: false }} onAdopt={noop} />));
    expect(viewer).toContain("Ask your organisation's owner");
    expect(viewer).not.toContain("See its settings");
    expect(text(inRouter(<SetupView data={{ ...CARD_DATA, outdated: true }} onAdopt={noop} />))).toContain("has been updated. Pick it again.");
  });
});

describe("Signals day one: his watch", () => {
  it("leads with what stands out; the rest is folded; area as a range on his own symbols", () => {
    const html = inRouter(<WatchView watch={WATCH} onRefresh={noop} onChange={noop} onStop={noop} />);
    const words = text(html);
    expect(words).toContain("Your watch: Moving-average pullback in trend");
    expect(words).toContain("NIFTY 50 stocks (50) · Daily chart · Paper");
    expect(words).toContain("1 new setup, 1 waiting for its trigger on your list of 4.");
    expect(words).toContain("DEMO2");
    expect(words).toContain("Buyer area: 1,234.5–1,250");
    expect(words).toContain("Waiting for its trigger");
    expect(words).toContain("Show the other 2 symbols");
    expect(words).not.toContain("No Daily history for this symbol yet.");      // folded
    expect(html).toContain("bg-up/10");                                         // Long: green, with its word and arrow
    expect(words).toContain("Long");
    expect(words).toContain("Short");
    expect(html).toContain('href="/deployments?template=D3&amp;timeframe=1d"');
    // UX-11b C3: the card no longer repeats it; the Signals page says "Nothing is placed until you approve it" once.
    expect(words).not.toContain("Nothing is placed; each setup waits for you.");
  });

  it("with no history at all it says so and shows nothing made up", () => {
    const none = { ...WATCH, note: "No Daily history is stored for these symbols yet, so nothing can be read.", rows: [row({ state: "no_data", text: "No Daily history for this symbol yet." })],
                   summary: { ...WATCH.summary, new: 0, armed: 0, watching: 0, no_data: 1 }, count: 1 };
    const words = text(inRouter(<WatchView watch={none} onRefresh={noop} onChange={noop} onStop={noop} />));
    expect(words).toContain("No Daily history is stored for these symbols yet");
    expect(words).not.toContain("Show the other");
    expect(headline(none)).toBe("");
  });

  it("a viewer reads the watch without the actions", () => {
    const words = text(inRouter(<WatchView watch={WATCH} canAct={false} onRefresh={noop} onChange={noop} onStop={noop} />));
    expect(words).not.toContain("Refresh");
    expect(words).not.toContain("Stop watching");
  });
});

describe("Signals day one: the signal language", () => {
  it("every shown line of his own signals passes the checker; a line that does not is withheld", () => {
    for (const r of WATCH.rows) {
      expect(check(rowText(r), "signal_own", { ownSymbols: [r.symbol] })).toEqual([]);
      if (r.area) expect(rowArea(r)).toBe(r.area);
    }
    expect(rowText(row({ text: "Buy above 1,250 with a stop at 1,200" }))).toBe(WITHHELD);
    expect(rowArea(row({ area: "Entry 1,250" }))).toBeNull();
    for (const label of Object.values(STATE_LABEL)) expect(callWords(label)).toEqual([]);
    // every sentence the card itself writes (the quoted strings with a space) is free of call words
    for (const src of [viewSource, dayOneSource]) {
      const sentences = [...src.matchAll(/"([^"\n]*\s[^"\n]*)"/g)].map((m) => m[1]).filter((t) => !/[{}=<>]|\b(?:h-|px-|text-|bg-|rounded)/.test(t));
      expect(sentences.length).toBeGreaterThan(5);
      for (const t of sentences) expect(callWords(t), t).toEqual([]);
    }
  });

  it("the list value round-trips and refuses other shapes", () => {
    expect(listRef(listValue({ kind: "system", key: "index-nifty50" }))).toEqual({ kind: "system", key: "index-nifty50" });
    expect(listRef(listValue({ kind: "own", id: 7 }))).toEqual({ kind: "own", id: 7 });
    expect(listRef("own:abc")).toBeNull();
    expect(listRef("other:x")).toBeNull();
  });

  it("the Signals page shows the card behind strategy_templates", () => {
    expect(pageSource).toContain('useFeatures().on("strategy_templates")');
    expect(pageSource).toContain("<DayOneV2");
  });
});
