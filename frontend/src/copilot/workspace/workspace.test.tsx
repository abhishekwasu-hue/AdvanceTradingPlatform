import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => [] as string[]);
vi.mock("../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../api/client")>();
  return {
    ...real,
    request: (path: string) => {
      calls.push(path);
      const type = new URLSearchParams(path.split("?")[1]).get("type");
      const rows: Record<string, { tradingsymbol: string; exchange: string; name: string; instrument_type: string }[]> = {
        INDEX: [{ tradingsymbol: "NIFTY 50", exchange: "NSE", name: "Nifty 50", instrument_type: "INDEX" }],
        EQ: [],
        FUT: [{ tradingsymbol: "NIFTY26NOVFUT", exchange: "NFO", name: "NIFTY", instrument_type: "FUT" }],
        CE: [{ tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", name: "NIFTY", instrument_type: "CE" }],
        PE: [],
      };
      return Promise.resolve(rows[type ?? ""] ?? []);
    },
  };
});

import foldedSource from "../chat/CopilotFoldedV2.tsx?raw";
import { searchSymbols } from "../../ui/SymbolPicker";
import { Reading } from "./ChartWorkspaceV2";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { contractEmptyNote, isContract, openingSymbol, readingKey } from "./model";

/**
 * M75 W-2 (owner order M75 CW-1; the PR plan's W-2 row): the Copilot's chart workspace. Its CW-8 proofs here are the
 * pure parts and the wiring; e2e/copilot-workspace-v2.e2e.ts proves the same in a browser (the reading starts once on
 * open, a new symbol / timeframe reads again, a contract without prices says so, no overflow at 360 px).
 */

const RECENT = [{ symbol: "RELIANCE", exchange: "NSE" }, { symbol: "SENSEX", exchange: "BSE" }];

describe("W-2: what the workspace opens on (nothing chosen for him)", () => {
  it("the symbol handed over (Ask AI's `?symbol=`), with its exchange when he picked it before", () => {
    expect(openingSymbol("sensex", RECENT)).toEqual({ symbol: "SENSEX", exchange: "BSE" });
    expect(openingSymbol("TCS", RECENT)).toEqual({ symbol: "TCS", exchange: "NSE" });
  });
  it("else his own most recent pick; with none, no chart (the picker asks)", () => {
    expect(openingSymbol(null, RECENT)).toEqual({ symbol: "RELIANCE", exchange: "NSE" });
    expect(openingSymbol("  ", [])).toBeNull();
  });
});

describe("W-2: the reading runs once per chart (open = read; a new symbol or timeframe = read again)", () => {
  const nifty = { symbol: "NIFTY 50", exchange: "NSE" };
  it("one key per exchange, symbol and server timeframe", () => {
    expect(readingKey(nifty, "15min")).toEqual({ instrument: "NSE:NIFTY 50", tf: "15m" });
    expect(readingKey(nifty, "15min")).toEqual(readingKey({ symbol: "nifty 50", exchange: "nse" }, "15min"));   // the same chart: no second read
    expect(readingKey(nifty, "5min")).not.toEqual(readingKey(nifty, "15min"));                                // a new timeframe: read again
    expect(readingKey({ symbol: "SENSEX", exchange: "BSE" }, "15min")).not.toEqual(readingKey(nifty, "15min")); // a new symbol: read again
  });
  it("nothing to read without a chart, or on a timeframe the reading is not offered on", () => {
    expect(readingKey(null, "15min")).toBeNull();
    expect(readingKey(nifty, "week")).toBeNull();
  });
  it("the hook reads on exactly those keys (the instrument and timeframe are its only inputs, plus Try again)", () => {
    expect(workspaceSource).toMatch(/useReadChart\(engine, key\?\.instrument \?\? "", key\?\.tf \?\? null, readOn && !!key, attempt, workspaceLayer\)/);   // M75 W-3: + the annotation layer
  });
});

describe("W-2: futures and option contracts", () => {
  it("the picker searches contracts only where the host opts in (the workspace does)", async () => {
    calls.length = 0;
    expect((await searchSymbols("NIFTY")).map((r) => r.symbol)).toEqual(["NIFTY 50"]);
    expect(calls.some((c) => /type=(FUT|CE|PE)/.test(c))).toBe(false);
    calls.length = 0;
    expect((await searchSymbols("NIFTY", true)).map((r) => `${r.exchange}:${r.symbol}`)).toEqual(["NSE:NIFTY 50", "NFO:NIFTY26NOVFUT", "NFO:NIFTY26N0324500CE"]);
    expect(calls.filter((c) => /type=(FUT|CE|PE)/.test(c))).toHaveLength(3);
    expect(workspaceSource).toMatch(/symbolUniverse="with-contracts"/);
  });
  it("a contract's chart without prices says so in plain words", () => {
    expect(isContract("NFO")).toBe(true);
    expect(isContract("nse")).toBe(false);
    expect(contractEmptyNote("NIFTY26N0324500CE")).toBe("No prices for the contract NIFTY26N0324500CE on this timeframe yet. Pick another timeframe, or another contract.");
    expect(workspaceSource).toMatch(/emptyNote=\{isContract\(pick\.exchange\) \? contractEmptyNote\(pick\.symbol\) : undefined\}/);
  });
});

describe("W-2: the analysis panel's states", () => {
  const html = (p: Partial<Parameters<typeof Reading>[0]>) => renderToStaticMarkup(
    <Reading state={{ status: "idle" }} readOn hasChart offered onRetry={() => undefined} {...p} />);
  it("says what is happening in plain words, never an error code", () => {
    expect(html({ readOn: false })).toContain("not available for your account yet");
    expect(html({ hasChart: false })).toContain("Open a chart: its reading starts by itself.");
    expect(html({ offered: false })).toContain("minute, hour and Daily timeframes");
    expect(html({ state: { status: "loading" } })).toContain("Reading this chart…");
    expect(html({ state: { status: "off" } })).toContain("The reading is not available for this chart.");
    expect(html({ state: { status: "failed" } })).toContain("This chart could not be read.");
    expect(html({ state: { status: "failed" } })).toContain("data-workspace-retry");
  });
});

describe("W-2: where it lives (Read the chart, behind copilot_workspace; Market Pulse kept under it)", () => {
  it("the folded Read the chart tab shows the workspace first while the flag is on", () => {
    expect(foldedSource).toMatch(/const workspace = useFeatures\(\)\.on\("copilot_workspace"\);/);
    expect(foldedSource).toMatch(/\{workspace && <Suspense fallback=\{<TabSkeleton \/>\}><ChartWorkspace \/><\/Suspense>\}\n\s+<Part id="copilot-read-pulse"/);
  });
  it("nothing in the workspace places an order or picks a value for him", () => {
    expect(workspaceSource).not.toMatch(/placeOrder|\/order|basket|ticket/i);
    expect(workspaceSource).toMatch(/openingSymbol\(params\.get\("symbol"\), readRecent\(storageKey\)\)/);
  });
  it("the chart is full width on a phone (outside a panel) and the analysis sits beside it from lg", () => {
    expect(workspaceSource).toMatch(/!inPanel && "-mx-space-4 md:mx-0"/);
    expect(workspaceSource).toMatch(/lg:grid-cols-\[minmax\(0,2fr\)_minmax\(0,1fr\)\]/);
  });
});
