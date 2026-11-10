import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import appSource from "../App.tsx?raw";
import signInSource from "../auth/SignInScreen.tsx?raw";
import { callWords } from "../signals/language";
import { chartRange } from "./GuestChart";
import linkSource from "./LookInsideLink.tsx?raw";
import { LookInsideView } from "./LookInsidePage";
import {
  FOOTER_LINE, dayText, guestSnapshot, lakhText, pctText, resetGuestSnapshot, signUpLabel, stepLabel, type GuestIndex, type GuestSnapshot,
} from "./lookInside";

/** M95 PR-5: the guest view (made-up snapshot; the real one is built on the server from stored index data). */

function index(name: string, change: number, withChain: boolean): GuestIndex {
  const bars: [number, number, number, number][] = Array.from({ length: 40 }, (_, i) => {
    const o = -4 + i * 0.1;
    return [o, o + 0.6, o - 0.5, o + (i % 2 ? 0.3 : -0.2)];
  });
  bars[bars.length - 1] = [0.2, 0.4, -0.3, 0];
  return {
    name,
    today: {
      change_pct: change, range_position_pct: 61.5, direction_daily: "up", direction_weekly: "down (weak)",
      structure_daily: "Structure on the daily chart: up.", structure_weekly: "Structure on the weekly chart: down, weakening.",
      lines: [`${name} closed ${Math.abs(change).toFixed(2)}% ${change > 0 ? "higher" : "lower"} than the day before.`, "Structure on the daily chart: up."],
    },
    chart: {
      timeframe: "1d", first_date: "2026-02-02", last_date: "2026-07-31", bars,
      areas: [
        { role: "demand", kind: "band", state: "fresh", timeframe: "1d", touches: 0, low_pct: -2.4, high_pct: -1.9, words: "Buyer area from the daily chart: untouched since it formed." },
        { role: "supply", kind: "band", state: "tested", timeframe: "1d", touches: 2, low_pct: 1.1, high_pct: 1.6, words: "Seller area from the daily chart: price has come back to it (2 touches)." },
      ],
    },
    chain: withChain ? {
      expiry: "2026-08-06", pcr: 0.84, most_call_oi_steps: 4, most_put_oi_steps: -3,
      rows: [-2, -1, 0, 1, 2].map((s) => ({ steps: s, call_oi_lakh: 10 + s * 2, put_oi_lakh: 12 - s * 2 })),
    } : null,
    report: [`${name} closed lower.`, "Nearest buyer area below: 1.90% away."],
  };
}

const SNAP: GuestSnapshot = {
  version: 1, as_of: "2026-07-31",
  indices: [index("NIFTY 50", 0.82, true), index("BANK NIFTY", -0.4, false)],
  screens: [{ name: "Momentum", definition: "RSI(14) above 60 and close above EMA(close, 20) on the scan's timeframe.", timeframe: "1d",
              results: [{ name: "NIFTY 50", matched: true, note: null }, { name: "BANK NIFTY", matched: false, note: null }] }],
};

const html = (snap: GuestSnapshot | null | undefined, open = true) =>
  renderToStaticMarkup(<MemoryRouter><LookInsideView snap={snap} signUpOpen={open} /></MemoryRouter>);
const readable = (s: string) => s.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

afterEach(() => { resetGuestSnapshot(); vi.unstubAllGlobals(); });

describe("the guest view", () => {
  it("shows Today, the chart, the chain, the screen run and the daily report with the As of line", () => {
    const out = html(SNAP);
    const text = readable(out);
    for (const card of ["today", "chart", "chain", "screens", "report"]) expect(out).toContain(`data-card="${card}"`);
    expect(text).toContain("As of 31 Jul 2026");
    expect(text).toContain("+0.82%");
    expect(text).toContain("BANK NIFTY");
    expect(text).toContain("NIFTY 50: matched");
    expect(text).toContain("BANK NIFTY: no match");
    expect(text).toContain("Nearest buyer area below: 1.90% away.");
    expect(out).toContain('data-steps="0"');
    expect(text).toContain("ATM");
    expect(out.match(/data-candle=/g)).toHaveLength(40);
    expect(out.match(/data-area="(demand|supply)"/g)).toHaveLength(2);
  });

  it("has no price axis and no figure that reads as an index level", () => {
    const out = html(SNAP);
    const text = readable(out).replace(/\b\d{1,2} [A-Z][a-z]{2} \d{4}\b/g, "");          // the dates
    expect(text).not.toMatch(/\b\d{4,}\b/);
    expect(text).not.toMatch(/₹|\bRs\b/);
    expect(out).not.toMatch(/<text\b/);                                                      // nothing written on the drawing
  });

  it("names no other symbol, says nothing like advice and never calls it sample or demo data", () => {
    const text = readable(html(SNAP));
    expect(callWords(text)).toEqual([]);
    expect(text).not.toMatch(/\bsample\b|demo data|\bdemo\b/i);
    expect(text).not.toMatch(/sensex|reliance|finnifty|\bbuy\b|\bsell\b|target|stop[- ]loss/i);
  });

  it("the sticky bar reads the sign-up state; the footer is the M94 sentence", () => {
    expect(readable(html(SNAP, true))).toContain("Create your free account");
    expect(readable(html(SNAP, false))).toContain("Ask for an invite");
    expect(html(SNAP)).toContain("data-sign-up-bar");
    expect(readable(html(SNAP))).toContain(FOOTER_LINE);
    expect(FOOTER_LINE).toBe("Information and education only, not investment advice.");
    expect(signUpLabel(true)).toBe("Create your free account");
  });

  it("an index without stored option rows says so", () => {
    const one = { ...SNAP, indices: [SNAP.indices[1]] };
    expect(readable(html(one))).toContain("The option chain of this day is not stored for BANK NIFTY.");
  });

  it("closed or loading", () => {
    expect(readable(html(null))).toContain("This page is not open yet.");
    expect(html(undefined)).toContain('aria-busy="true"');
    expect(readable(html(null))).toContain(FOOTER_LINE);
  });
});

describe("the helpers", () => {
  it("format dates, %, steps and lakhs", () => {
    expect(dayText("2026-07-31")).toBe("31 Jul 2026");
    expect(dayText("nope")).toBe("");
    expect(pctText(-1.2)).toBe("−1.20%");
    expect(pctText(null)).toBe("-");
    expect(stepLabel(0)).toBe("ATM");
    expect(stepLabel(3)).toBe("+3");
    expect(stepLabel(-2)).toBe("-2");
    expect(lakhText(12.345)).toBe("12.3 L");
  });

  it("the chart range holds the candles, the close and a near area", () => {
    const [lo, hi] = chartRange([[-1, 1, -2, 0.5]], [{ role: "supply", kind: "band", state: "fresh", timeframe: "1d", touches: 0, low_pct: 1.5, high_pct: 2, words: "" },
      { role: "demand", kind: "band", state: "fresh", timeframe: "1d", touches: 0, low_pct: -40, high_pct: -39, words: "" }]);
    expect(lo).toBe(-2);
    expect(hi).toBe(2);
  });

  it("one shared read; a 404 (closed) means no guest view", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await guestSnapshot()).toBeNull();
    expect(await guestSnapshot()).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("a stored day is read once and shared", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(SNAP), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    expect((await guestSnapshot())?.as_of).toBe("2026-07-31");
    expect(String(fetcher.mock.calls[0][0])).toContain("/guest-demo");
  });
});

describe("the wiring", () => {
  it("/look-inside is outside the sign-in gate and lazy-loaded", () => {
    expect(appSource).toContain('const LookInsidePage = lazy(() => import("./lookInside/LookInsidePage"));');
    const route = appSource.indexOf("path={LOOK_INSIDE_PATH}");
    expect(route).toBeGreaterThan(0);
    expect(route).toBeLessThan(appSource.indexOf('<Route path="*" element={<SignInGate>'));
  });

  it("the sign-in screen links it in one line, only while the guest view answers", () => {
    expect(signInSource).toContain("<StatusLink /><SiteLinks /><LookInsideLink /></footer>");
    expect(linkSource).toContain("Look inside without signing in");
    expect(linkSource).toContain("if (!on) return null;");
  });
});
