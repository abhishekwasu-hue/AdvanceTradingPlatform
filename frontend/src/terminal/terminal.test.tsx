import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { orderGroup, statusWords, terminalApi, type TerminalStatus } from "./api";
import {
  CHOOSER_PANEL_IDS, COLUMNS, DEFAULT_PRESET, EMPTY_PREFS, MAX_LAYOUTS, HIDDEN_PANELS, PANELS, PANEL_IDS, PHONE_TABS, PRESETS, PRESET_IDS, activeKey, changed, closePanel,
  firstFree, forSave, layoutFor, layoutProblem, movePanel, nameProblem, openPanel, presetLayout, readPrefs, readingOrder, remove, rename,
  resetToPreset, resizePanel, saveAs, saveOver, switchTo, type Layout, type TerminalPrefs,
} from "./layoutModel";
import { BasketPanelBodyV2, ChainPanelV2, OrdersPanelV2, PlaceholderPanelV2, chainProblem, exitAllConfirmText, exitAllPlan, legFigures } from "./PanelsV2";
import { chartLiveOf, pickSpot } from "./spot";
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

describe("layout model: presets", () => {
  it("three presets, each valid on the 12-column grid; Option seller is the default (T-D1)", () => {
    expect(PRESET_IDS).toEqual(["option_seller", "quick_trade", "multi_chart"]);
    expect(DEFAULT_PRESET).toBe("option_seller");
    expect(activeKey(EMPTY_PREFS)).toBe("option_seller");
    for (const id of PRESET_IDS) {
      const l = presetLayout(id);
      expect(layoutProblem(l.panels), id).toBeNull();
      for (const p of l.panels) expect(p.x + p.w).toBeLessThanOrEqual(COLUMNS);
    }
    const open = (id: (typeof PRESET_IDS)[number]) => presetLayout(id).panels.filter((p) => p.open).map((p) => p.id);
    expect(open("option_seller")).toEqual(["chart-1", "chain", "order", "positions", "basket", "orders"]);
    expect(open("quick_trade")).toEqual(["chart-1", "order", "positions", "orders"]);
    expect(open("multi_chart")).toEqual(["chart-1", "chart-2", "chart-3", "chart-4", "positions"]);
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
    expect(resizePanel(seller(), "chart-1", 5, 11)).toBeNull();                    // into the chain
    const small = resizePanel(seller(), "chart-1", 1, 1);
    expect(small?.find((p) => p.id === "chart-1")).toMatchObject({ w: PANELS["chart-1"].minW, h: PANELS["chart-1"].minH });
  });

  it("opening a closed panel finds a free place; closing keeps its place; the last open panel stays", () => {
    // A closed panel reopens at its last place ("bots" / "payoff" are hidden until T2/T3, so "basket" closed first).
    const opened = openPanel(closePanel(seller(), "basket"), "basket");
    expect(layoutProblem(opened)).toBeNull();
    expect(opened.find((p) => p.id === "basket")).toMatchObject({ open: true, x: 5, y: 11 });
    const chart2 = openPanel(seller(), "chart-2");                                // never in the preset: added at a free place
    expect(chart2.find((p) => p.id === "chart-2")).toMatchObject({ open: true });
    expect(layoutProblem(chart2)).toBeNull();
    const closed = closePanel(seller(), "chain");
    expect(closed.find((p) => p.id === "chain")).toMatchObject({ open: false, x: 3, y: 0 });
    const one = [{ id: "chart-1" as const, x: 0, y: 0, w: 6, h: 6, open: true }];
    expect(closePanel(one, "chart-1")[0].open).toBe(true);
    expect(firstFree(seller(), 12, 6)).toEqual({ x: 0, y: 18 });
  });

  it("reading order is top to bottom, then left to right", () => {
    expect(readingOrder(seller()).map((p) => p.id)).toEqual(["chart-1", "chain", "order", "positions", "basket", "orders"]);
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
    expect(paths).toEqual(["/positions?mine=true", "/orders?mine=true&limit=100", "/terminal/status", "/orders/7/events"]);
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
    expect(feedChip("none").label).toBe("No live prices");
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
    expect(PHONE_TABS.map((t) => t.label)).toEqual(["Chart", "Chain", "Order", "Positions", "Orders"]);
    const html = renderToStaticMarkup(<PhoneTabsV2 tab="order" onTab={() => undefined} counts={{ positions: 3 }} />);
    expect(html.match(/role="tab"/g)).toHaveLength(5);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(html).toContain('data-level="primary"');                     // PageTabs, not the old 2 x 3 grid
    expect(html).toContain('data-mode="scroll"');                       // one row, never wrapped (W0-1)
    expect(html).not.toContain("grid-cols-3");
    expect(html).toMatch(/id="terminal-tab-order"[^>]*aria-controls="terminal-panel-order"|aria-controls="terminal-panel-order"[^>]*id="terminal-tab-order"/);
    expect(html).toContain("data-terminal-tabs");
    expect(text(html)).toContain("Positions 3");
  });
});

describe("panels: plain words", () => {
  it("the later parts say so in plain words, with no part numbers", () => {
    const bots = text(renderToStaticMarkup(<MemoryRouter><PlaceholderPanelV2 kind="bots" /></MemoryRouter>));
    const payoff = text(renderToStaticMarkup(<MemoryRouter><PlaceholderPanelV2 kind="payoff" /></MemoryRouter>));
    expect(bots).toContain("Coming in a later update");
    expect(payoff).toContain("Coming in a later update");
    for (const t of [bots, payoff]) expect(t).not.toMatch(/\bT[0-9]\b|flag|terminal/);
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
    ];
    const out = text(renderToStaticMarkup(<OrdersPanelV2 read={{ status: "ok", value: rows }} terminal={{ events: async () => [] }} />));
    expect(out).toContain("Open 0");
    expect(out).toContain("Executed 1");
    expect(out).toContain("Rejected 1");
    expect(out).toContain("Sell NIFTY 25000 CE");
    expect(out).toContain("Filled, position open · Practice");
    expect(out).not.toContain("POSITION_OPEN");
    expect(orderGroup("PARTIAL_FILL")).toBe("open");
    expect(orderGroup("CANCELLED")).toBe("rejected");
    expect(statusWords("SOMETHING_NEW")).toBe("Updated");
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

  it("first in the Trading group with the flag on: 13 entries off, 14 on", () => {
    expect(entries(["ai_copilot", "screener_v2"])).toHaveLength(13);
    const on = entries(["ai_copilot", "screener_v2", "terminal"]);
    expect(on).toHaveLength(14);
    const trading = NAV_SECTIONS_V2.find((s) => s.id === "trading");
    expect(trading?.items[0]).toMatchObject({ key: "terminal", label: "Terminal", path: "/terminal", flag: "terminal" });
  });

  it("flag off: the address says Coming soon; ui_v2 off: back to the dashboard", () => {
    expect(shellKeyFromPath("/terminal")).toBe("terminal");
    expect(closedFlag("terminal", { answered: true, on: () => false })).toBe("terminal");
    expect(closedFlag("terminal", { answered: true, on: flags("terminal") })).toBeNull();
    expect(flagOffFallback("/terminal")).toBe("/");
    expect(PAGE_COMPONENTS_V2.terminal).toBeTruthy();
  });

  it("every panel has a label; ids match the server's allow-list order", () => {
    expect(PANEL_IDS).toEqual(["chart-1", "chart-2", "chart-3", "chart-4", "chain", "order", "positions", "orders", "basket", "bots", "payoff"]);
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
    expect(pageSource).toMatch(/chartLiveOf\(spot\)/);
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
    expect(PANELS.basket.label).toBe("Spread builder");
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
    expect(draftProblems(newDraft("BUY"))[0]).toBe(PRODUCT_MISSING);
    expect(draftProblems(newDraft("BUY", { product: "MIS" }))).not.toContain(PRODUCT_MISSING);
    expect(PRODUCT_UNSET_TEXT).toMatch(/^Pick MIS or NRML/);
    expect(pageSource).toMatch(/ticketPrefill\(useTradingDefaults/);
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

  it("T1-7 / TF-1: the terminal chart's timeframe control is the shared TimeframePicker", () => {
    expect(embeddedSource).toContain("<TimeframePicker");
    expect(panelsSource).toContain("<EmbeddedChartV2");
  });
});

describe("unbuilt panels stay hidden (R6, FN0 25 Oct)", () => {
  it("Bots & signals and Payoff are never in the Panels menu, never on the phone, never opened, and a stored open one reads closed", () => {
    expect([...HIDDEN_PANELS].sort()).toEqual(["bots", "payoff"]);
    expect(CHOOSER_PANEL_IDS).not.toContain("bots");
    expect(CHOOSER_PANEL_IDS).not.toContain("payoff");
    expect(CHOOSER_PANEL_IDS.length).toBe(PANEL_IDS.length - 2);
    expect(PHONE_TABS.map((t) => t.key)).not.toContain("bots");
    const base = PRESETS.option_seller.panels;
    expect(openPanel(base, "bots")).toEqual([...base]);
    expect(openPanel(base, "payoff")).toEqual([...base]);
    const stored = { layouts: { mine: { name: "Mine", underlying: null, panels: [
      { id: "chart-1", x: 0, y: 0, w: 6, h: 8, open: true }, { id: "bots", x: 6, y: 0, w: 6, h: 6, open: true },
      { id: "payoff", x: 0, y: 8, w: 6, h: 6, open: true }] } }, active: "mine" };
    const read = readPrefs(stored);
    const panels = read.layouts.mine.panels;
    expect(panels.filter((x) => x.open).map((x) => x.id)).toEqual(["chart-1"]);
  });
});
