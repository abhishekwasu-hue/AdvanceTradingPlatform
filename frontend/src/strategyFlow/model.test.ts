import { describe, expect, it } from "vitest";
import type { StrategyDraft } from "../copilot/strategyDraft";
import type { BacktestResult, Trade } from "../types";
import {
  BLOCK_PRESETS, FLOW_TIMEFRAMES, START_CARDS, SOON_BLOCKS, blockOpen, blockWords, drawdownPct, emptyFlow, entryProblems, exitProblems, flowConfig,
  handoffFromState, marketProblems, optionBacktest, paperContract, paperSymbol, resultSummary, thin, tradeR, withCard, withDraft, withTestedContract,
  EMPTY_EXITS, INDICATOR_LABEL, atrLengthMissing, higherTimeframes, type FlowState,
} from "./model";
import { INDICATOR_LABEL as SHARED_LABELS } from "../ui/strategyLabels";
import pageSource from "./StrategyFlowV2.tsx?raw";
import paperSource from "../paperbot/PaperBotV2.tsx?raw";
import pagesV2 from "../shell/pagesV2.ts?raw";
import ideaSource from "../copilot/tabs/IdeaBuilderTab.tsx?raw";
import builderSource from "../pages/StrategyBuilderPage.tsx?raw";

const card = (key: string) => START_CARDS.find((c) => c.key === key)!;

function bullPutFlow(): FlowState {
  let f = withCard(emptyFlow(), card("bull-put"));
  f = { ...f, symbol: "NIFTY", timeframe: "15min" };
  const b = BLOCK_PRESETS.find((p) => p.key === "price-ma")!.make("long");
  f = { ...f, blocks: [{ ...b, right: { ...b.right, period: 20 } }] };
  return { ...f, options: { ...f.options, strikeRule: "OTM", strikeOffset: "2", width: "2", expiry: "ROLL" } };
}

describe("SU1 start cards", () => {
  it("offers the order's templates; the area pullback is coming soon, breakout-retest is the template engine's", () => {
    expect(START_CARDS.map((c) => c.key)).toEqual(["bull-put", "bear-call", "iron-condor", "pullback-area", "breakout-retest", "own"]);
    expect(card("pullback-area").engine).toBe("soon");
    expect(card("breakout-retest")).toMatchObject({ engine: "template", templateId: "A5" });
  });

  it("a card sets only what it names: the structure and the side", () => {
    const f = withCard(emptyFlow(), card("bull-put"));
    expect(f.options).toMatchObject({ structure: "BULL_PUT_SPREAD", strikeRule: null, expiry: null, width: "", lots: "" });
    expect(f.side).toBe("long");
    expect(f.exits).toEqual({ stop: "", atrPeriod: "", target1: "", target2: "", minRr: "" });
    expect(withCard(f, card("pullback-area"))).toBe(f);
  });

  it("the words draft loads what the words state and nothing more", () => {
    const draft: StrategyDraft = {
      idea: "x", name: "RSI idea", symbol: "SENSEX", timeframe: "15min", sides: ["long"], asks: [], unsupported: [], unread: [], read_as: [], ready_made: [],
      rules: [{ side: "long", text: "RSI crosses above 60", words: "", condition: { left: { type: "indicator", indicator: "RSI", period: 14 }, operator: "CROSSES_ABOVE", right: { type: "value", value: 60 } } }],
      settings: { stop_loss_atr_mult: null, atr_period: null, target_rr: null, min_rr: null }, min_rr_source: null, lots: null, complete: false, mode: "PAPER",
    };
    const f = withDraft(emptyFlow(), draft);
    expect(f).toMatchObject({ symbol: "SENSEX", timeframe: "15min", side: "long", engine: "custom" });
    expect(blockWords(f.blocks[0])).toBe("RSI (14) crosses above 60");
    expect(f.exits.stop).toBe("");
  });
});

describe("SU2 market", () => {
  it("offers 1D and shows 1W as coming soon", () => {
    expect(FLOW_TIMEFRAMES.find((t) => t.value === "day")?.soon).toBeFalsy();
    expect(FLOW_TIMEFRAMES.find((t) => t.value === "week")?.soon).toBe(true);
    expect(marketProblems(emptyFlow())).toHaveLength(3);
    expect(paperSymbol("NIFTY")).toBe("NIFTY 50");
    expect(paperSymbol("SENSEX")).toBe("SENSEX");
  });
});

describe("SU3 entry blocks", () => {
  it("leaves every number to the trader and says so on the chip", () => {
    const open = BLOCK_PRESETS.filter((p) => blockOpen(p.make("long"))).map((p) => p.key);
    expect(open).toEqual(["price-ma", "ma-cross", "rsi-level", "supertrend", "any"]);   // VWAP and the gap need no number
    expect(blockWords(BLOCK_PRESETS[0].make("long"))).toBe("Close is above EMA (?)");
    expect(blockWords(BLOCK_PRESETS.find((p) => p.key === "gap")!.make("short"))).toBe("Today's open is below Previous day low");
  });

  it("lists the price-action blocks the engine cannot evaluate as coming soon", () => {
    expect(SOON_BLOCKS.join(" ")).toMatch(/demand \/ supply.*Pullback.*engulfing.*change of character.*RSI divergence.*Trendline/);
    expect(pageSource).toContain("data-soon-blocks");
  });

  it("needs one complete block per side", () => {
    const f = { ...bullPutFlow(), side: "both" as const };
    expect(entryProblems(f)).toContain("Add at least one short entry block.");
    expect(entryProblems(bullPutFlow())).toEqual([]);
  });
});

describe("SU4 exit & risk (DEF-0)", () => {
  it("blank exits are off and allowed; a target without a stop is not", () => {
    const f = bullPutFlow();
    expect(exitProblems(f.exits, f.options, "custom")).toEqual([]);
    expect(exitProblems({ ...f.exits, target1: "2" }, f.options, "custom")[0]).toMatch(/needs your stop-loss/);
    expect(exitProblems({ ...f.exits, stop: "1.5" }, f.options, "custom")[0]).toMatch(/ATR length/);
  });

  it("an option needs his strikes and expiry; nothing is picked for him", () => {
    const f = withCard(emptyFlow(), card("iron-condor"));
    expect(exitProblems(f.exits, f.options, "custom")).toEqual(["Pick your strikes.", "Write the spread width in strikes (1 to 20).", "Pick the expiry."]);
  });

  it("saves blank exits as null and the timeframe as chosen", () => {
    const cfg = flowConfig(bullPutFlow())!;
    expect(cfg).toMatchObject({ timeframe: "15min", stop_loss_atr_mult: null, target_rr: null, min_rr: null, short_conditions: [] });
    expect(cfg.long_conditions[0]).toMatchObject({ operator: "GT", right: { indicator: "EMA", period: 20 } });
    const own = flowConfig({ ...bullPutFlow(), exits: { stop: "1.5", atrPeriod: "10", target1: "2", target2: "", minRr: "" } })!;
    expect(own).toMatchObject({ stop_loss_atr_mult: 1.5, atr_period: 10, target_rr: [2, 2], min_rr: null });
  });

  it("tests and trades the same contract, incl. next weekly on expiry day", () => {
    const f = bullPutFlow();
    expect(optionBacktest(f.options, f.timeframe)).toMatchObject({ option_strategy: "BULL_PUT_SPREAD", expiry_rule: "ROLL", strike_rule: "OTM", strike_offset: 2, spread_width: 2, intraday: true });
    expect(optionBacktest(f.options, f.timeframe)?.max_lots).toBeUndefined();
    expect(paperContract(f.options)).toEqual({ instrument_kind: "OPTION", option_strategy: "BULL_PUT_SPREAD", expiry_rule: "ROLL", strike_rule: "OTM", strike_offset: 2, spread_width: 2 });
    expect(paperContract({ ...f.options, structure: "UNDERLYING" })).toEqual({ instrument_kind: "UNDERLYING" });
  });
});

const trade = (pnl: number, entry: number, stop: number): Trade => ({
  symbol: "NIFTY", strategy_id: "custom:1", direction: "LONG", entry_time: "2026-10-01T04:00:00Z", entry_price: entry, quantity: 1, stop_loss: stop,
  target1: 0, target2: null, exit_time: "2026-10-01T05:00:00Z", exit_price: entry + pnl, exit_reason: "x", pnl, charges: 0,
});

describe("SU5 result card", () => {
  it("max drawdown in percent from the peak; R per trade from its own stop", () => {
    expect(drawdownPct([100, 120, 90, 130])).toBe(25);
    expect(drawdownPct([])).toBeNull();
    expect(tradeR(trade(20, 100, 90))).toBe(2);
    expect(tradeR(trade(20, 100, 0))).toBeNull();
    expect(thin(Array.from({ length: 200 }, (_, i) => i), 60)).toHaveLength(60);
  });

  it("summarises the numbers first", () => {
    const r = { net_pnl: 10, win_rate: 50, max_drawdown: 10, total_trades: 2, equity_curve: [100, 110, 100, 110], trades: [trade(20, 100, 90), trade(-10, 100, 90)] } as BacktestResult;
    expect(resultSummary(r)).toMatchObject({ netPnl: 10, winRate: 50, maxDd: 10, maxDdPct: 9.09, trades: 2, avgR: 0.5 });
  });

  it("shows units and puts trades / chart / analytics behind tabs", () => {
    expect(pageSource).toMatch(/kind="money"/);
    expect(pageSource).toMatch(/% from the peak/);
    expect(pageSource).toMatch(/key: "trades".*key: "chart".*key: "analytics"/s);
  });
});

describe("SU6 start on paper", () => {
  it("hands over the saved strategy and the tested contract; the form picks them, nothing starts", () => {
    const h = handoffFromState({ flowStrategy: { strategyId: "custom:4", symbol: "NIFTY 50", contract: { instrument_kind: "OPTION", expiry_rule: "ROLL" }, name: "Mine" } });
    expect(h).toMatchObject({ strategyId: "custom:4", contract: { expiry_rule: "ROLL" } });
    expect(handoffFromState({ flowStrategy: { strategyId: "" } })).toBeNull();
    expect(handoffFromState(null)).toBeNull();
    expect(paperSource).toContain("handoffFromState(location.state)");
    expect(pageSource).not.toMatch(/createDeployment/);
  });

  it("uses the tested contract in place of the form's own and keeps PAPER", () => {
    const base = { strategy_id: "custom:4", symbol: "NIFTY 50", exchange: "NSE", timeframe: "1min", mode: "PAPER" as const, instrument_kind: "OPTION" as const,
      option_position: "BUY" as const, expiry_rule: "NEAREST" as const, strike_rule: "ATM" as const, strike_offset: 0, max_lots: 1 };
    const out = withTestedContract(base, { instrument_kind: "OPTION", option_strategy: "BULL_PUT_SPREAD", expiry_rule: "ROLL", strike_rule: "OTM", strike_offset: 2, spread_width: 2 });
    expect(out).toMatchObject({ mode: "PAPER", option_strategy: "BULL_PUT_SPREAD", expiry_rule: "ROLL", max_lots: 1 });
    const under = withTestedContract(base, { instrument_kind: "UNDERLYING" });
    expect(under).toEqual({ strategy_id: "custom:4", symbol: "NIFTY 50", exchange: "NSE", timeframe: "1min", mode: "PAPER", instrument_kind: "UNDERLYING" });
    expect(withTestedContract(base, null)).toBe(base);
  });
});

describe("wiring", () => {
  it("is the v2 page for Build; the full builder stays under Advanced; the duplicates open the flow", () => {
    expect(pagesV2).toContain('"strategy-builder": lazy(() => import("../strategyFlow/StrategyFlowV2"))');
    expect(pageSource).toContain("StrategyBuilderFull");
    expect(builderSource).toContain("data-open-flow");
    expect(ideaSource).toContain('navigate("/strategies/build"');
    expect(pageSource).toContain("api.draftStrategy");
    expect(pageSource).not.toMatch(/<select/);
  });
});

describe("SU7 Advanced: higher timeframe per operand and the ATR length", () => {
  it("offers only timeframes above the strategy's own; none for Daily", () => {
    expect(higherTimeframes("1min")).toEqual(["5min", "15min", "30min", "60min"]);
    expect(higherTimeframes("15min")).toEqual(["30min", "60min"]);
    expect(higherTimeframes("60min")).toEqual([]);
    expect(higherTimeframes("day")).toEqual([]);
    expect(higherTimeframes(null)).toEqual([]);
  });

  it("holds the entry step while a block reads a timeframe not above the strategy's", () => {
    const f = bullPutFlow();
    const onHigher = (tf: string) => ({ ...f, blocks: f.blocks.map((b) => ({ ...b, right: { ...b.right, timeframe: tf } })) });
    expect(entryProblems(onHigher("60min"))).toEqual([]);
    expect(entryProblems(onHigher("5min"))).toEqual(["A block reads a timeframe that is not above the strategy's own: change it under the block's Advanced."]);
    expect(flowConfig({ ...onHigher("60min"), exits: { ...EMPTY_EXITS } })?.long_conditions[0].right.timeframe).toBe("60min");
  });

  it("opens the exits' Advanced by itself only while a stop waits for its ATR length", () => {
    expect(atrLengthMissing({ ...EMPTY_EXITS })).toBe(false);
    expect(atrLengthMissing({ ...EMPTY_EXITS, stop: "1.5" })).toBe(true);
    expect(atrLengthMissing({ ...EMPTY_EXITS, stop: "1.5", atrPeriod: "14" })).toBe(false);
  });

  it("uses the one shared label map (no second copy of the indicator names)", () => {
    expect(INDICATOR_LABEL).toBe(SHARED_LABELS);
    expect(builderSource).toContain("INDICATOR_LABEL[ind]");
    expect(builderSource).not.toMatch(/deterministic|credentials|id \{s\.strategy_id\}/);
  });
});
