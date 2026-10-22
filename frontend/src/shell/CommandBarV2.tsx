import * as RD from "@radix-ui/react-dialog";
import { CornerDownLeft, Search } from "lucide-react";
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { useReturnFocus } from "./returnFocus";
import { searchPages, sectionTitle, type NavContext, type NavItemV2, type SectionId } from "./navModel";

/**
 * UI2-1 (UI v2 proposal §9 rule 3): the command bar - "Search pages", opened with Ctrl K (Cmd K on a Mac) or the top
 * bar's search. It jumps to any page the trader may see (hidden pages are never listed), found by today's names too.
 * Arrow keys move, Enter opens, Escape closes. Symbols, strategies and actions join it with the pages that own them
 * (Charts UI2-4, Library UI2-9b, broker login UI2-6).
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

export interface CommandBarV2Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ctx: NavContext;
  onGo: (path: string) => void;
}

export interface CommandListProps {
  query: string;
  results: (NavItemV2 & { section: SectionId })[];
  active: number;
  listId: string;
  optionId: (i: number) => string;
  onPick: (path: string) => void;
  onHover: (i: number) => void;
}

/** The result rows - no dialog, so tests render it directly. */
export function CommandList({ query, results, active, listId, optionId, onPick, onHover }: CommandListProps) {
  if (results.length === 0) {
    return <p role="status" className="px-space-4 py-space-6 text-center text-t14 text-fg-muted">No page matches &quot;{query.trim()}&quot;.</p>;
  }
  return (
    <ul id={listId} role="listbox" aria-label="Pages" className="max-h-[min(60vh,420px)] overflow-y-auto p-space-2">
      {results.map((item, i) => {
        const Icon = item.icon;
        const selected = i === active;
        return (
          <li key={item.key} id={optionId(i)} role="option" aria-selected={selected}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(item.path)} onMouseMove={() => { if (!selected) onHover(i); }}
            className={`flex min-h-11 cursor-pointer items-center gap-space-3 rounded-control px-space-3 text-t14 ${selected ? "bg-brand/10 text-fg" : "text-fg"}`}>
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

export default function CommandBarV2({ open, onOpenChange, ctx, onGo }: CommandBarV2Props) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const inputId = useId();
  const optionId = (i: number) => `${listId}-option-${i}`;
  const results = useMemo(() => searchPages(query, ctx), [query, ctx]);
  useEffect(() => { setActive(results.length ? 0 : -1); }, [results]);
  useEffect(() => { if (!open) setQuery(""); }, [open]);

  const pick = (path: string) => { onOpenChange(false); onGo(path); };
  const returnFocus = useReturnFocus(open);
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => moveActive(a, e.key === "ArrowDown" ? 1 : -1, results.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const hit = results[active];
      if (hit) pick(hit.path);
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
          <RD.Title className="sr-only">Search pages</RD.Title>
          <div className="flex items-center gap-space-2 border-b border-subtle px-space-4">
            <Search size={16} aria-hidden className="shrink-0 text-fg-muted" />
            <label htmlFor={inputId} className="sr-only">Search pages</label>
            <input id={inputId} role="combobox" aria-expanded={results.length > 0} aria-controls={listId} aria-autocomplete="list"
              aria-activedescendant={active >= 0 && results.length ? optionId(active) : undefined}
              value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
              placeholder="Search pages" autoComplete="off" spellCheck={false}
              className="h-12 min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-fg-muted focus:outline-none md:text-t15" />
            <kbd className="hidden shrink-0 rounded-control border border-border px-space-1 text-t12 text-fg-muted md:inline">Esc</kbd>
          </div>
          <CommandList query={query} results={results} active={active} listId={listId} optionId={optionId} onPick={pick} onHover={setActive} />
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
