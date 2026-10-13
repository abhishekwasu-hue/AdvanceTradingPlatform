// N2-2: pure helpers for the experience level card. The server derives the level from the user's own PAPER record and
// says which gates are open; this only shapes it for display.
import type { TFunction } from "i18next";
import type { ExperienceStatus } from "../types";

/** The first level not reached yet (with its criteria), or null when every level is reached. */
export function nextLevel(s: ExperienceStatus): ExperienceStatus["levels"][number] | null {
  return s.levels.find((l) => !l.reached) ?? null;
}

export type GateState = "allowed" | "optedOut" | "shut";

export function gateState(g: ExperienceStatus["gates"][number]): GateState {
  if (g.allowed_by_level) return "allowed";
  return g.opted_out_today ? "optedOut" : "shut";
}

export function gateLine(g: ExperienceStatus["gates"][number], t: TFunction<"progression">): string {
  const state = gateState(g);
  return state === "shut" ? t("experience.gate.shut", { level: g.opens_at_label }) : t(`experience.gate.${state}`);
}

/** Whether the typed reason is long enough to send (the server checks again). */
export function reasonOk(reason: string, min: number): boolean {
  return reason.trim().length >= min;
}
