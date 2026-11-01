import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { deploymentWith, suggestedExitsText } from "./CandidateCard";

/** M68 PR-8 (TS1-075 / 077 / 098): a draft or a plan only SUGGESTS values; nothing reaches a bot without his tick. */
describe("M68 PR-8: Copilot suggestions are applied only on the trader's own tick", () => {
  const suggested = { holding: "INTRADAY" as const, exit_rules: { time_exit_at: "15:10", break_even_at_r: 1 } };
  const bot = { strategy_id: "custom:7", symbol: "RELIANCE", timeframe: "5min", mode: "PAPER" as const };

  it("the suggested exits read in words", () => {
    expect(suggestedExitsText(suggested)).toBe("Intraday, time exit 15:10, break-even at 1R");
    expect(suggestedExitsText({ holding: "SWING", exit_rules: {} })).toBe("Carry forward");
    expect(suggestedExitsText(null)).toBe("");
  });

  it("not ticked: the bot carries none of them; ticked: it carries them all", () => {
    expect(deploymentWith(bot, suggested, false)).toEqual(bot);
    expect(deploymentWith(bot, suggested, true)).toEqual({ ...bot, holding: "INTRADAY", exit_rules: { time_exit_at: "15:10", break_even_at_r: 1 } });
    expect(deploymentWith(bot, undefined, true)).toEqual(bot);
  });

  it("no symbol, style, exchange or base candles is pre-selected in the bot forms and Strategy Lab", () => {
    const lab = readFileSync("src/copilot/tabs/StrategyLabTab.tsx", "utf8");
    expect(lab).toContain('const [symbol, setSymbol] = useState("");');
    expect(lab).toContain('useState<"" | "intraday" | "scalping">("")');
    const form = readFileSync("src/pages/DeploymentsPage.tsx", "utf8");
    expect(form).toContain('const [symbol, setSymbol] = useState("");');
    expect(form).toContain('const [exchange, setExchange] = useState("");');
    expect(form).toContain('const [timeframe, setTimeframe] = useState<string>("");');
    const chart = readFileSync("src/components/ChartStrategies.tsx", "utf8");
    expect(chart).not.toContain('timeframe: "1min"');
    const idea = readFileSync("src/copilot/tabs/IdeaBuilderTab.tsx", "utf8");
    expect(idea).toContain("aiInterviewDeploy(plan.candidate_id!, acceptRisk, acceptSuggested)");
  });
});
