/**
 * REV-1 M2 part 1 (docs/design/REV1_PLAN.md "M2 - Medium"): Charts, Option chain, Sectors & indices, Custom scan, Plan
 * an options trade, My trades, Performance, Journal and Coach - each Medium finding fixed at its root, one test each.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { ApiError, COMING_SOON_MESSAGE, SOMETHING_FAILED_MESSAGE, errorSentence, serverReason, serverReasonOr } from "./api/errors";
import { CandleFeed, SymbolReadError, candleErrorText, type FeedEvents } from "./chart/useChartCandles";
import type { CandlesResponse } from "./types";
import { problemText, staleText } from "./optionChain/model";
import { problemOf, problemReasonOf } from "./optionChain/useOptionChain";
import type { LiveChain } from "./optionChain/types";
import ladderSource from "./optionChain/ChainLadderV2.tsx?raw";
import { AUTO_RETRY_MS, ListDetailView, MarketsBody, failedText } from "./markets/MarketsPageV2";
import marketsSource from "./markets/MarketsPageV2.tsx?raw";
import { readableWarnings, swingWindowOf } from "./pages/ScannerPage";
import scannerSource from "./pages/ScannerPage.tsx?raw";
import { lotsOf } from "./pages/OptionsBuilderPage";
import builderSource from "./pages/OptionsBuilderPage.tsx?raw";
import basketSource from "./optionsBuilder/BasketDialog.tsx?raw";
import positionsSource from "./trading/PositionsV2.tsx?raw";
import { modeWord, taxModes } from "./pages/AnalyticsPage";
import analyticsSource from "./pages/AnalyticsPage.tsx?raw";
import journalSource from "./pages/JournalPage.tsx?raw";
import coachSource from "./components/TradeCoach.tsx?raw";
import { strategyDisplayName } from "./ui/strategyName";

const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");

describe("the shared reason helper (api/errors.ts)", () => {
  it("passes a readable server reason, drops a raw exception, a machine code and an empty value", () => {
    expect(serverReason("Your Upstox session has no valid token today. Log in to Upstox on the Broker page for live data."))
      .toBe("Your Upstox session has no valid token today. Log in to Upstox on the Broker page for live data.");
    expect(serverReason("Only end-of-day prices for this symbol")).toBe("Only end-of-day prices for this symbol.");
    for (const raw of ["TypeError: x is undefined", "BrokerAPIError: 401 Unauthorized", "budget used", "", "  ", null, 42,
      "Traceback (most recent call last): ...", "NIFTY: KeyError: 'close'", "Not found"]) {
      expect(serverReason(raw), String(raw)).toBe("");
    }
    expect(serverReason("The 'byob' feature is currently disabled by the platform operator")).toBe(COMING_SOON_MESSAGE);
    expect(serverReasonOr("RuntimeError: boom", "Plain fallback.")).toBe("Plain fallback.");
  });

  it("errorSentence never shows a raw exception line, even inside other words", () => {
    expect(errorSentence(new Error("No candles from upstox: NIFTY: TypeError: x is undefined"))).toBe(SOMETHING_FAILED_MESSAGE);
    expect(errorSentence(new Error("ValueError: bad"))).toBe(SOMETHING_FAILED_MESSAGE);
    expect(errorSentence(new Error("Enter at least one symbol."))).toBe("Enter at least one symbol.");
  });
});

describe("Charts: the server's reason, not 'check the symbol'", () => {
  const answer = (error: string | null): CandlesResponse => ({
    source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: "5min", base_interval: "1min", lookback_days: 10,
    fetched_at: "", warnings: [], note: "", symbols: { NIFTY: { bars: [], count: 0, first: null, last: null, error } },
  });
  const run = async (error: string | null) => {
    const shown: (string | null)[] = [];
    const on: FeedEvents = { loading: () => undefined, replace: () => undefined, merge: () => undefined, error: (t) => shown.push(t) };
    await new CandleFeed(async () => answer(error), "NIFTY", "5min", on).tick();
    return shown[shown.length - 1];
  };

  it("a broker failure or a plan limit on a valid symbol says so", async () => {
    expect(await run("Your broker could not send prices for NIFTY just now. Try again in a moment."))
      .toBe("Your broker could not send prices for NIFTY just now. Try again in a moment.");
    expect(await run("NIFTY is outside your Upstox session budget (5 symbols). End-of-day prices for it until a slot frees up."))
      .toContain("outside your Upstox session budget");
    expect(await run("TypeError: boom")).toContain("Check the symbol");          // unreadable: the old plain words
    expect(await run("budget used")).toContain("request limit");
  });

  it("candleErrorText keeps its plain fallbacks", () => {
    expect(candleErrorText(new SymbolReadError(null), "NIFTY")).toContain("Check the symbol");
    expect(candleErrorText(new SymbolReadError("Only end-of-day prices for this symbol."), "NIFTY")).toBe("Only end-of-day prices for this symbol.");
    const plan = "NIFTY is outside your Upstox session budget (5 symbols). End-of-day prices for it until a slot frees up.";
    expect(candleErrorText(new ApiError(409, plan, plan, "", null, "C"), "NIFTY")).toBe(plan);   // a whole-read refusal: its reason
    expect(candleErrorText(new ApiError(409, "x"), "NIFTY")).toContain("broker session has ended");
  });
});

describe("Option chain: no session and plan are different, old numbers are marked old", () => {
  const tier = (detail: string) => new ApiError(409, detail, detail, "", null, "C");

  it("a 409 with the data tier is 'no-session' when its reason is about the session, else 'plan'", () => {
    expect(problemOf(tier("Your Upstox session has no valid token today. Log in to Upstox on the Broker page for live data."))).toBe("no-session");
    expect(problemOf(tier("No broker linked to your login: end-of-day prices only. Link Upstox or Dhan on the Broker page for live prices."))).toBe("no-session");
    expect(problemOf(tier("NIFTY is outside your Upstox session budget (5 symbols). End-of-day prices for it until a slot frees up."))).toBe("plan");
    expect(problemOf(new ApiError(409, "No broker session", "No broker session"))).toBe("no-session");
    expect(problemOf(new ApiError(500, "x"))).toBe("failed");
    expect(problemReasonOf(tier("TypeError: x"))).toBe("");
  });

  it("the server's reason is shown; old numbers say when they are from and why they are not updating", () => {
    expect(problemText("plan", "")).toBe("This symbol is not available live on your plan.");
    expect(problemText("no-session", "Log in to Upstox again.")).toBe("Log in to Upstox again.");
    const live = { fetched_at: "2026-10-30T05:00:00Z", data_as_of: "2026-10-30T05:00:00Z" } as LiveChain;
    const lapsed = staleText(live, "no-session", "Your Upstox session has no valid token today. Log in to Upstox on the Broker page for live data.");
    expect(lapsed).toMatch(/^These numbers are old \(from .*\) and are not updating\. Your Upstox session/);
    expect(lapsed).not.toContain("try again in a moment");
    expect(staleText(live, "failed", "")).toContain("We will try again in a moment.");
    expect(code(ladderSource)).toContain("staleText(live, problem, problemReason)");
  });
});

describe("Sectors & indices: a real retry, honest words", () => {
  it("the page reads again on its own a few times, and only promises it while it will", () => {
    expect(AUTO_RETRY_MS.length).toBeGreaterThan(0);
    expect(code(marketsSource)).toMatch(/setTimeout\(\(\) => \{ tries\.current\.auto \+= 1; setNonce/);
    expect(failedText("the market view", true)).toBe("We could not load the market view. We will try again in a moment.");
    expect(failedText("the market view", false)).toBe("We could not load the market view. Tap Try again.");
    const html = renderToStaticMarkup(<MemoryRouter><MarketsBody state={{ status: "error", retrying: false }} view="sectors" period="day" onRetry={() => undefined} /></MemoryRouter>);
    expect(html).toContain("Tap Try again.");
    expect(html).not.toContain("We will try again");
    const list = renderToStaticMarkup(<MemoryRouter><ListDetailView state={{ status: "error", retrying: true }} view="sectors" period="day" onBack={() => undefined} onRetry={() => undefined} /></MemoryRouter>);
    expect(list).toContain("We could not load these stocks. We will try again in a moment.");
  });
});

describe("Custom scan: no raw exception text, no ids, the trader's own swing window, no score", () => {
  it("warnings show the server's readable reason or one plain line", () => {
    expect(readableWarnings(["NIFTY: TypeError: x is undefined", "TCS: Your broker could not send prices for TCS just now. Try again in a moment.",
      "NIFTY: RuntimeError: again"])).toEqual([
      "NIFTY: your broker could not send its data just now.",
      "TCS: Your broker could not send prices for TCS just now. Try again in a moment.",
    ]);
  });

  it("the swing window is the trader's own number; nothing fixed is sent", () => {
    expect(swingWindowOf("")).toBeNull();
    expect(swingWindowOf("0")).toBeNull();
    expect(swingWindowOf("2.5")).toBeNull();
    expect(swingWindowOf("5")).toBe(5);
    const src = code(scannerSource);
    expect(src).not.toMatch(/swing_window:\s*3/);
    expect(src).toContain("Set the swing window");
  });

  it("no provider / model / prompt ids and no coloured 0-100 score", () => {
    const src = code(scannerSource);
    expect(src).not.toMatch(/\.provider|\.model\b|prompt_version|r\.score|"\?"/);
  });
});

describe("Plan an options trade: the trader's own lots, no default width, an honest basket", () => {
  it("lots come from the trader (settings pre-fill or typed), never a fixed 1", () => {
    expect(lotsOf("")).toBeNull();
    expect(lotsOf("0")).toBeNull();
    expect(lotsOf("501")).toBeNull();
    expect(lotsOf("3")).toBe(3);
    const src = code(builderSource);
    expect(src).not.toMatch(/lots: 1\b/);
    expect(src).toContain("ticketPrefill(defaults).lots");
  });

  it("a blank width stays blank", () => {
    const src = code(builderSource);
    expect(src).not.toMatch(/2 \* step|default: 2 strike steps/);
    expect(src).toContain("const width = positive(widthText);");
  });

  it("the basket is review only: no place button that can never be pressed", () => {
    const src = code(basketSource);
    expect(src).not.toMatch(/disabled=\{!basket\?\.placeable\}/);
    expect(src).toContain("Order basket (review only)");
    expect(code(builderSource)).toContain(">Review basket<");
    expect(code(builderSource)).not.toContain(">Place as basket<");
  });
});

describe("My trades: a failed journal save is shown", () => {
  it("the error is kept and shown with what was typed", () => {
    const src = code(positionsSource);
    expect(src).not.toContain("then(onSaved, () => undefined)");
    expect(src).toContain("Your note was not saved.");
    expect(src).toContain("unsaved?.notes ?? row.notes");
  });
});

describe("Performance: own trades, PAPER, readable names, Tax on PAPER in Phase 0", () => {
  it("reads the user's own trades, labels PAPER, shows readable strategy names", () => {
    const src = code(analyticsSource);
    expect(src).toContain("api.performanceSummary()");
    expect(src).toContain("api.performanceVsBacktest()");
    expect(src).not.toMatch(/Live trades|Live win rate|>Key<|>\{r\.strategy_id\}<|>\{r\.key\}<|"LIVE"\)/);
    expect(modeWord("PAPER")).toBe("PAPER");
    expect(modeWord(undefined)).toBe("PAPER");
    expect(modeWord("LIVE")).toBe("LIVE");
    expect(strategyDisplayName("custom:12")).toBe("My strategy");
  });

  it("the Tax card opens on PAPER and offers no LIVE in Phase 0", () => {
    expect(taxModes(true)).toEqual(["PAPER"]);
    expect(taxModes(false)).toEqual(["LIVE", "PAPER", "ALL"]);
  });
});

describe("Journal and Coach", () => {
  it("Journal: no design-note text", () => {
    expect(journalSource).not.toMatch(/design note/i);
  });

  it("Coach: strategy bars show readable names", () => {
    expect(code(coachSource)).toContain("labelOf={(g) => strategyDisplayName(g.key) || \"My strategy\"}");
  });
});
