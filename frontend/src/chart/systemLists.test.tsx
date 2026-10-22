import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ListBrowser } from "./ListBrowser";
import { SystemListView } from "./WatchlistPanel";
import { cleanView } from "./chartsPage";
import {
  namesCount, MY_LISTS, browserGroups, copyNote, filterRows, groupOf, pickList, sortRows, type SystemGroup, type SystemItem, type SystemList,
} from "./systemLists";
import type { QuoteRow, WatchlistV2 } from "./watchlistsV2";

/** WL1 (handover item 4): the read-only system lists in the watchlist panel - groups, choice, search, sort, copy. */

const SYSTEM: SystemGroup[] = [
  { group: "INDICES", label: "Indices", lists: [{ key: "indices", group: "INDICES", group_label: "Indices", name: "Indices", count: 5, read_only: true }] },
  { group: "INDEX_STOCKS", label: "Index stocks", lists: [
    { key: "index-nifty50", group: "INDEX_STOCKS", group_label: "Index stocks", name: "NIFTY 50 stocks (50)", count: 50, read_only: true },
    { key: "index-niftybank", group: "INDEX_STOCKS", group_label: "Index stocks", name: "NIFTY BANK stocks (12)", count: 12, read_only: true },
  ] },
  { group: "SECTORS", label: "Sectors", lists: [
    { key: "sector-banking", group: "SECTORS", group_label: "Sectors", name: "Banking", count: 40, read_only: true },
    { key: "sector-it", group: "SECTORS", group_label: "Sectors", name: "IT", count: 60, read_only: true },
  ] },
];
const OWN: WatchlistV2[] = [{ id: 7, name: "Swing", items: [{ symbol: "TCS", exchange: "NSE" }], version: 1 }];
const ROWS: SystemItem[] = [
  { symbol: "HDFCBANK", exchange: "NSE", name: "HDFC Bank Limited" }, { symbol: "AXISBANK", exchange: "NSE", name: "Axis Bank Limited" },
  { symbol: "SBIN", exchange: "NSE", name: "State Bank of India" }, { symbol: "KOTAKBANK", exchange: "NSE", name: "Kotak Mahindra Bank" },
];
const quote = (symbol: string, ltp: number | null, pct: number | null, missing = false): [string, QuoteRow] =>
  [`NSE:${symbol}`, { symbol, exchange: "NSE", ltp, change: pct, change_pct: pct, missing }];

describe("WL1 list groups (pure)", () => {
  it("system groups come first, then My lists; a search keeps only matching lists and groups", () => {
    const all = browserGroups(SYSTEM, OWN);
    expect(all.map((g) => g.label)).toEqual(["Indices", "Index stocks", "Sectors", "My lists"]);
    expect(all[1].entries.map((e) => [e.name, e.readOnly])).toEqual([["NIFTY 50 stocks (50)", true], ["NIFTY BANK stocks (12)", true]]);
    expect(all[3].entries).toEqual([{ ref: { kind: "own", id: 7 }, name: "Swing", count: 1, readOnly: false }]);
    const bank = browserGroups(SYSTEM, OWN, " bank ");
    expect(bank.map((g) => [g.label, g.entries.map((e) => e.name)])).toEqual([["Index stocks", ["NIFTY BANK stocks (12)"]], ["Sectors", ["Banking"]]]);
    expect(browserGroups(SYSTEM, [], "zzz")).toEqual([]);
    expect(browserGroups([], [])).toEqual([{ id: MY_LISTS, label: "My lists", entries: [] }]);     // My lists is always there
  });

  it("the list shown: the remembered one if it still exists, else the trader's first own list, else Indices", () => {
    expect(pickList("sector-it", 7, OWN, SYSTEM)).toEqual({ kind: "system", key: "sector-it" });
    expect(pickList("gone", 7, OWN, SYSTEM)).toEqual({ kind: "own", id: 7 });
    expect(pickList(null, 99, OWN, SYSTEM)).toEqual({ kind: "own", id: 7 });               // a deleted list: the first own one
    expect(pickList(null, null, [], SYSTEM)).toEqual({ kind: "system", key: "indices" });    // first open: a ready list (UI4C-1)
    expect(pickList(null, null, [], SYSTEM.slice(1))).toEqual({ kind: "system", key: "index-nifty50" });
    expect(pickList(null, null, [], [])).toBeNull();
    expect(groupOf({ kind: "system", key: "sector-it" }, SYSTEM)).toBe("SECTORS");
    expect(groupOf({ kind: "own", id: 7 }, SYSTEM)).toBe(MY_LISTS);
  });

  it("search matches the symbol or the company, in the list's order", () => {
    expect(filterRows(ROWS, "bank").map((r) => r.symbol)).toEqual(["HDFCBANK", "AXISBANK", "SBIN", "KOTAKBANK"]);
    expect(filterRows(ROWS, "state").map((r) => r.symbol)).toEqual(["SBIN"]);
    expect(filterRows(ROWS, "  ").map((r) => r.symbol)).toEqual(ROWS.map((r) => r.symbol));
  });

  it("sort: A to Z, gainers, losers and price; a row without a price stays last and is never read as 0", () => {
    const quotes = new Map([quote("HDFCBANK", 1700, 1.2), quote("AXISBANK", 1100, -0.8), quote("SBIN", 820, 2.5), quote("KOTAKBANK", null, null, true)]);
    expect(sortRows(ROWS, "list", quotes).map((r) => r.symbol)).toEqual(["HDFCBANK", "AXISBANK", "SBIN", "KOTAKBANK"]);
    expect(sortRows(ROWS, "az", quotes).map((r) => r.symbol)).toEqual(["AXISBANK", "HDFCBANK", "KOTAKBANK", "SBIN"]);
    expect(sortRows(ROWS, "gainers", quotes).map((r) => r.symbol)).toEqual(["SBIN", "HDFCBANK", "AXISBANK", "KOTAKBANK"]);
    expect(sortRows(ROWS, "losers", quotes).map((r) => r.symbol)).toEqual(["AXISBANK", "HDFCBANK", "SBIN", "KOTAKBANK"]);
    expect(sortRows(ROWS, "price", quotes).map((r) => r.symbol)).toEqual(["HDFCBANK", "AXISBANK", "SBIN", "KOTAKBANK"]);
    expect(sortRows(ROWS, "gainers", new Map()).map((r) => r.symbol)).toEqual(ROWS.map((r) => r.symbol));
  });

  it("the copy note says what was copied, and when a long list was cut to the limit", () => {
    expect(copyNote({ name: "IT", truncated: false, source_count: 60, items: [] })).toBe('Copied to your list "IT". You can change it.');
    expect(copyNote({ name: "NIFTY TOTAL MARKET stocks", truncated: true, source_count: 750, items: Array(500).fill({ symbol: "X", exchange: "NSE" }) }))
      .toBe('Copied to your list "NIFTY TOTAL MARKET stocks": the first 500 of 750 symbols (a list holds 500). You can change it.');
  });

  it("the page remembers a system list by its key; anything else is dropped", () => {
    expect(cleanView({ watchlist_system: "index-nifty50" }).watchlist_system).toBe("index-nifty50");
    expect(cleanView({ watchlist_system: "../x" }).watchlist_system).toBeNull();
    expect(cleanView({ watchlist_system: 3 }).watchlist_system).toBeNull();
    expect(cleanView({}).watchlist_system).toBeNull();
  });
});

describe("WL1 list browser (collapsible groups)", () => {
  const render = (open: string[], query = "", current = { kind: "system", key: "index-nifty50" } as const) => renderToStaticMarkup(
    <ListBrowser groups={browserGroups(SYSTEM, OWN, query)} open={new Set(open)} onToggle={() => {}} current={current} onPick={() => {}}
      query={query} onQuery={() => {}} touch />);

  it("closed groups show only their header with the count; an open group lists its lists", () => {
    const html = render(["INDEX_STOCKS"]);
    expect(html.match(/aria-expanded="false"/g)).toHaveLength(3);                     // Indices, Sectors, My lists
    expect(html).toMatch(/aria-expanded="true"[^>]*>.*Index stocks/);
    expect(html).toContain("NIFTY 50 stocks (50)");
    expect(html).not.toContain("Banking");                                            // Sectors is closed
    expect(html).toMatch(/aria-current="true"[^>]*data-read-only="true"/);            // the list shown is marked
    expect(html.match(/<span class="sr-only">read-only<\/span>/g)).toHaveLength(2);   // each system list says read-only
    expect(html).toContain('placeholder="Find a list (e.g. bank, IT, NIFTY 50)"');
    expect(html).toContain("min-h-11");                                               // phone: 44 px targets
  });

  it("a search opens every group with a match; nothing found says so", () => {
    const html = render([], "bank");
    expect(html).not.toContain('aria-expanded="false"');
    expect(html).toContain("NIFTY BANK stocks (12)");
    expect(html).toContain("Banking");
    expect(render([], "zzz")).toContain("No list matches &quot;zzz&quot;.");
  });

  it("My lists entries carry no lock", () => {
    const html = render([MY_LISTS], "", { kind: "own", id: 7 } as never);
    expect(html).toMatch(/aria-current="true"(?![^>]*data-read-only)[^>]*>/);
    expect(html).toContain("Swing");
  });
});

describe("WL1 system list view (read-only, copy)", () => {
  const list: SystemList = { key: "sector-banking", group: "SECTORS", group_label: "Sectors", name: "Banking", count: 4, read_only: true, items: ROWS };
  const view = (over: Partial<Parameters<typeof SystemListView>[0]> = {}) => renderToStaticMarkup(
    <SystemListView summary={{ ...list, refreshed_at: "2026-10-21T12:30:00Z" }} list={list} rows={ROWS} total={4} matching={4} query="" onQuery={() => {}}
      sort="list" onSort={() => {}} quotes={new Map([quote("HDFCBANK", 1700, 1.2)])} quotesFailed={false} current={(i) => i.symbol === "SBIN"}
      onOpen={() => {}} onCopy={vi.fn()} copying={false} onRetry={() => {}} touch={false} field="" textBtn="" {...over} />);

  it("says read-only, offers Copy to my list, a search and a sort; rows have no move handle and no remove", () => {
    const html = view();
    expect(html).toContain("Read-only list · updated 21 Oct 2026");
    expect(html).toContain("Copy to my list");
    expect(html).toContain('placeholder="Search symbol or company"');
    expect(html).toContain(">Top gainers</option>");
    expect(html).not.toContain("data-handle");
    expect(html).not.toMatch(/aria-label="Remove /);
    expect(html).toContain("NSE · HDFC Bank Limited");
    expect(html).toMatch(/aria-current="true"[^>]*>.*SBIN/);
    expect(html).toContain('data-direction="up"');                                    // the priced row shows its change
  });

  it("loading, a failed load, a search with no match and a narrowed search are said in plain words", () => {
    expect(view({ list: undefined })).toContain("Loading the list…");
    expect(view({ list: "failed" })).toContain("This list could not be loaded. Check your connection, then try again.");
    expect(view({ rows: [], query: "zz", matching: 0 })).toContain("No symbol or company in this list matches &quot;zz&quot;.");
    expect(view({ rows: ROWS.slice(0, 1), matching: 1 })).toContain("1 of 4 symbols match.");
    expect(view({ copying: true })).toContain("Copying…");
  });
});

describe("WL1 list names that carry their count", () => {
  it("is recognised, so the chooser does not show the count twice", () => {
    expect(namesCount("NIFTY 50 stocks (50)")).toBe(true);
    expect(namesCount("F&O stocks (182) ")).toBe(true);
    expect(namesCount("Banking")).toBe(false);
    expect(namesCount("NIFTY (Made-up)")).toBe(false);
  });
});
