/**
 * Owner order (23 Oct) "the scan universe is never empty" (behind `ui_v2` + `screener_v2`): the Universe column's list
 * picker. NIFTY 50 is chosen on open; the trader can pick NIFTY 100 / NIFTY 500 / any index's stocks, a sector, the
 * F&O stocks, one of his watchlists, or type his own symbols. The line under it says what the scan runs on, and the
 * page's note says when an empty choice falls back to NIFTY 50. Choosing a list runs nothing.
 */
import { Combobox, type ComboOption } from "../ui/Combobox";
import type { Watchlist } from "./watchlists";
import { CUSTOM, DEFAULT_CHOICE, listLabel, type UniverseGroup } from "./universe";

export interface UniversePickerProps {
  groups: UniverseGroup[];
  /** True when the server holds no NIFTY 50 list: the built-in names stand in as the first choice. */
  builtinDefault: boolean;
  watchlists: Watchlist[] | null;
  choice: string;
  onChoice: (choice: string) => void;
  symbolsText: string;
  onSymbolsText: (text: string) => void;
  /** What the scan runs on now: its symbol count and the list's name. */
  count: number;
  name: string;
  loading?: boolean;
  note?: string | null;
  maxSymbols: number;
}

/** The picker's rows, grouped as the old <select>'s optgroups were: index stocks, the server's other groups, the trader's
 * watchlists, then "Type my own symbols". Pure. */
export function universeOptions(groups: UniverseGroup[], builtinDefault: boolean, watchlists: Watchlist[] | null): ComboOption[] {
  const index = groups.find((g) => g.group === "INDEX_STOCKS");
  const others = groups.filter((g) => g.group !== "INDEX_STOCKS");
  const INDEX = "Index stocks";
  return [
    ...(builtinDefault ? [{ value: DEFAULT_CHOICE, label: "NIFTY 50", group: INDEX }] : []),
    ...(index?.lists ?? []).map((l) => ({ value: `sys:${l.key}`, label: `${listLabel(l, "INDEX_STOCKS")} (${l.count})`, group: INDEX })),
    ...others.flatMap((g) => g.lists.map((l) => ({ value: `sys:${l.key}`, label: `${listLabel(l, g.group)} (${l.count})`, group: g.label }))),
    ...(watchlists ?? []).map((w) => ({ value: `wl:${w.id}`, label: `${w.name} (${w.symbols.length})`, group: "My watchlists" })),
    { value: CUSTOM, label: "Type my own symbols", group: "Other" },
  ];
}

export function UniversePicker({ groups, builtinDefault, watchlists, choice, onChoice, symbolsText, onSymbolsText, count, name,
  loading = false, note, maxSymbols }: UniversePickerProps) {
  const options = universeOptions(groups, builtinDefault, watchlists);
  return (
    <div data-testid="universe-picker">
      <label className="mt-3 block text-t12 text-fg-muted" htmlFor="screener-universe">Scan these stocks</label>
      {/* SCR-FIX D8 (carried into E): the shared picker - a bottom sheet with 44 px rows and a filter box on a phone */}
      <Combobox id="screener-universe" label="Scan these stocks" value={choice} onChange={onChoice} options={options} searchable
                className="mt-1 w-full" dataAttrs={{ "data-testid": "universe-select" }} />
      {choice === CUSTOM && (
        <>
          <label className="mt-3 block text-t12 text-fg-muted" htmlFor="screener-symbols">Your symbols (up to {maxSymbols})</label>
          <textarea id="screener-symbols" value={symbolsText} onChange={(e) => onSymbolsText(e.target.value)} rows={4}
                    placeholder="Separate with commas or new lines"
                    className="mt-1 w-full resize-y rounded-control border border-border bg-surface-inset px-2 py-1.5 font-mono text-t13 text-fg placeholder:text-fg-muted/60 focus:outline-none focus:ring-2 focus:ring-brand" />
        </>
      )}
      <p className="mt-1 text-t12 text-fg-muted" data-testid="universe-count" aria-live="polite">
        {loading ? "Loading the list…" : <><span className="font-mono font-tabular text-fg">{count}</span> {count === 1 ? "stock" : "stocks"} · {name}</>}
      </p>
      {note && <p className="mt-1 text-t12 text-warn" data-testid="universe-note">{note}</p>}
    </div>
  );
}
