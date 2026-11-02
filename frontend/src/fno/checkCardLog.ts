// M77 SX-6 (OPEN_QUESTIONS M77Q-6): the check card answers the ticket received in this tab, exactly as the server signed
// them, so "Save study" can hand them to the server. The server keeps one only when its signature verifies (his own
// answer, this underlying, checked before the study); the page never changes or makes up a card. Memory only (this tab).

import type { ChecksAnswer } from "./checkCard";

export interface SignedCard { instrument: string; findings: unknown[]; checked_at: string; signature: string }

const MAX_KEPT = 24;
let kept: SignedCard[] = [];

/** Keeps one signed answer (the newest last; an answer without a signature is not kept). */
export function recordCard(answer: Pick<ChecksAnswer, "instrument" | "findings"> & { checked_at?: string; signature?: string }): void {
  if (!answer.signature || !answer.checked_at || !answer.instrument) return;
  const card: SignedCard = { instrument: answer.instrument, findings: answer.findings, checked_at: answer.checked_at, signature: answer.signature };
  kept = [...kept.filter((c) => c.signature !== card.signature), card].slice(-MAX_KEPT);
}

/** Every signed answer kept in this tab, oldest first. The server keeps only those of the study's own underlying (the
 * contract key starts with the master's name, NIFTY|2026-11-03|24500|CE - a name the page does not map itself). */
export function keptCards(): SignedCard[] {
  return [...kept];
}

/** Test hook. */
export function clearCards(): void {
  kept = [];
}
