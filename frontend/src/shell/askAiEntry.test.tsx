import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import chartsSource from "../chart/ChartsPageV2.tsx?raw";
import { ReadChartPanel } from "../chart/ReadChartV2";
import { EXPIRIES, liveChain } from "../optionChain/chain.fixture";
import { DEFAULT_COLUMNS } from "../optionChain/model";
import { OptionChainView } from "../optionChain/OptionChainPageV2";
import chainSource from "../optionChain/OptionChainPageV2.tsx?raw";
import shellSource from "./AppShellV2.tsx?raw";
import { AskAiSheetBody, contextChips } from "./AskAiSheetV2";
import { AskAiProvider, askAiOptions, copilotPath, type AskAiPageContext } from "./askAiEntry";
import TopBarV2 from "./TopBarV2";

/** CT-1 C10: one Ask AI entry in the shell; the page hands it its context; each flag gates what it gated before. */
const flags = (...on: string[]) => (f: string) => on.includes(f);
const noop = () => undefined;
const ids = (ctx: AskAiPageContext | null, on: (f: string) => boolean, current: string | null = null) => askAiOptions(ctx, on, current).map((o) => o.id);
const charts: AskAiPageContext = { symbol: "NIFTY", timeframe: "15 min", read: { on: false, toggle: noop } };
const chain: AskAiPageContext = { symbol: "NIFTY", expiry: "2026-11-03", chain: { symbol: "NIFTY", expiry: "2026-11-03" } };
const ALL = flags("read_chart", "chart_analyze", "ai_copilot");

describe("CT-1 C10 the options", () => {
  it("Charts: Read this chart, Analyze a chart image and the Copilot with the symbol", () => {
    expect(ids(charts, ALL, "charts")).toEqual(["read", "analyze", "copilot"]);
    const copilot = askAiOptions(charts, ALL, "charts").find((o) => o.id === "copilot");
    expect(copilot).toMatchObject({ label: "Ask the Copilot about NIFTY", to: "/copilot?symbol=NIFTY" });
  });

  it("the option chain: Ask about this chain first", () => {
    expect(ids(chain, ALL, "option-chain")).toEqual(["chain", "analyze", "copilot"]);
    expect(ids(chain, flags(), "option-chain")).toEqual(["chain"]);                 // the chain's Ask AI had no flag
  });

  it("each flag gates what it gated before", () => {
    expect(ids(charts, flags(), "charts")).toEqual([]);
    expect(ids(charts, flags("read_chart"), "charts")).toEqual(["read"]);
    expect(ids(charts, flags("chart_analyze"), "charts")).toEqual(["analyze"]);
    expect(ids(charts, flags("ai_copilot"), "charts")).toEqual(["copilot"]);
    // A page with no context (Today): the image analysis and the Copilot only.
    expect(ids(null, ALL, "dashboard")).toEqual(["analyze", "copilot"]);
    expect(askAiOptions(null, ALL, "dashboard").find((o) => o.id === "copilot")?.to).toBe("/copilot");
  });

  it("the page on screen is not offered again; the reading's row says how to hide it while it is on", () => {
    expect(ids(null, ALL, "chart-analyze")).toEqual(["copilot"]);
    expect(ids(null, ALL, "copilot")).toEqual(["analyze"]);
    expect(askAiOptions({ ...charts, read: { on: true, toggle: noop } }, ALL, "charts")[0].label).toBe("Hide the reading");
    expect(copilotPath("M&M")).toBe("/copilot?symbol=M%26M");
  });

  it("the context chips: symbol, timeframe, expiry", () => {
    expect(contextChips(charts)).toEqual(["NIFTY", "15 min"]);
    expect(contextChips(chain)).toEqual(["NIFTY", "Expiry 3 Nov"]);
    expect(contextChips(null)).toEqual([]);
  });
});

describe("CT-1 C10 the sheet and the top bar", () => {
  const body = (ctx: AskAiPageContext, view: "list" | "chain" = "list") => renderToStaticMarkup(
    <MemoryRouter><AskAiSheetBody ctx={ctx} options={askAiOptions(ctx, ALL, null)} view={view} onView={noop} onClose={noop} /></MemoryRouter>,
  );

  it("one row per option, 44 px, links for the pages", () => {
    const html = body(charts);
    expect(html).toContain('data-ask-option="read"');
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain('href="/chart-analyze"');
    expect(html).toMatch(/<a [^>]*href="\/copilot\?symbol=NIFTY"/);
    expect(html.match(/min-h-11/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
    expect(html).toContain("data-ask-context");
  });

  it("the chain view has a way back to the list", () => {
    const html = body(chain, "chain");
    expect(html).toContain('data-ask-ai-sheet="chain"');
    expect(html).toContain("All AI tools");
    // Without a chain the chain view falls back to the list.
    expect(body(charts, "chain")).toContain('data-ask-ai-sheet="list"');
  });

  it("the top bar has one Ask AI button only while there is something to offer", () => {
    const bar = (onAskAi?: () => void) => renderToStaticMarkup(
      <MemoryRouter><AuthProvider><TopBarV2 title="Charts" user={null} notificationsActive={false} accountActive={false} accountPath="/account" onSearch={noop} onAskAi={onAskAi} /></AuthProvider></MemoryRouter>,
    );
    const on = bar(noop);
    expect(on.match(/data-ask-ai-open/g)).toHaveLength(1);
    expect(on).toContain('aria-label="Ask AI"');
    expect(on).toContain("h-11 w-11");
    expect(bar()).not.toContain("data-ask-ai-open");
    // The shell shows it for a signed-in trader with at least one option.
    expect(shellSource).toContain("const shown = !!bar.user && options.length > 0;");
  });
});

describe("CT-1 C10 the pages use the shell's entry", () => {
  const chainView = (inShell: boolean) => {
    const page = (
      <OptionChainView symbol="NIFTY" onSymbol={noop} marketOpen prefs={DEFAULT_COLUMNS} onPrefs={noop}
        chain={{ expiries: EXPIRIES, expiry: "2026-11-03", setExpiry: noop, live: liveChain(), loading: false, problem: null }} />
    );
    return renderToStaticMarkup(<MemoryRouter><AuthProvider>{inShell ? <AskAiProvider>{page}</AskAiProvider> : page}</AuthProvider></MemoryRouter>);
  };

  it("the option chain leaves out its own Ask AI inside the shell and keeps it outside", () => {
    expect(chainView(false)).toContain("data-oc-ask-open");
    expect(chainView(true)).not.toContain("data-oc-ask-open");
    expect(chainSource).toContain("useRegisterAskAi(shellAsk ? { symbol, expiry: askExpiry, chain:");
  });

  it("Charts: Read and Analyze leave the toolbar inside the shell; the reading has a Hide button there", () => {
    expect(chartsSource).toContain('...(!shellAsk && features.on("chart_analyze") && !fs.on ? [{ id: "analyze"');
    expect(chartsSource).toContain('...(!shellAsk && readOn && readTf ? [{ id: "read"');
    expect(chartsSource).toMatch(/read: readOn && readTf && onChartTab \? \{ on: reading, toggle:/);
    expect(renderToStaticMarkup(<ReadChartPanel state={{ status: "loading" }} onHide={noop} />)).toContain("Hide the reading");
    expect(renderToStaticMarkup(<ReadChartPanel state={{ status: "loading" }} />)).not.toContain("Hide the reading");
  });
});
