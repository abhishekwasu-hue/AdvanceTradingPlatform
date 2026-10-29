import { Check, Info, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Details } from "../ui/Details";
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

/** The parts that are known, as one box (nothing while none is). Owner 29 Oct ("unnecessary information is displayed"):
 * only what needs his eye stays open - the warnings; the checks that are fine and every source note move into one closed
 * Details (moved, not removed). The max-loss line joins this box instead of a box of its own. */
export function RiskChecksV2({ lines, testId }: { lines: readonly (RiskLine | null)[]; testId: string }) {
  const shown = lines.filter((l): l is RiskLine => l !== null);
  if (!shown.length) return null;
  const warn = shown.filter((l) => l.tone === "warn");
  const fine = shown.filter((l) => l.tone !== "warn");
  const notes = [...new Set([...shown.map((l) => l.note), CHECKS_NOTE].filter(Boolean))];
  return (
    <section aria-label="Before you place" data-risk-checks={testId}
      className="space-y-space-2 rounded-control border border-subtle px-space-3 py-space-2 text-t13">
      <h4 className="text-t12 font-semibold text-fg-muted">Before you place</h4>
      {warn.length > 0 ? (
        <ul className="space-y-space-2">
          {warn.map((l) => (
            <li key={l.text} className="flex items-start gap-space-2 text-warn" data-risk-part="warn">
              <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" />
              <span>{l.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="flex items-center gap-space-2 text-fg" data-risk-clear><Check size={16} aria-hidden className="shrink-0 text-up" />All checks are clear.</p>
      )}
      <Details id={`checks.${testId}`} label={fine.length ? `${fine.length} more check${fine.length === 1 ? "" : "s"} and sources` : "Sources"} className="mt-0">
        {fine.length > 0 && (
          <ul className="space-y-space-2">
            {fine.map((l) => (
              <li key={l.text} className="flex items-start gap-space-2 text-fg" data-risk-part={l.tone}>
                <Info size={16} aria-hidden className="mt-0.5 shrink-0" />
                <span>{l.text}</span>
              </li>
            ))}
          </ul>
        )}
        {notes.map((n) => <p key={n} className="text-t12">{n}</p>)}
      </Details>
    </section>
  );
}
