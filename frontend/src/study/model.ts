/**
 * M77 SX-7 (owner order M77 PD-1; docs/design/M77_REVIEW.md §6): the Save study sheet and My studies - pure parts.
 * The browser sends only references and his own inputs (the chart's instrument, the timeframes he ticks, the sections
 * he ticks, his title and his notes); the server reads every number itself. Nothing here is chosen for him: the
 * timeframe of his open chart is ticked because it is the chart he is studying; every section is on (PD-1: he unticks).
 */
import { formatNumber } from "../ui/format";

export const STUDY_TIMEFRAMES = [
  { id: "1d", label: "Daily" }, { id: "1h", label: "1 hour" }, { id: "15m", label: "15 min" }, { id: "5m", label: "5 min" },
] as const;
export const MAX_TIMEFRAMES = 4;
export const NOTES_MAX = 5000;

export const SECTIONS = [
  { id: "cover", label: "Cover: symbol, time, data as of" },
  { id: "charts", label: "Charts, one per timeframe" },
  { id: "structure", label: "Structure table" },
  { id: "areas", label: "Areas table" },
  { id: "ai_card", label: "AI explanation card" },
  { id: "option_context", label: "Option context (F&O)" },
  { id: "trade_plan", label: "Trade plan" },
  { id: "pre_trade_check", label: "Pre-trade check" },
  { id: "scenarios", label: "Scenarios" },
  { id: "similar_setups", label: "Similar past setups" },
  { id: "notes", label: "Your notes and checklist" },
] as const;
export type SectionId = (typeof SECTIONS)[number]["id"];

export type DownloadFormat = "pdf" | "png" | "png-one" | "csv" | "json";
export const FORMATS: readonly { id: DownloadFormat; label: string; hint: string }[] = [
  { id: "pdf", label: "PDF", hint: "A4 pages: charts and tables" },
  { id: "png", label: "Image (all charts)", hint: "One phone-friendly picture" },
  { id: "png-one", label: "Image (this chart)", hint: "The chart you have open" },
  { id: "csv", label: "CSV tables", hint: "The tables in a ZIP" },
  { id: "json", label: "JSON", hint: "For your own tools" },
];

export interface SaveStudyBody {
  instrument: string; timeframes: string[]; sections: SectionId[]; title?: string; notes?: string;
}
export interface StudySummary {
  id: number; title: string | null; symbol: string; exchange: string; instrument_kind: string | null; timeframes: string[];
  sections: string[]; created_at: string; fingerprint: string; size_bytes: number; reading_source: string;
}
export interface StudyList { items: StudySummary[]; total: number }

export interface SheetState { timeframes: string[]; sections: SectionId[]; format: DownloadFormat; title: string; notes: string }

/** The sheet as it opens: his open chart's timeframe ticked (when the study offers it), every section on, PDF. Pure. */
export function initialSheet(openTf: string | null): SheetState {
  const offered = STUDY_TIMEFRAMES.some((t) => t.id === openTf);
  return { timeframes: offered && openTf ? [openTf] : [], sections: SECTIONS.map((s) => s.id), format: "pdf", title: "", notes: "" };
}

/** Ticks or unticks one item, keeping the list's own order. Pure. */
export function toggle<T extends string>(list: readonly T[], id: T, order: readonly T[]): T[] {
  const next = list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
  return order.filter((x) => next.includes(x));
}

/** Why Save cannot run yet, or null. Pure. */
export function sheetProblem(s: SheetState, format: DownloadFormat, openTf: string | null): string | null {
  if (!s.timeframes.length) return "Tick at least one timeframe.";
  if (s.timeframes.length > MAX_TIMEFRAMES) return `Tick at most ${MAX_TIMEFRAMES} timeframes.`;
  if (!s.sections.length) return "Tick at least one section.";
  if (s.notes.length > NOTES_MAX) return `Your notes are longer than ${formatNumber(NOTES_MAX)} characters.`;
  if ((format === "png" || format === "png-one") && !s.sections.includes("charts")) return "An image needs the Charts section ticked.";
  if (format === "png-one" && !(openTf && s.timeframes.includes(openTf))) return "Tick the timeframe of the chart you have open for its image.";
  if (format === "csv" && !s.sections.some((x) => x === "structure" || x === "areas" || x === "charts" || x === "notes" || x === "ai_card" || x === "option_context"))
    return "CSV needs a section with a table.";
  return null;
}

/** The request body: references and his own words only - never a number from the page. Pure. */
export function saveBody(instrument: string, s: SheetState): SaveStudyBody {
  const body: SaveStudyBody = { instrument, timeframes: [...s.timeframes], sections: [...s.sections] };
  if (s.title.trim()) body.title = s.title.trim().slice(0, 120);
  if (s.notes.trim()) body.notes = s.notes;
  return body;
}

/** The words of a study's timeframes ("Daily, 15 min"). Pure. */
export function timeframeWords(tfs: readonly string[]): string {
  return tfs.map((t) => STUDY_TIMEFRAMES.find((x) => x.id === t)?.label ?? t).join(", ");
}

/** The formats a saved study offers (an image only when it holds charts; one chart per timeframe it holds). Pure. */
export function studyFormats(s: Pick<StudySummary, "sections" | "timeframes">): { format: DownloadFormat; tf?: string; label: string }[] {
  const out: { format: DownloadFormat; tf?: string; label: string }[] = [{ format: "pdf", label: "PDF" }];
  if (s.sections.includes("charts")) {
    out.push({ format: "png", label: "Image" });
    if (s.timeframes.length > 1) for (const tf of s.timeframes) out.push({ format: "png-one", tf, label: `Image ${timeframeWords([tf])}` });
  }
  out.push({ format: "csv", label: "CSV" }, { format: "json", label: "JSON" });
  return out;
}
