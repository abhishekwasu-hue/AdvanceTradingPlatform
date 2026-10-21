/**
 * UI2-4c (Charts page plan §3, CHP-8): the drawing rail's tools without React. The rail holds the brief's tools -
 * trend line, horizontal line, ray, rectangle, text, measure - then Hide all and Delete all. Every other kind the
 * drawings store knows (Fibonacci, channel, vertical line, long and short position) sits under "More", so a drawing of
 * those kinds made earlier still shows, moves and can be drawn again.
 */
import { ANCHOR_RULES, type DrawingKind } from "../charting/drawings";

export interface RailTool { kind: DrawingKind; label: string }

/** The rail, top to bottom (CHP-8). */
export const RAIL_TOOLS: readonly RailTool[] = [
  { kind: "trendline", label: "Trend line" },
  { kind: "hline", label: "Horizontal line" },
  { kind: "ray", label: "Ray" },
  { kind: "rectangle", label: "Rectangle" },
  { kind: "text", label: "Text" },
  { kind: "measure", label: "Measure" },
];

/** Under "More": the other CH kinds. */
export const MORE_TOOLS: readonly RailTool[] = [
  { kind: "fib_retracement", label: "Fib retracement" },
  { kind: "fib_extension", label: "Fib extension" },
  { kind: "channel", label: "Parallel channel" },
  { kind: "vline", label: "Vertical line" },
  { kind: "long_position", label: "Long position" },
  { kind: "short_position", label: "Short position" },
];

export const isMoreTool = (kind: DrawingKind | null | undefined): boolean => !!kind && MORE_TOOLS.some((t) => t.kind === kind);

export const toolLabel = (kind: DrawingKind): string => [...RAIL_TOOLS, ...MORE_TOOLS].find((t) => t.kind === kind)?.label ?? kind;

/** What to do while a tool is picked: how many points to place, and how to stop. */
export function placeHint(kind: DrawingKind | null | undefined, touch = false): string | null {
  if (!kind) return null;
  const n = ANCHOR_RULES[kind]?.length ?? 1;
  const verb = touch ? "Tap" : "Click";
  const where = n === 1 ? `${verb} the chart to place the ${toolLabel(kind).toLowerCase()}.` : `${verb} ${n} points on the chart for the ${toolLabel(kind).toLowerCase()}.`;
  return touch ? where : `${where} Press Esc to cancel.`;
}

/** The "Delete all" question: what goes, where, what stays, and that Undo cannot bring it back (UI-11). */
export function deleteAllText(symbol: string, drawings: readonly { locked: boolean }[]): { title: string; body: string } {
  const locked = drawings.filter((d) => d.locked).length;
  const gone = drawings.length - locked;
  const noun = (n: number) => (n === 1 ? "drawing" : "drawings");
  const kept = locked ? ` ${locked} locked ${noun(locked)} ${locked === 1 ? "stays" : "stay"}.` : "";
  return {
    title: `Delete ${gone} ${noun(gone)} on ${symbol}?`,
    body: `They are removed from every timeframe of ${symbol}, and Undo cannot bring them back.${kept}`,
  };
}
