import { useLayoutEffect, useRef, useState } from "react";
import { cx } from "../components/primitives/cx";
import { useColourV2 } from "../ui/colourV2";
import { formatPrice } from "../ui/format";
import {
  CALL_COLUMNS, PUT_COLUMNS, atmIndex, barPercent, centreScrollTop, columnShown, formatCount, formatCountChange, formatGreek,
  formatIv, formatOiChangePct, formatStrike, isItm, maxOi, type ColumnDef, type ColumnPrefs,
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
 * - FNO-1 (with `onPickLeg`, chain trading on): a tap on a call cell or a put cell opens that contract's actions (the
 *   strike cell still opens the row's details); on the focused row, C opens the call's and P the put's. A "P" next to
 *   the strike marks a call or put the user holds in PAPER (`held`, keys "strike|CE" / "strike|PE"). With
 *   `prefs.pct` the OI change shows in percent.
 * - F&O 8b-a (`setup`, the chain opened from one of the user's own signals): the call or put cells his own deployment
 *   settings land on are shaded, and the strike says "Main" / "Wing" ("from your settings" for a screen reader). A mark
 *   only shows; nothing is picked or opened by it.
 * - Colour v2 (flag `colour_v2`, owner 23 Oct): the calls side sits on a light red tint and the puts side on a light
 *   green one, an in-the-money strike a step stronger than an out-of-the-money one; the OI bars are red for calls and
 *   green for puts (a soft fill under the number and a solid edge, 3 : 1); the ATM row is a brand-blue tint. Text stays
 *   the plain text colours (AA on every tint, tokens.test.ts). Colour-blind mode: orange calls, blue puts.
 */
const SIDE_TINT = {
  call: { otm: "bg-chain-call", itm: "bg-chain-call-itm", bar: "border-b-2 border-oi-call bg-oi-call/25", top: "border-b-2 border-oi-call bg-oi-call/40" },
  put: { otm: "bg-chain-put", itm: "bg-chain-put-itm", bar: "border-b-2 border-oi-put bg-oi-put/25", top: "border-b-2 border-oi-put bg-oi-put/40" },
} as const;

function cellText(key: ColumnDef["key"], leg: ChainLeg, pct = false): string {
  switch (key) {
    case "oi": return formatCount(leg.oi);
    case "oi_change": return pct ? formatOiChangePct(leg.oi, leg.oi_change) : formatCountChange(leg.oi_change);
    case "volume": return formatCount(leg.volume);
    case "iv": return formatIv(leg.iv);
    case "ltp": case "bid": case "ask": return formatPrice(leg[key]);
    default: return formatGreek(key, leg[key]);
  }
}

const SHOWN_CLASS = { all: "", desktop: "hidden md:table-cell", none: "hidden" } as const;

export default function ChainTableV2({ live, prefs, centreKey, onPick, onPickLeg, held, setup }: {
  live: LiveChain;
  prefs: ColumnPrefs;
  /** The ATM row is centred once per key (symbol and expiry; FNO-1: and each "recentre" tap). */
  centreKey: string;
  onPick: (row: ChainRow) => void;
  /** FNO-1: a call or put cell tapped (chain trading on). */
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
  /** FNO-1: the contracts the user holds in PAPER, as "strike|CE" / "strike|PE". */
  held?: ReadonlySet<string>;
  /** 8b-a: where the user's own settings land, "strike|CE" / "strike|PE" -> "Main" / "Wing" / "2nd". */
  setup?: ReadonlyMap<string, string>;
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
  const colour = useColourV2();
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
    const marked = setup?.get(`${row.strike}|${side === "call" ? "CE" : "PE"}`);
    const pick = onPickLeg ? (e: { stopPropagation: () => void }) => { e.stopPropagation(); onPickLeg(row, side); } : undefined;
    return cols.map((c) => {
      const shown = SHOWN_CLASS[columnShown(c, prefs)];
      const tint = colour ? (isAtm ? null : itm ? SIDE_TINT[side].itm : SIDE_TINT[side].otm) : (itm ? "bg-surface-2" : null);
      const base = cx("relative whitespace-nowrap px-space-2 py-space-2 font-tabular", side === "call" ? "text-right" : "text-left",
        marked ? "bg-brand/15" : tint, shown);
      if (c.key === "oi") {
        const top = highest[side] === row.strike;
        return (
          <td key={c.key} className={cx(base, top && "font-semibold shadow-[inset_0_0_0_1px_rgb(var(--brand))]")} data-itm={itm || undefined} data-setup={marked ? side : undefined}
            data-max-oi={top ? side : undefined} title={top ? `Highest ${side} OI` : undefined} onClick={pick}>
            <span aria-hidden data-oi-bar={side} className={cx("absolute inset-y-1", side === "call" ? "right-0" : "left-0",
              colour ? (top ? SIDE_TINT[side].top : SIDE_TINT[side].bar) : top ? "bg-brand/30" : "bg-brand/15")}
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
        <td key={c.key} className={cx(base, "text-fg")} data-itm={itm || undefined} data-setup={marked ? side : undefined} onClick={pick}>
          {cellText(c.key, leg, prefs.pct === true)}
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
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(row); }
                  else if (onPickLeg && (e.key === "c" || e.key === "p")) { e.preventDefault(); onPickLeg(row, e.key === "c" ? "call" : "put"); }
                }}
                aria-keyshortcuts={onPickLeg ? "C P" : undefined}
                className={cx("cursor-pointer hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand [&>td]:border-b [&>td]:border-subtle",
                  isAtm && (colour ? "bg-brand-tint" : "bg-brand/10"))}>
                {legCells("call", calls, row, isAtm)}
                <th scope="row" className={cx(strikeSticky, "px-space-2 py-space-2 font-tabular font-semibold text-fg border-b border-subtle", isAtm ? (colour ? "bg-brand-tint" : "bg-surface-3") : "bg-surface-1")}>
                  {held?.has(`${row.strike}|CE`) && <HeldMark side="call" />}
                  {formatStrike(row.strike)}
                  {held?.has(`${row.strike}|PE`) && <HeldMark side="put" />}
                  {isAtm && <span className={cx("block text-t12", colour ? "font-semibold text-fg" : "font-medium text-brand")}>ATM<span className="sr-only">, at the money</span></span>}
                  {setup && <SetupMark call={setup.get(`${row.strike}|CE`)} put={setup.get(`${row.strike}|PE`)} />}
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

/** FNO-1: "P" next to the strike - the user holds this call (left) or put (right) in PAPER. */
function HeldMark({ side }: { side: "call" | "put" }) {
  return (
    <span data-held={side} className={cx("inline-block rounded-pill bg-brand px-space-1 text-t12 font-semibold leading-4 text-on-brand", side === "call" ? "mr-space-1" : "ml-space-1")}>
      P<span className="sr-only">{side === "call" ? " (you hold the call)" : " (you hold the put)"}</span>
    </span>
  );
}

/** 8b-a: "Main" / "Wing" under the strike where the user's own settings land (call on the left, put on the right). */
function SetupMark({ call, put }: { call?: string; put?: string }) {
  if (!call && !put) return null;
  return (
    <span className="block text-t12 font-semibold text-brand" data-setup-mark>
      {call && <span>{call} call<span className="sr-only">, from your settings</span></span>}
      {call && put && " · "}
      {put && <span>{put} put<span className="sr-only">, from your settings</span></span>}
    </span>
  );
}

/** "Max" next to the highest OI of a side (from the md breakpoint; a phone has the outline and the bold figure). */
function MaxMark() {
  return <span aria-hidden className="hidden rounded-pill bg-brand px-space-1 text-t12 font-semibold leading-4 text-on-brand md:inline">Max</span>;
}
