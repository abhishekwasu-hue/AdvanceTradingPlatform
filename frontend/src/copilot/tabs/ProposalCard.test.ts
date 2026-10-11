import { describe, expect, it } from "vitest";
import { proposalTitle } from "./ProposalCard";

const t = (key: string) => key;

describe("proposalTitle (Z3)", () => {
  it("asks 'Exit now?' for a target reached on an approval-only position", () => {
    expect(proposalTitle({ action: "EXIT_POSITION", rule: "TARGET_1_REACHED" }, t)).toBe("watch.exitNow");
    expect(proposalTitle({ action: "EXIT_POSITION", rule: "TARGET_2_REACHED" }, t)).toBe("watch.exitNow");
  });

  it("names every other proposal by its action, entries included", () => {
    expect(proposalTitle({ action: "EXIT_POSITION", rule: "STALE_POSITION" }, t)).toBe("watch.action.EXIT_POSITION");
    expect(proposalTitle({ action: "ENTRY", rule: "ENTRY_SIGNAL" }, t)).toBe("watch.action.ENTRY");
    expect(proposalTitle({ action: "PAUSE_DEPLOYMENT", rule: "LOSING_STREAK" }, t)).toBe("watch.action.PAUSE_DEPLOYMENT");
  });
});
