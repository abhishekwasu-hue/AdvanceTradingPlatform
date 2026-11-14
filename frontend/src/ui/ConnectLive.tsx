import { Plug } from "lucide-react";
import type { ReactNode } from "react";
import { BROKER_PATH } from "../broker/paths";
import { cx } from "../components/primitives/cx";
import { useInPlace } from "../inplace/context";
import { CONNECT_LIVE } from "./lastClose";

/**
 * M86 D3: the ONE small "Connect your broker for live prices" button of a card. It opens the broker sheet in place
 * (the same drawer as the sign-in prompt's Connect, M75 NAV-6b) - the page underneath stays as it was, so closing the
 * sheet returns to the same state. One per card, never on each row.
 */
export function ConnectLiveButton({ className, compact = false }: { className?: string;
  /** Owner 14 Nov (the Terminal's watchlist header): a small chip in a narrow column - smaller words that wrap inside the
   * chip when the column is too narrow for one line, so the words are never cut. */
  compact?: boolean }) {
  const inPlace = useInPlace();
  return (
    <button type="button" data-connect-live onClick={() => inPlace.open(BROKER_PATH)} title={compact ? CONNECT_LIVE : undefined}
      className={cx("inline-flex min-h-11 items-center gap-space-1 rounded-control border border-subtle bg-surface-2 font-semibold text-brand hover:border-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9",
        compact ? "min-w-0 max-w-full px-space-2 text-left text-t12 leading-tight" : "shrink-0 px-space-3 text-t13", className)}>
      <Plug size={compact ? 12 : 14} aria-hidden className={compact ? "shrink-0" : undefined} />
      {compact ? <span className="min-w-0 break-words">{CONNECT_LIVE}</span> : CONNECT_LIVE}
    </button>
  );
}

/** One line for a card or a page: what is shown now (muted, optional) and the one button. */
export function NoBrokerLine({ children, className, testId, button = true }: { children?: ReactNode; className?: string; testId?: string;
  /** False where the page already shows its one button (a card inside a page that has the page line). */
  button?: boolean }) {
  return (
    <div data-no-broker={testId ?? "line"} className={cx("flex flex-wrap items-center gap-x-space-3 gap-y-space-2 text-t13 text-fg-muted", className)}>
      {children && <p className="min-h-11 min-w-0 flex-[1_1_14rem] content-center md:min-h-9">{children}</p>}
      {button && <ConnectLiveButton />}
    </div>
  );
}
