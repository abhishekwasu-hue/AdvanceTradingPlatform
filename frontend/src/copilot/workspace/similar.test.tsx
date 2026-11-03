import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Annotations } from "./annotations";
import similarSource from "./SimilarSetupsV2.tsx?raw";
import teachSource from "./TeachV2.tsx?raw";
import placeSource from "./PlanPlaceV2.tsx?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { keyWords, similarKey, type SimilarApi } from "./similar";
import { SimilarSetupsV2 } from "./SimilarSetupsV2";
import { teachArea, teachAt } from "./teach";
import { TeachV2 } from "./TeachV2";
import { glossary } from "../../ui/glossary";

/** M76 MP-14: L-1 similar past setups by area context (recorded at entry), L-2 teach-on-chart from the one glossary. */
const area = (role: "demand" | "supply", low: number, high: number, extra: Partial<Annotations["areas"][number]> = {}) => ({
  role, side: role === "demand" ? "buyer" as const : "seller" as const, low, high, low_text: low.toFixed(2), high_text: high.toFixed(2),
  timeframe: "1h", state: "tested", state_words: "price has come back to it", tests: 1, words: "Area.", ...extra,
});
const A: Annotations = {
  source: "atp", source_words: "", timeframe: "15m", as_of: null, last_close: 96,
  areas: [area("demand", 90, 100), area("demand", 95, 97, { state: "fresh", state_words: "untouched since it formed" }), area("supply", 104, 106)],
  swings: [{ label: "HL", kind: "L", time: 10, price: 96, price_text: "96.00", timeframe: "15m" }],
  events: [{ type: "CHOCH", name: "CHoCH", direction: "up", time: 30, swing_time: 20, price: 103, price_text: "103.00", timeframe: "15m", words: "x" }],
  trend: [], next_areas: { above: null, below: null, inside: null },
  divergence: { side: "bullish", role: "demand", timeframe: "15m", first: { time: 5, price: 96.5 }, second: { time: 25, price: 95.5 }, words: "x" },
  level: null, summary: [], not_given: [],
};
A.next_areas = { above: A.areas[2], below: null, inside: A.areas[0] };

describe("L-1 similar past setups", () => {
  it("the match key is the area the last close is inside (source, its timeframe, side, state); none = no key", () => {
    expect(similarKey(A)).toEqual({ source: "atp", tf: "1h", side: "buyer", state: "tested" });
    expect(keyWords(similarKey(A)!)).toBe("Buyer area · 1h · tested");
    expect(similarKey({ ...A, next_areas: { ...A.next_areas, inside: null } })).toBeNull();
    expect(similarKey(null)).toBeNull();
  });
  it("says so when the price is inside no area; read-only, no call words", () => {
    const api: SimilarApi = { read: () => new Promise(() => undefined), record: () => Promise.resolve({ stored: 0 }) };
    expect(renderToStaticMarkup(<SimilarSetupsV2 annotations={{ ...A, next_areas: { ...A.next_areas, inside: null } }} api={api} />))
      .toContain("not inside an area");
    expect(renderToStaticMarkup(<SimilarSetupsV2 annotations={A} api={api} />)).toContain('data-similar="loading"');
    expect(similarSource).not.toMatch(/\b(buy|sell|target|stop-loss)\b/i);
  });
  it("the area is recorded after a fill from the workspace, by the server, and a failed record never stops the fill (the order path unchanged)", () => {
    expect(placeSource).toContain("onPlaced={(r) => { setPlaced(r); setArmed(null); recordArea([r.trade_id]); }}");
    expect(placeSource).toContain("onPlaced={(r) => recordArea(r.legs.map((l) => l.trade_id))}");
    expect(placeSource).toContain("similarApi.record(ids, chart.instrument, chart.tf).catch(() => undefined)");
    expect(workspaceSource).toContain("<SimilarSetupsV2 annotations={annotations} />");
  });
});

describe("L-2 teach-on-chart", () => {
  it("a label on the tapped bar first: CHoCH, a swing, a divergence end; each from the one glossary", () => {
    expect(teachAt(A, 30, 0)).toMatchObject({ kind: "event", title: "CHoCH · 15m · 103.00", meaning: glossary("choch").definition });
    expect(teachAt(A, 10, 0)).toMatchObject({ kind: "swing", title: "HL · 15m · 96.00", meaning: glossary("swing-hl").definition });
    expect(teachAt(A, 25, null)?.kind).toBe("divergence");
  });
  it("else the narrowest area holding the price, with its state in the reading's own words; nothing = null", () => {
    const t = teachAt(A, 99, 96)!;
    expect(t.kind).toBe("area");
    expect(t.title).toContain("95.00 - 97.00");
    expect(t.meaning).toBe(glossary("buyer-area").definition);
    expect(t.state).toBe("Fresh area: untouched since it formed");
    expect(teachArea(area("supply", 1, 2, { state: null, state_words: null })).state).toBeNull();
    expect(teachAt(A, 99, 102)).toBeNull();
    expect(teachAt(null, 10, 96)).toBeNull();
  });
  it("lists the areas as buttons too; no advice, nothing placed", () => {
    const html = renderToStaticMarkup(<TeachV2 engine={null} annotations={A} />);
    expect(html).toContain("Tap an area or a label on the chart for its meaning.");
    expect(html.match(/data-teach-area=/g)?.length).toBe(3);
    expect(teachSource).not.toMatch(/\/order|placeOrder|request\(/);
    expect(workspaceSource).toContain("<TeachV2 engine={engine} annotations={annotations} />");
  });
});

describe("M76 MP-14 teach-on-chart: no repeated state", () => {
  it("state words that only repeat the state's name give the glossary meaning instead", async () => {
    const { teachArea } = await import("./teach");
    const base = { side: "buyer", low_text: "24,000.00", high_text: "24,060.00", timeframe: "15m", state: "tested" } as never;
    const t = teachArea({ ...(base as object), state_words: "tested" } as never);
    expect(t.state ?? "").not.toMatch(/:\s*tested$/i);
    const own = teachArea({ ...(base as object), state_words: "tested twice, held" } as never);
    expect(own.state).toMatch(/tested twice, held/);
  });
});
