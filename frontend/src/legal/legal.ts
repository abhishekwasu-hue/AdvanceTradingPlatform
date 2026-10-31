// Z10-f: the legal pages (Privacy notice, Terms of use, Risk disclosure) and their acceptance. The text is versioned
// content served by the backend (`/api/legal/documents`, public); this module holds the types and the pure helpers the
// pages, the sign-up form and the acceptance banner share.

export type LegalSlug = "terms" | "privacy" | "risk";
export const LEGAL_SLUGS: readonly LegalSlug[] = ["terms", "privacy", "risk"];
export const LEGAL_TITLES: Record<LegalSlug, string> = { terms: "Terms of use", privacy: "Privacy notice", risk: "Risk disclosure" };

export interface LegalSummary {
  slug: LegalSlug;
  title: string;
  version: string;
  sha256: string;
  draft: boolean;
  path: string;
}

export interface LegalDocument extends LegalSummary {
  body: string;
  open_placeholders: string[];
}

export interface LegalAcceptanceRow extends LegalSummary {
  accepted: boolean;
  accepted_at: string | null;
}

export interface LegalAcceptanceStatus {
  documents: LegalAcceptanceRow[];
  pending: LegalSlug[];
  all_accepted: boolean;
}

export function isLegalSlug(value: string | undefined): value is LegalSlug {
  return !!value && (LEGAL_SLUGS as readonly string[]).includes(value);
}

export function legalPath(slug: LegalSlug): string {
  return `/legal/${slug}`;
}

/** What the client read: the version and the SHA-256 of the text it showed (the server refuses a mismatch with 409). */
export interface LegalAckBody { version: string; sha256: string }

/** {slug: {version, sha256}} for the documents named - what the sign-up form and the banner send. */
export function acceptanceFor(docs: Pick<LegalSummary, "slug" | "version" | "sha256">[], only?: readonly LegalSlug[]): Record<string, LegalAckBody> {
  const out: Record<string, LegalAckBody> = {};
  for (const d of docs) if (!only || only.includes(d.slug)) out[d.slug] = { version: d.version, sha256: d.sha256 };
  return out;
}

/** "Terms of use, Privacy notice and Risk disclosure" - the pending documents in a sentence. */
export function listTitles(slugs: readonly LegalSlug[]): string {
  const titles = slugs.map((s) => LEGAL_TITLES[s]);
  if (titles.length <= 1) return titles.join("");
  return `${titles.slice(0, -1).join(", ")} and ${titles[titles.length - 1]}`;
}

// The banner's "Later" lasts for this browser session only: the next sign-in asks again.
const LATER_KEY = "atp_legal_later";

export function laterKey(userId: number, pending: readonly LegalSlug[], acks: Record<string, LegalAckBody>): string {
  return `${userId}:${pending.map((s) => `${s}@${acks[s]?.version ?? ""}:${(acks[s]?.sha256 ?? "").slice(0, 12)}`).join(",")}`;
}

export function deferredFor(key: string): boolean {
  try { return sessionStorage.getItem(LATER_KEY) === key; } catch { return false; }
}

/** Signing out forgets "Later", so the next sign-in asks again. */
export function clearLater(): void {
  try { sessionStorage.removeItem(LATER_KEY); } catch { /* intentional: storage unavailable */ }
}

export function deferLater(key: string): void {
  try { sessionStorage.setItem(LATER_KEY, key); } catch { /* intentional: storage unavailable: the banner just shows again */ }
}
