import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import { formatStrike } from "./model";

/**
 * Owner 14 Nov ("set the panel up neatly; the OI table has gone down - bring it up; cut unnecessary things or give an
 * on/off toggle to hide them; on the OI table no button is visible on the strike - how will a user know where to
 * click?"):
 * - `useShownBlock`: one remembered show / hide choice per optional block of the Terminal's chain panel, on this
 *   browser (the same storage pattern as the one Details disclosure, src/ui/Details.tsx).
 * - `HideableBlock`: a slim on / off bar over such a block; the block's content is as before.
 * - `LegTradeButton`: the visible "B/S" button beside a strike, for its CE (left) or PE (right). It calls the chain's
 *   existing `onPickLeg` (the strike action sheet: Buy / Sell, Add to basket, Chart, Watchlist, Alert, then the
 *   existing order ticket) - no order path of its own.
 */
export const BLOCK_KEY = "atp_chain_block:";

export function readShown(id: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(BLOCK_KEY + id);
    return v === null ? fallback : v === "1";
  } catch { return fallback; }
}

export function writeShown(id: string, shown: boolean): void {
  try { localStorage.setItem(BLOCK_KEY + id, shown ? "1" : "0"); } catch { /* intentional: storage unavailable, the choice lasts this view */ }
}

export function useShownBlock(id: string, fallback: boolean): [boolean, (next: boolean) => void] {
  const [shown, setShown] = useState(() => readShown(id, fallback));
  return [shown, (next) => { setShown(next); writeShown(id, next); }];
}

/** The small Show / Hide button beside a block's own heading. */
export function ShowHide({ id, shown, onShown }: { id: string; shown: boolean; onShown: (next: boolean) => void }) {
  return (
    <button type="button" aria-expanded={shown} onClick={() => onShown(!shown)} data-hideable-toggle={id}
      className="inline-flex min-h-11 shrink-0 items-center gap-space-1 rounded-control px-space-1 text-t12 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-6">
      {shown ? "Hide" : "Show"}
      <ChevronDown size={14} aria-hidden className={cx("transition-transform duration-fast", shown && "rotate-180")} />
    </button>
  );
}

/** A slim on / off bar over one optional block (Terminal chain panel); the content itself is unchanged. */
export function HideableBlock({ id, label, defaultShown = true, children }: {
  id: string; label: string; defaultShown?: boolean; children: ReactNode;
}) {
  const [shown, setShown] = useShownBlock(id, defaultShown);
  return (
    <div data-hideable={id} data-shown={shown || undefined}>
      <button type="button" aria-expanded={shown} onClick={() => setShown(!shown)} data-hideable-toggle={id}
        className="flex min-h-11 w-full items-center justify-between gap-space-2 rounded-control border border-subtle bg-surface-1 px-space-2 text-left text-t12 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8">
        <span className="min-w-0 truncate">{label}</span>
        <span className="inline-flex shrink-0 items-center gap-space-1 text-brand">
          {shown ? "Hide" : "Show"}
          <ChevronDown size={14} aria-hidden className={cx("transition-transform duration-fast", shown && "rotate-180")} />
        </span>
      </button>
      {shown && <div className="mt-space-2">{children}</div>}
    </div>
  );
}

/** A HideableBlock only where `on` (the Terminal's panel); elsewhere the block as it is. */
export function Hideable({ on, children, ...rest }: { on: boolean; id: string; label: string; defaultShown?: boolean; children: ReactNode }) {
  return on ? <HideableBlock {...rest}>{children}</HideableBlock> : <>{children}</>;
}

/** The "B/S" button beside a strike: opens that contract's existing action sheet (Buy / Sell and the rest). */
export function LegTradeButton({ underlying, strike, side, onPick }: {
  underlying: string; strike: number; side: "call" | "put"; onPick: () => void;
}) {
  const right = side === "call" ? "CE" : "PE";
  const name = `${underlying ? `${underlying} ` : ""}${formatStrike(strike)} ${right}`;
  return (
    <button type="button" data-leg-trade={`${strike}|${right}`} aria-label={`Buy or sell ${name}`} title={`Buy or sell ${name}`}
      onClick={(e) => { e.stopPropagation(); onPick(); }} onKeyDown={(e) => e.stopPropagation()}
      className={cx("inline-flex h-11 min-w-11 shrink-0 items-center justify-center rounded-control border border-brand bg-surface-1 px-space-1 align-middle text-t12 font-semibold leading-none text-brand",
        "hover:bg-brand-tint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-6 md:min-w-7",
        side === "call" ? "mr-space-1" : "ml-space-1")}>
      B/S
    </button>
  );
}

/** The one line above a table whose strikes carry the B/S buttons. */
export function TradeHint() {
  return <p role="note" className="text-t12 text-fg-muted" data-trade-hint>Tap <span className="font-semibold text-brand">B/S</span> beside a strike to trade its CE / PE.</p>;
}
