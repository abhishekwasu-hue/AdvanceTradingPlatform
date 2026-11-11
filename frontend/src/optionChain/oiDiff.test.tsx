import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { formatContracts, formatCount, formatLakhs } from "../ui/format";
import { leg, liveChain, rows } from "./chain.fixture";
import { DEFAULT_COLUMNS, formatOi, readColumns, COLUMNS_KEY } from "./model";
import { buildupOf, diffWordOf, oiDiffRows } from "./oiDiff";
import { OiDiffSideCell, OiDiffTableBody } from "./OiDiffTableV2";
import ChainTableV2 from "./ChainTableV2";
import pageSource from "./OptionChainPageV2.tsx?raw";
import diffSource from "./OiDiffTableV2.tsx?raw";
import terminalSource from "../terminal/TerminalPageV2.tsx?raw";
import { ticketTargetOf } from "../fno/ChainTradingV2";
import { TicketBody } from "../fno/OrderTicketV2";
import { LOTS_UNSET_TEXT, firstMissing, newDraft } from "../fno/ticketModel";
import type { ChainRow } from "./types";

/** M96 L1 / O1 / Q3 (owner): the OI-difference table, the ticket opened from it, and OI in lakhs. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;

function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}

describe("M96 L1: OI difference per strike (the fixture chain)", () => {
  it("call change, put change and put minus call for every strike, in the chain's order", () => {
    const d = oiDiffRows(rows());
    // fixture: call change 5,000 x (i - 3), put change -2,000 x i
    expect(d.map((r) => r.strike)).toEqual([24200, 24300, 24400, 24500, 24600, 24700, 24800]);
    expect(d.map((r) => r.call)).toEqual([-15000, -10000, -5000, 0, 5000, 10000, 15000]);
    expect(d.map((r) => r.put)).toEqual([-0, -2000, -4000, -6000, -8000, -10000, -12000]);
    expect(d.map((r) => r.diff)).toEqual([15000, 8000, 1000, -6000, -13000, -20000, -27000]);
    for (const [i, r] of rows().entries()) expect(d[i].diff).toBe((r.put.oi_change as number) - (r.call.oi_change as number));
    expect(d.map((r) => r.word)).toEqual(["Calls cut more", "Calls cut more", "Calls cut more", "Puts cut more",
      "Calls added more", "Calls added more", "Calls added more"]);
  });

  it("a missing change stays missing: no difference, no word", () => {
    const r: ChainRow = { strike: 100, call: leg({ oi_change: null }), put: leg({ oi_change: 500 }) };
    expect(oiDiffRows([r])[0]).toMatchObject({ call: null, put: 500, diff: null, word: null });
  });

  it("the difference's plain word", () => {
    expect(diffWordOf(1000, 3000)).toBe("Puts added more");
    expect(diffWordOf(3000, 1000)).toBe("Calls added more");
    expect(diffWordOf(-3000, -1000)).toBe("Calls cut more");
    expect(diffWordOf(-1000, -3000)).toBe("Puts cut more");
    expect(diffWordOf(-500, 2000)).toBe("Puts added more");
    expect(diffWordOf(2000, -500)).toBe("Calls added more");
    expect(diffWordOf(2000, 2000)).toBe("Even");
    expect(diffWordOf(null, 2000)).toBeNull();
  });

  it("the tag truth table: price change x OI change, per side", () => {
    expect(buildupOf(5, 1000)).toBe("Long buildup");
    expect(buildupOf(-5, 1000)).toBe("Short buildup");
    expect(buildupOf(5, -1000)).toBe("Short covering");
    expect(buildupOf(-5, -1000)).toBe("Long unwinding");
    for (const [p, o] of [[0, 1000], [5, 0], [null, 1000], [5, null], [undefined, 1000], [Number.NaN, 1000]] as const) {
      expect(buildupOf(p, o)).toBeNull();
    }
  });

  it("each side's tag is read from that side's own price and OI change; no price change is a dash", () => {
    const r: ChainRow = { strike: 24500, call: leg({ oi_change: 20_000, ltp_change: -3 }), put: leg({ oi_change: -10_000, ltp_change: 4 }) };
    const d = oiDiffRows([r])[0];
    expect([d.callTag, d.putTag]).toEqual(["Short buildup", "Short covering"]);
    const html = renderToStaticMarkup(<OiDiffTableBody rows={[r]} atm={24500} />);
    expect(html).toContain('data-oi-tag="Short buildup"');
    expect(html).toContain('data-oi-tag="Short covering"');
    // the fixture has no price change: every tag is "–" (nothing is worked out from another baseline)
    const plain = renderToStaticMarkup(<OiDiffTableBody rows={rows()} atm={24500} />);
    expect(plain.match(/data-oi-tag="none"/g)).toHaveLength(14);
    expect(plain).not.toMatch(/data-oi-tag="(Long|Short)/);
  });

  it("the table: four columns, lakhs by default, the ATM row marked, no advice words", () => {
    const html = renderToStaticMarkup(<OiDiffTableBody rows={rows()} atm={24500} />);
    expect(text(html)).toContain("Call OI chg Strike Put OI chg Put − Call");
    expect(html).toMatch(/data-oi-diff-row="24200"[\s\S]*?−0.15 L[\s\S]*?24,200[\s\S]*?0.00 L[\s\S]*?\+0.15 L[\s\S]*?Calls cut more/);
    expect(html).toMatch(/data-oi-diff-row="24500" data-atm="true"/);
    expect(html).toMatch(/<th scope="row"[^>]*>24,500<span class="block text-t12 font-semibold text-fg">ATM/);
    const contracts = renderToStaticMarkup(<OiDiffTableBody rows={rows()} atm={24500} units="contracts" />);
    expect(contracts).toContain("+15,000");
    expect(text(html)).not.toMatch(/\b(buy|sell|should|recommend\w*|advice|target)\b/i);
    expect(diffSource).toContain('<details className="text-t12 text-fg-muted" data-oi-diff-help>');   // the explanation sits in Details
  });
});

describe("M96 O1: a tap in the OI-difference table opens that contract's ticket - contract only", () => {
  it("a call or put cell calls the chain's own leg handler with that row and side (off: plain cells)", () => {
    const pick = vi.fn();
    const r = rows()[3];
    const d = oiDiffRows([r])[0];
    for (const side of ["call", "put"] as const) {
      const btn = elements(OiDiffSideCell({ side, d, row: r, onPickLeg: pick })).find((e) => e.type === "button")!;
      expect((btn.props as Record<string, unknown>)["data-oi-diff-cell"]).toBe(`24500|${side === "call" ? "CE" : "PE"}`);
      expect(String((btn.props as { className: string }).className)).toContain("min-h-11");          // 44 px target
      (btn.props as { onClick: () => void }).onClick();
    }
    expect(pick.mock.calls).toEqual([[r, "call"], [r, "put"]]);
    expect(elements(OiDiffSideCell({ side: "call", d, row: r })).some((e) => e.type === "button")).toBe(false);
  });

  it("the page wires the table to the SAME path as the chain table (action sheet -> ticket / Terminal order panel)", () => {
    expect(pageSource).toMatch(/<OiDiffTableV2 [^>]*onPickLeg=\{trading\.onPickLeg\}/);
    expect(pageSource).toMatch(/<ChainLadderV2 [\s\S]*?onPickLeg=\{trading\.onPickLeg\}/);
    expect(pageSource).toContain("const trading = useChainTrading({ symbol, live, prefs, onPrefs, expiries: expiryOptions, signal, onTicket });");
    expect(terminalSource).toContain("onTicket={orderPanelShown ? (t) => setTicket(t) : undefined}");
  });

  it("the prefill is the contract and its quotes only; the ticket starts with no lots, order type or product (M68)", () => {
    const r = rows()[3];
    const t = ticketTargetOf({ underlying: "NIFTY", expiry: "2026-11-03", strike: r.strike, right: "PE", leg: r.put, spotSymbol: "NIFTY 50" },
      { underlying: "NIFTY", tradingsymbol: "X", exchange: "NFO", instrument_key: "k", expiry: "2026-11-03", strike: r.strike, right: "PE",
        lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 PE", freeze_quantity: null });
    expect(Object.keys(t).sort()).toEqual(["ask", "bid", "expiry", "label", "lotSize", "ltp", "right", "strike", "underlying"]);
    const draft = newDraft("BUY");
    expect([draft.lots, draft.orderType, draft.product]).toEqual([null, null, null]);
    expect(firstMissing(draft)).toBe("lots");
    const html = renderToStaticMarkup(<TicketBody target={t} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="t" saveDefaults
      onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
    expect(html).toMatch(/value=""[^>]*data-ticket="lots"|data-ticket="lots"[^>]*value=""/);
    expect(html).toContain(LOTS_UNSET_TEXT);
    expect(html).toMatch(/disabled=""[^>]*data-ticket="review"|data-ticket="review"[^>]*disabled=""/);   // he presses Place himself
  });
});

describe("M96 Q3: OI, OI change and volume in lakhs (two decimals), with a Contracts choice", () => {
  it("formatLakhs: always lakhs, two decimals, Indian grouping, signed for a change", () => {
    expect(formatLakhs(50_000)).toBe("0.50 L");
    expect(formatLakhs(400_000)).toBe("4.00 L");
    expect(formatLakhs(1_234_567)).toBe("12.35 L");
    expect(formatLakhs(25_000_000)).toBe("250.00 L");
    expect(formatLakhs(1_234_500_000)).toBe("12,345.00 L");
    expect(formatLakhs(-4_500)).toBe("−0.05 L");
    expect(formatLakhs(4_500, { signed: true })).toBe("+0.05 L");
    expect(formatLakhs(-4_500, { signed: true })).toBe("−0.05 L");
    expect(formatLakhs(400, { signed: true })).toBe("0.00 L");
    expect(formatLakhs(0)).toBe("0.00 L");
    expect(formatLakhs(null)).toBe("–");
    expect(formatLakhs(Number.NaN)).toBe("–");
    expect(formatContracts(50_000)).toBe("50,000");
    expect(formatContracts(15_000, { signed: true })).toBe("+15,000");
    expect(formatContracts(-4_500, { signed: true })).toBe("−4,500");
    expect(formatContracts(0, { signed: true })).toBe("0");
    expect(formatOi(50_000, undefined)).toBe("0.50 L");
    expect(formatOi(50_000, "contracts")).toBe("50,000");
    expect(formatCount(50_000)).toBe("50,000");                         // other callers keep formatCount as it was
  });

  it("the chain table shows OI, OI change and volume in the chosen unit", () => {
    const html = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} />);
    expect(html).toMatch(/data-col="call-oi"[\s\S]*?1.00 L/);
    expect(html).toContain("+0.15 L");
    expect(html).toContain("0.20 L");                                   // volume 20,000
    const contracts = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={{ ...DEFAULT_COLUMNS, units: "contracts" }} centreKey="k" onPick={noop} />);
    expect(contracts).toContain("1,00,000");
    expect(contracts).toContain("+15,000");
  });

  it("the unit is kept in the columns prefs (lakhs unless Contracts was picked)", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) });
    try {
      expect(readColumns().units).toBe("lakhs");
      store.set(COLUMNS_KEY, JSON.stringify({ units: "contracts" }));
      expect(readColumns().units).toBe("contracts");
      store.set(COLUMNS_KEY, JSON.stringify({ units: "shares" }));
      expect(readColumns().units).toBe("lakhs");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
