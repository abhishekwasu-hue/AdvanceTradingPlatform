import { ChevronDown, ChevronUp, Info } from "lucide-react";
import { useCallback, useId, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useHasPageHelpSlot, useRegisterPageHelp } from "../shell/pageHelp";
import { useTours } from "../tours/state";

/**
 * UI2-1 (UI v2 proposal §9 rule 2), reworked by the owner's user-friendly pass (23 Oct, item 5): the page intro. It is
 * ONE short line on how to use the page (the page's subtitle says what it is; the two never repeat each other), with
 * no "What is this page?" box title. It opens on the trader's first visit; "Hide" collapses it to a small "About this
 * page" button, and that choice is remembered per page for that user (keyed by user id, so another account on the
 * same browser still sees it open).
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
  /** Owner's user-friendly pass (23 Oct, item 5): ONE short line - how to use the page. The page's subtitle already
   * says what the page is, so the intro never repeats it (src/ui/pageIntro.test.ts checks every page). Older callers
   * may still pass two lines; they are shown as one paragraph. */
  lines: readonly string[];
  /** "Show me": opens Help on this topic. Left out until the page has a Help topic. */
  showMe?: { label?: string; href: string };
  /** Shown as the short line (open) or only the "About this page" button (collapsed). */
  collapsed?: boolean;
  /** Collapse: remembered for this page. */
  onDismiss: () => void;
  /** Open it again (this visit only; collapsing it again remembers it). */
  onExpand?: () => void;
  /** Kept for older callers; the intro has no heading of its own any more (the page title is the heading). */
  title?: string;
}

export const INTRO_TOGGLE = "About this page";

/** The intro's look - no storage, so stories and tests render it directly. Open: one quiet line with "Hide"; collapsed:
 * a small "About this page" button that opens it again. Neutral styling: it is information, not a warning. */
export function PageIntroView({ lines, showMe, collapsed = false, onDismiss, onExpand }: PageIntroViewProps) {
  const bodyId = useId();
  if (collapsed) {
    return (
      <div data-page-intro="collapsed" className="-mb-space-2 flex">
        <button type="button" onClick={onExpand} aria-expanded={false}
          className="inline-flex min-h-11 items-center gap-space-1 rounded-control text-t13 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0 md:py-space-1">
          <Info size={14} aria-hidden className="shrink-0" />{INTRO_TOGGLE}<ChevronDown size={14} aria-hidden className="shrink-0" />
        </button>
      </div>
    );
  }
  return (
    <section aria-label={INTRO_TOGGLE} data-page-intro="open"
      className="flex items-start gap-space-3 rounded-control border border-subtle bg-surface-1 px-space-3 py-space-2">
      <Info size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
      <div id={bodyId} className="min-w-0 flex-1 text-t14 text-fg-muted">
        <p>{lines.join(" ")}</p>
        {showMe && (
          <a href={showMe.href} className="mt-space-1 inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">
            {showMe.label ?? "Show me"}
          </a>
        )}
      </div>
      <button type="button" onClick={onDismiss} aria-expanded aria-controls={bodyId} aria-label="Hide this tip"
        className="-my-space-1 -mr-space-1 inline-flex min-h-11 shrink-0 items-center gap-space-1 rounded-control px-space-2 text-t13 font-medium text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8">
        Hide<ChevronUp size={14} aria-hidden />
      </button>
    </section>
  );
}

/** The intro for page `id`. Inside the v2 shell (UX-6) it draws nothing in the page: its line goes behind the (?) beside
 * the page title in the header. Elsewhere: open until the signed-in user hides it, then collapsed to "About this page"
 * (remembered per page). "Skip all tips" leaves the card out altogether; a signed-out visitor never sees it. */
export function PageIntro({ id, store = LOCAL_INTRO_STORE, ...view }: Omit<PageIntroViewProps, "onDismiss" | "onExpand" | "collapsed"> & { id: string; store?: IntroStore }) {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const inShell = useHasPageHelpSlot();
  useRegisterPageHelp(inShell ? { id, lines: view.lines, showMe: view.showMe } : null);
  const tips = useTours(userId);
  const [closedNow, setClosedNow] = useState<string | null>(null);
  const [openedNow, setOpenedNow] = useState<string | null>(null);
  const key = `${userId}:${id}`;
  const dismiss = useCallback(() => { dismissIntro(store, userId, id); setClosedNow(`${userId}:${id}`); setOpenedNow(null); }, [store, userId, id]);
  const expand = useCallback(() => setOpenedNow(`${userId}:${id}`), [userId, id]);
  if (inShell || userId === null || tips.marks.skip_all_at) return null;
  const collapsed = openedNow !== key && (closedNow === key || isIntroDismissed(store, userId, id));
  return <PageIntroView {...view} collapsed={collapsed} onDismiss={dismiss} onExpand={expand} />;
}
