import { Search } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { request } from "../api/client";
import { cx } from "../components/primitives/cx";

/**
 * UI2-4b2 (UI v2 proposal §9 rule 6): the one symbol picker for v2 pages. Type to search the instrument list
 * (`GET /api/instrument-master/search`, indices and shares), or pick one of the recent symbols shown when the field is
 * empty. Each row shows the exchange. Enter takes the highlighted row, or the text as typed when nothing matches.
 * Recent symbols are kept per browser (the last eight). Combobox pattern: arrow keys, Enter, Escape.
 */
export interface PickedSymbol { symbol: string; exchange: string; name?: string | null }

export const RECENT_KEY = "atp_recent_symbols";
const MAX_RECENT = 8;

export function readRecent(): PickedSymbol[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((r): r is PickedSymbol => !!r && typeof r.symbol === "string" && typeof r.exchange === "string").slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

/** The recent list with `pick` first (no repeats). Pure. */
export function withRecent(list: readonly PickedSymbol[], pick: PickedSymbol): PickedSymbol[] {
  const same = (r: PickedSymbol) => r.symbol === pick.symbol && r.exchange === pick.exchange;
  return [pick, ...list.filter((r) => !same(r))].slice(0, MAX_RECENT);
}

export function rememberSymbol(pick: PickedSymbol): void {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(withRecent(readRecent(), pick))); } catch { /* storage unavailable */ }
}

interface InstrumentRow { tradingsymbol: string; exchange: string; name: string | null; instrument_type: string }

/** Indices first, then shares; futures and options are not charts of their own here. */
export async function searchSymbols(q: string): Promise<PickedSymbol[]> {
  const one = (type: string) => request<InstrumentRow[]>(`/instrument-master/search?q=${encodeURIComponent(q)}&type=${type}&limit=8`).catch(() => []);
  const [indices, shares] = await Promise.all([one("INDEX"), one("EQ")]);
  return [...indices, ...shares].map((r) => ({ symbol: r.tradingsymbol.toUpperCase(), exchange: r.exchange.toUpperCase(), name: r.name }));
}

export function SymbolPicker({ value, exchange, onPick, label = "Symbol", className = "min-w-0 flex-1 sm:w-56 sm:flex-none" }: {
  value: string; exchange: string; onPick: (pick: PickedSymbol) => void; label?: string; className?: string;
}) {
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<PickedSymbol[]>([]);
  const [active, setActive] = useState(0);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { setText(value); }, [value]);

  const q = text.trim();
  useEffect(() => {
    if (!open) return undefined;
    if (!q || q.toUpperCase() === value) { setRows(readRecent()); setActive(0); return undefined; }
    let live = true;
    const t = window.setTimeout(() => { void searchSymbols(q).then((r) => { if (live) { setRows(r); setActive(0); } }); }, 250);
    return () => { live = false; window.clearTimeout(t); };
  }, [q, open, value]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) { setOpen(false); setText(value); } };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open, value]);

  const pick = (p: PickedSymbol) => {
    rememberSymbol(p);
    setOpen(false);
    setText(p.symbol);
    onPick(p);
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      if (rows.length) setActive((a) => (a + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = open ? rows[active] : undefined;
      if (row) pick(row);
      else if (q) pick({ symbol: q.toUpperCase(), exchange });
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      setOpen(false);
      setText(value);
    }
  };

  const showList = open && rows.length > 0;
  return (
    <div ref={box} className={cx("relative", className)}>
      <label htmlFor={`${id}-input`} className="sr-only">{label}</label>
      <Search size={14} aria-hidden className="pointer-events-none absolute left-space-3 top-1/2 -translate-y-1/2 text-fg-muted" />
      <input id={`${id}-input`} role="combobox" aria-expanded={showList} aria-controls={`${id}-list`} aria-autocomplete="list"
        aria-activedescendant={showList ? `${id}-opt-${active}` : undefined}
        value={text} onChange={(e) => { setText(e.target.value); setOpen(true); }} onFocus={(e) => { e.target.select(); setOpen(true); }} onKeyDown={onKey}
        autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="Search a symbol"
        className="h-11 w-full rounded-control border border-border bg-surface-2 pl-space-8 pr-space-3 text-t14 font-semibold text-fg placeholder:font-normal placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
      {showList && (
        <ul id={`${id}-list`} role="listbox" aria-label={q && q.toUpperCase() !== value ? "Matching symbols" : "Recent symbols"}
          className="absolute left-0 top-full z-40 mt-space-1 max-h-80 w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-panel border border-subtle bg-surface-1 p-space-1 shadow-raised">
          {rows.map((r, i) => (
            <li key={`${r.exchange}:${r.symbol}`} id={`${id}-opt-${i}`} role="option" aria-selected={i === active}
              onPointerDown={(e) => e.preventDefault()} onClick={() => pick(r)} onPointerEnter={() => setActive(i)}
              className={cx("flex min-h-11 cursor-pointer items-center gap-space-2 rounded-control px-space-2 text-t14 md:min-h-9", i === active && "bg-surface-2")}>
              <span className="font-semibold text-fg">{r.symbol}</span>
              {r.name && r.name.toUpperCase() !== r.symbol && <span className="min-w-0 flex-1 truncate text-t12 text-fg-muted">{r.name}</span>}
              <span className="ml-auto rounded-pill border border-subtle px-space-2 text-t12 text-fg-muted">{r.exchange}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
