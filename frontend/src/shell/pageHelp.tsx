import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/**
 * UX-6 (owner order UX-2, 25 Oct): no "About this page" paragraph above a page's first data - the page's short help
 * line lives behind the (?) beside the page title in the header. A page states its help with `PageIntro` as before;
 * inside the v2 shell that only hands the line to the header through this context (nothing is drawn in the page).
 * Outside the shell (no provider) `PageIntro` keeps drawing its card, so nothing else changes.
 */
export interface PageHelp {
  /** The page whose help this is (PageIntro's id). */
  id: string;
  /** One short line on how to use the page (several are joined). */
  lines: readonly string[];
  /** "Show me": opens Help on this topic. */
  showMe?: { label?: string; href: string };
}

interface PageHelpSlot {
  help: PageHelp | null;
  set: (help: PageHelp | null) => void;
}

const PageHelpContext = createContext<PageHelpSlot | null>(null);

/** The shell holds one page's help at a time (the page on screen). */
export function PageHelpProvider({ children, initial = null }: { children: ReactNode; initial?: PageHelp | null }) {
  const [help, set] = useState<PageHelp | null>(initial);
  const slot = useMemo(() => ({ help, set }), [help]);
  return <PageHelpContext.Provider value={slot}>{children}</PageHelpContext.Provider>;
}

/** The help of the page on screen, for the header (null: the page has none, or no shell). */
export function usePageHelp(): PageHelp | null {
  return useContext(PageHelpContext)?.help ?? null;
}

/** Whether a shell is there to show the help in its header. */
export function useHasPageHelpSlot(): boolean {
  return useContext(PageHelpContext) !== null;
}

/** Hands this page's help to the header while the page is on screen; `null` hands nothing. */
export function useRegisterPageHelp(help: PageHelp | null): void {
  const set = useContext(PageHelpContext)?.set;
  const text = help ? help.lines.join(" ") : "";
  const id = help?.id ?? null;
  const href = help?.showMe?.href;
  const label = help?.showMe?.label;
  useEffect(() => {
    if (!set || id === null) return undefined;
    set({ id, lines: [text], showMe: href ? { href, label } : undefined });
    return () => set(null);
  }, [set, id, text, href, label]);
}
