// Z10-f: the support bot's reply and the small rules the help dialog follows. The bot answers how-to questions from the
// help and legal pages only; it refuses advice, account-data and order questions, and hands anything it cannot match
// to the feedback form. It is behind the flag `support_bot` (off by default): the help button is hidden while it is off.

export type SupportOutcome = "answer" | "handoff" | "refused";
export type SupportRefusal = "advice" | "account" | "orders";

export interface SupportLink { title: string; url: string; source: "help" | "legal" }

export interface SupportReply {
  outcome: SupportOutcome;
  answer: string;
  source: "help" | "ai";
  confidence: number | null;
  links: SupportLink[];
  refusal: SupportRefusal | null;
  handoff: boolean;
  feedback_draft: string | null;
  note?: string;
}

export const QUESTION_MIN_CHARS = 2;
export const QUESTION_MAX_CHARS = 500;

export const SUGGESTED_QUESTIONS: readonly string[] = [
  "How do I connect my broker?",
  "How do I get alerts on Telegram?",
  "How do I close a paper position?",
  "How do I turn on two-factor authentication?",
  "Does the platform place real orders?",
];

/** The trimmed question, or null when it cannot be sent. */
export function supportQuestion(text: string): string | null {
  const q = (text || "").replace(/\s+/g, " ").trim();
  return q.length >= QUESTION_MIN_CHARS && q.length <= QUESTION_MAX_CHARS ? q : null;
}

/** Only in-app paths are followed from an answer (the server sends nothing else; anything else is dropped). */
export function safeLinks(links: SupportLink[] | undefined): SupportLink[] {
  return (links ?? []).filter((l) => /^\/(?!\/)[A-Za-z0-9/_-]*$/.test(l.url));
}

/** The label under an answer: where it came from. */
export function sourceLabel(reply: Pick<SupportReply, "outcome" | "source">): string {
  if (reply.outcome === "refused") return "Not something the help desk answers";
  if (reply.outcome === "handoff") return "Not in the help pages";
  return reply.source === "ai" ? "From the help pages, worded by your organisation's AI provider" : "From the help pages";
}
