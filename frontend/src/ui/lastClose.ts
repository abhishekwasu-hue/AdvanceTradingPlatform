import { formatDateIST } from "./format";

/** M86 D2 / D3: the words a user without a broker reads (pure). */

/** "Last close · 6 Nov" (the pattern of the Copilot's chart card, #932); "Last close" alone when the date is unknown. */
export function lastCloseText(date: string | null | undefined): string {
  if (!date) return "Last close";
  const text = formatDateIST(date, { year: false });
  return text === "–" || text === "" ? "Last close" : `Last close · ${text}`;
}
