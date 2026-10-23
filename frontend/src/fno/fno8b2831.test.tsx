import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { leg } from "../optionChain/chain.fixture";
import type { ChainRow } from "../optionChain/types";
import limitsSource from "../settings/liquidityLimits.ts?raw";
import { LIMITS_OFF, activeLimits, limitsFormOf, limitsOf, readLimits, saveLimits, type LiquidityLimits } from "../settings/liquidityLimits";
import { DefaultsFormV2 } from "../settings/TradingDefaultsV2";
import pageSource from "../settings/TradingDefaultsV2.tsx?raw";
import { EMPTY_DEFAULTS, formOf } from "../settings/tradingDefaults";
import basketSource from "./BasketPanelV2.tsx?raw";
import journalSource from "./JournalDraftsV2.tsx?raw";
import ticketSource from "./OrderTicketV2.tsx?raw";
import { LIMITS_NOTE, LIQUIDITY_NOTE, NO_CHAIN, checkLimits, legLiquidity, limitsText, liquidityLine } from "./riskParts";
import { inlineThemeColours, snapshotFileName } from "./snapshotImage";
import imageSource from "./snapshotImage.ts?raw";

/**
 * FNO-8B-28 (his own liquidity limits, OFF by default; information only) and FNO-8B-31 (the journal chart's "Download
 * image", saved on his device; nothing stored on the server). Made-up numbers.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const CALL_WORDS = /\b(buy|sell|target|stop[- ]?loss|should|recommend\w*|advice)\b/i;

/** Ten strikes: tight quotes and deep OI near the middle, the 25,400 call thin (as in slice4.test.tsx). */
function ladder(): ChainRow[] {
  return Array.from({ length: 10 }, (_, i) => {
    const strike = 24500 + i * 100;
    const thin = strike === 25400;
    return {
      strike,
      call: leg({ ltp: 100 - i * 9, bid: thin ? 2 : 99.5 - i * 9, ask: thin ? 8 : 100.5 - i * 9, oi: thin ? 900 : 200_000 + i * 1000, volume: thin ? 0 : 50_000 }),
      put: leg({ ltp: 20 + i * 9, bid: 19.5 + i * 9, ask: 20.5 + i * 9, oi: 150_000, volume: 40_000 }),
    };
  });
}
const ON = (over: Partial<LiquidityLimits> = {}): LiquidityLimits => ({ on: true, max_spread_pct: 2, min_oi: 100_000, ...over });

describe("FNO-8B-28 his own liquidity limits: the stored value", () => {
  it("is off and empty by default, and a server value is read defensively", () => {
    expect(readLimits(undefined)).toEqual(LIMITS_OFF);
    expect(readLimits({ liquidity: { on: false, max_spread_pct: null, min_oi: null } })).toEqual(LIMITS_OFF);
    expect(readLimits({ liquidity: { on: true, max_spread_pct: 2.5, min_oi: 5000 } })).toEqual({ on: true, max_spread_pct: 2.5, min_oi: 5000 });
    expect(readLimits({ liquidity: { on: true, max_spread_pct: 0, min_oi: 1.5 } })).toEqual(LIMITS_OFF);       // nothing valid -> off
    expect(readLimits({ liquidity: { on: "yes", max_spread_pct: 3, min_oi: null } })).toEqual({ on: false, max_spread_pct: 3, min_oi: null });
    expect(activeLimits(LIMITS_OFF)).toBeNull();
    expect(activeLimits({ on: false, max_spread_pct: 3, min_oi: null })).toBeNull();                         // kept, but off
    expect(activeLimits(ON())).toEqual(ON());
  });
  it("the page's fields become limits with plain problems; on needs one limit", () => {
    expect(limitsOf({ on: true, max_spread_pct: "2.5", min_oi: "10,000" })).toEqual({ value: { on: true, max_spread_pct: 2.5, min_oi: 10000 }, problems: [] });
    expect(limitsOf({ on: false, max_spread_pct: "", min_oi: "" })).toEqual({ value: LIMITS_OFF, problems: [] });
    expect(limitsOf({ on: true, max_spread_pct: "", min_oi: "" }).problems).toEqual(["Set a max bid-ask gap or a min open interest to use your own limits."]);
    expect(limitsOf({ on: true, max_spread_pct: "0", min_oi: "" }).problems).toEqual(["Max bid-ask gap: a number above 0, up to 100."]);
    expect(limitsOf({ on: true, max_spread_pct: "101", min_oi: "" }).problems).toHaveLength(1);
    expect(limitsOf({ on: true, max_spread_pct: "", min_oi: "2.5" }).problems).toEqual(["Min open interest: a whole number from 1."]);
    expect(limitsOf({ on: false, max_spread_pct: "abc", min_oi: "7" })).toEqual({ value: { on: false, max_spread_pct: null, min_oi: 7 }, problems: [] });
    expect(limitsFormOf({ on: true, max_spread_pct: 2, min_oi: null })).toEqual({ on: true, max_spread_pct: "2", min_oi: "" });
  });
  it("saves to its own namespace with the version, and only the three fields", async () => {
    const seen: { path: string; body: string }[] = [];
    const fetcher = (async (path: string, init?: RequestInit) => {
      seen.push({ path, body: String(init?.body) });
      return { value: { liquidity: { on: true, max_spread_pct: 2, min_oi: null } }, version: 4 };
    }) as never;
    const saved = await saveLimits({ on: true, max_spread_pct: 2, min_oi: null }, 3, fetcher);
    expect(seen).toEqual([{ path: "/me/prefs/fno?version=3", body: JSON.stringify({ liquidity: { on: true, max_spread_pct: 2, min_oi: null } }) }]);
    expect(saved).toEqual({ value: { on: true, max_spread_pct: 2, min_oi: null }, version: 4 });
  });
});

describe("FNO-8B-28 the liquidity line with his own limits", () => {
  it("off (the default): the line is exactly the comparison with the chain, as before", () => {
    const l = legLiquidity(ladder(), 24600, "CE", "24600 CE");
    expect(liquidityLine([l], true, null)).toEqual(liquidityLine([l]));
    expect(liquidityLine([l], true, { on: false, max_spread_pct: 0.5, min_oi: 1 })).toEqual(liquidityLine([l]));
    expect(liquidityLine([l])?.note).toBe(LIQUIDITY_NOTE);
  });
  it("on: says the strike is inside his limits, naming them", () => {
    const line = liquidityLine([legLiquidity(ladder(), 24600, "CE", "24600 CE")], true, ON());
    expect(line).toMatchObject({ tone: "info", note: LIMITS_NOTE });
    expect(line?.text).toBe("Liquidity: bid-ask gap 1.00 (1.1% of the price), open interest 2,01,000, volume 50,000 today. "
      + "Inside your own limits (bid-ask gap up to 2%, open interest at least 1,00,000).");
  });
  it("on: says what is outside his limits, even where the chain comparison finds nothing thin", () => {
    const l = legLiquidity(ladder(), 24600, "CE", "24600 CE");
    const line = liquidityLine([l], true, ON({ max_spread_pct: 0.5, min_oi: 300_000 }));
    expect(line?.tone).toBe("warn");
    expect(line?.text).toContain("Outside your own limits: bid-ask gap 1.1% is above your 0.5%; open interest 2,01,000 is below your 3,00,000.");
    expect(checkLimits(l, ON({ max_spread_pct: 1.25, min_oi: null }))).toEqual({ outside: [], unknown: [] });
    expect(limitsText(ON({ max_spread_pct: 1.25, min_oi: null }))).toBe("bid-ask gap up to 1.25%");
  });
  it("on: a basket names each leg outside, or says every leg is inside", () => {
    const rows = ladder();
    const inside = liquidityLine([legLiquidity(rows, 24600, "CE", "A"), legLiquidity(rows, 24700, "PE", "B")], true, ON({ max_spread_pct: 5 }));
    expect(inside?.text).toContain("Every leg is inside your own limits (bid-ask gap up to 5%, open interest at least 1,00,000).");
    const out = liquidityLine([legLiquidity(rows, 24600, "CE", "A"), legLiquidity(rows, 25400, "CE", "B")], true, ON({ max_spread_pct: 5 }));
    expect(out?.text).toMatch(/Outside your own limits: B: bid-ask gap 120\.0% is above your 5%; open interest 900 is below your 1,00,000\.$/);
    expect(out?.text).not.toContain("Outside your own limits: A:");
  });
  it("on: says plainly when a limit cannot be checked (no quote, not on screen, no chain)", () => {
    const rows = ladder();
    rows[1].call = leg({ ltp: 5, bid: null, ask: 6, oi: 150_000 });
    expect(liquidityLine([legLiquidity(rows, 24600, "CE", "x")], true, ON())?.text)
      .toContain("Your own limits (bid-ask gap up to 2%, open interest at least 1,00,000) cannot be fully checked here: the bid-ask gap is not known.");
    expect(liquidityLine([legLiquidity(rows, 26000, "PE", "x")], true, ON())?.text).toContain("the bid-ask gap and the open interest are not known.");
    expect(liquidityLine([legLiquidity(rows, 24600, "CE", "A"), legLiquidity(rows, 26000, "PE", "B")], true, ON())?.text)
      .toContain("cannot be fully checked here: not known for A (the bid-ask gap), B (the bid-ask gap and the open interest).");
    expect(liquidityLine([], false, ON())).toEqual({ tone: "info", note: LIMITS_NOTE,
      text: `${NO_CHAIN} Your own limits (bid-ask gap up to 2%, open interest at least 1,00,000) cannot be checked here.` });
  });
  it("is words only: no call words, nothing reads it to place or block, exits untouched", () => {
    const rows = ladder();
    for (const limits of [ON(), ON({ max_spread_pct: 0.1, min_oi: 10_000_000 })]) {
      const line = liquidityLine([legLiquidity(rows, 24600, "CE", "A"), legLiquidity(rows, 25400, "CE", "B")], true, limits);
      expect(line?.text).not.toMatch(CALL_WORDS);
    }
    // The ticket and the basket only pass the limits into the line; the place / execute buttons do not read them.
    expect(ticketSource).toContain("liquidityLine([legLiquidity(chainRows, target.strike, target.right, target.label ?? \"This strike\")], true, limits)");
    expect(ticketSource).toContain("useLiquidityLimits(open && features.on(\"ui_v2\"))");
    expect(basketSource).toContain(", true, limits) : null");
    for (const src of [ticketSource, basketSource]) {
      expect(src.match(/limits/g)?.length).toBeLessThanOrEqual(8);
      expect(src).not.toMatch(/disabled=\{[^}]*limits/);
    }
    expect(limitsSource).not.toMatch(/\/paper\/fno\/(order|basket)|\/positions|\/exit/);
  });
});

describe("FNO-8B-28 the setting on Settings → My trading defaults", () => {
  const form = formOf(EMPTY_DEFAULTS);
  const page = (limits?: { on: boolean; max_spread_pct: string; min_oi: string }) => text(renderToStaticMarkup(<MemoryRouter>
    <DefaultsFormV2 form={form} onChange={() => undefined} approvalAllowed={false} busy={false} note={null} onSave={() => undefined}
      onClear={() => undefined} limits={limits} onLimits={() => undefined} /></MemoryRouter>));
  it("off by default: the card explains the usual check and shows only the switch", () => {
    const off = page(limitsFormOf(LIMITS_OFF));
    expect(off).toContain("My own liquidity limits (F&O)");
    expect(off).toContain("Use my own limits too");
    expect(off).toContain("Information only: it never holds back an order or an exit.");
    expect(off).not.toContain("Max bid-ask gap %");
  });
  it("on: the two limits, with their ranges", () => {
    const on = page({ on: true, max_spread_pct: "2", min_oi: "" });
    expect(on).toContain("Max bid-ask gap %");
    expect(on).toContain("Min open interest");
    expect(on).toContain("Above 0, up to 100.");
  });
  it("is saved with the same Save button and cleared by Clear all", () => {
    expect(page()).not.toContain("My own liquidity limits");
    expect(pageSource).toContain("const l = await saveLimits(own.value, limitsVersion);");
    expect(pageSource).toContain("save(formOf(EMPTY_DEFAULTS), limitsFormOf(LIMITS_OFF), true)");
  });
});

describe("FNO-8B-31 the journal chart's Download image", () => {
  it("a plain file name: the symbol, 'trade-chart' and the closing day (IST), never an id", () => {
    expect(snapshotFileName("NIFTY 50", Date.parse("2026-10-16T08:50:00Z"))).toBe("nifty-50-trade-chart-2026-10-16.png");
    expect(snapshotFileName("M&M", Date.parse("2026-10-16T20:00:00Z"))).toBe("mandm-trade-chart-2026-10-17.png");     // after IST midnight
    expect(snapshotFileName("", NaN)).toBe("chart-trade-chart.png");
    expect(journalSource).toContain("snapshotFileName(symbol, closed)");
    expect(journalSource).not.toMatch(/fileName: [^,]*draft\.(key|trade_ids)/);
  });
  it("writes the theme colours into the image (a stand-alone image has no page styles)", () => {
    const svg = '<svg><line stroke="rgb(var(--chart-grid))"/><rect fill="rgb(var(--surface-1))"/><text fill="rgb(var(--unknown))"/></svg>';
    const out = inlineThemeColours(svg, (t) => ({ "chart-grid": " 26 35 51", "surface-1": "255 255 255" } as Record<string, string>)[t] ?? "");
    expect(out).toBe('<svg><line stroke="rgb(26 35 51)"/><rect fill="rgb(255 255 255)"/><text fill="rgb(var(--unknown))"/></svg>');
  });
  it("is saved on his device only: no upload, no server write; the chart is still a redraw on his tap", () => {
    expect(imageSource).not.toMatch(/fetch\(|request\(|method:|upload\(/);
    expect(imageSource).toContain("a.download = fileName");
    expect(journalSource).not.toMatch(/method: "POST"|upload/);
    expect(journalSource).toContain('data-draft-chart="download"');
    expect(journalSource).toContain("Download image");
  });
});
