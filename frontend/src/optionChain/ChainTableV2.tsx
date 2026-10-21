import { useLayoutEffect, useRef, useState } from "react";
import { cx } from "../components/primitives/cx";
import { formatPrice } from "../ui/format";
import {
  CALL_COLUMNS, PUT_COLUMNS, atmIndex, barPercent, centreScrollTop, columnShown, formatCount, formatCountChange, formatGreek,
  formatIv, formatStrike, isItm, maxOi, type ColumnDef, type ColumnPrefs,
} from "./model";
import type { ChainLeg, ChainRow, LiveChain } from "./types";

/**
 * OC1: the chain - CALLS on the left, the strike in the centre, PUTS on the right (the puts' columns mirror the calls').
 * - Default columns on each side: OI, OI change, LTP, IV, Volume (a phone: OI and LTP); Greeks and bid / ask under
 *   "Columns". The strike column stays in the centre when a phone scrolls the table sideways.
 * - The ATM row is highlighted and scrolled to the middle of the table when a symbol or expiry opens (not on every
 *   refresh: a trader who scrolled keeps the place). In-the-money cells are shaded (calls below the spot, puts above).
 * - A bar inside each OI cell shows its share of the side's highest OI; the strikes with the highest call OI and the
 *   highest put OI are marked "Max".
 * - OI change is neutral text with its sign only (+ / −): the up / down colours are for prices and P&L.
 * - A row opens its full details (`onPick`): a tap, a click, or Enter / Space on the focused row (each row is a tab
 *   stop, not a button: the table stays one control for the phone layout rules).
 */
function cellText(key: ColumnDef["key"], leg: ChainLeg): string {
  switch (key) {
    case "oi": return formatCount(leg.oi);
    case "oi_change": return formatCountChange(leg.oi_change);
    case "volume": return formatCount(leg.volume);
    case "iv": return formatIv(leg.iv);
    case "ltp": case "bid": case "ask": return formatPrice(leg[key]);
    default: return formatGreek(key, leg[key]);
  }
}

const SHOWN_CLASS = { all: "", desktop: "hidden md:table-cell", none: "hidden" } as const;

export default function ChainTableV2({ live, prefs, centreKey, onPick }: {
  live: LiveChain;
  prefs: ColumnPrefs;
  /** The ATM row is centred once per key (symbol and expiry). */
  centreKey: string;
  onPick: (row: ChainRow) => void;
}) {
  const { rows, summary } = live;
  const spot = summary.spot;
  const atm = summary.atm_strike;
  const max = { call: maxOi(rows, "call"), put: maxOi(rows, "put") };
  const highest = { call: summary.max_call_oi_strike, put: summary.max_put_oi_strike };
  const calls = CALL_COLUMNS.filter((c) => columnShown(c, prefs) !== "none");
  const puts = PUT_COLUMNS.filter((c) => columnShown(c, prefs) !== "none");

  const box = useRef<HTMLDivElement>(null);
  const atmRow = useRef<HTMLTableRowElement>(null);
  const centred = useRef<string | null>(null);
  const atAt = atmIndex(rows, atm);
  useLayoutEffect(() => {
    if (centred.current === centreKey || atAt < 0 || !box.current || !atmRow.current) return;
    const head = box.current.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    // The rows' area below the sticky header: the ATM row's middle in the middle of it.
    box.current.scrollTop = centreScrollTop(atmRow.current.offsetTop - head, atmRow.current.offsetHeight, box.current.clientHeight - head);
    centred.current = centreKey;
  }, [centreKey, atAt]);

  const legCells = (side: "call" | "put", cols: readonly ColumnDef[], row: ChainRow, isAtm: boolean) => {
    const leg = row[side];
    const itm = !isAtm && isItm(side, row.strike, spot);
    return cols.map((c) => {
      const shown = SHOWN_CLASS[columnShown(c, prefs)];
      const base = cx("relative whitespace-nowrap px-space-2 py-space-2 font-tabular", side === "call" ? "text-right" : "text-left",
        itm && "bg-surface-2", shown);
      if (c.key === "oi") {
        const top = highest[side] === row.strike;
        return (
          <td key={c.key} className={cx(base, top && "font-semibold shadow-[inset_0_0_0_1px_rgb(var(--brand))]")} data-itm={itm || undefined}
            data-max-oi={top ? side : undefined} title={top ? `Highest ${side} OI` : undefined}>
            <span aria-hidden className={cx("absolute inset-y-1", side === "call" ? "right-0" : "left-0", top ? "bg-brand/30" : "bg-brand/15")}
              style={{ width: `${barPercent(leg.oi, max[side])}%` }} />
            <span className="relative inline-flex items-center gap-space-1">
              {top && side === "put" && <MaxMark />}
              <span>{cellText("oi", leg)}</span>
              {top && side === "call" && <MaxMark />}
            </span>
            {top && <span className="sr-only">{` (highest ${side} OI)`}</span>}
          </td>
        );
      }
      return (
        <td key={c.key} className={cx(base, "text-fg")} data-itm={itm || undefined}>
          {cellText(c.key, leg)}
        </td>
      );
    });
  };

  // The strike column stays in the middle of the visible table when the table is wider than its box and scrolls
  // sideways (sticky on both edges). A table that fits keeps every column where it is, the strike already central.
  const [wide, setWide] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const measure = () => setWide(el.scrollWidth > el.clientWidth + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    const table = el.querySelector("table");
    if (table) ro.observe(table);
    return () => ro.disconnect();
  }, [calls.length, puts.length]);
  const strikeSticky = cx(wide && "sticky left-[calc(50%-2.75rem)] right-[calc(50%-2.75rem)] z-[1]", "w-[5.5rem] min-w-[5.5rem] text-center");
  return (
    <div ref={box} data-chain-scroll data-wide={wide || undefined}
      className="max-h-[60vh] overflow-auto overscroll-contain rounded-panel border border-subtle bg-surface-1 md:max-h-[min(70vh,44rem)]">
      <table className="w-full border-separate border-spacing-0 text-t13" aria-label={`Option chain for ${live.underlying}`}>
        <thead className="sticky top-0 z-[2] bg-surface-1">
          <tr className="text-t12 font-medium text-fg-muted">
            {calls.map((c) => (
              <th key={c.key} scope="col" className={cx("whitespace-nowrap border-b border-subtle bg-surface-1 px-space-2 py-space-2 text-right font-medium", SHOWN_CLASS[columnShown(c, prefs)])}>
                <span className="sr-only">Call </span>{c.label}
              </th>
            ))}
            <th scope="col" className={cx(strikeSticky, "border-b border-subtle bg-surface-2 px-space-2 py-space-2 font-semibold text-fg")}>Strike</th>
            {puts.map((c) => (
              <th key={c.key} scope="col" className={cx("whitespace-nowrap border-b border-subtle bg-surface-1 px-space-2 py-space-2 text-left font-medium", SHOWN_CLASS[columnShown(c, prefs)])}>
                <span className="sr-only">Put </span>{c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isAtm = row.strike === atm;
            return (
              <tr key={row.strike} ref={isAtm ? atmRow : undefined} data-strike={row.strike} data-atm={isAtm || undefined}
                tabIndex={0} onClick={() => onPick(row)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(row); } }}
                className={cx("cursor-pointer hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand [&>td]:border-b [&>td]:border-subtle",
                  isAtm && "bg-brand/10")}>
                {legCells("call", calls, row, isAtm)}
                <th scope="row" className={cx(strikeSticky, "px-space-2 py-space-2 font-tabular font-semibold text-fg border-b border-subtle", isAtm ? "bg-surface-3" : "bg-surface-1")}>
                  {formatStrike(row.strike)}
                  {isAtm && <span className="block text-t12 font-medium text-brand">ATM<span className="sr-only">, at the money</span></span>}
                </th>
                {legCells("put", puts, row, isAtm)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** "Max" next to the highest OI of a side (from the md breakpoint; a phone has the outline and the bold figure). */
function MaxMark() {
  return <span aria-hidden className="hidden rounded-pill bg-brand px-space-1 text-t12 font-semibold leading-4 text-on-brand md:inline">Max</span>;
}
