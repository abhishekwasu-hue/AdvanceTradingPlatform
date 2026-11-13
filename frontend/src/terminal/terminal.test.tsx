import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { orderGroup, orderRowWords, statusWords, terminalApi, type TerminalStatus } from "./api";
import {
  CHOOSER_PANEL_IDS, COLUMNS, DEFAULT_PRESET, EMPTY_PREFS, MAX_LAYOUTS, HIDDEN_PANELS, PANELS, PANEL_IDS, PHONE_TABS, PRESETS, PRESET_IDS, activeKey, chainHiddenColumns, changed, closePanel,
  isPreset,
  firstFree, forSave, layoutFor, layoutProblem, liftedY, movePanel, nameProblem, openPanel, presetLayout, readPrefs, readingOrder, remove, rename,
  resetToPreset, resizePanel, saveAs, saveOver, switchTo, type Layout, type TerminalPrefs,
} from "./layoutModel";
import { BasketPanelBodyV2, BotsViewV2, ChainPanelV2, OrdersPanelV2, PAYOFF_NEEDS_SPOT, PayoffViewV2, chainProblem, exitAllConfirmText, exitAllConsequence, exitAllPlan, legFigures } from "./PanelsV2";
import { SPOT_AS_OF_AFTER_MS, TICK_FRESH_MS, chartLiveOf, freshTick, pickSpot, spotAsOf } from "./spot";
import { SpreadBuilderV2, placementOrder, unhedgedSells } from "./SpreadBuilderV2";
import embeddedSource from "../chart/EmbeddedChartV2.tsx?raw";
import { TicketBody } from "../fno/OrderTicketV2";
import { PRODUCT_MISSING, PRODUCT_UNSET_TEXT, draftProblems, newDraft } from "../fno/ticketModel";
import { completeLabel, exitFilledLabel } from "../fno/incompleteApi";
import type { ChainTrading } from "../fno/ChainTradingV2";
import type { BasketLeg } from "../fno/basket";
import type { PositionGroup } from "../fno/contracts";
import type { ChainLeg, LiveChain } from "../optionChain/types";
import { NO_STOP, stopText, type HeldRow } from "../trading/model";
import { ContextBarV2, contextFigures } from "./ContextBarV2";
import { terminalLevels } from "./chartLevels";
import { AccountPanelV2 } from "./AccountPanelV2";
import exitAllSource from "../trading/ExitAllV2.tsx?raw";
import { ExitAllV2, exitableRows } from "../trading/ExitAllV2";
import listSource from "../trading/PositionsV2.tsx?raw";
import basketSource from "../fno/BasketPanelV2.tsx?raw";
import type { Deployment, TradeRecord } from "../types";
import type { SignalFeedItem } from "../signals/feed";
import type { PosLeg } from "../fno/positionInsights";
import { heldSide, payoffScope } from "./payoffModel";
import type { OptionChainData } from "../optionChain/useOptionChain";
import { summaryCards } from "../optionChain/SummaryStripV2";
import { foldLive } from "../chart/timeframes";
import { strategyDisplayName } from "../ui/strategyName";
import { formatPrice, formatTimeIST } from "../ui/format";
import { PhoneTabsV2 } from "./PhoneTabsV2";
import { StatusBarV2 } from "./StatusBarV2";
import { MASK, MASK_KEY, feedChip, marginNote, money, readMask, writeMask } from "./statusModel";
import pageSource from "./TerminalPageV2.tsx?raw";
import panelsSource from "./PanelsV2.tsx?raw";
import gridSource from "./TerminalGridV2.tsx?raw";
import { NAV_SECTIONS_V2, visibleSections, visiblePinned } from "../shell/navModel";
import { closedFlag } from "../shell/pageFlags";
import { flagOffFallback } from "../shell/aliases";
import { shellKeyFromPath } from "../shell/shellRoutes";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";

/**
 * Trading Terminal T1: the layout model (presets, move / resize / open / close on the 12-column grid, save / save as /
 * rename / delete / switch / reset, the 10-layout limit), the status bar (figures, the privacy mask, the phone's short
 * bar), the phone tabs, the panels' plain words, and the navigation behind the `terminal` flag. No DOM: views are
 * rendered to markup.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const DEV = /\b(undefined|null|NaN|error:|exception|id=|T[1-9]\b|PAPER_|ui_v2|terminal_)\b/;
const T = new Date("2026-10-24T10:00:00Z");

describe("owner 13 Nov: the Option buyer ready layout", () => {
  it("chart, the chain at 5 columns (IV and OI change on screen), positions & orders and a wider basket; Reset and Save as mine keep it", () => {
    const l = presetLayout("option_buyer");
    expect(l).toMatchObject({ name: "Option buyer", preset: "option_buyer", underlying: null });
    const at = (id: string) => l.panels.find((p) => p.id === id);
    expect(at("chain")).toMatchObject({ x: 7, y: 0, w: 5, open: true });
    expect(chainHiddenColumns(at("chain")!.w)).toEqual([]);
    expect(at("chart-1")).toMatchObject({ open: true });
    expect(at("positions")).toMatchObject({ x: 0, w: 8, open: true });
    expect(at("basket")).toMatchObject({ x: 8, w: 4, open: true });
    expect(at("basket")!.w).toBeGreaterThan(PRESETS.option_seller.panels.find((p) => p.id === "basket")!.w);
    expect(isPreset("option_buyer")).toBe(true);
    const moved = { ...l, underlying: "BANKNIFTY", panels: l.panels.map((p) => (p.id === "basket" ? { ...p, open: false } : p)) };
    expect(resetToPreset(moved)).toMatchObject({ preset: "option_buyer", underlying: "BANKNIFTY", panels: presetLayout("option_buyer").panels });
    const mine = saveAs(EMPTY_PREFS, moved, "My buyer desk", "buy00001", T);
    if ("error" in mine) throw new Error(mine.error);
    expect(mine.prefs.layouts.buy00001).toMatchObject({ name: "My buyer desk", preset: "option_buyer" });
    expect(readPrefs(mine.prefs).layouts.buy00001.preset).toBe("option_buyer");
    expect(switchTo(EMPTY_PREFS, "option_buyer").active).toBe("option_buyer");
  });
});

describe("layout model: presets", () => {
  it("four presets, each valid on the 12-column grid; Option seller is the default (T-D1)", () => {
    expect(PRESET_IDS).toEqual(["option_seller", "option_buyer", "quick_trade", "multi_chart"]);
    expect(DEFAULT_PRESET).toBe("option_seller");
    expect(activeKey(EMPTY_PREFS)).toBe("option_seller");
    for (const id of PRESET_IDS) {
      const l = presetLayout(id);
      expect(layoutProblem(l.panels), id).toBeNull();
      for (const p of l.panels) expect(p.x + p.w).toBeLessThanOrEqual(COLUMNS);
    }
    const open = (id: (typeof PRESET_IDS)[number]) => presetLayout(id).panels.filter((p) => p.open).map((p) => p.id);
    // TERM-2 step 3: chart | chain | basket, and chain | order - each above the one bottom panel (positions & orders).
    // UX-3 (PR 8): the watchlist docked left and open in every preset.
    expect(open("option_seller")).toEqual(["watchlist", "chart-1", "chain", "positions", "basket"]);
    expect(open("option_buyer")).toEqual(["watchlist", "chart-1", "chain", "positions", "basket"]);
    expect(open("quick_trade")).toEqual(["watchlist", "chain", "order", "positions"]);
    expect(open("multi_chart")).toEqual(["watchlist", "chart-1", "chart-2", "chart-3", "chart-4", "positions"]);
    for (const id of PRESET_IDS) expect(presetLayout(id).panels.find((p) => p.id === "watchlist"), id).toMatchObject({ x: 0, y: 0, open: true });
  });

  it("a preset is a fresh copy every time (editing one never changes the preset)", () => {
    const a = presetLayout("quick_trade");
    a.panels[0].x = 4;
    expect(presetLayout("quick_trade").panels[0].x).toBe(0);
    expect(PRESETS.quick_trade.panels[0].x).toBe(0);
  });
});

describe("layout model: move, resize, open, close", () => {
  const seller = () => presetLayout("option_seller").panels;

  it("a move that covers another open panel is refused; a free move is kept inside the grid", () => {
    expect(movePanel(seller(), "chain", 1, 0)).toBeNull();                       // onto the chart
    const moved = movePanel(seller(), "positions", 0, 20);
    expect(moved?.find((p) => p.id === "positions")).toMatchObject({ x: 0, y: 20 });
    const clamped = movePanel(seller(), "positions", -3, 30);
    expect(clamped?.find((p) => p.id === "positions")).toMatchObject({ x: 0, y: 30 });
  });

  it("a resize keeps the panel's minimum and refuses an overlap", () => {
    expect(resizePanel(seller(), "chart-1", 6, 12)).toBeNull();                    // into the chain
    const small = resizePanel(seller(), "chart-1", 1, 1);
    expect(small?.find((p) => p.id === "chart-1")).toMatchObject({ w: PANELS["chart-1"].minW, h: PANELS["chart-1"].minH });
  });

  it("M63 review: a panel opened from the menu sits directly under the lowest open panel, in its own columns - no empty band", () => {
    const base = seller();
    const bottom = Math.max(...base.filter((p) => p.open).map((p) => p.y + p.h));          // Positions & orders ends here
    const both = openPanel(openPanel(base, "bots"), "payoff");
    expect(layoutProblem(both)).toBeNull();
    expect(both.find((p) => p.id === "bots")).toMatchObject({ open: true, x: 0, y: bottom, w: 6 });
    expect(both.find((p) => p.id === "payoff")).toMatchObject({ open: true, x: 6, y: bottom, w: 6 });   // side by side
    for (const p of base.filter((q) => q.open)) expect(both.find((q) => q.id === p.id)).toEqual(p);   // the open ones never move
    // A stored place that overlaps an open panel: directly under the lowest one, full width when its columns do not fit.
    const blocked = base.map((p) => (p.id === "bots" ? { ...p, x: 0, y: 4, w: 12 } : p));
    expect(openPanel(blocked, "bots").find((p) => p.id === "bots")).toMatchObject({ open: true, x: 0, y: bottom, w: 12 });
    // A panel never placed before (no stored place): under the lowest open panel at its usual size.
    const noBots = base.filter((p) => p.id !== "bots");
    expect(openPanel(noBots, "bots").find((p) => p.id === "bots")).toMatchObject({ open: true, x: 0, y: bottom });
    expect(liftedY(base, { id: "bots", x: 0, y: 31, w: 6, h: 6, open: false })).toBe(bottom);
  });

  it("opening a closed panel finds a free place; closing keeps its place; the last open panel stays", () => {
    // A closed panel reopens at its last place ("bots" / "payoff" are hidden until T2/T3, so "basket" closed first).
    const opened = openPanel(closePanel(seller(), "basket"), "basket");
    expect(layoutProblem(opened)).toBeNull();
    expect(opened.find((p) => p.id === "basket")).toMatchObject({ open: true, x: 9, y: 12 });
    const chart2 = openPanel(seller(), "chart-2");                                // never in the preset: added at a free place
    expect(chart2.find((p) => p.id === "chart-2")).toMatchObject({ open: true });
    expect(layoutProblem(chart2)).toBeNull();
    const closed = closePanel(seller(), "chain");
    expect(closed.find((p) => p.id === "chain")).toMatchObject({ open: false, x: 7, y: 0 });
    const one = [{ id: "chart-1" as const, x: 0, y: 0, w: 6, h: 6, open: true }];
    expect(closePanel(one, "chart-1")[0].open).toBe(true);
    expect(firstFree(seller(), 12, 6)).toEqual({ x: 0, y: 20 });
  });

  it("reading order is top to bottom, then left to right", () => {
    expect(readingOrder(seller()).map((p) => p.id)).toEqual(["watchlist", "chart-1", "chain", "positions", "basket"]);
  });
});

describe("layout model: his own layouts", () => {
  const working = (): Layout => ({ ...presetLayout("option_seller"), underlying: "BANKNIFTY" });

  it("save as: stored and in use, then save over, rename, switch, reset and delete", () => {
    const a = saveAs(EMPTY_PREFS, working(), "My desk", "abcd1234", T);
    if ("error" in a) throw new Error(a.error);
    expect(a.prefs.active).toBe("abcd1234");
    expect(a.prefs.layouts.abcd1234).toMatchObject({ name: "My desk", preset: "option_seller", underlying: "BANKNIFTY", saved_at: T.toISOString() });
    const edited = { ...layoutFor(a.prefs, "abcd1234"), panels: closePanel(working().panels, "basket") };
    expect(changed(a.prefs, "abcd1234", edited)).toBe(true);
    const b = saveOver(a.prefs, "abcd1234", edited, T);
    if ("error" in b) throw new Error(b.error);
    expect(changed(b.prefs, "abcd1234", edited)).toBe(false);
    const c = rename(b.prefs, "abcd1234", "Expiry day");
    if ("error" in c) throw new Error(c.error);
    expect(c.prefs.layouts.abcd1234.name).toBe("Expiry day");
    expect(switchTo(c.prefs, "multi_chart").active).toBe("multi_chart");
    expect(switchTo(c.prefs, "nope").active).toBe("abcd1234");                         // unknown: unchanged
    expect(resetToPreset(edited).panels).toEqual(presetLayout("option_seller").panels);
    expect(resetToPreset(edited).underlying).toBe("BANKNIFTY");                       // his underlying is kept
    const d = remove(c.prefs, "abcd1234");
    expect(d.layouts).toEqual({});
    expect(d.active).toBe("option_seller");                                            // falls back to its preset
  });

  it("at most 10 layouts; names follow the server's rule and are unique", () => {
    let prefs: TerminalPrefs = EMPTY_PREFS;
    for (let i = 0; i < MAX_LAYOUTS; i += 1) {
      const r = saveAs(prefs, working(), `Layout ${i}`, `lay${String(i).padStart(5, "0")}`, T);
      if ("error" in r) throw new Error(r.error);
      prefs = r.prefs;
    }
    expect(Object.keys(prefs.layouts)).toHaveLength(MAX_LAYOUTS);
    const eleventh = saveAs(prefs, working(), "One more", "lay99999", T);
    expect("error" in eleventh && eleventh.error).toMatch(/You can keep 10 layouts/);
    expect(nameProblem(prefs, "layout 3")).toMatch(/already have/);                   // case does not make a new name
    expect(nameProblem(prefs, "")).toMatch(/name/);
    expect(nameProblem(prefs, "<script>")).toMatch(/letters, numbers/);
    expect(nameProblem(prefs, "x".repeat(41))).toMatch(/40/);
    expect(nameProblem(prefs, "Layout 3", "lay00003")).toBeNull();                    // renaming to its own name
  });

  it("reads the server's value defensively and sends only what the server takes", () => {
    const raw = {
      layouts: {
        good0001: { name: "Good", preset: "quick_trade", underlying: null, panels: presetLayout("quick_trade").panels, extra: 1 },
        bad00001: { name: "Bad", panels: [{ id: "chart-1", x: 10, y: 0, w: 6, h: 6 }] },              // outside the grid
        bad00002: { name: "Bad 2", panels: [{ id: "ladder", x: 0, y: 0, w: 6, h: 6 }] },              // unknown panel
      },
      active: "good0001",
    };
    const read = readPrefs(raw);
    expect(Object.keys(read.layouts)).toEqual(["good0001"]);
    expect(activeKey(read)).toBe("good0001");
    expect(activeKey({ ...read, active: "gone0001" })).toBe("option_seller");
    expect(readPrefs(null)).toEqual(EMPTY_PREFS);
    const odd: TerminalPrefs = { active: null, layouts: { a1b2c3d4: { ...presetLayout("option_seller"),
      panels: presetLayout("option_seller").panels.map((p) => (p.id === "chain" ? { ...p, symbol: "SHOULD GO" } : p)) } } };
    expect(forSave(odd).layouts.a1b2c3d4.panels.find((p) => p.id === "chain")?.symbol).toBeNull();
  });

  it("saves through the prefs namespace with a compare-and-set version", async () => {
    const calls: { path: string; init?: RequestInit }[] = [];
    const fetcher = (async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      if (path === "/me/prefs") return { prefs: { terminal: { layouts: {}, active: null } }, versions: { terminal: 3 } };
      return { value: JSON.parse(String(init?.body)), version: 4 };
    }) as never;
    const t = terminalApi(fetcher);
    expect(await t.loadLayouts()).toEqual({ value: EMPTY_PREFS, version: 3 });
    const saved = await t.saveLayouts({ layouts: {}, active: "quick_trade" }, 3);
    expect(calls[1].path).toBe("/me/prefs/terminal?version=3");
    expect(calls[1].init?.method).toBe("PUT");
    expect(saved).toEqual({ value: { layouts: {}, active: "quick_trade" }, version: 4 });
  });

  it("reads only his own rows (TT-15) and the status", async () => {
    const paths: string[] = [];
    const t = terminalApi((async (path: string) => { paths.push(path); return []; }) as never);
    await t.positions(); await t.orders(); await t.status(); await t.events(7);
    expect(paths).toEqual(["/positions?mine=true", "/orders?mine=true&today=true&limit=100", "/terminal/status", "/orders/7/events"]);
  });
});

const STATUS: TerminalStatus = {
  today: { mtm: 1250.5, booked: 1000, note: "Booked today plus your open positions' profit or loss since entry, at their last price." },
  open: { count: 2, priced: 1, mtm: 250.5, as_of: "2026-10-24T09:59:00Z" },
  funds: { balance: 1_001_000, available: 996_500, source: "practice", note: "Your practice balance minus the margin estimate." },
  margin: { used: 4500, estimated: true, not_estimated: 1, note: "ATP's estimate, not your broker's number." },
  mode: { mode: "PAPER", live_allowed: false, message: "Practice mode: no real order is sent and no money moves." },
  feed: { state: "reconnecting", message: "Live prices from Upstox are reconnecting." },
  kill_switch: { engaged: false, scope: null, message: "New orders are allowed." },
  as_of: "2026-10-24T10:00:00Z",
};

describe("status bar", () => {
  const bar = (over: Partial<Parameters<typeof StatusBarV2>[0]> = {}) =>
    renderToStaticMarkup(<StatusBarV2 status={STATUS} failed={false} masked={false} onMask={() => undefined} {...over} />);

  it("desktop: MTM, open MTM, funds, margin (an estimate), feed, mode, kill switch and the mask button", () => {
    const out = text(bar());
    expect(out).toContain("Today's MTM +₹1,251");
    expect(out).toContain("Open MTM · 1 of 2 priced +₹251");
    expect(out).toContain("Funds available ₹9,96,500");
    expect(out).toContain("Margin used (estimate) ₹4,500");
    expect(out).toContain("Reconnecting");
    expect(out).toContain("Paper");
    expect(out).toContain("New orders allowed");
    expect(bar()).toContain('aria-label="Hide amounts"');
    expect(marginNote(STATUS)).toBe("Estimate. 1 position needs your broker's margin and is not counted.");
    expect(out).not.toMatch(DEV);
  });

  it("the privacy mask hides every amount", () => {
    const html = bar({ masked: true });
    const out = text(html);
    expect(out.match(/₹••••/g)).toHaveLength(4);
    expect(out).not.toMatch(/₹[0-9]/);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('aria-label="Show amounts"');
  });

  it("phone: MTM, funds and the mode only", () => {
    const out = text(bar({ compact: true }));
    expect(out).toContain("Today +₹1,251");
    expect(out).toContain("Funds ₹9,96,500");
    expect(out).toContain("Paper");
    expect(out).not.toMatch(/Margin|Open MTM|Reconnecting|New orders/);
    expect(bar({ compact: true })).toContain("min-h-11");                            // the mask button is a 44 px target
  });

  it("loading and failed states in plain words; the kill switch when engaged", () => {
    expect(text(bar({ status: null }))).toContain("Loading your figures");
    expect(text(bar({ status: null, failed: true }))).toContain("could not be read. We will try again in a moment.");
    const stopped = text(bar({ status: { ...STATUS, kill_switch: { engaged: true, scope: "account", message: "New orders are stopped for your account. Exits still work." } } }));
    expect(stopped).toContain("New orders stopped");
  });

  it("feed words for every state; the mask is kept per device and survives a broken storage", () => {
    expect(feedChip("live")).toEqual({ label: "Live prices", tone: "up" });
    expect(feedChip("renew_needed").label).toBe("Log in to your broker");
    expect(feedChip("none").label).toBe("Waiting for live prices");
    expect(money(12.4, true)).toBe(MASK);
    expect(money(-700, false, true)).toBe("−₹700");
    const m = new Map<string, string>();
    const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); } };
    expect(readMask(store)).toBe(false);
    writeMask(true, store);
    expect(m.get(MASK_KEY)).toBe("1");
    expect(readMask(store)).toBe(true);
    writeMask(false, store);
    expect(readMask(store)).toBe(false);
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
    expect(readMask(broken)).toBe(false);
    expect(() => writeMask(true, broken)).not.toThrow();
  });
});

describe("phone tabs", () => {
  it("T1-1: the shared PageTabs - one scrolling row, one selected, 44 px targets, the tab ids the panel names", () => {
    // TERM-2 step 3, then M63 I-5 (Payoff, Bots last)
    expect(PHONE_TABS.map((t) => t.label)).toEqual(["Chart", "Chain", "Basket", "Positions", "Orders", "Payoff", "Bots"]);
    const html = renderToStaticMarkup(<PhoneTabsV2 tab="basket" onTab={() => undefined} counts={{ positions: 3 }} />);
    expect(html.match(/role="tab"/g)).toHaveLength(7);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(html).toContain('data-level="primary"');                     // PageTabs, not the old 2 x 3 grid
    expect(html).toContain('data-mode="scroll"');                       // one row, never wrapped (W0-1)
    expect(html).not.toContain("grid-cols-3");
    expect(html).toMatch(/id="terminal-tab-basket"[^>]*aria-controls="terminal-panel-basket"|aria-controls="terminal-panel-basket"[^>]*id="terminal-tab-basket"/);
    expect(html).toContain("data-terminal-tabs");
    expect(text(html)).toContain("Positions 3");
  });
});

describe("panels: plain words", () => {
  it("M63 I-5 Bots & signals: his bots (running first) and the waiting signals in structure words, read only, links to the pages", () => {
    const bot = (id: number, status: string, mode = "PAPER") => ({ id, strategy_id: "orb_15m_5m", symbol: "NIFTY 50", instrument_kind: "OPTION", holding: "INTRADAY",
      mode, status, pause_reason: null, last_signal_at: null, last_evaluated_at: null, last_error: null, open_positions: 0 }) as unknown as Deployment;
    const signal = { id: "action:7", at: "2026-10-27T04:40:00Z", symbol: "RELIANCE", exchange: "NSE", direction: "LONG", setup: "Pullback to the buyer area",
      entry: 2900, stop: 2850, target: 3000, rr: 2, source: { kind: "autopilot", name: "Deployment #4" }, mode: "PAPER", status: "waiting",
      expires_at: null, snoozed_until: null, reasons: [], action_id: 7, outcome: null } as SignalFeedItem;
    const html = renderToStaticMarkup(<MemoryRouter><BotsViewV2 now={new Date("2026-10-27T04:45:00Z")}
      bots={{ status: "ok", value: [bot(1, "PAUSED"), bot(2, "ACTIVE", "LIVE"), bot(3, "STOPPED")] }} signals={{ status: "ok", value: [signal] }} /></MemoryRouter>);
    const t = text(html);
    expect(html.match(/data-bot="\d+"/g)).toEqual(['data-bot="2"', 'data-bot="1"']);    // running first; a stopped bot is not listed
    expect(t).toContain("Opening Range Breakout");
    expect(t).toContain("Running");
    expect(t).toContain("Paused");
    expect(html).toContain('data-chip="bot-live"');
    expect(t).toContain("Pullback to the buyer area");
    expect(html).toContain('href="/signals?proposal=7"');
    expect(t).not.toMatch(/2,?900|2,?850|3,?000|Deployment #4|\bbuy\b|\bsell\b|target|stop-?loss/i);   // no price, no call word
    expect(html).not.toMatch(/<button/);                                                                 // read only
    expect(t).toContain("Open Signals");
    expect(t).toContain("Open Trading bot");
    const empty = text(renderToStaticMarkup(<MemoryRouter><BotsViewV2 now={new Date()} bots={{ status: "ok", value: [] }} signals={{ status: "error" }} /></MemoryRouter>));
    expect(empty).toContain("No bot is running.");
    expect(empty).toContain("We could not load your signals.");
    expect(empty).not.toMatch(/Coming in a later update|\bT[0-9]\b|flag|terminal/);
  });

  it("M63 I-5 Payoff: his positions and basket on the one underlying + expiry, exact at expiry, read only", () => {
    const leg = (id: number, strike: number, side: "BUY" | "SELL", entry: number, expiry = "2026-11-03"): PosLeg => ({
      id, label: `NIFTY ${strike} CE`, symbol: `NIFTY${strike}CE`, exchange: "NFO", underlying: "NIFTY", spotSymbol: "NIFTY 50", spotExchange: "NSE",
      expiry, strike, right: "CE", side, units: 75, lotSize: 75, entry, ltp: null, exitLevel: null, profitLevel: null });
    const basketLeg = (id: string, expiry: string, underlying = "NIFTY") =>
      ({ id, underlying, expiry, strike: 24500, right: "PE", side: "SELL", lots: 1, price: 90, label: null }) as BasketLeg;
    const held = [leg(41, 25000, "SELL", 120), leg(42, 25100, "BUY", 80), leg(43, 25200, "SELL", 60, "2026-11-10")];
    const scope = payoffScope({ underlying: "NIFTY", expiry: "2026-11-03", held, basket: [basketLeg("b1", "2026-11-03"), basketLeg("b2", "2026-11-03", "BANKNIFTY")] });
    expect(scope.expiry).toBe("2026-11-03");
    expect(scope.held.map((l) => l.id)).toEqual([41, 42]);
    expect(scope.basket).toHaveLength(1);                                    // another underlying's leg is not his basket here
    expect(scope.otherExpiry).toBe(1);                                       // counted, not drawn
    // The context bar's expiry holds none of his legs: the nearest of his own.
    expect(payoffScope({ underlying: "NIFTY", expiry: "2026-10-27", held, basket: [] }).expiry).toBe("2026-11-03");
    expect(payoffScope({ underlying: "NIFTY", expiry: "2026-11-03", held: [], basket: [] }).expiry).toBeNull();

    const html = renderToStaticMarkup(<MemoryRouter><PayoffViewV2 underlying="NIFTY" expiry="2026-11-03" spot={24480} otherExpiry={1}
      columns={[{ name: "Your positions", legs: heldSide(scope), shift: 0, pop: null }]} /></MemoryRouter>);
    const t = text(html);
    expect(html).toContain("data-compare-chart");
    expect(html).toMatch(/data-figure-row="Max profit"[\s\S]*?₹3,000/);
    expect(html).toMatch(/data-figure-row="Max loss"[\s\S]*?₹4,500/);
    expect(html).toMatch(/data-figure-row="Breakevens"[\s\S]*?25,040/);
    expect(html).not.toContain('data-figure-row="Margin"');                  // the margin is the basket panel's
    expect(t).toContain("At expiry 3 Nov");
    expect(html).not.toMatch(/<button/);                                     // read only
    expect(text(renderToStaticMarkup(<MemoryRouter><PayoffViewV2 underlying="NIFTY" expiry={null} spot={null} columns={[]} /></MemoryRouter>)))
      .toContain("No open position or basket leg on NIFTY yet.");
  });

  it("M63 I-5 Payoff: a basket still being worked out is listed, not drawn as a flat line", () => {
    const html = renderToStaticMarkup(<MemoryRouter><PayoffViewV2 underlying="NIFTY" expiry="2026-11-03" spot={null}
      columns={[{ name: "Your basket", legs: [], shift: 0, pop: null, note: PAYOFF_NEEDS_SPOT }]} /></MemoryRouter>);
    expect(html).not.toContain("data-curve");
    expect(text(html)).toContain(PAYOFF_NEEDS_SPOT);
  });

  it("the chain without a broker asks to connect it; an empty basket says how to fill it", () => {
    expect(chainProblem({ status: "error" })).toBe("Connect your broker to see live prices on the option chain.");
    const basket = text(renderToStaticMarkup(<BasketPanelBodyV2 underlying="NIFTY" legs={[]} onOpen={() => undefined} />));
    expect(basket).toContain("Your basket on NIFTY is empty.");
    expect(basket).toContain("Open the basket");
  });

  it("orders: Executed first, the state in words, the counts per group", () => {
    const rows = [
      { id: 1, mode: "PAPER", symbol: "NIFTY 25000 CE", direction: "SHORT", quantity: 75, status: "POSITION_OPEN", trade_id: 9, reasons: [], created_at: "2026-10-24T04:00:00Z", updated_at: "2026-10-24T04:00:00Z" },
      { id: 2, mode: "PAPER", symbol: "NIFTY 24000 PE", direction: "LONG", quantity: 75, status: "REJECTED", trade_id: null, reasons: ["Today's loss limit is reached."], created_at: "2026-10-24T04:01:00Z", updated_at: "2026-10-24T04:01:00Z" },
      { id: 3, mode: "PAPER", symbol: "NIFTY 24100 PE", direction: "LONG", quantity: 75, status: "CANCELLED", trade_id: null, reasons: [], created_at: "2026-10-24T04:02:00Z", updated_at: "2026-10-24T04:02:00Z" },
    ];
    const out = text(renderToStaticMarkup(<OrdersPanelV2 read={{ status: "ok", value: rows }} terminal={{ events: async () => [] }} />));
    expect(out).toContain("Open 0");
    expect(out).toContain("Executed 1");
    expect(out).toContain("Rejected 1");                                    // REV-1 M3: the cancelled one is not counted here
    expect(out).toContain("Cancelled 1");
    expect(out).toContain("Sell NIFTY 25000 CE");
    expect(out).toContain("Filled, position open");
    expect(out).not.toContain("Practice");                                  // CT-1 C8: the one PAPER badge says it
    expect(out).not.toContain("POSITION_OPEN");
    expect(orderGroup("PARTIAL_FILL")).toBe("open");
    expect(orderGroup("CANCELLED")).toBe("cancelled");        // REV-1 M3: never under "Rejected"
    expect(orderGroup("REJECTED")).toBe("rejected");
    expect(orderGroup("FAILED")).toBe("rejected");
    expect(statusWords("SOMETHING_NEW")).toBe("Updated");
  });

  it("a filled entry whose position has closed says so, with the exit's reason (owner bug report)", () => {
    expect(orderRowWords({ status: "POSITION_OPEN", position_closed: true, exit_reason: "Premium floor" })).toBe("Filled, position closed (Premium floor)");
    expect(orderRowWords({ status: "POSITION_OPEN", position_closed: true, exit_reason: null })).toBe("Filled, position closed");
    expect(orderRowWords({ status: "POSITION_OPEN", position_closed: false })).toBe("Filled, position open");
    expect(orderRowWords({ status: "POSITION_OPEN" })).toBe("Filled, position open");          // an older server's answer
    expect(orderRowWords({ status: "REJECTED", position_closed: true })).toBe("Refused");
    expect(panelsSource).toContain("PAST_ORDERS_PATH");
  });

  it("one order path: the Terminal places nothing itself and the AI is never imported (ADR-0006)", () => {
    for (const src of [pageSource, panelsSource, gridSource]) {
      expect(src).not.toMatch(/fnoApi\.place|\.place\(askOf|basketApi\.execute|kill-switch|copilot/i);
    }
    expect(panelsSource).toContain("useOrderTicket");
    expect(pageSource).toContain("useChainTrading");
    expect(pageSource).not.toMatch(/text-\[\d+px\]/);
  });
});

describe("navigation behind the `terminal` flag (TT-18)", () => {
  const flags = (...on: string[]) => (f: string) => on.includes(f);
  const entries = (on: string[]) => [...visiblePinned({ on: flags(...on), role: "OWNER" }), ...visibleSections({ on: flags(...on), role: "OWNER" }).flatMap((s) => s.items)];

  it("M60: second in Trade with the flag on, where Charts and Option chain stood in for it while it was off", () => {
    const keys = (on: string[]) => entries(on).map((i) => i.key);
    expect(keys(["ai_copilot", "screener_v2"]).slice(0, 4)).toEqual(["dashboard", "charts", "option-chain", "positions"]);
    expect(keys(["ai_copilot", "screener_v2", "terminal"]).slice(0, 3)).toEqual(["dashboard", "terminal", "positions"]);
    const trade = NAV_SECTIONS_V2.find((s) => s.id === "trade");
    expect(trade?.items[1]).toMatchObject({ key: "terminal", label: "Terminal", path: "/terminal", flag: "terminal" });
  });

  it("flag off: the address says Coming soon; ui_v2 off: back to the dashboard", () => {
    expect(shellKeyFromPath("/terminal")).toBe("terminal");
    expect(closedFlag("terminal", { answered: true, on: () => false })).toBe("terminal");
    expect(closedFlag("terminal", { answered: true, on: flags("terminal") })).toBeNull();
    expect(flagOffFallback("/terminal")).toBe("/");
    expect(PAGE_COMPONENTS_V2.terminal).toBeTruthy();
  });

  it("every panel has a label; ids match the server's allow-list order", () => {
    expect(PANEL_IDS).toEqual(["watchlist", "chart-1", "chart-2", "chart-3", "chart-4", "chain", "order", "positions", "orders", "basket", "bots", "payoff"]);
    for (const id of PANEL_IDS) expect(PANELS[id].label).toMatch(/^[A-Z]/);
  });
});

// --- Owner fixes T1-2 .. T1-7 (24 Oct) ----------------------------------------------------------------------------------

const leg = (o: Partial<ChainLeg> = {}): ChainLeg => ({ oi: null, oi_change: null, ltp: null, iv: null, volume: null, bid: null, ask: null, delta: null, gamma: null, theta: null, vega: null, ...o });
const CHAIN: LiveChain = {
  underlying: "NIFTY", expiry: "2026-10-27", fetched_at: "2026-10-24T09:00:00Z", market_open: true, data_as_of: "2026-10-24T09:00:00Z",
  oi_change: { status: "ready", source: "broker", since: null },
  summary: { spot: 24510, spot_source: "broker", futures: null, atm_strike: 24500, pcr_oi: null, max_pain: null, atm_iv: null, days_to_expiry: 3,
    max_call_oi_strike: null, max_put_oi_strike: null, total_call_oi: 0, total_put_oi: 0 },
  rows: [24400, 24500, 24600].map((k) => ({ strike: k,
    call: leg({ oi: 1250000, oi_change: -50000, ltp: 120.5, iv: 13.2, delta: 0.52 }),
    put: leg({ oi: k === 24600 ? null : 980000, oi_change: 25000, ltp: 98.05, iv: 14.1, delta: -0.48 }) })),
};
const TRADING = { enabled: true, held: new Set<string>(), onPickLeg: () => undefined } as unknown as ChainTrading;

describe("owner fixes T1-2 .. T1-7", () => {
  it("T1-2: the phone ticket's main action is sticky above the bottom navigation (44 px target)", () => {
    const target = { underlying: "NIFTY", expiry: "2026-10-27", strike: 24500, right: "CE" as const, label: "NIFTY 27 OCT 24500 CE", lotSize: 75, ltp: 120, bid: 119.9, ask: 120.1 };
    const draft = newDraft("BUY");
    const html = renderToStaticMarkup(<TicketBody target={target} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="t"
      onDraft={() => undefined} onReview={() => undefined} onPlace={() => undefined} onBack={() => undefined} onClose={() => undefined} stickyActions />);
    expect(html).toMatch(/data-ticket-actions="sticky"[^>]*class="[^"]*fixed inset-x-0 bottom-\[calc\(4rem\+1px\+env\(safe-area-inset-bottom\)\)\][^"]*md:sticky/);
    expect(html).toMatch(/data-ticket-body[^>]*|class="[^"]*pb-24 md:pb-space-3[^"]*"[^>]*data-ticket-body/);
    expect(html).toMatch(/data-ticket="review"[^>]*class="[^"]*min-h-11|class="[^"]*min-h-11[^"]*"[^>]*data-ticket="review"/);
    expect(panelsSource).toContain("stickyActions");
    const plain = renderToStaticMarkup(<TicketBody target={target} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="u"
      onDraft={() => undefined} onReview={() => undefined} onPlace={() => undefined} onBack={() => undefined} onClose={() => undefined} />);
    expect(plain).not.toContain('data-ticket-actions="sticky"');                 // the chain's own ticket sheet is unchanged
  });

  it("T1-3: desktop shows OI, change in OI, IV, delta and LTP per side; a missing figure is \"–\", never 0", () => {
    const out = renderToStaticMarkup(<MemoryRouter><ChainPanelV2 underlying="NIFTY" read={{ status: "ok", value: CHAIN }} trading={TRADING} rows={3} /></MemoryRouter>);
    const t = text(out);
    for (const h of ["OI", "Chg OI", "IV %", "Delta", "Call LTP", "Strike", "Put LTP"]) expect(t).toContain(h);
    expect(out).toContain('data-chain-view="full"');
    expect(out).toContain('data-col="call-oi"');
    expect(out).toContain('data-col="put-delta"');
    expect(t).toContain("13.2");
    expect(t).toContain("0.52");
    expect(legFigures(leg()).oi).toBe("–");
    expect(Object.values(legFigures(leg())).every((v) => v === "–")).toBe(true);
  });

  it("T1-3: phone shows LTP + OI per side; tapping a strike opens the rest", () => {
    const out = renderToStaticMarkup(<MemoryRouter><ChainPanelV2 underlying="NIFTY" read={{ status: "ok", value: CHAIN }} trading={TRADING} rows={3} compact /></MemoryRouter>);
    expect(out).toContain('data-chain-view="compact"');
    expect(out).not.toContain('data-col="call-iv"');
    expect(out).toContain('data-col="call-oi"');
    expect(out).toMatch(/data-expand="24500"/);
    expect(out).toMatch(/aria-expanded="false"/);
    expect(text(out)).toContain("Tap a strike for change in OI, IV and delta.");
  });

  it("T1-3: the chain's spot is the chart's spot - one read, one time, shown once", () => {
    const tick = { symbol: "NIFTY 50", exchange: "NSE", ltp: 24533.4, ts: "2026-10-24T09:01:00Z" };
    const ltp = { ltp: 24520, timestamp: "2026-10-24T09:00:30Z", fetched_at: "2026-10-24T09:00:31Z" };
    expect(pickSpot(tick, ltp, CHAIN)).toEqual({ ltp: 24533.4, at: tick.ts, source: "tick" });   // the hub's tick first
    expect(pickSpot(undefined, ltp, CHAIN)).toEqual({ ltp: 24520, at: ltp.timestamp, source: "ltp" });
    expect(pickSpot(undefined, null, CHAIN)).toEqual({ ltp: 24510, at: CHAIN.data_as_of, source: "chain" });
    const spot = pickSpot(tick, ltp, CHAIN);
    expect(chartLiveOf(spot)).toEqual({ ltp: 24533.4, timestamp: tick.ts, fetched_at: tick.ts });  // the chart gets the same value
    expect(chartLiveOf(pickSpot(undefined, null, CHAIN))).toBeNull();                             // the chain's own spot never moves the chart
    const out = text(renderToStaticMarkup(<MemoryRouter><ChainPanelV2 underlying="NIFTY" read={{ status: "ok", value: CHAIN }} trading={TRADING} spot={spot} /></MemoryRouter>));
    expect(out).toContain(`spot ${formatPrice(24533.4)} (as of ${formatTimeIST(tick.ts)})`);
    expect(out.match(/as of/g)).toHaveLength(1);
    // UX-11 U1: how to use the chain sits in the panel's Details (closed by default), word for word.
    const html = renderToStaticMarkup(<MemoryRouter><ChainPanelV2 underlying="NIFTY" read={{ status: "ok", value: CHAIN }} trading={TRADING} spot={spot} /></MemoryRouter>);
    expect(html).toMatch(/<details data-details="terminal.chain"[\s\S]*Tap a price to buy, sell or add it to your basket\.[\s\S]*<\/details>/);
    expect(pageSource).toMatch(/useTerminalSpot\(/);
    expect(pageSource).toMatch(/chartLiveOf\(spot, live\?\.market_open === true\)/);
    expect(pageSource).toContain("areaMarks={areaMarks.size ? areaMarks : undefined} spot={spot}");         // the chain view shows the page's ONE spot
  });

  it("T1-4: the spread builder places the hedge (buy) first, then the sell; flags a sell with no hedge", () => {
    const legs = [
      { id: "a", underlying: "NIFTY", expiry: "2026-10-27", strike: 24500, right: "PE" as const, side: "SELL" as const, lots: 1, price: 98, label: null, stopLoss: null },
      { id: "b", underlying: "NIFTY", expiry: "2026-10-27", strike: 24300, right: "PE" as const, side: "BUY" as const, lots: 1, price: 40, label: null, stopLoss: null },
    ] as unknown as BasketLeg[];
    expect(placementOrder(legs).map((x) => `${x.role}:${x.leg.strike}`)).toEqual(["hedge:24300", "sell:24500"]);
    expect(unhedgedSells(legs)).toEqual([]);
    expect(unhedgedSells([legs[0]])).toEqual([24500]);
    const out = renderToStaticMarkup(<SpreadBuilderV2 underlying="NIFTY" legs={legs} spot={24510} onOpen={() => undefined} api={{ evaluate: async () => ({ ok: false, errors: [] }) }} />);
    const t = text(out);
    expect(t).toMatch(/1\. Hedge · Buy 24,300 PE.*2\. Sell · Sell 24,500 PE/);
    for (const f of ["Net premium", "Max loss", "Breakeven", "Margin with the hedge"]) expect(t).toContain(f);
    expect(t).toContain("the hedge (buy) first, then the sell. On exit the sold leg closes first, then the hedge.");
    expect(t).toContain('"Complete the leg" or "Exit the filled leg"');
    const naked = text(renderToStaticMarkup(<SpreadBuilderV2 underlying="NIFTY" legs={[legs[0]]} spot={24510} api={{ evaluate: async () => ({ ok: false, errors: [] }) }} />));
    expect(naked).toContain("has no hedge: its loss has no limit");
    expect(PANELS.basket.label).toBe("Basket");                      // TERM-2 step 2: the one basket panel
  });

  it("T1-4: a broken leg is an alert on the page itself with the owner's two choices", () => {
    expect(completeLabel(1)).toBe("Complete the leg");
    expect(exitFilledLabel(1)).toBe("Exit the filled leg");
    expect(completeLabel(2)).toBe("Complete the legs");
    expect(pageSource).toContain("<IncompleteOrdersV2 />");                    // above every panel and tab
  });

  it("T1-5 / owner F1: the product starts from his trading defaults; with none saved nothing is pre-selected and Review waits for his pick", () => {
    expect(newDraft("BUY", { product: "NRML" }).product).toBe("NRML");
    expect(newDraft("BUY").product).toBeNull();
    expect(draftProblems(newDraft("BUY", { orderType: "MARKET", lots: 1 }))[0]).toBe(PRODUCT_MISSING);   // M68: lots and order type are asked first
    expect(draftProblems(newDraft("BUY", { product: "MIS" }))).not.toContain(PRODUCT_MISSING);
    expect(PRODUCT_UNSET_TEXT).toMatch(/^Pick MIS or NRML/);
    // TERM-2 step 2: the Terminal's basket panel is the one basket, which starts from his trading defaults itself.
    expect(basketSource).toMatch(/ticketPrefill\(useTradingDefaults/);
    expect(pageSource).toContain("<BasketPanelV2 embedded");
  });

  it("T1-6: plain words - \"Manual trade\", \"no stop\" (never the one-tick placeholder)", () => {
    expect(strategyDisplayName("manual_fno")).toBe("Manual trade");
    expect(strategyDisplayName("manual")).toBe("Manual trade");
    expect(stopText(null)).toBe(NO_STOP);
    expect(stopText(0.05, true)).toBe(NO_STOP);
    expect(stopText(0.05)).toBe(formatPrice(0.05));
    expect(stopText(85.5, true)).toBe(formatPrice(85.5));
  });

  it("T1-6: Exit all closes groups (sold legs first, on the server), then sold singles, then bought; P&L on every card", () => {
    const row = (id: number, side: "Buy" | "Sell") => ({ id, side, pnl: 10 }) as unknown as HeldRow;
    const plan = exitAllPlan([row(1, "Buy"), row(2, "Sell"), row(3, "Sell"), row(4, "Buy")], [{ key: "g", kind: "basket", trade_ids: [3, 4], hedge_ids: [4] } as unknown as PositionGroup]);
    expect(plan.groups).toEqual([3]);
    expect(plan.sells).toEqual([2]);
    expect(plan.buys).toEqual([1]);
    expect(exitAllConfirmText(4)).toBe("Exit all 4 open positions at the current price? Sold legs close first, then the legs that protect them.");
    expect(panelsSource).toMatch(/<PositionList rows=\{rows\} byId=\{byId\} actions=\{actions\} prices /);
    expect(panelsSource).not.toMatch(/data-terminal="exit-all"[^>]*disabled/);   // never disabled (ADR-0004)
  });

  it("UI-28 U3.4: Exit all asks in the app with the rupee impact, never the browser's confirm", () => {
    expect(exitAllConsequence(3, -1250)).toBe("Exit all 3 open positions at the current price. Sold legs close first, then the legs that protect them. Open P&L now: −₹1,250.");
    expect(exitAllConsequence(2, null)).toBe("Exit all 2 open positions at the current price. Sold legs close first, then the legs that protect them.");
    // CT-1 T4: Exit all lives in the one position list (trading/ExitAllV2.tsx), for My trades and the Terminal alike.
    expect(exitAllSource).not.toContain("window.confirm(exitAllConfirmText");
    expect(exitAllSource).toMatch(/<ConfirmDialog open=\{askExitAll\}[\s\S]*consequence=\{exitAllConsequence\(rows\.length, known\.length === rows\.length \? total : null\)\}/);
  });

  it("T1-7 / TF-1: the terminal chart's timeframe control is the shared TimeframePicker", () => {
    expect(embeddedSource).toContain("<TimeframePicker");
    expect(panelsSource).toContain("<EmbeddedChartV2");
  });
});

describe("M63 I-5: Bots & signals and Payoff are built, so they are offered (R6: only an unbuilt panel is hidden)", () => {
  it("in the Panels menu and on the phone (last), they open, a stored open one stays open; closed in every preset", () => {
    expect([...HIDDEN_PANELS]).toEqual([]);
    expect(CHOOSER_PANEL_IDS).toEqual(PANEL_IDS);
    expect(PHONE_TABS.slice(-2).map((t) => t.key)).toEqual(["payoff", "bots"]);
    for (const id of PRESET_IDS) {
      for (const p of PRESETS[id].panels.filter((x) => x.id === "bots" || x.id === "payoff")) expect(p.open).toBe(false);
    }
    const base = PRESETS.option_seller.panels;
    expect(openPanel(base, "bots").find((x) => x.id === "bots")?.open).toBe(true);
    expect(openPanel(base, "payoff").find((x) => x.id === "payoff")?.open).toBe(true);
    const stored = { layouts: { mine: { name: "Mine", underlying: null, panels: [
      { id: "chart-1", x: 0, y: 0, w: 6, h: 8, open: true }, { id: "bots", x: 6, y: 0, w: 6, h: 6, open: true },
      { id: "payoff", x: 0, y: 8, w: 6, h: 6, open: true }] } }, active: "mine" };
    const read = readPrefs(stored);
    const panels = read.layouts.mine.panels;
    expect(panels.filter((x) => x.open).map((x) => x.id)).toEqual(["chart-1", "bots", "payoff"]);
  });
});

describe("owner bug 13 Nov: one spot for the context bar, the chain and the chart", () => {
  const at = (hhmmss: string) => `2026-11-13T${hhmmss}+05:30`;
  const chainAt = (spot: number, when: string, open = true): LiveChain => ({ ...CHAIN, market_open: open, data_as_of: at(when), fetched_at: at(when),
    summary: { ...CHAIN.summary, spot } });

  it("a tick or quote older than the chain's newer spot never holds the spot (the screenshot: 25,168.70 vs 25,131.80)", () => {
    const oldTick = { symbol: "NIFTY 50", exchange: "NSE", ltp: 25168.7, ts: at("11:52:00") };
    const oldLtp = { ltp: 25168.7, timestamp: at("11:52:00"), fetched_at: at("12:05:10") };
    const chain = chainAt(25131.8, "12:05:00");
    expect(pickSpot(oldTick, null, chain)).toEqual({ ltp: 25131.8, at: at("12:05:00"), source: "chain" });
    expect(pickSpot(undefined, oldLtp, chain)?.ltp).toBe(25131.8);
    // a newer tick still wins over the chain (it is the newest read)
    expect(pickSpot({ ...oldTick, ltp: 25130.5, ts: at("12:05:20") }, null, chain)).toMatchObject({ ltp: 25130.5, source: "tick" });
    // an LTP the server flags stale is never used
    expect(pickSpot(undefined, { ...oldLtp, timestamp: at("12:05:30"), stale: true }, chain)?.source).toBe("chain");
  });

  it("a tick this tab stopped hearing goes out (the LTP read comes back)", () => {
    const tick = { symbol: "NIFTY 50", exchange: "NSE", ltp: 25168.7, ts: at("11:52:00") };
    const now = Date.parse(at("12:05:00"));
    expect(freshTick(tick, now - 5_000, now)).toBe(tick);
    expect(freshTick(tick, now - TICK_FRESH_MS - 1, now)).toBeUndefined();
    expect(freshTick(tick, undefined, now)).toBeUndefined();
  });

  it("the context bar and the chain's spot card show the SAME spot from one source", () => {
    const chain = chainAt(25131.8, "12:05:00");
    const spot = pickSpot({ symbol: "NIFTY 50", exchange: "NSE", ltp: 25168.7, ts: at("11:52:00") }, null, chain);
    const data = { expiries: null, expiry: "2026-10-27", setExpiry: () => undefined, loading: false, problem: null, live: chain } as unknown as OptionChainData;
    const f = contextFigures(data, spot, 25299.35, 16.17, Date.parse(at("12:05:20")));
    const card = summaryCards(chain.summary, chain.expiry, spot, 25299.35).find((c) => c.key === "spot");
    expect(f.spot).toBe(25131.8);
    expect(card?.num).toBe(25131.8);
    expect(f.moved?.change).toBeCloseTo(-167.55);                          // the chain banner's own change
    expect(f.spotAsOf).toBeNull();                                         // current: no "as of"
  });

  it("a spot that is not current says \"as of <time>\"; the closed market's spot too", () => {
    const chain = chainAt(25131.8, "12:05:00");
    const data = { expiries: null, expiry: "2026-10-27", setExpiry: () => undefined, loading: false, problem: null, live: chain } as unknown as OptionChainData;
    const spot = pickSpot(undefined, null, chain);
    const later = Date.parse(at("12:05:00")) + SPOT_AS_OF_AFTER_MS + 1_000;
    expect(contextFigures(data, spot, null, null, later).spotAsOf).toBe(at("12:05:00"));
    expect(spotAsOf(spot, false, Date.parse(at("12:05:01")))).toBe(at("12:05:00"));
    const html = renderToStaticMarkup(<MemoryRouter><ContextBarV2 symbol="NIFTY" onSymbol={() => undefined} chain={data} spot={spot} prevClose={null}
      vix={null} mode={{ label: "Paper", title: "Practice" }} /></MemoryRouter>);
    expect(html).toContain("data-spot-as-of");                             // the fixture's time is long past: labelled, never "live"
  });

  it("the chart's forming bar takes the one spot on EVERY timeframe while open; a closed chain's last close never moves it", () => {
    const chain = chainAt(25131.8, "12:05:00");
    const spot = pickSpot(undefined, null, chain);
    const live = chartLiveOf(spot, true);
    expect(live).toEqual({ ltp: 25131.8, timestamp: at("12:05:00"), fetched_at: at("12:05:00") });
    expect(chartLiveOf(spot, false)).toBeNull();
    const bar = (start: string, close: number) => ({ timestamp: at(start), open: close, high: close, low: close, close, volume: 0 });
    for (const [tf, start] of [["1min", "12:05:00"], ["15min", "12:00:00"], ["60min", "11:15:00"]] as const) {
      const out = foldLive([bar(start, 25146.7)], live?.ltp, live?.timestamp, tf);
      expect(out[out.length - 1].close).toBe(25131.8);
    }
  });
});

describe("TERM-2 step 1: the context bar", () => {
  const chainData = (summary: Record<string, unknown> | null) => ({
    expiries: { underlying: "NIFTY", today: "2026-10-27", source: "master", default: "2026-11-03",
      expiries: [{ date: "2026-10-27", kind: "monthly" }, { date: "2026-11-03", kind: "weekly" }] },
    expiry: "2026-11-03", setExpiry: () => undefined, loading: false, problem: null,
    live: summary ? { underlying: "NIFTY", expiry: "2026-11-03", rows: [], summary } : null,
  }) as unknown as OptionChainData;

  it("figures: the live spot wins over the chain's, its Signed change from the previous close, PCR, DTE, VIX band", () => {
    const f = contextFigures(chainData({ spot: 24400, pcr_oi: 0.5, pcr_oi_atm: 0.91, days_to_expiry: 7, atm_strike: 24500 }),
      { ltp: 24480, at: null, source: "tick" }, 24400, 13.4);
    expect(f.spot).toBe(24480);
    expect(f.moved?.change).toBeCloseTo(80);
    expect(f.pcr).toBe(0.91);                          // M96: the PCR over ATM ± 6 (Trade), not the whole chain's 0.5
    expect(f.dte).toBe(7);
    expect(f.vix?.level).toBeTruthy();
    const none = contextFigures(chainData(null), null, null, null);
    expect(none.spot).toBeNull();
    expect(none.moved).toBeNull();
    expect(none.vix).toBeNull();
  });

  it("renders the symbol, the expiry chosen by the chain read, and ONE PAPER badge", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ContextBarV2 symbol="NIFTY" onSymbol={() => undefined}
      chain={chainData({ spot: 24400, pcr_oi: 0.5, pcr_oi_atm: 0.91, days_to_expiry: 7, atm_strike: 24500 })} spot={null} prevClose={null} vix={null}
      mode={{ label: "Paper", title: "Practice" }} /></MemoryRouter>);
    expect(html).toContain('data-expiry="2026-11-03"');
    expect(html).toContain('data-symbol="NIFTY"');
    expect(html.match(/>PAPER</g)?.length).toBe(1);
    expect(html).not.toMatch(/\bPractice\b(?![^<]*")/);
  });
});

describe("TERM-2 step 2: the chart's lines", () => {
  const leg = (o: Partial<BasketLeg>): BasketLeg => ({ id: "a", underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", side: "SELL", lots: 1,
    price: 150, label: null, ...o } as BasketLeg);
  it("his legs, the strikes he holds, the breakevens and his own spot stop - nothing else", () => {
    const lines = terminalLevels({ underlying: "NIFTY", expiry: "2026-11-03",
      basket: [leg({}), leg({ id: "b", strike: 24600, side: "BUY" }), leg({ id: "c", underlying: "BANKNIFTY" }), leg({ id: "d", expiry: "2026-11-10" })],
      held: new Set(["25000|CE"]), breakevens: [24651.5],
      combined: [{ measure: "SPOT", status: "ARMED", spot_symbol: "NIFTY 50", sl_value: 24800, target_value: null } as never,
        { measure: "MTM_INR", status: "ARMED", spot_symbol: null, sl_value: -2000, target_value: null } as never],
      spotSymbol: "NIFTY 50" });
    expect(lines).toEqual([
      { price: 24500, title: "Sell 24500 CE", tone: "down" },
      { price: 24600, title: "Buy 24600 CE", tone: "up" },
      { price: 25000, title: "Held 25000 CE", tone: "neutral" },
      { price: 24651.5, title: "Breakeven", tone: "neutral" },
      { price: 24800, title: "Your stop", tone: "down" },
    ]);
  });
});

describe("CT-1 T4: Exit all in the one position list", () => {
  it("counts only the PAPER positions (a LIVE one closes in the broker's app)", () => {
    const rec = (id: number, mode: string) => ({ id, mode }) as unknown as TradeRecord;
    const rows = [1, 2, 3].map((id) => ({ id, side: "Buy", pnl: 10 }) as unknown as HeldRow);
    const byId = new Map([[1, rec(1, "PAPER")], [2, rec(2, "PAPER")], [3, rec(3, "LIVE")]]);
    const paper = exitableRows(rows, byId);
    expect(paper.map((r) => r.id)).toEqual([1, 2]);
    const html = renderToStaticMarkup(<ExitAllV2 rows={paper} byId={byId} groups={[]} hooks={{ onChanged: () => undefined, onNote: () => undefined }} />);
    expect(html).toContain("Exit all (2)");
    expect(listSource).toContain("exitableRows(rows, byId)");
  });
});

describe("TERM-2 step 3: the one bottom panel", () => {
  it("Positions · Orders · Alerts as tabs, positions first, the count on Positions", () => {
    const html = renderToStaticMarkup(<MemoryRouter><AccountPanelV2 positions={<p>the list</p>} orders={<p>orders</p>} counts={{ positions: 2 }} /></MemoryRouter>);
    expect(text(html)).toContain("Positions 2");
    expect(html.match(/role="tab"/g)).toHaveLength(3);
    expect(html).toContain('data-account-tab="positions"');
    expect(text(html)).toContain("the list");
    expect(text(html)).not.toContain("orders orders");
  });
});
