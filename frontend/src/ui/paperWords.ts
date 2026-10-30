/** CT-1 C8: the words of the one mode badge (ui/PaperBadge.tsx); plain constants so models can use them too. */
export const PAPER_WORD = "PAPER";
export const PAPER_TEXT = "Practice mode: no real order is sent and no money moves.";
export const LIVE_WORD = "LIVE";
export const LIVE_TEXT = "A real order at your broker, with real money.";

/** Whether a screen shows its PAPER badge: any paper row on it (LIVE rows are marked one by one). Pure. */
export function showsPaper(modes: readonly (string | null | undefined)[]): boolean {
  return modes.some((m) => (m ?? "").toUpperCase() === "PAPER");
}
