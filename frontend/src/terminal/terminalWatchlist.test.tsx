import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  CHOOSER_PANEL_IDS, COLUMNS, PANELS, PHONE_TABS, PRESET_IDS, chainHiddenColumns, layoutProblem, phoneTabs, presetLayout, withoutWatchlist, type PanelPlace,
} from "./layoutModel";
import { PhoneTabsV2 } from "./PhoneTabsV2";
import { markedItem, watchlistTarget } from "./TerminalWatchlistV2";
import pageSource from "./TerminalPageV2.tsx?raw";
import classicSource from "./TerminalClassicV2.tsx?raw";
import panelSource from "./TerminalWatchlistV2.tsx?raw";
import sharedSource from "../chart/WatchlistPanel.tsx?raw";
import chartsSource from "../chart/ChartsPageV2.tsx?raw";
import pageWatchSource from "../watchlist/WatchlistPageV2.tsx?raw";
import { RowName, SystemListView, SystemRowTools } from "../chart/WatchlistPanel";
import { MemoryRouter } from "react-router";
import { ConnectLiveButton } from "../ui/ConnectLive";
import { CONNECT_LIVE } from "../ui/lastClose";
import type { SystemList } from "../chart/systemLists";

/** UX-3 + TA-4 (UX2 plan PR 8): the watchlist as a Terminal panel, docked left in every preset. */
const open = (panels: readonly PanelPlace[]) => panels.filter((p) => p.open);
const at = (panels: readonly PanelPlace[], id: string) => panels.find((p) => p.id === id)!;

describe("TA-4: the Option seller layout", () => {
  it("Watchlist (left) · Chart · Chain on top; Positions & orders (tabs) below with the basket compact beside it", () => {
    const panels = presetLayout("option_seller").panels;
    const row = (y: number) => open(panels).filter((p) => p.y === y).sort((a, b) => a.x - b.x).map((p) => [p.id, p.w]);
    expect(row(0)).toEqual([["watchlist", 3], ["chart-1", 5], ["chain", 4]]);   // owner 13 Nov: the chart widest
    expect(row(12)).toEqual([["positions", 9], ["basket", 3]]);
    const chart = at(panels, "chart-1");
    expect(chart.w).toBeGreaterThanOrEqual(PANELS["chart-1"].minW + 1);             // TA-4 "Chart largest" vs T1-3: owner decision (PR 8)
    expect(at(panels, "watchlist").w).toBe(PANELS.watchlist.w);                     // 3: symbol, price and change readable
    expect(chart.w).toBeGreaterThan(at(panels, "chain").w);                         // owner 13 Nov: "make the default like this"
    expect(chainHiddenColumns(at(panels, "chain").w)).toEqual(["iv"]);             // at 4 columns IV goes first; OI change stays
    expect(at(panels, "order").open).toBe(false);                                   // TERM-2: Buy / Sell open the one ticket
    expect(open(panels).reduce((n, p) => n + p.w * p.h, 0)).toBe(COLUMNS * 20);     // the 20 rows are filled, no gap
  });

  it("the watchlist is a panel like the others: in the Panels menu, with a label and a minimum size", () => {
    expect(CHOOSER_PANEL_IDS[0]).toBe("watchlist");
    expect(PANELS.watchlist).toMatchObject({ label: "Watchlist", minW: 2 });
  });
});

describe("UX-3: while `watchlists` is off the panel is not shown and leaves no gap", () => {
  it("each preset: the watchlist reads closed, its neighbours take its columns, the layout stays valid", () => {
    for (const id of PRESET_IDS) {
      const base = presetLayout(id).panels;
      const off = withoutWatchlist(base);
      expect(at(off, "watchlist").open, id).toBe(false);
      expect(layoutProblem(off), id).toBeNull();
      const wl = at(base, "watchlist");
      for (const p of open(off)) {
        const before = at(base, p.id);
        const beside = before.x === wl.x + wl.w && before.y >= wl.y && before.y + before.h <= wl.y + wl.h;
        expect(p, `${id} ${p.id}`).toMatchObject(beside ? { x: 0, w: before.w + wl.w } : { x: before.x, w: before.w });
      }
    }
    expect(open(withoutWatchlist(presetLayout("option_seller").panels)).filter((p) => p.y === 0).map((p) => [p.id, p.x, p.w]))
      .toEqual([["chart-1", 0, 8], ["chain", 8, 4]]);
  });

  it("a layout with the watchlist closed (or without it) is left as it is", () => {
    const closed = presetLayout("option_seller").panels.map((p) => (p.id === "watchlist" ? { ...p, open: false } : p));
    expect(withoutWatchlist(closed)).toEqual(closed);
    const none: PanelPlace[] = [{ id: "chart-1", x: 0, y: 0, w: 12, h: 10, open: true }];
    expect(withoutWatchlist(none)).toEqual(none);
  });

  it("the page gates the panel, the Panels menu and the phone tab on the flag; the previous Terminal never shows it", () => {
    expect(pageSource).toMatch(/const watchOn = features\.on\("watchlists"\)/);
    expect(pageSource).toMatch(/watchOn \? working\.panels : withoutWatchlist\(working\.panels\)/);
    expect(pageSource).toMatch(/ids=\{watchOn \? CHOOSER_PANEL_IDS : CHOOSER_PANEL_IDS\.filter\(\(id\) => id !== "watchlist"\)\}/);
    expect(pageSource).toMatch(/<PhoneTabsV2 tab=\{tab\} onTab=\{setTab\} watchlists=\{watchOn\}/);
    expect(pageSource).toMatch(/<TerminalGridV2 panels=\{shownPanels\}/);
    expect(classicSource).toMatch(/withoutWatchlist\(working\.panels\)/);
    expect(classicSource).toMatch(/CHOOSER_PANEL_IDS\.filter\(\(id\) => id !== "watchlist"\)/);
  });
});

describe("UX-3: a row drives the Terminal's one active symbol", () => {
  it("an index or an F&O stock becomes the active symbol (the chart, chain, basket and order follow)", () => {
    expect(watchlistTarget({ symbol: "NIFTY 50", exchange: "NSE_INDEX" })).toEqual({ kind: "underlying", underlying: "NIFTY" });
    expect(watchlistTarget({ symbol: "NIFTY BANK", exchange: "NSE_INDEX" })).toEqual({ kind: "underlying", underlying: "BANKNIFTY" });
    expect(watchlistTarget({ symbol: "nifty fin service", exchange: "NSE" })).toEqual({ kind: "underlying", underlying: "FINNIFTY" });
    expect(watchlistTarget({ symbol: "SENSEX", exchange: "BSE" })).toEqual({ kind: "underlying", underlying: "SENSEX" });
    expect(watchlistTarget({ symbol: "RELIANCE", exchange: "NSE" })).toEqual({ kind: "underlying", underlying: "RELIANCE" });
  });

  it("anything without options (a share, a contract, an index without options) opens on Charts", () => {
    expect(watchlistTarget({ symbol: "IRCTC-XYZ", exchange: "NSE" })).toEqual({ kind: "chart", path: expect.stringContaining("IRCTC-XYZ") });
    expect(watchlistTarget({ symbol: "NIFTY IT", exchange: "NSE_INDEX" })).toMatchObject({ kind: "chart" });
    expect(watchlistTarget({ symbol: "NIFTY26NOV24500CE", exchange: "NFO" })).toMatchObject({ kind: "chart" });
  });

  it("the marked row: the one he opened while it is the active symbol, else the active symbol's spot", () => {
    const picked = { symbol: "NIFTY 50", exchange: "NSE_INDEX" };
    expect(markedItem(picked, "NIFTY", "NIFTY 50")).toBe(picked);
    expect(markedItem(picked, "BANKNIFTY", "NIFTY BANK")).toEqual({ symbol: "NIFTY BANK", exchange: "NSE" });
    expect(markedItem(null, "SENSEX", "SENSEX")).toEqual({ symbol: "SENSEX", exchange: "BSE" });
  });

  it("reuses the Charts watchlist panel and its list choice; changes the symbol the context bar's way", () => {
    expect(panelSource).toContain('import { WatchlistPanel } from "../chart/WatchlistPanel"');
    expect(panelSource).toMatch(/listId=\{view\.watchlist_id\}/);
    expect(panelSource).toMatch(/systemKey=\{view\.watchlist_system\}/);
    expect(panelSource).toMatch(/\{ \.\.\.readView\(userId\), \.\.\.patch \}/);
    expect(panelSource).not.toMatch(/watchlistsV2Api|systemListsApi/);           // no second copy of the list logic
    expect(pageSource).toMatch(/<ContextBarV2 symbol=\{underlying\} onSymbol=\{setUnderlying\}/);
    expect(pageSource).toMatch(/onUnderlying=\{\(v\) => \{ setUnderlying\(v\); if \(narrow\) setTab\("chart"\); \}\}/);
    expect(panelSource).not.toMatch(/text-\[\d+px\]|#[0-9a-f]{3,6}\b|rgb\(/i);
  });
});

describe("UX-3 on a phone: a Watchlist tab first, only with the flag", () => {
  it("the tabs", () => {
    expect(phoneTabs(false)).toBe(PHONE_TABS);
    expect(phoneTabs(true).map((t) => t.label)).toEqual(["Watchlist", "Chart", "Chain", "Basket", "Positions", "Orders", "Payoff", "Bots"]);
    const on = renderToStaticMarkup(<PhoneTabsV2 tab="chart" onTab={() => undefined} watchlists />);
    expect(on.match(/role="tab"/g)).toHaveLength(8);
    expect(on).toContain('id="terminal-tab-watchlist"');
    const off = renderToStaticMarkup(<PhoneTabsV2 tab="chart" onTab={() => undefined} />);
    expect(off.match(/role="tab"/g)).toHaveLength(7);
    expect(off).not.toContain("terminal-tab-watchlist");
  });
});

describe("UX-3: the narrow panel never cuts a name (OWNER_RULES D.3)", () => {
  it("a row's name is the whole symbol on its own line, no exchange line, wrapped never truncated", () => {
    const html = renderToStaticMarkup(<RowName item={{ symbol: "NIFTY FIN SERVICE", exchange: "NSE_INDEX" }} facts={undefined} q={undefined} narrow />);
    expect(html).toContain("NIFTY FIN SERVICE");
    expect(html).toContain("break-words");
    expect(html).not.toContain("truncate");
    expect(html).not.toContain("NSE_INDEX");
    const wide = renderToStaticMarkup(<RowName item={{ symbol: "NIFTY BANK", exchange: "NSE_INDEX" }} facts={undefined} q={undefined} />);
    expect(wide).toContain("NSE_INDEX");                                           // Charts / Watchlist page: as before
  });

  it("the Terminal asks for the narrow layout on the desktop grid only (the phone tab keeps the full-width rows)", () => {
    expect(panelSource).toMatch(/touch=\{touch\} narrow=\{!touch\}/);
    expect(sharedSource).toMatch(/narrow && "basis-full"/);                         // the list chooser takes the full width (not compact)
    expect(sharedSource).toMatch(/narrow \? "break-words" : "truncate"/);
    expect(chartsSource).not.toMatch(/narrow=/);                                    // Charts lays the panel out as before
    expect(pageWatchSource).not.toMatch(/narrow=/);
  });
});

describe("Owner 14 Nov: the Terminal's watchlist header is compact, every control kept", () => {
  const list: SystemList = { key: "index-niftyfmcg", group: "INDEX_STOCKS", group_label: "Index stocks", name: "NIFTY FMCG stocks (15)", count: 2, read_only: true,
    items: [{ symbol: "BRITANNIA", exchange: "NSE", name: "Made-up FMCG A" }, { symbol: "COLPAL", exchange: "NSE", name: "Made-up FMCG B" }] };
  const view = (compact: boolean) => renderToStaticMarkup(
    <SystemListView summary={{ ...list, refreshed_at: "2026-11-14T03:00:00Z" }} list={list} rows={list.items} total={2} matching={2} query="" onQuery={() => {}}
      sort="list" onSort={() => {}} quotes={new Map()} quotesFailed={false} current={() => false} onOpen={() => {}} onCopy={() => {}} copying={false}
      onRetry={() => {}} touch={false} field="" textBtn="" narrow compact={compact} />);

  it("the Terminal (desktop grid and phone tab) asks for the compact header; Charts and the Watchlist page do not", () => {
    expect(panelSource).toMatch(/narrow=\{!touch\} compact /);
    expect(chartsSource).not.toMatch(/\bcompact\b/);
    expect(pageWatchSource).not.toMatch(/\bcompact\b/);
  });

  it("compact: the list view leaves read-only, Copy, the search and the sort to the panel's header; the rows stay", () => {
    const compact = view(true);
    expect(compact).not.toContain("Read-only list");
    expect(compact).not.toContain("Copy to my list");
    expect(compact).not.toContain("watchlist-row-search");
    expect(compact).toContain('aria-label="Symbols in NIFTY FMCG stocks (15)"');
    expect(compact).toContain("BRITANNIA");
    const before = view(false);                                                        // Charts / Watchlist page: as before
    expect(before).toContain("Read-only list · updated 14 Nov 2026");
    expect(before).toContain("Copy to my list");
    expect(before).toContain('placeholder="Search symbol or company"');
  });

  it("the search and the sort: short and wrapping in the compact header, unchanged elsewhere", () => {
    const tools = (compact: boolean) => renderToStaticMarkup(
      <SystemRowTools name="NIFTY FMCG stocks (15)" query="" onQuery={() => {}} sort="list" onSort={() => {}} field="f" compact={compact} />);
    const c = tools(true);
    expect(c).toContain('placeholder="Search"');
    expect(c).toContain('title="Search symbol or company"');
    expect(c).toContain("flex-wrap");
    expect(c).toContain(">Search in NIFTY FMCG stocks (15)</label>");                 // still labelled
    expect(c).toContain(">Sort the list</label>");
    const wide = tools(false);
    expect(wide).toContain('placeholder="Search symbol or company"');
    expect(wide).toContain("w-32");
  });

  it("the connect chip: the full words, wrapping inside the chip, never cut; the usual button unchanged", () => {
    const chip = renderToStaticMarkup(<MemoryRouter><ConnectLiveButton compact /></MemoryRouter>);
    expect(chip).toContain(CONNECT_LIVE);
    expect(chip).toContain(`title="${CONNECT_LIVE}"`);
    expect(chip).toContain("break-words");
    expect(chip).not.toContain("truncate");
    expect(chip).toContain("min-h-11");                                                 // 44 px on a phone
    const button = renderToStaticMarkup(<MemoryRouter><ConnectLiveButton /></MemoryRouter>);
    expect(button).not.toContain("title=");
    expect(button).toContain("px-space-3 text-t13");
  });

  it("the header keeps every control: New / Rename / Delete beside the chooser, Copy, the connect chip, Screen as a button", () => {
    expect(sharedSource).toMatch(/\{sysShown && screenToggle\}/);                       // a read-only list: New and Screen beside the chooser
    expect(sharedSource).toMatch(/aria-label=\{copying \? "Copying to my list" : "Copy to my list"\}/);
    expect(sharedSource).toMatch(/eodShown && connectButton && <ConnectLiveButton compact \/>/);
    expect(sharedSource).toMatch(/data-screen-toggle aria-expanded=\{screenOpen\}/);
    expect(sharedSource).toMatch(/\(!compact \|\| screenOpen\)/);                        // the Screen row opens on demand
    expect(sharedSource).toMatch(/setScreenOpen\(choice\.screen_id != null\)/);        // by itself for a list with a screen chosen
  });
});
