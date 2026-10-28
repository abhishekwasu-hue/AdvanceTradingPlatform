import type { ChipTone } from "./Chip";

/**
 * UX-11b (owner, 28 Oct; docs/owner-orders/ATP_ORDER_UX11B.md) - the one colour table, used everywhere:
 *   green --up = up / profit / pass / inside limits / low VIX;  red --down = down / loss / fail / breached / high VIX;
 *   amber --warn = caution / elevated / stale / near invalidation / CHoCH / holiday / >= 80 % of a budget;
 *   blue --info = information / link / action;  grey --fg-muted = neutral / none / sideways / normal.
 * Colour is always paired with a mark (▲ ▼ ◆) or an icon, never alone. The band tables below are the ONE source for
 * TrendChip and LevelChip; the backend sends the same keys (app/rhythm/words.trend_key, market_memory.vix_level).
 */
export type Mark = "▲" | "▼" | "◆" | "!" | "✓" | "✕";
export interface Meaning { tone: ChipTone; mark: Mark; label: string }

export type Trend = "UP" | "DOWN" | "SIDEWAYS";
export const TREND: Record<Trend, Meaning> = {
  UP: { tone: "up", mark: "▲", label: "Up" },
  DOWN: { tone: "down", mark: "▼", label: "Down" },
  SIDEWAYS: { tone: "neutral", mark: "◆", label: "Sideways" },
};

/** India VIX bands - the same edges as the backend (app/ai/market_memory.VIX_BANDS: 12 / 16 / 20). */
export const VIX_BANDS: ReadonlyArray<readonly [number, VixLevel]> = [[12, "LOW"], [16, "NORMAL"], [20, "ELEVATED"]];
export type VixLevel = "LOW" | "NORMAL" | "ELEVATED" | "HIGH";
export function vixLevel(v: number): VixLevel {
  return VIX_BANDS.find(([edge]) => v < edge)?.[1] ?? "HIGH";
}

export type Mood = "POSITIVE" | "NEGATIVE" | "MIXED";
/** A budget's use: under 80 % inside, from 80 % near the limit, at 100 % reached. */
export type BudgetLevel = "INSIDE" | "NEAR" | "REACHED";
export const BUDGET_NEAR = 0.8;
export function budgetLevel(used: number, limit: number): BudgetLevel | null {
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return null;
  const share = used / limit;
  return share >= 1 ? "REACHED" : share >= BUDGET_NEAR ? "NEAR" : "INSIDE";
}

export const LEVELS = {
  vix: {
    LOW: { tone: "up", mark: "▼", label: "Low" },
    NORMAL: { tone: "neutral", mark: "◆", label: "Normal" },
    ELEVATED: { tone: "warn", mark: "▲", label: "Elevated" },
    HIGH: { tone: "down", mark: "▲", label: "High" },
  },
  mood: {
    POSITIVE: { tone: "up", mark: "▲", label: "Positive" },
    NEGATIVE: { tone: "down", mark: "▼", label: "Negative" },
    MIXED: { tone: "neutral", mark: "◆", label: "Mixed" },
  },
  budget: {
    INSIDE: { tone: "up", mark: "✓", label: "Inside" },
    NEAR: { tone: "warn", mark: "!", label: "Near the limit" },
    REACHED: { tone: "down", mark: "✕", label: "Limit reached" },
  },
} as const satisfies { vix: Record<VixLevel, Meaning>; mood: Record<Mood, Meaning>; budget: Record<BudgetLevel, Meaning> };

export type LevelScale = keyof typeof LEVELS;
/** Every tone the chips use (the AA contrast test checks each on every surface). */
export const CHIP_TONES: ChipTone[] = ["up", "down", "warn", "info", "neutral"];
