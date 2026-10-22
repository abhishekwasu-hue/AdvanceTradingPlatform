import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SHARE_MS, fetchAnalysesShared, type ChainDish } from "./analyses";
import { DEMO_ANALYSES } from "./analyses.fixture";
import { ChainDishView } from "./ChainDishV2";
import pageSource from "./OptionChainPageV2.tsx?raw";
import slotSource from "./AnalysisSlotV2.tsx?raw";

/** MO4 slice 2: the Option chain "What the chain shows" summary - its look, its shared read and where it is mounted. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(recommend\w*|advice|advise|should|must|buy|buying|sell|selling|reduce|guarantee\w*|best|target\w*|bullish|bearish|stop[- ]?loss)\b/i;

// The backend's own lines for the hand-made chain of backend/tests/test_mo4_markets.py (made-up numbers).
const DISH: ChainDish = {
  title: "What the chain shows",
  note: "Information only: what the chain's numbers show right now. It never says what to trade.",
  points: [
    { key: "pcr_zone", label: "PCR zone", state: "ok", text: "PCR is 0.84, in the low zone (0.70 to 0.90): call open interest is larger than put open interest." },
    { key: "oi_walls", label: "OI walls", state: "ok", text: "The largest put open interest is at 100 and the largest call open interest is at 130; the spot 118.00 is between them." },
    { key: "max_pain", label: "Max pain", state: "ok", text: "Max pain is 120, 2.00 points (1.69%) above the spot 118.00." },
    { key: "iv_change", label: "IV change", state: "ok", text: "ATM IV is 15.00%, up 2.00 points since 09:20 IST (13.00% then)." },
    { key: "expected_move", label: "Expected move", state: "unknown", text: "I don't know the expected move: the ATM call or put has no price." },
  ],
};

afterEach(() => { vi.restoreAllMocks(); });

describe("the summary", () => {
  it("shows the five lines with their labels; a line without its number is muted and says I don't know", () => {
    const html = renderToStaticMarkup(<ChainDishView dish={DISH} loading={false} />);
    const words = text(html);
    expect(words).toContain("What the chain shows");
    for (const p of DISH.points) {
      expect(words).toContain(p.label);
      expect(words).toContain(p.text);
    }
    expect(html).toMatch(/data-point="expected_move" data-state="unknown"/);
    expect(words).toContain("Information only");
    expect(words).not.toMatch(ADVICE);
  });

  it("shows nothing without a summary (the tabs below say why), placeholders while the first read is under way", () => {
    expect(renderToStaticMarkup(<ChainDishView dish={null} loading={false} />)).toBe("");
    expect(renderToStaticMarkup(<ChainDishView dish={null} loading />)).toContain('aria-busy="true"');
  });

  it("is mounted only while `markets_view` is on, above the summary strip; the tabs share its read", () => {
    expect(pageSource).toMatch(/const dishOn = useFeatures\(\)\.on\("markets_view"\)/);
    expect(pageSource.indexOf("<ChainDishV2")).toBeLessThan(pageSource.indexOf("<SummaryStripV2"));
    expect(slotSource).toContain("load={fetchAnalysesShared}");
  });
});

describe("the shared read", () => {
  it("one read serves every reader of the same symbol and expiry within a few seconds; a failure is not kept", async () => {
    let clock = 1_000_000;
    const now = () => clock;
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(DEMO_ANALYSES), { status: 200 }));
    const a = fetchAnalysesShared("NIFTY", "2026-10-27", now);
    const b = fetchAnalysesShared("nifty", "2026-10-27", now);
    expect(a).toBe(b);
    await a;
    expect(spy).toHaveBeenCalledTimes(1);
    fetchAnalysesShared("NIFTY", "2026-11-03", now);                       // another expiry: its own read
    expect(spy).toHaveBeenCalledTimes(2);
    clock += SHARE_MS;
    await fetchAnalysesShared("NIFTY", "2026-10-27", now);                  // later: read again
    expect(spy).toHaveBeenCalledTimes(3);
    spy.mockImplementation(async () => new Response("{}", { status: 500 }));
    clock += SHARE_MS;
    await fetchAnalysesShared("BANKNIFTY", null, now).catch(() => undefined);
    await fetchAnalysesShared("BANKNIFTY", null, now).catch(() => undefined);
    expect(spy).toHaveBeenCalledTimes(5);
  });
});
