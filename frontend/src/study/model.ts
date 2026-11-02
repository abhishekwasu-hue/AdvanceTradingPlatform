/**
 * M77 SX-7 (owner order M77 PD-1; docs/design/M77_REVIEW.md §6): the Save study sheet and My studies - pure parts.
 * The browser sends only references and his own inputs (the chart's instrument, the timeframes he ticks, the sections
 * he ticks, his title and his notes); the server reads every number itself. Nothing here is chosen for him: the
 * timeframe of his open chart is ticked because it is the chart he is studying; every section is on (PD-1: he unticks).
 */
import type { Leg } from "../optionsBuilder/model";
import type { SignedCard } from "../fno/checkCardLog";
import { positive, whole, type EquityDraft, type ExitDraft, type PlanKind } from "../copilot/workspace/plan";
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

/**
 * M77 SX-6 (OPEN_QUESTIONS M77Q-5): the trade plan he has open, as references and his own inputs only - each leg's
 * contract, side, lots and the price he typed for it, the equity side / quantity / price, and the exits he set. Never a
 * computed number: the server reads the lot sizes and works out the payoff, margin and scenarios itself.
 */
export interface StudyPlanLeg { expiry: string; strike: number | null; right: "CE" | "PE" | "FUT"; side: "BUY" | "SELL"; lots: number; price: number | null }
export interface StudyPlanExits {
  stop_kind?: string; stop?: number; target_kind?: string; target?: number; trail_kind?: string; trail?: number;
  time_minutes?: number; time_at?: string; combined?: string;
}
export interface StudyPlan {
  kind: PlanKind; legs: StudyPlanLeg[]; equity?: { side?: "BUY" | "SELL"; quantity?: number; price?: number }; exits: StudyPlanExits;
}

export interface SaveStudyBody {
  instrument: string; timeframes: string[]; sections: SectionId[]; title?: string; notes?: string;
  plan?: StudyPlan; check_cards?: SignedCard[];
}

const HH_MM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** The plan panel's state as the study's references (null while no instrument type is chosen). Pure. */
export function studyPlanOf(kind: PlanKind | null, legs: readonly Leg[], equity: EquityDraft, exits: ExitDraft): StudyPlan | null {
  if (!kind) return null;
  const x: StudyPlanExits = {};
  const stop = positive(exits.stop), target = positive(exits.target), trail = positive(exits.trail), minutes = whole(exits.timeMinutes);
  if (exits.stopKind) x.stop_kind = exits.stopKind;
  if (stop != null) x.stop = stop;
  if (exits.targetKind) x.target_kind = exits.targetKind;
  if (target != null) x.target = target;
  if (exits.trailKind) x.trail_kind = exits.trailKind;
  if (trail != null) x.trail = trail;
  if (minutes != null) x.time_minutes = minutes;
  if (HH_MM.test(exits.timeAt)) x.time_at = exits.timeAt;
  if (exits.combined) x.combined = exits.combined;
  if (kind === "EQUITY") {
    const eq: NonNullable<StudyPlan["equity"]> = {};
    const qty = whole(equity.quantity, 10_000_000), price = positive(equity.price);
    if (equity.direction) eq.side = equity.direction;
    if (qty != null) eq.quantity = qty;
    if (price != null) eq.price = price;
    return { kind, legs: [], equity: eq, exits: x };
  }
  return {
    kind, exits: x,
    legs: legs.map((l) => ({
      expiry: l.expiry, strike: l.option_type === "FUT" ? null : l.strike, right: l.option_type, side: l.direction, lots: l.lots,
      price: l.premium > 0 ? l.premium : null,
    })),
  };
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
  if (format === "csv" && !s.sections.some((x) => x === "structure" || x === "areas" || x === "charts" || x === "notes" || x === "ai_card" || x === "option_context"
    || x === "trade_plan" || x === "pre_trade_check" || x === "scenarios" || x === "similar_setups"))
    return "CSV needs a section with a table.";
  return null;
}

const PLAN_SECTIONS: readonly SectionId[] = ["trade_plan", "scenarios", "similar_setups", "option_context"];
const MAX_CARDS = 24;

/** The request body: references and his own words only - never a computed number from the page. SX-6: his open plan
 * (only when a section that reads it is ticked) and the check cards exactly as the server signed them. Pure. */
export function saveBody(instrument: string, s: SheetState, plan: StudyPlan | null = null, cards: readonly SignedCard[] = []): SaveStudyBody {
  const body: SaveStudyBody = { instrument, timeframes: [...s.timeframes], sections: [...s.sections] };
  if (s.title.trim()) body.title = s.title.trim().slice(0, 120);
  if (s.notes.trim()) body.notes = s.notes;
  if (plan && s.sections.some((x) => PLAN_SECTIONS.includes(x))) body.plan = plan;
  if (cards.length && s.sections.includes("pre_trade_check")) body.check_cards = cards.slice(-MAX_CARDS);
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
