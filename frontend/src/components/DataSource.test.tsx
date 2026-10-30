import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

/** M55 T3 (data honesty): sample data is never the default; it sits behind a labelled switch and shows a SAMPLE chip. */
const ctx = vi.hoisted(() => ({ role: "OWNER" as string, flags: [] as string[] }));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { role: ctx.role } }),
  useOptionalAuth: () => ({ user: { role: ctx.role } }),
}));
vi.mock("../ui/useFeatures", () => ({
  useFeatures: () => ({ on: (n: string) => ctx.flags.includes(n), loaded: true, answered: true, features: {} }),
  useUiV2: () => ctx.flags.includes("ui_v2"),
}));

import { candleFields } from "../api/client";
import {
  DataSourceBar, NEEDS_BROKER_FOR_DATA, SAMPLE_CHIP, SAMPLE_TOGGLE, dataRequestFor, sourceWords, useCandleSource, type CandleSourceState,
} from "./DataSource";

const render = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

function Probe({ stored }: { stored?: boolean }) {
  const s = useCandleSource(30, { storedHistory: stored });
  return <span data-mode={s.mode} data-label={s.label} data-stored={String(!!s.stored)} />;
}

const noop = () => {};
const source = (over: Partial<CandleSourceState>): CandleSourceState => ({
  mode: "broker", setMode: noop, broker: "upstox", setBroker: noop, lookbackDays: 30, setLookbackDays: noop, sources: [], usable: false,
  loading: false, label: "none", fetch: () => Promise.reject(new Error("unused")), fetchChains: () => Promise.reject(new Error("unused")),
  sampleHidden: false, ...over,
});

describe("M55 T3: no sample data by default", () => {
  it("the default is the server's own candles, for every user and with ui_v2 on or off", () => {
    for (const [role, flags] of [["OWNER", []], ["SUPER_ADMIN", ["ui_v2"]], ["USER", ["ui_v2"]]] as const) {
      ctx.role = role; ctx.flags = [...flags];
      const html = render(<Probe />);
      expect(html).toContain('data-mode="broker"');
      expect(html).not.toContain('data-label="sample"');
    }
  });

  it("a backtest page uses stored history without a broker when the flag is on (not only for users without sample data)", () => {
    ctx.role = "SUPER_ADMIN"; ctx.flags = ["ui_v2", "backtest_stored_history"];
    const html = render(<Probe stored />);
    expect(html).toContain('data-stored="true"');
    expect(html).toContain('data-label="lake:daily"');
    ctx.flags = [];
  });

  it("the bar offers sample data only behind the labelled switch, off by default, with no SAMPLE chip", () => {
    const html = render(<DataSourceBar source={source({})} />);
    expect(html).toContain(SAMPLE_TOGGLE);
    expect(html).toMatch(/<input[^>]*type="checkbox"[^>]*data-sample-toggle/);
    expect(html).toContain('aria-checked="false"');
    expect(html).not.toMatch(/\schecked=""/);
    expect(html).not.toContain("data-sample-chip");
  });

  it("with the switch on the bar shows the SAMPLE chip", () => {
    const html = render(<DataSourceBar source={source({ mode: "sample", label: "sample" })} />);
    expect(html).toContain("data-sample-chip");
    expect(html).toContain(`>${SAMPLE_CHIP}<`);
    expect(html).toMatch(/\schecked=""/);
  });

  it("a user without sample data never sees the switch", () => {
    const html = render(<DataSourceBar source={source({ sampleHidden: true })} />);
    expect(html).not.toContain(SAMPLE_TOGGLE);
    expect(html).not.toContain("data-sample-chip");
  });

  it("a request names the server's data or pastes candles; never a trusted label", () => {
    expect(dataRequestFor(source({ usable: true }))).toEqual({ data_mode: "server", lookback_days: 30, broker: "upstox" });
    expect(dataRequestFor(source({ stored: true, storedDays: 400 }))).toEqual({ data_mode: "server", lookback_days: 400 });
    expect(dataRequestFor(source({ mode: "sample" }))).toEqual({ data_mode: "paste" });
    expect(() => dataRequestFor(source({}))).toThrow(NEEDS_BROKER_FOR_DATA);
    const bar = { timestamp: "2026-01-02T09:15:00+05:30", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 };
    expect(candleFields([bar], { data_mode: "server", lookback_days: 20, broker: "upstox" })).toEqual({ data_mode: "server", lookback_days: 20, broker: "upstox" });
    expect(candleFields([bar], { data_mode: "paste" })).toEqual({ data_mode: "paste", candles: [bar] });
    expect(candleFields([bar])).toEqual({ candles: [bar] });
  });

  it("the run history names the source in plain words", () => {
    expect(sourceWords("uploaded")).toBe("Your own data");
    expect(sourceWords("broker:upstox")).toBe("Your broker");
    expect(sourceWords("lake:daily")).toBe("Stored history");
    expect(sourceWords("sample")).toBe("Sample");
  });
});
