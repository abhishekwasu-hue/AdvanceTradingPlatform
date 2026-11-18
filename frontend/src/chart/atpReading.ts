import type { LayerBand } from "../charting/engine";
import type { ChartMarker } from "../charting/markers";
import type { LevelLine } from "./chartCore";

/**
 * Owner 15 Nov ("Copilot chart war analysis anotation karun dile pahije" - the Copilot must annotate its analysis on
 * the chart): ATP's own reading of one answer, drawn read-only on its chart - the protected levels per timeframe
 * (labelled lines), the last breaks at their bars (markers), the areas (bands with their role at the price the answer
 * read them at) and the trend per timeframe (a small legend). It rides on the chart's existing level, band and marker
 * layers, never on the user's own drawings; the chart shows one "ATP reading" toggle for it. Only what the answer holds.
 */
export interface AtpReadingTrend { tf: string; label: string; trend: "up" | "down" | "sideways" | null; weak: boolean; text: string }
export interface AtpReading {
  levels: LevelLine[];
  areas: LayerBand[];
  markers: ChartMarker[];
  trends: AtpReadingTrend[];
}

export const EMPTY_READING: AtpReading = { levels: [], areas: [], markers: [], trends: [] };

export function readingIsEmpty(r: AtpReading | null | undefined): boolean {
  return !r || (r.levels.length === 0 && r.areas.length === 0 && r.markers.length === 0 && r.trends.length === 0);
}

/** A reading handed over as navigation state (the answer's "Open chart"): accepted only in its own shape. Pure. */
export function asAtpReading(v: unknown): AtpReading | null {
  if (!v || typeof v !== "object") return null;
  const r = v as Record<string, unknown>;
  const lists = ["levels", "areas", "markers", "trends"] as const;
  if (!lists.every((k) => Array.isArray(r[k]))) return null;
  return { levels: r.levels as LevelLine[], areas: r.areas as LayerBand[], markers: r.markers as ChartMarker[], trends: r.trends as AtpReadingTrend[] };
}

/** The navigation-state key the answer's "Open chart" hands its reading over under. */
export const ATP_READING_STATE = "atpReading";
