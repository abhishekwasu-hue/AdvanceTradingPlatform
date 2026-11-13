import { useLayoutEffect, useRef, type ReactNode, type Ref } from "react";
import { cx } from "../components/primitives/cx";
import { useColourV2 } from "../ui/colourV2";
import { formatPrice, formatTimeIST } from "../ui/format";
import { centreScrollTop, formatOi, formatStrike, oiUnitCaption, type OiUnits } from "./model";
import { netDiffRows, netDiffStatus, netDiffTotal, type Dominance, type NetDiffShown } from "./oiDiff";
import type { ChainRow, LiveChain } from "./types";

/**
 * M96 L1 (owner, 11 Nov: "taken from the Trade repo AS IS"): the OI-difference table on top of the chain view = the
 * Trade dashboard's chain table "Net Diff" (backend app/option_chain/net_diff.py; the pure parts in oiDiff.ts):
 * - ATM ± 6 strikes (Trade's step: NIFTY 50, BANKNIFTY / SENSEX 100, else the chain's own);
 * - CE Chg OI / PE Chg OI = the change since the app's first reading of the day of that strike (Trade's caption: not the
 *   exchange's previous-day change); "Building" while this is the day's first reading;
 * - Net Diff = PE Chg OI − CE Chg OI; Dominance by its sign ("Bulls" / "Bears" / "Neutral"); a TOTAL row;
 * - the changes and Net Diff green when above 0 and red below (Trade's colours); OI in the user's unit (lakhs unless he
 *   picked Contracts, M96 Q3). A phone shows CE Chg OI | Strike | PE Chg OI | Net Diff | Dominance; from md every Trade
 *   column (CE Chg OI, CE Total OI, CE LTP, Strike, PE LTP, PE Total OI, PE Chg OI, Net Diff, Dominance).
 * - Owner 13 Nov ("no 'L' in the OI table; say it once above: OI in lakhs; the entries should display properly"): the
 *   unit is said once above the table ("OI in lakhs" / "OI in contracts"), the cells carry the bare figures; every cell
 *   is one line (whitespace-nowrap, tabular figures, numbers right-aligned), the headers are two short deliberate lines
 *   ("CE" over "Chg OI"), and a table wider than its box (a narrow Terminal panel, a phone) scrolls sideways inside its
 *   own box with the Strike column sticky - it never clips or overlaps. The cause of the overlap before: a fixed
 *   table layout (equal column widths) with the `md` columns chosen by the SCREEN width, so a 5-column Terminal panel
 *   on a wide screen squeezed nine columns into ~65 px each and the figures ran into each other.
 * - O1: with chain trading on (`onPickLeg`), a tap on a CE or PE change opens THAT contract's actions - the same action
 *   sheet, ticket and basket as the chain table (src/fno/ChainTradingV2.tsx); nothing else is filled in (M68).
 * - The ATM row is tinted and scrolled to the middle once per `centreKey`.
 */
const TONE = (v: number) => (v > 0 ? "text-up" : v < 0 ? "text-down" : "text-fg");
const DOMINANCE_TONE: Record<Dominance, string> = { Bulls: "text-up", Bears: "text-down", Neutral: "text-fg-muted" };
const WIDE = "hidden md:table-cell";

export default function OiDiffTableV2({ live, units, centreKey, onPickLeg }: {
  live: LiveChain;
  units?: OiUnits;
  centreKey: string;
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const atmRow = useRef<HTMLTableRowElement>(null);
  const centred = useRef<string | null>(null);
  const nd = live.net_diff;
  const status = netDiffStatus(nd);
  useLayoutEffect(() => {
    if (centred.current === centreKey || status === "none" || !box.current || !atmRow.current) return;
    const head = box.current.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    box.current.scrollTop = centreScrollTop(atmRow.current.offsetTop - head, atmRow.current.offsetHeight, box.current.clientHeight - head);
    centred.current = centreKey;
  }, [centreKey, status]);
  return (
    <section aria-label="OI difference" data-block="oi-diff" className="space-y-space-2">
      <h2 className="text-t14 font-semibold text-fg">OI difference by strike{" "}
        <span className="font-normal text-fg-muted">(ATM ± 6 · <span className="whitespace-nowrap" data-oi-unit>{oiUnitCaption(units)}</span>)</span></h2>
      {status === "none" || !nd ? (
        <p className="text-t13 text-fg-muted" data-oi-diff-state="none">The OI difference shows once the spot price of the chain is known.</p>
      ) : (
        <>
          <p className="text-t12 text-fg-muted" data-oi-diff-state={status}>
            {status === "building"
              ? `Building: the day's first reading was taken at ${formatTimeIST(nd.since)}; the changes show from the next reading.`
              : `Change since the day's first reading at ${formatTimeIST(nd.since)}.`}
          </p>
          <div ref={box} data-oi-diff-scroll className="max-h-[min(55vh,30rem)] overflow-auto rounded-panel border border-subtle bg-surface-1">
            <NetDiffTableBody rows={netDiffRows(nd.rows)} atm={nd.atm ?? null} chainRows={live.rows} units={units} onPickLeg={onPickLeg}
              atmRow={atmRow} underlying={live.underlying} />
          </div>
        </>
      )}
      <details className="text-t12 text-fg-muted" data-oi-diff-help>
        <summary className="inline-flex min-h-11 cursor-pointer items-center md:min-h-8">How to read this table</summary>
        <ul className="list-disc space-y-space-1 pl-space-5">
          <li>Chg OI is the change since today's baseline: the app's first reading of that strike today (not the exchange's
            previous-day change).</li>
          <li>Net Diff = PE Chg OI − CE Chg OI: where new put or call OI came in today. Dominance is its sign: Bulls above 0,
            Bears below 0, Neutral at 0.</li>
          <li>The History table's Diff is different: Total Put OI − Total Call OI (a level). Do not compare the two directly.</li>
        </ul>
      </details>
    </section>
  );
}

/** The table itself (no reads, no effects), so tests render it directly. */
export function NetDiffTableBody({ rows, atm, chainRows, units, onPickLeg, atmRow, underlying = "" }: {
  rows: readonly NetDiffShown[];
  atm: number | null;
  chainRows: readonly ChainRow[];
  units?: OiUnits;
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
  atmRow?: Ref<HTMLTableRowElement>;
  underlying?: string;
}) {
  const colour = useColourV2();
  const total = netDiffTotal(rows);
  const byStrike = new Map(chainRows.map((r) => [r.strike, r]));
  const head = "border-b border-subtle bg-surface-1 px-space-2 py-space-1 align-bottom font-medium leading-tight";
  const cell = "px-space-2 py-space-1 font-tabular";
  // The Strike column stays at the edge it reaches when the table scrolls sideways (an opaque fill, so it only covers
  // cells that are leaving the box).
  const sticky = "sticky left-0 right-0 z-[1]";
  const atmFill = colour ? "bg-brand-tint" : "bg-surface-3";
  return (
    <table className="w-full whitespace-nowrap border-separate border-spacing-0 text-t13"
      aria-label={`OI difference by strike${underlying ? ` for ${underlying}` : ""}, ${oiUnitCaption(units)}`}>
      <thead className="sticky top-0 z-[2] bg-surface-1">
        <tr className="text-t12 text-fg-muted">
          <th scope="col" className={cx(head, "text-right")}><Two top="CE" bottom="Chg OI" /></th>
          <th scope="col" className={cx(head, WIDE, "text-right")}><Two top="CE" bottom="Total OI" /></th>
          <th scope="col" className={cx(head, WIDE, "text-right")}><Two top="CE" bottom="LTP" /></th>
          <th scope="col" className={cx(head, sticky, "bg-surface-2 text-center font-semibold text-fg")}>Strike</th>
          <th scope="col" className={cx(head, WIDE, "text-right")}><Two top="PE" bottom="LTP" /></th>
          <th scope="col" className={cx(head, WIDE, "text-right")}><Two top="PE" bottom="Total OI" /></th>
          <th scope="col" className={cx(head, "text-right")}><Two top="PE" bottom="Chg OI" /></th>
          <th scope="col" className={cx(head, "text-right")}><Two top="Net" bottom="Diff" /></th>
          <th scope="col" className={cx(head, "text-left")}>Dominance</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => {
          const isAtm = d.strike === atm;
          const row = byStrike.get(d.strike);
          return (
            <tr key={d.strike} ref={isAtm ? atmRow : undefined} data-oi-diff-row={d.strike} data-oi-diff-atm={isAtm || undefined}
              className={cx("[&>*]:border-b [&>*]:border-subtle", isAtm && (colour ? "bg-brand-tint" : "bg-brand/10"))}>
              <ChgCell side="call" d={d} row={row} units={units} onPickLeg={onPickLeg} />
              <td className={cx(cell, WIDE, "text-right text-fg")}>{formatOi(d.ceOi, units, false, false)}</td>
              <td className={cx(cell, WIDE, "text-right text-fg")}>{formatPrice(d.ceLtp)}</td>
              <th scope="row" className={cx(cell, sticky, "text-center font-semibold text-fg", isAtm ? atmFill : "bg-surface-2")}>
                {formatStrike(d.strike)}
                {isAtm && <span className="ml-space-1 text-t12 font-semibold text-fg">ATM<span className="sr-only">, at the money</span></span>}
              </th>
              <td className={cx(cell, WIDE, "text-right text-fg")}>{formatPrice(d.peLtp)}</td>
              <td className={cx(cell, WIDE, "text-right text-fg")}>{formatOi(d.peOi, units, false, false)}</td>
              <ChgCell side="put" d={d} row={row} units={units} onPickLeg={onPickLeg} />
              <td className={cx(cell, "text-right font-semibold", TONE(d.diff))} data-oi-diff="diff">{formatOi(d.diff, units, true, false)}</td>
              <td className={cx(cell, "text-left font-semibold", DOMINANCE_TONE[d.dominance])} data-dominance={d.dominance}>{d.dominance}</td>
            </tr>
          );
        })}
        <tr data-oi-diff-total className="bg-surface-2 font-semibold text-fg">
          <Num v={total.ceChg} units={units} signed tone />
          <Num v={total.ceOi} units={units} wide />
          <td className={cx(cell, WIDE)} />
          <th scope="row" className={cx(cell, sticky, "bg-surface-2 text-center")}>TOTAL</th>
          <td className={cx(cell, WIDE)} />
          <Num v={total.peOi} units={units} wide />
          <Num v={total.peChg} units={units} signed tone />
          <Num v={total.diff} units={units} signed tone />
          <td className={cell} />
        </tr>
      </tbody>
    </table>
  );
}

/** A deliberate two-line header: the side ("CE") over the figure ("Chg OI"), each line on one line. */
function Two({ top, bottom }: { top: string; bottom: string }) {
  return <><span className="block">{top}</span><span className="block">{bottom}</span></>;
}

function Num({ v, units, signed = false, tone = false, wide = false }: {
  v: number; units?: OiUnits; signed?: boolean; tone?: boolean; wide?: boolean;
}) {
  return <td className={cx("px-space-2 py-space-1 text-right font-tabular", wide && WIDE, tone ? TONE(v) : "text-fg")}>
    {formatOi(v, units, signed, false)}</td>;
}

export function ChgCell({ side, d, row, units, onPickLeg }: {
  side: "call" | "put"; d: NetDiffShown; row: ChainRow | undefined; units?: OiUnits; onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
}) {
  const v = side === "call" ? d.ceChg : d.peChg;
  const value = formatOi(v, units, true, false);
  const right = side === "call" ? "CE" : "PE";
  const inner: ReactNode = <span className={cx("block font-tabular", TONE(v))}>{value}</span>;
  if (!onPickLeg || !row) {
    return <td className="px-space-2 py-space-1 text-right" data-oi-diff={side}>{inner}</td>;
  }
  return (
    <td className="p-0 text-right" data-oi-diff={side}>
      <button type="button" data-oi-diff-cell={`${d.strike}|${right}`} onClick={() => onPickLeg(row, side)}
        aria-label={`${formatStrike(d.strike)} ${right}: change in OI ${formatOi(v, units, true)}. Open its actions`}
        className="block min-h-11 w-full px-space-2 py-space-1 text-right hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand">
        {inner}
      </button>
    </td>
  );
}
