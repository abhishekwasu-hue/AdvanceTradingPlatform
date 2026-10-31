/** Z10-e: the staged beta rollout (50 -> 200 -> 500) as the admin console shows it (`GET /api/v1/admin/beta/cohort`).
 * Pure helpers, no React. */
import { formatDateTimeIST } from "../ui/format";

export interface BetaCohort {
  gate_on: boolean;
  /** The effective cap the gate enforces: min(stage, env_cap). */
  cap: number;
  used: number;
  remaining: number;
  stage: number;
  stages: number[];
  next_stage: number | null;
  /** BETA_COHORT_CAP from the server's environment. */
  env_cap: number;
  capped_by_env: boolean;
  headroom: number;
  stage_source: "admin" | "default";
  stage_set_by: number | null;
  stage_set_at: string | null;
}

/** "47 of 50 places used · 3 left" */
export function usageLine(c: BetaCohort): string {
  return `${c.used} of ${c.cap} places used · ${c.headroom} left`;
}

/** 0..100, for the meter. */
export function usedPercent(c: BetaCohort): number {
  if (c.cap <= 0) return 100;
  return Math.max(0, Math.min(100, Math.round((c.used / c.cap) * 100)));
}

/** Why the effective cap is what it is. */
export function capNote(c: BetaCohort): string {
  if (c.capped_by_env) return `Effective cap ${c.cap}: BETA_COHORT_CAP (${c.env_cap}) is below the stage (${c.stage}).`;
  return `Effective cap ${c.cap} = stage ${c.stage} (BETA_COHORT_CAP ${c.env_cap}).`;
}

export function stageSourceNote(c: BetaCohort): string {
  if (c.stage_source === "default") return "Stage from the BETA_COHORT_STAGE setting; no stage has been set here yet.";
  return c.stage_set_at ? `Set here on ${formatDateTimeIST(new Date(c.stage_set_at))}.` : "Set here.";
}

/** The stages below the current one (moving down may skip; it never touches existing accounts). */
export function lowerStages(c: BetaCohort): number[] {
  return c.stages.filter((s) => s < c.stage);
}
