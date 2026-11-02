import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import cardSource from "./CheckCardV2.tsx?raw";
import { CheckCardV2 } from "./CheckCardV2";
import ticketSource from "./OrderTicketV2.tsx?raw";
import basketSource from "./BasketPanelV2.tsx?raw";
import {
  VISIBLE_LINES, askFirst, assembleLines, editField, factKey, liquidityCheck, markSeen, resetSessionSeen, sessionSeen, statusLines, switchesOf,
  type CheckLine, type Switches, WARNING_IDS,
} from "./checkCard";
import type { RiskLine } from "./insights";
import { cardSurfaces } from "./ticketMetrics";
import type { TradeSettingsView } from "../settings/tradeSettings";

/**
 * M76 MP-2: the ONE check card - each warning type from its source, his switches, accepted today / seen this session,
 * worst first, three lines visible, "ask me" (his choice of an extra tap), Place never disabled, facts only.
 */
const ON: Switches = switchesOf(null);
const warn = (text: string): RiskLine => ({ tone: "warn", text, note: "" });
const info = (text: string): RiskLine => ({ tone: "info", text, note: "" });

const ALL = {
  previewWarnings: ["This contract expires today.", "NIFTY: in the F&O ban list today.", "No live price just now: these figures use the chain's price.",
    "Daily loss used: 60% of your limit.", "Waits for your price 150.00 and is placed when the price reaches it."],
  previewIds: ["expiry_day", "fo_ban", "price_source", "own_limits", "status"],
  screen: { margin: warn("Margin ₹1,12,400 is more than your available ₹96,800."), event: warn("Before the 03 Nov expiry: RBI policy on 01 Nov."),
    risk: warn("Max loss ₹12,000 is 1.2% of your capital, above your 1%."),
    liquidity: { id: "liquidity_limits" as const, severity: "fact" as const, text: "Outside your own limits: bid-ask gap 3.1% is above your 2%.", ask: false } },
  server: [
    { id: "duplicate_order", severity: "mistake" as const, text: "Open: NIFTY 03 NOV 24500 CE buy 2 lots · this order: buy 2 lots" },
    { id: "limit_far_from_ltp", severity: "mistake" as const, text: "Limit 182.00 · LTP 160.40 · 13.5% away · your band 5%" },
    { id: "market_state", severity: "fact" as const, text: "Market: pre-open (09:00-09:15)" },
    { id: "mis_cutoff", severity: "fact" as const, text: "MIS · your square-off 15:10 · 12 min left" },
    // M76 MP-3: structure lines (the server sends them after the facts; the card keeps them last)
    { id: "room", severity: "fact" as const, text: "Next seller area (daily, 24600.00 - 24680.00): 62.00 points above the last close 24538.00 · ATP chart readers" },
    { id: "counter_trend", severity: "fact" as const, text: "Daily trend: down · last structure event: BOS down at 24380.00 · your setup: bullish · ATP chart readers" },
    { id: "trend_conflict", severity: "fact" as const, text: "1-hour trend: down · your setup: bullish · ATP chart readers" },
    { id: "area_conflict", severity: "fact" as const, text: "Last close 24538.00 inside a seller area (1-hour, 24520.00 - 24560.00) · your setup: bullish · ATP chart readers" },
    { id: "strike_in_area", severity: "fact" as const, text: "Short 24500.00 PE inside a buyer area (daily, 24420.00 - 24520.00) · ATP chart readers" },
  ],
};

describe("M76 MP-2: every warning type reaches the one card from its source", () => {
  it("each type, once, worst first (the three pure mistakes, then the facts)", () => {
    const lines = assembleLines({ ...ALL, switches: ON });
    expect(lines.map((l) => l.id)).toEqual(["duplicate_order", "limit_far_from_ltp", "margin_short", "market_state", "expiry_day", "mis_cutoff",
      "fo_ban", "event", "price_source", "liquidity_limits", "own_limits", "own_limits",
      "counter_trend", "trend_conflict", "area_conflict", "strike_in_area", "room"]);
    expect(new Set(lines.map((l) => l.id))).toEqual(new Set(WARNING_IDS));
    expect(lines.slice(0, 3).every((l) => l.severity === "mistake")).toBe(true);
    expect(lines.some((l) => l.text.startsWith("Waits for your price"))).toBe(false);          // a status line, not a warning
    expect(statusLines(ALL.previewWarnings, ALL.previewIds)).toEqual(["Waits for your price 150.00 and is placed when the price reaches it."]);
  });

  it("information lines (info tone) are never warnings", () => {
    const lines = assembleLines({ screen: { margin: info("Margin ₹10,000 · available ₹96,800."), event: info("No event in your events calendar.") }, switches: ON });
    expect(lines).toEqual([]);
  });

  it("a switch off removes its type; the rest stay", () => {
    const off = { ...ON, fo_ban: { on: false, ask: false }, duplicate_order: { on: false, ask: false } };
    const ids = assembleLines({ ...ALL, switches: off }).map((l) => l.id);
    expect(ids).not.toContain("fo_ban");
    expect(ids).not.toContain("duplicate_order");
    expect(ids).toContain("event");
  });

  it("his saved switches and the owner's start states come from his Trade settings", () => {
    const view = { admin: false, page: "", sections: [{ id: "warnings", title: "Warnings", today: null, fields: [
      { key: "warnings.event", kind: "switch", value: false, saved: true, default: true },
      { key: "warnings.event_ask", kind: "switch", value: null, saved: false, default: false },
      { key: "warnings.fo_ban_ask", kind: "switch", value: true, saved: true, default: false },
    ] }] } as unknown as TradeSettingsView;
    const sw = switchesOf(view);
    expect(sw.event).toEqual({ on: false, ask: false });
    expect(sw.fo_ban).toEqual({ on: true, ask: true });
    expect(sw.market_state).toEqual({ on: true, ask: false });
  });

  it("accepted today for this contract, muted here, or seen this session: not shown again", () => {
    const lines = assembleLines({ ...ALL, switches: ON, accepted: ["market_state"], hidden: new Set(["event"]),
      seen: new Set([factKey({ id: "fo_ban", text: "NIFTY: in the F&O ban list today." })]) });
    const ids = lines.map((l) => l.id);
    expect(ids).not.toContain("market_state");
    expect(ids).not.toContain("event");
    expect(ids).not.toContain("fo_ban");
  });

  it("the same fact from two sources is one line", () => {
    const twice = assembleLines({ previewWarnings: ["This contract expires today."], previewIds: ["expiry_day"],
      server: [{ id: "expiry_day", severity: "fact", text: "This contract expires today." }], switches: ON });
    expect(twice).toHaveLength(1);
  });

  it("a preview without ids (older server) keeps every text as a plain line - nothing is dropped", () => {
    expect(assembleLines({ previewWarnings: ["This contract expires today."], previewIds: null, switches: ON })).toEqual([]);
    expect(statusLines(["This contract expires today."], null)).toEqual(["This contract expires today."]);
  });

  it("liquidity: only against his own limits, never ATP's comparison", () => {
    const thin = { label: "24500 CE", bid: 10, ask: 12, oi: 900, volume: 10, onScreen: true } as never;
    expect(liquidityCheck([thin], null)).toBeNull();
    expect(liquidityCheck([thin], { on: true, max_spread_pct: 2, min_oi: null } as never)?.text).toMatch(/^Outside your own limits: bid-ask gap/);
    expect(liquidityCheck([thin], { on: false, max_spread_pct: 2, min_oi: null } as never)).toBeNull();
  });
});

describe("M76 MP-2: facts only - no advice, no judgement", () => {
  it("no line text carries advice or judgement words", () => {
    for (const l of assembleLines({ ...ALL, switches: ON })) {
      expect(l.text).not.toMatch(/\b(risky|dangerous|should|avoid|recommend|bad|good idea|don't|do not)\b/i);
      expect(l.text).toMatch(/\d|today|pre-open|ban|live price/);
    }
  });
});

describe("M76 MP-2: one compact card", () => {
  const lines = assembleLines({ ...ALL, switches: ON });
  const html = (over: Partial<Parameters<typeof CheckCardV2>[0]> = {}) => renderToStaticMarkup(
    <CheckCardV2 testId="ticket" lines={lines} expanded={false} onExpand={() => undefined} onEdit={() => undefined} onDontShow={() => undefined} {...over} />);

  it("one card, three lines visible, the rest behind 'N more'; Edit and Don't show again on each", () => {
    const out = html();
    expect(out.match(/data-check-card=/g)).toHaveLength(1);
    expect(out.match(/data-check-line=/g)).toHaveLength(VISIBLE_LINES);
    expect(out).toContain(`${lines.length - VISIBLE_LINES} more`);
    expect(out.match(/data-check-edit=/g)).toHaveLength(VISIBLE_LINES);
    expect(out.match(/data-check-mute=/g)).toHaveLength(VISIBLE_LINES);
    expect(html({ expanded: true }).match(/data-check-line=/g)).toHaveLength(lines.length);
  });

  it("no pop-up, no modal, never fixed over Place; no button of the card can disable Place", () => {
    for (const bad of ["role=\"dialog\"", "alertdialog", "Dialog", "disabled"]) expect(cardSource).not.toContain(bad);
    expect(cardSource).not.toMatch(/className=[^>]*\b(fixed|absolute|sticky)\b/);
    const placeLine = ticketSource.split("\n").find((l) => l.includes('data-ticket="place"'))!;
    expect(placeLine).toContain("disabled={busy || !step.preview.can_place}");                     // unchanged: blocks only
    expect(basketSource).toContain("disabled={busy || !p?.can_execute}");
  });

  it("pending checks: the card says it is still checking; Place does not wait (it only reads what is there)", () => {
    expect(html({ lines: [], pending: 2 })).toContain("Checking…");
    expect(html({ lines: [] })).toContain("No warnings.");
    const place = ticketSource.slice(ticketSource.indexOf("async function place("), ticketSource.indexOf("await api.place"));
    expect(place).not.toMatch(/await\s+(checks|api\.run|checksApi)/);
  });

  it("ask me: Place asks for the lines of types he set to 'ask me', each with Accept and continue", () => {
    const askLines: CheckLine[] = lines.map((l) => (l.id === "fo_ban" ? { ...l, ask: true } : l));
    const need = askFirst(askLines, new Set());
    expect(need.map((l) => l.id)).toEqual(["fo_ban"]);
    expect(askFirst(askLines, new Set([factKey(need[0])]))).toEqual([]);                            // accepted: Place goes on
    const out = renderToStaticMarkup(<CheckCardV2 testId="t" lines={askLines} expanded onExpand={() => undefined} asking={need} onAccept={() => undefined} />);
    expect(out.match(/data-check-accept=/g)).toHaveLength(1);
    expect(out).toContain("Accept and continue");
    expect(askFirst(lines, new Set())).toEqual([]);                                                 // default: no extra tap
  });

  it("Edit goes back to the field the line is about", () => {
    expect(editField("limit_far_from_ltp")).toBe("price");
    expect(editField("mis_cutoff")).toBe("product");
    expect(editField("market_state")).toBeNull();
  });

  it("seen this session: a fact he pressed Place on is not repeated in a later ticket", () => {
    resetSessionSeen();
    markSeen([lines[0]]);
    expect(sessionSeen().has(factKey(lines[0]))).toBe(true);
    resetSessionSeen();
  });

  it("the metric: at most one card per ticket; lines folded past three", () => {
    expect(cardSurfaces(0, 3)).toEqual({ cards: 0, lines: 0, visible: 0 });
    expect(cardSurfaces(5, 3)).toEqual({ cards: 1, lines: 5, visible: 3 });
    expect(cardSurfaces(2, 3)).toEqual({ cards: 1, lines: 2, visible: 2 });
  });
});
