import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { capNote, lowerStages, usageLine, usedPercent, type BetaCohort } from "../beta/rollout";
import BetaRolloutCard from "./BetaRolloutCard";

const base: BetaCohort = {
  gate_on: true, cap: 50, used: 47, remaining: 3, stage: 50, stages: [50, 200, 500], next_stage: 200, env_cap: 500,
  capped_by_env: false, headroom: 3, stage_source: "default", stage_set_by: null, stage_set_at: null,
};
const render = (c: BetaCohort | null) => renderToStaticMarkup(<BetaRolloutCard cohort={c} busy={false} onSetStage={() => undefined} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("BetaRolloutCard (Z10-e)", () => {
  it("shows the stage, the effective cap and the headroom", () => {
    const html = render(base);
    const words = text(html);
    expect(words).toContain("47 of 50 places used · 3 left");
    expect(words).toContain("94% used");
    expect(words).toContain("Effective cap 50 = stage 50 (BETA_COHORT_CAP 500).");
    expect(words).toContain("Stage from the BETA_COHORT_STAGE setting");
    expect(words).toContain("Move up to 200");
    expect(html).toContain('aria-current="step"');
    expect(html).toContain('role="meter"');
    expect(html).toContain('aria-valuetext="47 of 50 places used · 3 left"');
    expect(html).toContain('aria-label="Rollout stages"');
  });

  it("disables moving up until a reason is typed (the button starts disabled)", () => {
    expect(render(base)).toMatch(/<button type="button" disabled=""[^>]*>Move up to 200/);
  });

  it("offers moving down from a higher stage and says when the last stage is reached", () => {
    const top = { ...base, stage: 500, cap: 500, next_stage: null, used: 500, headroom: 0, remaining: 0, stage_source: "admin" as const,
                  stage_set_at: "2026-10-13T05:30:00+00:00" };
    const words = text(render(top));
    expect(words).toContain("This is the last stage.");
    expect(words).toContain("Move down to 50");
    expect(words).toContain("Move down to 200");
    expect(words).toContain("Full");
    expect(words).toContain("Set here on");
  });

  it("says when the env cap binds and when the gate is off", () => {
    const capped = { ...base, stage: 200, cap: 120, env_cap: 120, capped_by_env: true, next_stage: 500 };
    expect(capNote(capped)).toBe("Effective cap 120: BETA_COHORT_CAP (120) is below the stage (200).");
    expect(text(render({ ...base, gate_on: false }))).toContain("The beta_cohort_gate flag is off");
    expect(text(render(null))).toContain("Loading the cohort");
  });

  it("helpers", () => {
    expect(usageLine(base)).toBe("47 of 50 places used · 3 left");
    expect(usedPercent({ ...base, used: 80 })).toBe(100);
    expect(usedPercent({ ...base, cap: 0 })).toBe(100);
    expect(lowerStages({ ...base, stage: 200 })).toEqual([50]);
  });

  it("uses no Devanagari", () => {
    expect(/[ऀ-ॿ]/.test(text(render(base)))).toBe(false);
  });
});
