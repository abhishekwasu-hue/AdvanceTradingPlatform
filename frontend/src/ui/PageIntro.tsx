import { Info, X } from "lucide-react";
import { useCallback, useId, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useTours } from "../tours/state";

/**
 * UI2-1 (UI v2 proposal §9 rule 2): the "What is this page?" card. A page shows it on the trader's first visit: two
 * short lines saying what the page answers and how to use it, an optional "Show me" link to Help, and a close button.
 * Once closed it stays closed for that user (the dismissal is keyed by user id, so another account on the same
 * browser still sees it).
 *
 * Storage: this browser's localStorage for now. UI-8 stores it server-side per user (`GET/PUT /api/me/setup`, built in
 * UI2-7); `IntroStore` is the seam that swap replaces, and localStorage stays the cache.
 *
 * MO2 part C: the card is one of the page's first-time tips. "Skip all tips" (stored on the server, src/tours/state.ts)
 * hides it on every page, and Help -> "Show all tips again" brings every closed card back (`clearIntroDismissals`).
 *
 * Neutral styling: it is information, not a warning. Wording is plain English, no developer terms.
 */

export interface IntroStore {
  read: (key: string) => string | null;
  write: (key: string, value: string) => void;
}

/** The browser's localStorage; a blocked or full storage just means the card shows again next visit. */
export const LOCAL_INTRO_STORE: IntroStore = {
  read: (key) => { try { return localStorage.getItem(key); } catch { return null; } },
  write: (key, value) => { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
};

const storageKey = (userId: number | string) => `atp_page_intro_dismissed:${userId}`;

function readIds(store: IntroStore, userId: number | string): string[] {
  try {
    const parsed: unknown = JSON.parse(store.read(storageKey(userId)) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** True when this user closed the card for page `id`. No user (signed out): never dismissed, never stored. */
export function isIntroDismissed(store: IntroStore, userId: number | string | null, id: string): boolean {
  return userId !== null && readIds(store, userId).includes(id);
}

/** Remembers that this user closed the card for page `id`. */
export function dismissIntro(store: IntroStore, userId: number | string | null, id: string): void {
  if (userId === null) return;
  const ids = readIds(store, userId);
  if (!ids.includes(id)) store.write(storageKey(userId), JSON.stringify([...ids, id]));
}

/** Help -> "Show all tips again": every card this user closed shows again. */
export function clearIntroDismissals(store: IntroStore, userId: number | string | null): void {
  if (userId !== null) store.write(storageKey(userId), "[]");
}

export interface PageIntroViewProps {
  /** The page's question, as the card's title: "What is this page?" by default. */
  title?: string;
  /** Two short lines (one sentence each). */
  lines: readonly string[];
  /** "Show me": opens Help on this topic. Left out until the page has a Help topic. */
  showMe?: { label?: string; href: string };
  onDismiss: () => void;
}

/** The card's look - no storage, so stories and tests render it directly. */
export function PageIntroView({ title = "What is this page?", lines, showMe, onDismiss }: PageIntroViewProps) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} data-page-intro
      className="mb-space-4 flex items-start gap-space-3 rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card">
      <Info size={18} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
      <div className="min-w-0 flex-1">
        <h2 id={titleId} className="text-t15 font-semibold text-fg">{title}</h2>
        {lines.map((line) => <p key={line} className="mt-space-1 text-t14 text-fg-muted">{line}</p>)}
        {showMe && (
          <a href={showMe.href} className="mt-space-2 inline-flex min-h-11 items-center text-t14 font-semibold text-brand hover:underline md:min-h-0">
            {showMe.label ?? "Show me"}
          </a>
        )}
      </div>
      <button type="button" onClick={onDismiss} aria-label="Close this explanation"
        className="-mr-space-2 -mt-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <X size={16} aria-hidden />
      </button>
    </section>
  );
}

/** The card for page `id`, shown until the signed-in user closes it. */
export function PageIntro({ id, store = LOCAL_INTRO_STORE, ...view }: Omit<PageIntroViewProps, "onDismiss"> & { id: string; store?: IntroStore }) {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const tips = useTours(userId);
  const [closedNow, setClosedNow] = useState<string | null>(null);
  const dismiss = useCallback(() => { dismissIntro(store, userId, id); setClosedNow(`${userId}:${id}`); }, [store, userId, id]);
  if (userId === null || tips.marks.skip_all_at || closedNow === `${userId}:${id}` || isIntroDismissed(store, userId, id)) return null;
  return <PageIntroView {...view} onDismiss={dismiss} />;
}
