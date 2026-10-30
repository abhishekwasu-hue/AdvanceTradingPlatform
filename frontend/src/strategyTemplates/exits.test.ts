import { describe, expect, it } from "vitest";
import pageSource from "../paperbot/PaperBotV2.tsx?raw";
import {
  EMPTY_PICK, STOP_WORDS, TARGET_WORDS, effectivePick, exitClass, exitNotes, exitProblem, stopOptions, targetOptions, templateExitsBody,
  type TemplateExitChoices,
} from "./exits";

/** Strategy templates section 5: the template's exits as the user's own settings in the v2 Trading bot form. */

const LEVEL = ["NONE", "R_MULTIPLE", "TEMPLATE_EXIT"] as const;
const D1: TemplateExitChoices = {
  precedence: {
    UNDERLYING: { stops: ["TEMPLATE_INVALIDATION"], targets: [...LEVEL] },
    FUTURE: { stops: ["TEMPLATE_INVALIDATION"], targets: [...LEVEL] },
    OPTION_SINGLE: { stops: ["TEMPLATE_INVALIDATION", "PREMIUM_PCT"], targets: [...LEVEL] },
    OPTION_STRUCTURE: { stops: ["CREDIT_PCT"], targets: ["CREDIT_PCT", "TEMPLATE_EXIT"] },
  },
  exit_signal: true, wave_projection: false, base_waves: [], target_r: { min: 0.1, max: 20 }, ratio: { min: 0, max: 10 },
};
const G1: TemplateExitChoices = {
  ...D1, wave_projection: true, base_waves: [{ value: "previous", label: "the wave before the running one" }, { value: "1", label: "wave one" }],
  precedence: { ...D1.precedence, UNDERLYING: { stops: ["TEMPLATE_INVALIDATION"], targets: ["NONE", "R_MULTIPLE", "WAVE_PROJECTION"] } },
};

describe("the exit choices per instrument", () => {
  it("names the instrument the bot trades as the server's table does", () => {
    expect(exitClass({ instrument_kind: "UNDERLYING" })).toBe("UNDERLYING");
    expect(exitClass({ instrument_kind: "FUTURE" })).toBe("FUTURE");
    expect(exitClass({ instrument_kind: "OPTION" })).toBe("OPTION_SINGLE");
    expect(exitClass({ instrument_kind: "OPTION", option_strategy: "SINGLE" })).toBe("OPTION_SINGLE");
    expect(exitClass({ instrument_kind: "OPTION", option_strategy: "BULL_PUT_SPREAD" })).toBe("OPTION_STRUCTURE");
  });

  it("shows only the choices the server offers there, in plain words", () => {
    expect(stopOptions(D1, "UNDERLYING")).toEqual([{ value: "TEMPLATE_INVALIDATION", label: "Where the idea is wrong" }]);
    expect(stopOptions(D1, "OPTION_SINGLE").map((o) => o.label)).toEqual([STOP_WORDS.TEMPLATE_INVALIDATION, "Only my premium stop"]);
    expect(targetOptions(D1, "OPTION_STRUCTURE").map((o) => o.label)).toEqual(["My spread's target % of the credit", TARGET_WORDS.TEMPLATE_EXIT]);
    expect(targetOptions(G1, "UNDERLYING").map((o) => o.value)).toEqual(["NONE", "R_MULTIPLE", "WAVE_PROJECTION"]);
    for (const words of [...Object.values(STOP_WORDS), ...Object.values(TARGET_WORDS)]) {
      expect(words).not.toMatch(/[A-Z]{2,}_|_/);                                   // never a code on the screen
    }
  });

  it("is pre-filled with the template's defaults, and a choice that does not fit the instrument falls back to them", () => {
    expect(effectivePick(EMPTY_PICK, D1, "UNDERLYING")).toMatchObject({ stop: "TEMPLATE_INVALIDATION", target: "NONE" });
    expect(effectivePick(EMPTY_PICK, D1, "OPTION_STRUCTURE")).toMatchObject({ stop: "CREDIT_PCT", target: "CREDIT_PCT" });
    const premium = { ...EMPTY_PICK, stop: "PREMIUM_PCT" as const, target: "R_MULTIPLE" as const };
    expect(effectivePick(premium, D1, "OPTION_SINGLE")).toMatchObject({ stop: "PREMIUM_PCT", target: "R_MULTIPLE" });
    expect(effectivePick(premium, D1, "UNDERLYING")).toMatchObject({ stop: "TEMPLATE_INVALIDATION", target: "R_MULTIPLE" });
    expect(effectivePick(premium, D1, "OPTION_STRUCTURE")).toMatchObject({ stop: "CREDIT_PCT", target: "CREDIT_PCT" });
  });
});

describe("what the form sends", () => {
  it("nothing for the defaults: the bot is stored exactly as before", () => {
    for (const cls of ["UNDERLYING", "FUTURE", "OPTION_SINGLE", "OPTION_STRUCTURE"] as const) {
      expect(templateExitsBody(EMPTY_PICK, D1, cls)).toBeNull();
      expect(templateExitsBody(effectivePick(EMPTY_PICK, D1, cls), D1, cls)).toBeNull();
    }
  });

  it("the choice and its number, only what was chosen", () => {
    expect(templateExitsBody({ ...EMPTY_PICK, target: "R_MULTIPLE", targetR: "2.5" }, D1, "UNDERLYING")).toEqual({ target_rule: "R_MULTIPLE", target_r: 2.5 });
    expect(templateExitsBody({ ...EMPTY_PICK, target: "TEMPLATE_EXIT", targetR: "9" }, D1, "UNDERLYING")).toEqual({ target_rule: "TEMPLATE_EXIT" });
    expect(templateExitsBody({ ...EMPTY_PICK, stop: "PREMIUM_PCT" }, D1, "OPTION_SINGLE")).toEqual({ stop_source: "PREMIUM_PCT" });
    expect(templateExitsBody({ ...EMPTY_PICK, target: "TEMPLATE_EXIT" }, D1, "OPTION_STRUCTURE")).toEqual({ target_rule: "TEMPLATE_EXIT" });
    expect(templateExitsBody({ ...EMPTY_PICK, target: "WAVE_PROJECTION", baseWave: "1", ratio: "1.618" }, G1, "UNDERLYING"))
      .toEqual({ target_rule: "WAVE_PROJECTION", target_base_wave: "1", target_ratio: 1.618 });
  });

  it("asks for a missing or out-of-range number in plain words, and nothing invents one", () => {
    expect(exitProblem({ ...EMPTY_PICK, target: "R_MULTIPLE" }, D1)).toBe("Type how many times your risk to take profit at (0.1 to 20).");
    expect(exitProblem({ ...EMPTY_PICK, target: "R_MULTIPLE", targetR: "0.05" }, D1)).not.toBeNull();
    expect(exitProblem({ ...EMPTY_PICK, target: "R_MULTIPLE", targetR: "2x" }, D1)).not.toBeNull();
    expect(exitProblem({ ...EMPTY_PICK, target: "R_MULTIPLE", targetR: "2" }, D1)).toBeNull();
    expect(exitProblem({ ...EMPTY_PICK, target: "WAVE_PROJECTION", ratio: "1" }, G1)).toBe("Pick the wave the projection is measured on.");
    expect(exitProblem({ ...EMPTY_PICK, target: "WAVE_PROJECTION", baseWave: "1", ratio: "0" }, G1)).not.toBeNull();
    expect(exitProblem({ ...EMPTY_PICK, target: "WAVE_PROJECTION", baseWave: "1", ratio: "11" }, G1)).not.toBeNull();
    expect(exitProblem({ ...EMPTY_PICK, target: "WAVE_PROJECTION", baseWave: "1", ratio: "1.618" }, G1)).toBeNull();
    expect(exitProblem(EMPTY_PICK, D1)).toBeNull();
    expect(EMPTY_PICK.targetR).toBe("");                                             // the form never suggests a multiple
  });

  it("says what still applies", () => {
    expect(exitNotes({ ...EMPTY_PICK, target: "R_MULTIPLE" }, "UNDERLYING")).toContain("Your minimum reward to risk in your risk settings still applies.");
    expect(exitNotes({ ...EMPTY_PICK, target: "NONE" }, "OPTION_SINGLE")).toContain("Your premium stop always stays as a safety net.");
    expect(exitNotes({ ...EMPTY_PICK, target: "TEMPLATE_EXIT" }, "OPTION_STRUCTURE")).toEqual([
      "The trade closes at the next price after the bar where the idea ended.", "Your spread's own target and stop % always stay as set.",
      "Trailing, break-even and time exits work as before."]);
  });
});

describe("the trading bot form", () => {
  it("shows the exits only for a picked template the server sent choices for, and sends them with the same PAPER request", () => {
    expect(pageSource).toContain("const exitChoices = picked && adoption?.exits ? adoption.exits : null;");
    expect(pageSource).toContain("const request = exitsBody ? { ...adopted, template_exits: exitsBody } : adopted;");
    expect(pageSource).toContain("if (problem || !effectiveId || exitIssue) return;");
    expect(pageSource).toContain("Exits for this bot");
    expect(pageSource).toContain("useEffect(() => { setExitPick(EMPTY_PICK); }, [pickedId]);");    // a new template starts at its defaults
  });
});
