import type { ReactNode } from "react";
import { cx } from "../components/primitives/cx";

/**
 * M86 D2: the one line a card shows while its prices are the last end-of-day close (NSE's files) because the user has
 * no broker of his own: what is shown, with its date. One line per card, never on each row.
 */
export function NoBrokerLine({ children, className, testId }: { children?: ReactNode; className?: string; testId?: string }) {
  return (
    <div data-no-broker={testId ?? "line"} className={cx("flex flex-wrap items-center gap-x-space-3 gap-y-space-2 text-t13 text-fg-muted", className)}>
      {children && <p className="min-w-0 flex-1">{children}</p>}
    </div>
  );
}
