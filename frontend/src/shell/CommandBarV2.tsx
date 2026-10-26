import * as RD from "@radix-ui/react-dialog";
import { CandlestickChart, CornerDownLeft, History, Search } from "lucide-react";
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { readRecent, recentKeyFor, rememberSymbol } from "../ui/SymbolPicker";
import { useReturnFocus } from "./returnFocus";
import { findSymbols, mergeHits, recentHits, type SymbolHit } from "./symbolSearch";
import { searchPages, sectionTitle, type NavContext, type NavItemV2, type SectionId } from "./navModel";

/**
 * UI2-1 (UI v2 proposal §9 rule 3): the command bar, opened with Ctrl K (Cmd K on a Mac) or the top bar's search. It
 * jumps to any page the trader may see (hidden pages are never listed), found by today's names too. Arrow keys move,
 * Enter opens, Escape closes.
 * UX-2 (owner order UX-2): symbols first - the user's recent symbols, then indices, shares and F&O contracts
 * ("nifty 22700 ce"; src/shell/symbolSearch.ts) - then pages. Enter on a symbol opens its chart.
 */

/** True on a Mac, iPhone or iPad (Cmd is the command key there; Ctrl K is "delete to end of line" in text fields). */
export function isApplePlatform(platform: string): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** The platform string of this browser ("" outside one). */
export function currentPlatform(): string {
  if (typeof navigator === "undefined") return "";
  const data = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
  return data?.platform || navigator.platform || navigator.userAgent || "";
}

/** Cmd K on Apple devices, Ctrl K elsewhere - never the other modifier, never with Alt or Shift (review M6). */
export function isCommandShortcut(e: Pick<globalThis.KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">, apple: boolean): boolean {
  if (e.altKey || e.shiftKey || e.key.toLowerCase() !== "k") return false;
  return apple ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}

/** True while a dialog other than the command bar and the More sheet is open: Ctrl K / Cmd K is then left alone. */
export function otherDialogOpen(root: Pick<Document, "querySelector">): boolean {
  return root.querySelector('[role="dialog"]:not([data-command-bar]):not([data-more-sheet]), [role="alertdialog"]') !== null;
}

/** The highlighted row after an arrow key: wraps around; -1 when there are no rows. */
export function moveActive(index: number, delta: number, count: number): number {
  if (count <= 0) return -1;
  if (index < 0) return delta > 0 ? 0 : count - 1;
  return (((index + delta) % count) + count) % count;
}

/** "⌘K" on Apple devices, "Ctrl K" elsewhere. */
export function shortcutLabel(platform: string): string {
  return isApplePlatform(platform) ? "⌘K" : "Ctrl K";
}

/** The bar's name for screen readers ("Search"; never the word "symbol", which the pages' own Symbol fields use) and the
 * hint shown in the top bar's box (UX-2: symbols first, the Kite-style example). */
export const COMMAND_LABEL = "Search";
export const COMMAND_HINT = "Search, e.g. reliance or nifty 22700 ce";

export interface CommandBarV2Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ctx: NavContext;
  onGo: (path: string) => void;
  /** The signed-in user (his recent symbols are kept per user on this browser); null while signed out. */
  userId?: number | string | null;
}

export interface CommandListProps {
  query: string;
  results: (NavItemV2 & { section: SectionId })[];
  /** UX-2: the symbol rows, listed before the pages; `active` counts them first. */
  symbols?: readonly SymbolHit[];
  /** The symbol search for the text has not answered yet. */
  searching?: boolean;
  active: number;
  listId: string;
  optionId: (i: number) => string;
  onPick: (path: string) => void;
  onPickSymbol?: (hit: SymbolHit) => void;
  onHover: (i: number) => void;
}

const rowClass = (selected: boolean) => `flex min-h-11 cursor-pointer items-center gap-space-3 rounded-control px-space-3 text-t14 text-fg ${selected ? "bg-brand/10" : ""}`;
const groupClass = "px-space-3 pb-space-1 pt-space-2 text-t12 font-semibold text-fg-muted";

/** The result rows - no dialog, so tests render it directly. Symbols (recent first) above pages, each under a small
 * group heading; one highlighted row across both. */
export function CommandList({ query, results, symbols = [], searching = false, active, listId, optionId, onPick, onPickSymbol, onHover }: CommandListProps) {
  if (results.length === 0 && symbols.length === 0) {
    return (
      <p role="status" className="px-space-4 py-space-6 text-center text-t14 text-fg-muted">
        {searching ? "Searching…" : `No symbol or page matches "${query.trim()}".`}
      </p>
    );
  }
  const offset = symbols.length;
  return (
    <ul id={listId} role="listbox" aria-label="Symbols and pages" className="max-h-[min(60vh,420px)] overflow-y-auto p-space-2">
      {symbols.length > 0 && (
        <li role="presentation" className={groupClass} data-command-group="symbols">{symbols.every((h) => h.type === "symbol" && h.recent) ? "Recent symbols" : "Symbols"}</li>
      )}
      {symbols.map((hit, i) => {
        const selected = i === active;
        const Icon = hit.type === "symbol" && hit.recent ? History : CandlestickChart;
        return (
          <li key={hit.id} id={optionId(i)} role="option" aria-selected={selected} data-command-symbol={hit.type}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPickSymbol?.(hit)} onMouseMove={() => { if (!selected) onHover(i); }} className={rowClass(selected)}>
            <Icon size={16} aria-hidden className={selected ? "shrink-0 text-brand" : "shrink-0 text-fg-muted"} />
            <span className="min-w-0 flex-1 truncate font-medium tabular-nums">{hit.label}</span>
            <span className="shrink-0 truncate text-t12 text-fg-muted">{hit.detail}</span>
            {selected && <CornerDownLeft size={14} aria-hidden className="shrink-0 text-fg-muted" />}
          </li>
        );
      })}
      {offset > 0 && results.length > 0 && <li role="presentation" className={groupClass} data-command-group="pages">Pages</li>}
      {results.map((item, j) => {
        const i = offset + j;
        const Icon = item.icon;
        const selected = i === active;
        return (
          <li key={item.key} id={optionId(i)} role="option" aria-selected={selected}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(item.path)} onMouseMove={() => { if (!selected) onHover(i); }} className={rowClass(selected)}>
            <Icon size={16} aria-hidden className={selected ? "shrink-0 text-brand" : "shrink-0 text-fg-muted"} />
            <span className="min-w-0 flex-1 truncate font-medium">{item.label}</span>
            <span className="shrink-0 text-t12 text-fg-muted">{sectionTitle(item.section)}</span>
            {selected && <CornerDownLeft size={14} aria-hidden className="shrink-0 text-fg-muted" />}
          </li>
        );
      })}
    </ul>
  );
}

/** The symbol rows for the text: recent ones at once, the server's after a short pause (only the newest answer counts). */
function useSymbolHits(query: string, open: boolean, userId: number | string | null): { hits: SymbolHit[]; searching: boolean } {
  const key = recentKeyFor(userId);
  const recent = useMemo(() => (open ? recentHits(readRecent(key), query) : []), [open, key, query]);
  const [found, setFound] = useState<{ q: string; hits: SymbolHit[] }>({ q: "", hits: [] });
  const q = query.trim();
  useEffect(() => {
    if (!open || q.length < 2) return undefined;
    let live = true;
    const t = setTimeout(() => { void findSymbols(q).then((hits) => { if (live) setFound({ q, hits }); }); }, 150);
    return () => { live = false; clearTimeout(t); };
  }, [open, q]);
  const fresh = found.q === q ? found.hits : [];
  return { hits: q ? mergeHits(recent, fresh) : recent.slice(0, 5), searching: q.length >= 2 && found.q !== q };
}

export default function CommandBarV2({ open, onOpenChange, ctx, onGo, userId = null }: CommandBarV2Props) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const inputId = useId();
  const optionId = (i: number) => `${listId}-option-${i}`;
  const results = useMemo(() => searchPages(query, ctx), [query, ctx]);
  const { hits, searching } = useSymbolHits(query, open, userId);
  const count = hits.length + results.length;
  const firstIds = `${hits.map((h) => h.id).join("|")}#${results.map((r) => r.key).join("|")}`;
  useEffect(() => { setActive(count ? 0 : -1); }, [firstIds, count]);
  useEffect(() => { if (!open) setQuery(""); }, [open]);

  const pick = (path: string) => { onOpenChange(false); onGo(path); };
  const pickSymbol = (hit: SymbolHit) => {
    onOpenChange(false);
    if (hit.type === "contract") { window.location.assign(hit.href); return; }   // the contract chart is its own window
    rememberSymbol(recentKeyFor(userId), hit.pick);
    onGo(hit.path);
  };
  const returnFocus = useReturnFocus(open);
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => moveActive(a, e.key === "ArrowDown" ? 1 : -1, count));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && active < hits.length) pickSymbol(hits[active]);
      else if (results[active - hits.length]) pick(results[active - hits.length].path);
    }
  };
  useEffect(() => {
    if (active >= 0) document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" });
  }, [active]);

  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-command-bar onCloseAutoFocus={returnFocus}
          className="fixed left-1/2 top-space-4 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-panel border border-subtle bg-surface-1 text-fg shadow-raised focus:outline-none md:top-[12vh]">
          <RD.Title className="sr-only">{COMMAND_LABEL}</RD.Title>
          <div className="flex items-center gap-space-2 border-b border-subtle px-space-4">
            <Search size={16} aria-hidden className="shrink-0 text-fg-muted" />
            <label htmlFor={inputId} className="sr-only">{COMMAND_LABEL}</label>
            <input id={inputId} role="combobox" aria-expanded={count > 0} aria-controls={listId} aria-autocomplete="list"
              aria-activedescendant={active >= 0 && count ? optionId(active) : undefined}
              value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
              placeholder="Share, index, contract (nifty 22700 ce) or page" autoComplete="off" spellCheck={false}
              className="h-12 min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-fg-muted focus:outline-none md:text-t15" />
            <kbd className="hidden shrink-0 rounded-control border border-border px-space-1 text-t12 text-fg-muted md:inline">Esc</kbd>
          </div>
          <CommandList query={query} results={results} symbols={hits} searching={searching} active={active} listId={listId} optionId={optionId}
            onPick={pick} onPickSymbol={pickSymbol} onHover={setActive} />
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
