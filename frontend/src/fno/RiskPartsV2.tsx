import { Info, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { cx } from "../components/primitives/cx";
import type { RiskLine } from "./insights";
import { CHECKS_NOTE, riskPartsApi, type EventRisk, type RiskPartsApi } from "./riskParts";

/**
 * F&O workspace 8b, slice 4: the risk line's other parts (pure parts in riskParts.ts) - the events before the expiry, the
 * strike's liquidity and the margin beside the PAPER balance - one box under the max-loss line. Text only: no control,
 * and nothing reads it (the ticket's `can_place` / the basket's `can_execute` alone decide its buttons).
 */

/** The events calendar's entries up to this expiry, read once per underlying + expiry (null while reading, "error" when
 * it could not be read - the line then says so). */
export function useEventRisk(on: boolean, underlying: string | null | undefined, expiry: string | null | undefined,
                             api: Pick<RiskPartsApi, "eventRisk"> = riskPartsApi): EventRisk | "error" | null {
  const [state, setState] = useState<{ key: string; value: EventRisk | "error" } | null>(null);
  const key = on && underlying && expiry ? `${underlying}|${expiry}` : "";
  useEffect(() => {
    if (!key || !underlying || !expiry || state?.key === key) return undefined;
    let alive = true;
    api.eventRisk(underlying, expiry)
      .then((value) => { if (alive) setState({ key, value }); })
      .catch(() => { if (alive) setState({ key, value: "error" }); });
    return () => { alive = false; };
  }, [key, underlying, expiry, api, state?.key]);
  return key && state?.key === key ? state.value : null;
}

/** The parts that are known, as one box (nothing while none is). */
export function RiskChecksV2({ lines, testId }: { lines: readonly (RiskLine | null)[]; testId: string }) {
  const shown = lines.filter((l): l is RiskLine => l !== null);
  if (!shown.length) return null;
  return (
    <section aria-label="Before you place (information only)" data-risk-checks={testId}
      className="space-y-space-2 rounded-control border border-subtle px-space-3 py-space-2 text-t13">
      <h4 className="text-t12 font-semibold text-fg-muted">Before you place (information only)</h4>
      <ul className="space-y-space-2">
        {shown.map((l) => {
          const Icon = l.tone === "warn" ? TriangleAlert : Info;
          return (
            <li key={l.text} className={cx("flex items-start gap-space-2", l.tone === "warn" ? "text-warn" : "text-fg")} data-risk-part={l.tone}>
              <Icon size={16} aria-hidden className="mt-0.5 shrink-0" />
              <span>
                <span className="block">{l.text}</span>
                <span className={cx("block text-t12", l.tone === "warn" ? "" : "text-fg-muted")}>{l.note}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="text-t12 text-fg-muted">{CHECKS_NOTE}</p>
    </section>
  );
}
