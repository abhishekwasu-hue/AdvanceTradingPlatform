import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/errors";
import { thenT } from "./i18n";
import { SCAN_THEN_EN } from "./locales/en";
import {
  bodyOf, chipKey, codeLabel, CONFIRM_TFS, GATE_FIELDS, gateErrors, isOwnerConflict, isThenTemplate, levelsVars, needsTakeOver, outcomeOf,
  placesAutoPaper, THEN_MODES, visibleModes, withOverride,
  type ThenIntent, type ThenTemplate,
} from "./model";
import { AutoPaperBox, ThenChip, ThenDrawerView } from "./ThenPanel";
import sample from "./thenSample.json";

/** N1 X1b: the Then chip and drawer. `thenSample.json` is the server's own payload (backend
 * tests/test_n1_x1_scan_then.py fails when its keys drift from GET /then and /then/intents). */
const template = sample.template as unknown as ThenTemplate;
const intents = sample.intents as unknown as ThenIntent[];
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const DEVANAGARI = /[\u0900-\u097f]/;
const SOURCES = import.meta.glob<string>(["./*.ts", "./*.tsx", "!./*.test.tsx"], { query: "?raw", import: "default", eager: true });

function lookup(path: string): unknown {
  return path.split(".").reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined), SCAN_THEN_EN);
}

function strings(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (node && typeof node === "object") return Object.values(node).flatMap(strings);
  return [];
}

describe("scan Then i18n (namespace scanThen)", () => {
  it("has an English string for every literal key the code asks for", () => {
    const missing: string[] = [];
    for (const [file, src] of Object.entries(SOURCES)) {
      for (const m of src.matchAll(/\bt\(\s*"([A-Za-z0-9_.]+)"/g)) if (typeof lookup(m[1]) !== "string") missing.push(`${file}: ${m[1]}`);
    }
    expect(missing).toEqual([]);
    expect(Object.keys(SOURCES).length).toBeGreaterThanOrEqual(3);
  });

  it("has the built keys too: every mode, timeframe, gate field and chip", () => {
    for (const m of THEN_MODES) {
      expect(typeof lookup(`modes.${m}`)).toBe("string");
      expect(typeof lookup(`chip.${m}`)).toBe("string");
    }
    for (const tf of CONFIRM_TFS) expect(typeof lookup(`tfs.${tf}`)).toBe("string");
    for (const f of GATE_FIELDS) expect(typeof lookup(`gate.${f.key}`)).toBe("string");
    for (const code of Object.values(template.status ?? {}).map((s) => s.code)) expect(typeof lookup(`codes.${code}`)).toBe("string");
  });

  it("is English only and makes no recommendation", () => {
    const all = strings(SCAN_THEN_EN);
    expect(all.filter((s) => DEVANAGARI.test(s))).toEqual([]);
    for (const s of all) expect(s.toLowerCase()).not.toMatch(/\b(we recommend|best|guarantee|sure profit)\b/);
  });
});

describe("the Then model", () => {
  it("never offers Auto LIVE, even if a server listed it", () => {
    expect(THEN_MODES).not.toContain("AUTO_LIVE" as never);
    expect(visibleModes()).toEqual(["NOTIFY", "ONE_CLICK", "AUTO_PAPER"]);
    expect(isThenTemplate(template)).toBe(true);
    expect(isThenTemplate({ detail: "x" })).toBe(false);
    expect(bodyOf({ ...template, mode: "AUTO_LIVE" }).mode).toBe("NOTIFY");
  });

  it("picks the chip from the template", () => {
    expect(chipKey(null)).toBe("chip.off");
    expect(chipKey({ ...template, configured: false })).toBe("chip.off");
    expect(chipKey({ ...template, enabled: false })).toBe("chip.paused");
    expect(chipKey(template)).toBe("chip.ONE_CLICK");
  });

  it("checks the gate overrides within the server's bounds", () => {
    expect(gateErrors({ min_rr: 2.5, max_armed_bars: 10, wait_next_candle: true }, thenT)).toEqual([]);
    expect(gateErrors({ min_rr: 0.5 }, thenT)).toEqual(["Minimum reward:risk: between 1 and 10."]);
    expect(gateErrors({ max_armed_bars: 3.5 }, thenT)[0]).toMatch(/whole number/);
    expect(gateErrors({ mr_n: Number.NaN }, thenT)[0]).toMatch(/a number/);
    expect(withOverride({ min_rr: 2 }, "min_rr", "")).toEqual({});
    expect(withOverride({}, "min_rr", "2.5")).toEqual({ min_rr: 2.5 });
    expect(withOverride({}, "wait_next_candle", true)).toEqual({ wait_next_candle: true });
  });

  it("says what happened to each setup", () => {
    const base = intents[0];
    expect(thenT(outcomeOf(base).key, outcomeOf(base).vars)).toBe("Proposal #55");
    expect(outcomeOf({ ...base, route: "NOTIFY" }).key).toBe("intents.outcomes.notified");
    expect(thenT(outcomeOf({ ...base, status: "REFUSED", reason: "paused" }).key, { reason: "paused" })).toBe("Not sent: paused");
    expect(outcomeOf({ ...base, status: "EXPIRED" }).key).toBe("intents.outcomes.expired");
    expect(codeLabel("SOMETHING_NEW", thenT)).toBe("SOMETHING_NEW");
    expect(levelsVars(base).sl).toBe("22,234.9");
  });
});

describe("the Then drawer and chip (server sample)", () => {
  it("shows the mode chip", () => {
    expect(text(renderToStaticMarkup(<ThenChip template={template} />))).toContain("Then: One-click");
    expect(text(renderToStaticMarkup(<ThenChip template={null} />))).toContain("Then: off");
  });

  it("shows the per-symbol status with its reason code and the setups sent, and never Auto LIVE", () => {
    const html = renderToStaticMarkup(<ThenDrawerView template={template} intents={intents} body={bodyOf(template)} onBody={() => {}}
      errors={[]} notice="" busy={false} onSave={() => {}} onRemove={() => {}} />);
    const out = text(html);
    expect(out).toContain("Confirmed - the setup was sent");
    expect(html).toContain('data-code="GATE_CONFIRMED"');
    expect(out).toContain("GATEDEMO");
    expect(out).toContain("Proposal #55");
    expect(out).toContain("entry 22,252.66 · SL 22,234.9 · target 22,306 · R:R 3");
    expect(out).toContain("Setups sent to you");
    expect(out).toContain("nothing is placed without your approval");
    expect(out).not.toMatch(/AUTO_LIVE|Auto LIVE/);
  });

  it("says the last check is not shown to other members, and names the new codes", () => {
    const hidden = { ...template, status: {}, last_problem: null, status_shown: false };
    const out = text(renderToStaticMarkup(<ThenDrawerView template={hidden} intents={[]} body={bodyOf(hidden)} onBody={() => {}}
      errors={[]} notice="" busy={false} onSave={() => {}} onRemove={() => {}} />));
    expect(out).toContain("Shown to the template's owner and the organisation owner only.");
    expect(out).not.toContain("Not checked yet.");
    for (const code of ["DATA_LATE", "TREND_PENDING", "SKIPPED_GATE_CONFLICT", "SKIPPED_INTENT_REFUSED"]) expect(codeLabel(code, thenT)).not.toBe(code);
  });

  it("says who set the Then and asks before taking another member's over (review of X1b)", () => {
    const theirs = { ...template, set_by_you: false };
    const props = { intents, body: bodyOf(theirs), onBody: () => {}, errors: [], notice: "", busy: false, onSave: () => {}, onRemove: () => {} };
    expect(text(renderToStaticMarkup(<ThenDrawerView template={template} {...props} />))).toContain("Set by: you");
    const asked = text(renderToStaticMarkup(<ThenDrawerView template={theirs} {...props} confirming />));
    expect(asked).toContain("Set by: another member");
    expect(asked).toContain("Saving makes you the owner of this Then; setups will go to you, not to the member who set it.");
    expect(asked).toContain("Save and take over");
    expect(asked).not.toContain("Remove Then");
    expect(needsTakeOver(theirs)).toBe(true);
    expect(needsTakeOver(template)).toBe(false);
    expect(needsTakeOver({ ...theirs, configured: false })).toBe(false);
  });

  it("asks before removing another member's Then, and reloads on a 409 save (re-check of X1b)", () => {
    const theirs = { ...template, set_by_you: false };
    const asked = text(renderToStaticMarkup(<ThenDrawerView template={theirs} intents={intents} body={bodyOf(theirs)} onBody={() => {}}
      errors={[]} notice="" busy={false} onSave={() => {}} onRemove={() => {}} confirmingRemove />));
    expect(asked).toContain("Another member set this Then. Removing it stops the setups it sends them for this rule.");
    expect(asked).toContain("Remove their Then");
    expect(asked).not.toContain("Remove Then");
    expect(isOwnerConflict(new ApiError(409, "Another member set this Then."), false)).toBe(true);
    expect(isOwnerConflict(new ApiError(409, "x"), true)).toBe(false);
    expect(isOwnerConflict(new ApiError(422, "x"), false)).toBe(false);
    expect(isOwnerConflict(new Error("x"), false)).toBe(false);
  });

  it("offers no Save or Remove to someone the server would refuse", () => {
    const locked = { ...template, may_change: false };
    const out = text(renderToStaticMarkup(<ThenDrawerView template={locked} intents={intents} body={bodyOf(locked)} onBody={() => {}}
      errors={[]} notice="" busy={false} onSave={() => {}} onRemove={() => {}} />));
    expect(out).toContain("Only the rule's creator or the organisation owner can change this Then.");
    expect(out).not.toContain("Remove Then");
    expect(out).not.toMatch(/\bSave\b/);
  });

  it("asks a new rule to pick a mode, with no status or setups yet", () => {
    const fresh = { ...template, configured: false, status: {} };
    const out = text(renderToStaticMarkup(<ThenDrawerView template={fresh} intents={null} body={bodyOf(fresh)} onBody={() => {}}
      errors={["x"]} notice="" busy={false} onSave={() => {}} onRemove={() => {}} />));
    expect(out).toContain("No Then template yet");
    expect(out).not.toContain("Setups sent to you");
    expect(out).not.toContain("Remove Then");
  });
});

describe("Auto PAPER (N1 X2): armed only with all three switches", () => {
  const auto = { ...template, mode: "AUTO_PAPER" };
  const on = { available: true, armed: true, why: "", today: 1, max_per_day: 3 };

  it("chips 'armed' only when available and armed; anything missing is 'as a proposal'", () => {
    expect(chipKey({ ...auto, auto_paper: on })).toBe("chip.AUTO_PAPER_ARMED");
    expect(placesAutoPaper({ ...auto, auto_paper: on })).toBe(true);
    for (const off of [{ ...on, available: false }, { ...on, armed: false }, undefined]) {
      expect(chipKey({ ...auto, auto_paper: off })).toBe("chip.AUTO_PAPER");
      expect(placesAutoPaper({ ...auto, auto_paper: off })).toBe(false);
    }
    expect(placesAutoPaper({ ...auto, enabled: false, auto_paper: on })).toBe(false);
    expect(text(renderToStaticMarkup(<ThenChip template={{ ...auto, auto_paper: on }} />))).toContain("Then: Auto PAPER - armed");
    expect(text(renderToStaticMarkup(<ThenChip template={auto} />))).toContain("Then: Auto PAPER - as a proposal");
  });

  it("shows why it is a proposal, today's count, and Arm only when the platform offers it", () => {
    const off = text(renderToStaticMarkup(<AutoPaperBox template={{ ...auto, auto_paper: template.auto_paper }} busy={false} onArm={() => {}} />));
    expect(off).toContain("Not placing on its own:");
    expect(off).toContain("operator");
    expect(off).not.toContain("Arm Auto PAPER");
    const can = renderToStaticMarkup(<AutoPaperBox template={{ ...auto, auto_paper: { ...on, armed: false } }} busy={false} onArm={() => {}} />);
    expect(text(can)).toContain("Arm Auto PAPER");
    expect(can).toContain("disabled");                                  // until AUTO_PAPER is typed
    const armed = text(renderToStaticMarkup(<AutoPaperBox template={{ ...auto, auto_paper: on }} busy={false} onDisarm={() => {}} />));
    expect(armed).toContain("Armed: a confirmed setup is placed as a PAPER order");
    expect(armed).toContain("Auto PAPER entries today: 1 of 3");
    expect(armed).toContain("Disarm");
    expect(armed).not.toMatch(/\b(recommend\w*|should|guarantee\w*|win rate)\b/i);
  });

  it("puts the box in the drawer for an Auto PAPER template only", () => {
    const view = (tpl: typeof template) => renderToStaticMarkup(<ThenDrawerView template={tpl} intents={[]} body={bodyOf(tpl)} onBody={() => {}}
      errors={[]} notice="" busy={false} onSave={() => {}} onRemove={() => {}} />);
    expect(view({ ...auto, auto_paper: on })).toContain('data-testid="auto-paper"');
    expect(view(template)).not.toContain('data-testid="auto-paper"');
  });
});
