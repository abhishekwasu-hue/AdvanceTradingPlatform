import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { nodeText } from "../ui/nodeText";

/**
 * UX-6 (owner order UX-2, 25 Oct): no "About this page" paragraph above a page's first data - the page's short help
 * line lives behind the (?) beside the page title in the header. A page states its help with `PageIntro` as before;
 * inside the v2 shell that only hands the line to the header through this context (nothing is drawn in the page).
 * Outside the shell (no provider) `PageIntro` keeps drawing its card, so nothing else changes.
 *
 * PROSE-1 (owner 30 Oct, no theory text on a page): the page's subtitle (PageHeader `line`, PageTitleV2 `subtitle`) goes
 * behind the same (?) - first in it, then the help line - and is no longer drawn under the title inside the shell.
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
  subtitle: ReactNode;
  setSubtitle: (subtitle: ReactNode) => void;
}

const PageHelpContext = createContext<PageHelpSlot | null>(null);

/** The shell holds one page's help at a time (the page on screen). */
export function PageHelpProvider({ children, initial = null, initialSubtitle = null }: { children: ReactNode; initial?: PageHelp | null; initialSubtitle?: ReactNode }) {
  const [help, set] = useState<PageHelp | null>(initial);
  const [subtitle, setSubtitle] = useState<ReactNode>(initialSubtitle);
  const slot = useMemo(() => ({ help, set, subtitle, setSubtitle }), [help, subtitle]);
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

/** PROSE-1: the subtitle of the page on screen, for the header's (?) (null: none, or no shell). */
export function usePageSubtitle(): ReactNode {
  return useContext(PageHelpContext)?.subtitle ?? null;
}

/** PROSE-1: hands the page's subtitle to the header's (?) while the page is on screen (keyed by its text). */
export function useRegisterPageSubtitle(subtitle: ReactNode): void {
  const setSubtitle = useContext(PageHelpContext)?.setSubtitle;
  const latest = useRef(subtitle);
  latest.current = subtitle;
  const key = nodeText(subtitle);
  useEffect(() => {
    if (!setSubtitle || key === "") return undefined;
    setSubtitle(latest.current);
    return () => setSubtitle(null);
  }, [setSubtitle, key]);
}
