/**
 * M97 PR-5 (Z6): the Supply & Demand zones overlay's options as the server stores them (`ChartPrefs.sd_overlay`,
 * backend app/me/prefs.py) and the trading-style presets. A preset only preselects the overlay's timeframes; the user
 * can edit them, and an edit is kept as his own timeframes for that style. Pure: no React, no network.
 */
export const SD_TIMEFRAMES = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "1d", "1w", "1M"] as const;
export type SdTimeframe = (typeof SD_TIMEFRAMES)[number];
/** At most this many timeframes at once (the server's MAX_TIMEFRAMES / MAX_SD_TIMEFRAMES). */
export const SD_MAX_TIMEFRAMES = 4;

export const SD_STYLES = ["investor", "positional", "swing", "short_term", "intraday", "scalper"] as const;
export type SdStyle = (typeof SD_STYLES)[number];
/** The owner's ladder (M97 Z6): investor M, positional W, swing D / 2h, short-term 60m / 30m, intraday 15m / 10m, scalper 5m / 3m. */
export const SD_STYLE_TIMEFRAMES: Record<SdStyle, readonly SdTimeframe[]> = {
  investor: ["1M"], positional: ["1w"], swing: ["1d", "2h"], short_term: ["1h", "30m"], intraday: ["15m", "10m"], scalper: ["5m", "3m"],
};
export const SD_STYLE_LABELS: Record<SdStyle, string> = {
  investor: "Investor", positional: "Positional", swing: "Swing", short_term: "Short-term", intraday: "Intraday", scalper: "Scalper",
};

export interface SdOverlayPrefs {
  /** The chosen style; null = the user's own timeframes. */
  style: SdStyle | null;
  /** The timeframes shown; null = the chart's own. */
  timeframes: SdTimeframe[] | null;
  /** The user's own timeframes for a style he edited (a style left out keeps its ladder). */
  presets: Partial<Record<SdStyle, SdTimeframe[]>>;
  show_violated: boolean;
}
export const DEFAULT_SD_OVERLAY: SdOverlayPrefs = { style: null, timeframes: null, presets: {}, show_violated: false };

const isStyle = (x: unknown): x is SdStyle => typeof x === "string" && (SD_STYLES as readonly string[]).includes(x);

/** A stored list as valid timeframes: known ones, once each, in the list's order, at most four; none -> null. */
export function cleanSdTimeframes(raw: unknown): SdTimeframe[] | null {
  if (!Array.isArray(raw)) return null;
  const tfs = SD_TIMEFRAMES.filter((t) => raw.includes(t)).slice(0, SD_MAX_TIMEFRAMES);
  return tfs.length ? tfs : null;
}

/** Any value (the server's, the cache's) as valid overlay options; a wrong field takes its default. */
export function cleanSdOverlay(raw: unknown): SdOverlayPrefs {
  const v = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const presets: SdOverlayPrefs["presets"] = {};
  const given = (v.presets && typeof v.presets === "object" ? v.presets : {}) as Record<string, unknown>;
  for (const style of SD_STYLES) {
    const tfs = cleanSdTimeframes(given[style]);
    if (tfs) presets[style] = tfs;
  }
  return { style: isStyle(v.style) ? v.style : null, timeframes: cleanSdTimeframes(v.timeframes), presets, show_violated: v.show_violated === true };
}

/** A style's timeframes: the user's own edit of it, else the ladder. */
export const styleTimeframes = (o: SdOverlayPrefs, style: SdStyle): SdTimeframe[] => [...(o.presets[style] ?? SD_STYLE_TIMEFRAMES[style])];

/** Choosing a style preselects its timeframes (null: back to the user's own choice, the timeframes as they are). */
export function pickStyle(o: SdOverlayPrefs, style: SdStyle | null): SdOverlayPrefs {
  return style === null ? { ...o, style: null } : { ...o, style, timeframes: styleTimeframes(o, style) };
}

/** The timeframes edited: shown at once, and kept as the chosen style's own (an edit back to the ladder clears it). */
export function editTimeframes(o: SdOverlayPrefs, tfs: readonly SdTimeframe[]): SdOverlayPrefs {
  const timeframes = cleanSdTimeframes(tfs);
  if (!o.style) return { ...o, timeframes };
  const presets = { ...o.presets };
  const ladder = SD_STYLE_TIMEFRAMES[o.style];
  if (!timeframes || (timeframes.length === ladder.length && timeframes.every((t) => ladder.includes(t)))) delete presets[o.style];
  else presets[o.style] = timeframes;
  return { ...o, timeframes, presets };
}
