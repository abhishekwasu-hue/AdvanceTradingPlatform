import { useLayoutEffect, useRef, type Ref } from "react";
import { cx } from "../components/primitives/cx";
import { useColourV2 } from "../ui/colourV2";
import { TermHint } from "../ui/TermHint";
import { atmIndex, centreScrollTop, formatOi, formatStrike, type OiUnits } from "./model";
import { oiDiffRows, tagText, type Buildup, type OiDiffRow } from "./oiDiff";
import type { ChainRow, LiveChain } from "./types";

/**
 * M96 L1 (owner): the OI-difference table - on top of the chain view, above the chain table, for the strikes the
 * "Strikes" chips show (the rows passed in). Per strike: the call's OI change, the put's OI change, the difference put
 * minus call with its plain fact word, and under each side's change the standard word from that side's own price change
 * and OI change (src/optionChain/oiDiff.ts). Numbers in the user's unit (lakhs unless he picked Contracts, M96 Q3).
 * - O1: with chain trading on (`onPickLeg`, the PAPER account), a tap on a call or put cell opens THAT contract's actions -
 *   the same action sheet, ticket and basket as a tap on the chain table (src/fno/ChainTradingV2.tsx). Nothing else is
 *   filled in: the lots, order type and product come only from his saved settings or what he enters (M68).
 * - The ATM row is tinted and scrolled to the middle once per `centreKey` (as the chain table).
 * - The tag's colour follows that side's price direction (up / down), the fact the word is read from; the difference
 *   stays neutral text.
 */
const TAG_TONE: Record<Buildup, string> = {
  "Long buildup": "text-up", "Short covering": "text-up", "Short buildup": "text-down", "Long unwinding": "text-down",
};

export default function OiDiffTableV2({ live, rows, units, centreKey, onPickLeg }: {
  live: LiveChain;
  /** The rows the strikes chips show (ATM ± N, or all). */
  rows: readonly ChainRow[];
  units?: OiUnits;
  centreKey: string;
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const atmRow = useRef<HTMLTableRowElement>(null);
  const centred = useRef<string | null>(null);
  const atm = live.summary.atm_strike;
  const atAt = atmIndex(rows, atm);
  useLayoutEffect(() => {
    if (centred.current === centreKey || atAt < 0 || !box.current || !atmRow.current) return;
    const head = box.current.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    box.current.scrollTop = centreScrollTop(atmRow.current.offsetTop - head, atmRow.current.offsetHeight, box.current.clientHeight - head);
    centred.current = centreKey;
  }, [centreKey, atAt]);
  return (
    <section aria-label="OI difference" data-block="oi-diff" className="space-y-space-2">
      <div className="flex items-center gap-space-1">
        <h2 className="text-t14 font-semibold text-fg">OI change by strike</h2>
        <TermHint term="oi-change" />
      </div>
      <div ref={box} data-oi-diff-scroll className="max-h-[min(50vh,26rem)] overflow-auto rounded-panel border border-subtle bg-surface-1">
        <OiDiffTableBody rows={rows} atm={atm} units={units} onPickLeg={onPickLeg} atmRow={atmRow} underlying={live.underlying} />
      </div>
      <details className="text-t12 text-fg-muted" data-oi-diff-help>
        <summary className="inline-flex min-h-11 cursor-pointer items-center md:min-h-8">How to read this table</summary>
        <ul className="list-disc space-y-space-1 pl-space-5">
          <li>Put − Call: the put side's OI change minus the call side's, per strike.</li>
          <li>Under each change, the word from that side's own price change and OI change: price up with OI up is long
            buildup, price down with OI up is short buildup, price up with OI down is short covering, price down with OI
            down is long unwinding. A dash: the price change is not known.</li>
        </ul>
      </details>
    </section>
  );
}

/** The table itself (no reads, no effects), so tests render it directly. */
export function OiDiffTableBody({ rows, atm, units, onPickLeg, atmRow, underlying = "" }: {
  rows: readonly ChainRow[];
  atm: number | null;
  units?: OiUnits;
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
  atmRow?: Ref<HTMLTableRowElement>;
  underlying?: string;
}) {
  const colour = useColourV2();
  const diff = oiDiffRows(rows);
  const head = "border-b border-subtle bg-surface-1 px-space-2 py-space-2 font-medium";
  return (
    <table className="w-full border-separate border-spacing-0 text-t13" aria-label={`OI change by strike${underlying ? ` for ${underlying}` : ""}`}>
      <thead className="sticky top-0 z-[2] bg-surface-1">
        <tr className="text-t12 text-fg-muted">
          <th scope="col" className={cx(head, "text-right")}>Call OI chg</th>
          <th scope="col" className={cx(head, "bg-surface-2 text-center font-semibold text-fg")}>Strike</th>
          <th scope="col" className={cx(head, "text-left")}>Put OI chg</th>
          <th scope="col" className={cx(head, "text-right")}>Put − Call</th>
        </tr>
      </thead>
      <tbody>
        {diff.map((d, i) => {
          const row = rows[i];
          const isAtm = d.strike === atm;
          return (
            <tr key={d.strike} ref={isAtm ? atmRow : undefined} data-oi-diff-row={d.strike} data-oi-diff-atm={isAtm || undefined}
              className={cx("[&>*]:border-b [&>*]:border-subtle", isAtm && (colour ? "bg-brand-tint" : "bg-brand/10"))}>
              <OiDiffSideCell side="call" d={d} row={row} units={units} onPickLeg={onPickLeg} />
              <th scope="row" className={cx("px-space-2 py-space-1 text-center font-tabular font-semibold text-fg", isAtm ? (colour ? "bg-brand-tint" : "bg-surface-3") : "bg-surface-2")}>
                {formatStrike(d.strike)}
                {isAtm && <span className="block text-t12 font-semibold text-fg">ATM<span className="sr-only">, at the money</span></span>}
              </th>
              <OiDiffSideCell side="put" d={d} row={row} units={units} onPickLeg={onPickLeg} />
              <td className="px-space-2 py-space-1 text-right font-tabular text-fg" data-oi-diff="diff">
                <span className="block">{formatOi(d.diff, units, true)}</span>
                <span className="block text-t12 text-fg-muted" data-oi-diff-word>{d.word ?? "–"}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function OiDiffSideCell({ side, d, row, units, onPickLeg }: {
  side: "call" | "put"; d: OiDiffRow; row: ChainRow; units?: OiUnits; onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
}) {
  const tag = side === "call" ? d.callTag : d.putTag;
  const value = formatOi(side === "call" ? d.call : d.put, units, true);
  const right = side === "call" ? "CE" : "PE";
  const inner = (
    <>
      <span className="block font-tabular">{value}</span>
      <span className={cx("block text-t12", tag ? TAG_TONE[tag] : "text-fg-muted")} data-oi-tag={tag ?? "none"}>{tagText(tag)}</span>
    </>
  );
  const align = side === "call" ? "text-right" : "text-left";
  if (!onPickLeg) {
    return <td className={cx("px-space-2 py-space-1 text-fg", align)} data-oi-diff={side}>{inner}</td>;
  }
  return (
    <td className={cx("p-0", align)} data-oi-diff={side}>
      <button type="button" data-oi-diff-cell={`${d.strike}|${right}`} onClick={() => onPickLeg(row, side)}
        aria-label={`${formatStrike(d.strike)} ${right}: OI change ${value}, ${tagText(tag)}. Open its actions`}
        className={cx("block min-h-11 w-full px-space-2 py-space-1 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand", align)}>
        {inner}
      </button>
    </td>
  );
}
