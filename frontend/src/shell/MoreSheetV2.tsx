import * as RD from "@radix-ui/react-dialog";
import { Download, LifeBuoy, MessageSquarePlus, Search, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { ThemeToggle } from "../components/AppearanceCard";
import { installHelp, isIosSafari, isStandalone, promptInstall, usePwa } from "../pwa/pwa";
import { MORE_SHEET_ID } from "./BottomNavV2";
import { useReturnFocus } from "./returnFocus";
import { StatusDot } from "../broker/marks";
import type { BrokerDot } from "../broker/brokerState";
import { moreSheetPinned, moreSheetSections, navDotFor, navEntryFor, searchPages, sectionTitle, type NavContext, type NavItemV2, type NavSectionV2, type SectionId, type ShellKey } from "./navModel";
import StatusLink from "../status/StatusLink";

/**
 * UI2-1 (UI v2 proposal §4, mockup nav-bottom-mobile "More opens a sheet"): the phone's More sheet. A search box, then
 * every sidebar entry that is not in the bottom bar, grouped (Markets first, Admin last; the sidebar shows the same
 * entries as one flat list).
 * Typing searches every page the trader may see, today's names included ("Screener" finds Find setups). Tiles are
 * 44 px tall at least. Below the pages: install the app (always says why when it cannot), help, feedback and the theme.
 */

export interface MoreSheetContentProps {
  ctx: NavContext;
  current: ShellKey | null;
  query: string;
  onQuery: (q: string) => void;
  onPick: () => void;
  /** P2b: the broker's status dot on the Broker tile. */
  dot?: BrokerDot | null;
}

/** What the sheet lists for a query: the sections when empty, else the matches (each with its section's name). */
export function moreSheetResults(ctx: NavContext, query: string):
  | { kind: "sections"; sections: NavSectionV2[] }
  | { kind: "matches"; items: (NavItemV2 & { section: SectionId })[] } {
  if (!query.trim()) return { kind: "sections", sections: moreSheetSections(ctx) };
  return { kind: "matches", items: searchPages(query, ctx) };
}

function Tile({ item, current, onPick, caption, dot = null }: { item: NavItemV2; current: ShellKey | null; onPick: () => void; caption?: string; dot?: BrokerDot | null }) {
  const Icon = item.icon;
  // UX3-9: a Settings sub-page lights up Settings, a merged page's tab its entry (navEntryFor).
  const active = current === item.key || navEntryFor(current) === item.key;
  return (
    <Link to={item.path} onClick={onPick} aria-current={active ? "page" : undefined}
      className={`flex min-h-11 items-center gap-space-2 rounded-control px-space-3 py-space-2 text-t14 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
        active ? "bg-brand/10 text-fg" : "bg-surface-2 text-fg hover:bg-surface-3"
      }`}>
      <Icon size={18} aria-hidden className={active ? "shrink-0 text-brand" : "shrink-0 text-fg-muted"} />
      <span className="min-w-0">
        <span className="block truncate">{item.label}</span>
        {caption && <span className="block truncate text-t12 font-normal text-fg-muted">{caption}</span>}
      </span>
      {dot && <StatusDot dot={dot} announce className="ml-auto" />}
    </Link>
  );
}

/** The sheet's lists - no dialog, so tests render it directly. */
export function MoreSheetContent({ ctx, current, query, onQuery, onPick, dot = null }: MoreSheetContentProps) {
  const searchId = useId();
  const results = useMemo(() => moreSheetResults(ctx, query), [ctx, query]);
  const pinned = useMemo(() => moreSheetPinned(ctx), [ctx]);
  return (
    <div className="space-y-space-3">
      <div className="relative">
        <label htmlFor={searchId} className="sr-only">Search pages</label>
        <Search size={16} aria-hidden className="pointer-events-none absolute left-space-3 top-1/2 -translate-y-1/2 text-fg-muted" />
        <input id={searchId} type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Search pages"
          autoComplete="off" spellCheck={false}
          className="h-11 w-full rounded-control border border-border bg-surface-2 pl-space-10 pr-space-3 text-sm text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand" />
      </div>
      {results.kind === "sections" && pinned.length > 0 && (
        <div className="grid grid-cols-2 gap-space-2" data-sheet-pinned>
          {pinned.map((item) => <Tile key={item.key} item={item} current={current} onPick={onPick} dot={navDotFor(item.key, dot)} />)}
        </div>
      )}
      {results.kind === "sections" ? results.sections.map((section) => (
        <section key={section.id} aria-label={section.title}>
          <h3 className="mb-space-2 text-t12 font-semibold text-fg-muted">{section.title}</h3>
          <div className="grid grid-cols-2 gap-space-2">
            {section.items.map((item) => <Tile key={item.key} item={item} current={current} onPick={onPick} />)}
          </div>
        </section>
      )) : results.items.length > 0 ? (
        <section aria-label="Matching pages">
          <h3 className="mb-space-2 text-t12 font-semibold text-fg-muted">Pages</h3>
          <div className="grid grid-cols-1 gap-space-2 min-[360px]:grid-cols-2">
            {results.items.map((item) => <Tile key={item.key} item={item} current={current} onPick={onPick} caption={sectionTitle(item.section)} dot={navDotFor(item.key, dot)} />)}
          </div>
        </section>
      ) : (
        <p role="status" className="py-space-4 text-center text-t14 text-fg-muted">No page matches &quot;{query.trim()}&quot;.</p>
      )}
    </div>
  );
}

/** "Install the app": the browser's own prompt when it offers one; otherwise the plain reason (never silence). */
export function InstallEntry() {
  const pwa = usePwa();
  const [message, setMessage] = useState<string | null>(null);
  const standalone = typeof window !== "undefined" && isStandalone();
  if (standalone) return null;
  const onClick = () => {
    const help = installHelp({
      blocked: pwa.blocked, canPrompt: pwa.canPrompt, standalone,
      ios: isIosSafari(navigator.userAgent, navigator.maxTouchPoints),
    });
    if (help.kind === "prompt") { setMessage(null); void promptInstall(); }
    else setMessage(help.text);
  };
  return (
    <div>
      <button type="button" onClick={onClick}
        className="flex min-h-11 w-full items-center gap-space-2 rounded-control bg-surface-2 px-space-3 text-t14 font-medium text-fg hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <Download size={18} aria-hidden className="shrink-0 text-fg-muted" /> Install the app
      </button>
      <p role="status" aria-live="polite" className={message ? "mt-space-2 text-t13 text-fg-muted" : "sr-only"} data-install-message>
        {message ?? ""}
      </p>
    </div>
  );
}

export interface MoreSheetV2Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ctx: NavContext;
  current: ShellKey | null;
  onHelp?: () => void;
  onFeedback?: () => void;
  dot?: BrokerDot | null;
}

export default function MoreSheetV2({ open, onOpenChange, ctx, current, onHelp, onFeedback, dot = null }: MoreSheetV2Props) {
  const [query, setQuery] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  // Closed by a choice or a route change too: the search starts empty next time.
  useEffect(() => { if (!open) setQuery(""); }, [open]);
  const returnFocus = useReturnFocus(open, () => document.querySelector<HTMLElement>("[data-bottom-nav-v2] button"));
  const close = () => onOpenChange(false);
  // A tap on the bottom bar is not "outside": More toggles the sheet itself, and a page link navigates (the shell
  // closes the sheet on every route change).
  const fromBar = (target: EventTarget | null) => target instanceof Element && target.closest(".bottom-nav") !== null;
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50 md:hidden" />
        <RD.Content id={MORE_SHEET_ID} ref={panel} tabIndex={-1} data-more-sheet onCloseAutoFocus={returnFocus}
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => { e.preventDefault(); panel.current?.focus({ preventScroll: true }); }}
          onPointerDownOutside={(e) => { if (fromBar(e.target)) e.preventDefault(); }}
          onInteractOutside={(e) => { if (fromBar(e.target)) e.preventDefault(); }}
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-panel border-t border-subtle bg-surface-1 pb-[calc(4rem+env(safe-area-inset-bottom))] text-fg shadow-raised focus:outline-none md:hidden">
          <div className="flex items-center justify-between px-space-4 pt-space-2">
            <span aria-hidden className="mx-auto h-1 w-10 rounded-pill bg-border" />
          </div>
          <div className="flex items-center justify-between px-space-4 pb-space-2">
            <RD.Title className="text-t15 font-semibold text-fg">All pages</RD.Title>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-space-4 pb-space-4">
            <MoreSheetContent ctx={ctx} current={current} query={query} onQuery={setQuery} onPick={close} dot={dot} />
            {!query.trim() && (
              <section aria-label="App" className="mt-space-4 space-y-space-2">
                <h3 className="text-t12 font-semibold text-fg-muted">App</h3>
                <InstallEntry />
                <div className="grid grid-cols-2 gap-space-2">
                  {onHelp && (
                    <button type="button" onClick={() => { close(); onHelp(); }}
                      className="flex min-h-11 items-center gap-space-2 rounded-control bg-surface-2 px-space-3 text-t14 font-medium text-fg hover:bg-surface-3">
                      <LifeBuoy size={18} aria-hidden className="text-fg-muted" /> Help
                    </button>
                  )}
                  {onFeedback && (
                    <button type="button" onClick={() => { close(); onFeedback(); }}
                      className="flex min-h-11 items-center gap-space-2 rounded-control bg-surface-2 px-space-3 text-t14 font-medium text-fg hover:bg-surface-3">
                      <MessageSquarePlus size={18} aria-hidden className="text-fg-muted" /> Send feedback
                    </button>
                  )}
                  <div className="flex min-h-11 items-center gap-space-2 rounded-control bg-surface-2 pr-space-3 text-t14 font-medium text-fg">
                    <ThemeToggle /> Theme
                  </div>
                </div>
                <div className="flex justify-center empty:hidden" data-more-footer><StatusLink className="px-space-3" /></div>
              </section>
            )}
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
