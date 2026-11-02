import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { copilotI18n } from "../i18n";
import { givenAnswers, interviewSymbol } from "./IdeaBuilderTab";

/** M68 PR-8b: the strategy interview pre-chooses no symbol - an unanswered one reads "Not set" and is asked for. */
describe("M68 PR-8b: the interview's empty symbol state", () => {
  const t = copilotI18n.getFixedT("en", "copilot");

  it("no answer is no symbol, never NIFTY 50", () => {
    expect(interviewSymbol(undefined)).toBeNull();
    expect(interviewSymbol(null)).toBeNull();
    expect(interviewSymbol("   ")).toBeNull();
    expect(interviewSymbol(" infy ")).toBe("INFY");
    expect(t("idea.symbolNotSet")).toBe("Not set — choose a symbol");
    expect(t("idea.chooseSymbol")).toBe("Choose a symbol");
  });

  it("a Not set answer from the server stays unanswered (not the text \"null\")", () => {
    expect(givenAnswers({ symbol: null, style: "intraday", capital: 300000, vehicle: "" })).toEqual({ style: "intraday", capital: "300000" });
  });

  it("the tab has no NIFTY 50 fallback and shows the Not set state before reading the market", () => {
    const tab = readFileSync("src/copilot/tabs/IdeaBuilderTab.tsx", "utf8");
    expect(tab).not.toContain('"NIFTY 50"');
    expect(tab).toContain('data-testid="idea-symbol-not-set"');
    expect(tab).toContain('interviewSymbol(plan.answers.symbol) ?? t("idea.symbolNotSet")');
  });
});
