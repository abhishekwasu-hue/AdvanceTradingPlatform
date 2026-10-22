import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { formatPrice } from "../ui/format";
import { formatCount, formatCountChange, formatGreek, formatIv, formatStrike } from "./model";
import type { ChainLeg, ChainRow } from "./types";

/**
 * OC1: one strike's full details - every figure of its call and its put (LTP, OI, OI change, volume, IV, bid, ask and
 * the Greeks). A sheet from the bottom on a phone, a dialog on a desktop. Read-only: nothing here places an order.
 */
const LINES: { label: string; value: (leg: ChainLeg) => string }[] = [
  { label: "LTP", value: (l) => formatPrice(l.ltp) },
  { label: "OI", value: (l) => formatCount(l.oi) },
  { label: "OI change", value: (l) => formatCountChange(l.oi_change) },
  { label: "Volume", value: (l) => formatCount(l.volume) },
  { label: "IV", value: (l) => formatIv(l.iv) },
  { label: "Bid", value: (l) => formatPrice(l.bid) },
  { label: "Ask", value: (l) => formatPrice(l.ask) },
  { label: "Delta", value: (l) => formatGreek("delta", l.delta) },
  { label: "Gamma", value: (l) => formatGreek("gamma", l.gamma) },
  { label: "Theta", value: (l) => formatGreek("theta", l.theta) },
  { label: "Vega", value: (l) => formatGreek("vega", l.vega) },
];

export default function RowDetailsV2({ row, atm, underlying, onClose }: {
  row: ChainRow | null; atm: number | null; underlying: string; onClose: () => void;
}) {
  return (
    <RD.Root open={row !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-row-details
          className="fixed inset-x-0 bottom-0 z-50 max-h-[85dvh] overflow-y-auto rounded-t-panel border-t border-subtle bg-surface-1 p-space-4 pb-[calc(1rem+env(safe-area-inset-bottom))] text-fg shadow-raised focus:outline-none md:inset-x-auto md:bottom-auto md:left-1/2 md:top-1/2 md:w-[28rem] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-panel md:border">
          {row && (
            <>
              <div className="flex items-center justify-between gap-space-3">
                <RD.Title className="text-t18 font-semibold text-fg">
                  {underlying} {formatStrike(row.strike)}
                  {row.strike === atm && <span className="ml-space-2 rounded-pill bg-surface-2 px-space-2 text-t12 font-medium text-fg-muted">ATM</span>}
                </RD.Title>
                <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
                  <X size={18} aria-hidden />
                </RD.Close>
              </div>
              <RowDetailsBody row={row} />
            </>
          )}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** The two columns of figures (Call, Put) for one strike. */
export function RowDetailsBody({ row }: { row: ChainRow }) {
  return (
    <table className="mt-space-3 w-full text-t14" aria-label={`Strike ${formatStrike(row.strike)} details`}>
      <thead>
        <tr className="text-t12 text-fg-muted">
          <th scope="col" className="py-space-1 text-left font-medium"><span className="sr-only">Figure</span></th>
          <th scope="col" className="py-space-1 text-right font-medium">Call</th>
          <th scope="col" className="py-space-1 text-right font-medium">Put</th>
        </tr>
      </thead>
      <tbody>
        {LINES.map((line) => (
          <tr key={line.label} className="border-t border-subtle">
            <th scope="row" className="py-space-2 text-left font-normal text-fg-muted">{line.label}</th>
            <td className="py-space-2 text-right font-tabular text-fg">{line.value(row.call)}</td>
            <td className="py-space-2 text-right font-tabular text-fg">{line.value(row.put)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
