import { useId, useState, type FormEvent } from "react";
import { useOptionalAuth } from "../auth/AuthContext";
import { SymbolPicker, recentKeyFor } from "../ui/SymbolPicker";
import { Button } from "../components/primitives";
import { guardStrikes } from "./signalLanguage";
import type { StrikesAnswer } from "./types";
import { EMPTY, formatDateIST } from "../ui/format";

/**
 * Analyze a chart: "Option strikes · from your settings". The analysis never picks a strike: this block only shows what
 * the user's OWN option deployment settings on this symbol work out to at the setup's trigger level (backend
 * app/chart_analyze/strikes.py), always flagged "From your settings", approximate in Image-only mode. Structure words
 * only (main strike, wing); no order button.
 */

export type StrikesState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: StrikesAnswer };

const RIGHT = { CE: "Call", PE: "Put" } as const;

function strikeText(value: number): string {
  return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function expiryText(iso: string | null): string {
  if (!iso) return "";
  const text = formatDateIST(iso);                          // REV-1 M3: a bare date is that day in IST
  return text === EMPTY ? iso : text;
}

export const FROM_SETTINGS = "From your settings";
export const NOT_PICKED = "The analysis does not pick strikes. These come from your own deployment settings, worked out at the trigger level.";

export default function StrikesSectionV2({ state, onSymbol }: { state: StrikesState; onSymbol?: (symbol: string) => void }) {
  const [symbol, setSymbol] = useState("");
  const symbolId = useId();
  const auth = useOptionalAuth();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (symbol.trim()) onSymbol?.(symbol.trim());
  };
  const data = state.status === "ready" ? guardStrikes(state.data) : null;
  const askSymbol = data != null && data.symbol == null && data.side !== "none";
  const ref = data?.reference;

  return (
    <section aria-labelledby="ca-strikes" className="rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5" data-block="ca-strikes">
      <div className="mb-space-3 flex flex-wrap items-center gap-space-2">
        <h3 id="ca-strikes" className="text-t15 font-semibold text-fg">Option strikes</h3>
        <span className="rounded-pill border border-brand px-space-2 py-0.5 text-t12 font-medium text-brand" data-testid="from-settings">{FROM_SETTINGS}</span>
      </div>
      <p className="text-t12 text-fg-muted">{NOT_PICKED}</p>

      {state.status === "loading" && <p className="mt-space-3 text-t14 text-fg-muted" aria-busy="true">Working out the strikes…</p>}
      {state.status === "error" && <p className="mt-space-3 text-t14 text-fg" role="alert">{state.message}</p>}

      {data && (
        <div className="mt-space-3 space-y-space-3">
          {ref?.price != null && data.items.length > 0 && (
            <p className="text-t14 text-fg">
              Worked out at {ref.text}{" "}
              <span className="font-tabular text-fg-muted">{ref.approximate ? "≈ " : ""}{strikeText(ref.price)}</span>
              {data.symbol && <> on {data.symbol}</>}.
            </p>
          )}
          {data.approximate_note && data.items.length > 0 && (
            <p className="rounded-control bg-warn-tint px-space-3 py-space-2 text-t12 text-fg" role="note">{data.approximate_note}</p>
          )}
          {data.message && <p className="text-t14 text-fg-muted" data-testid="strikes-message">{data.message}</p>}
          {askSymbol && (
            <form onSubmit={submit} className="flex flex-col gap-space-2 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1 space-y-1 sm:max-w-xs">
                <label htmlFor={symbolId} className="block text-xs font-medium text-fg-muted">Symbol</label>
                {/* CT-1 C6: the one symbol picker, over the symbols that have options. */}
                <SymbolPicker id={symbolId} label="Symbol" value={symbol} exchange="NSE" universe="optionable" storageKey={recentKeyFor(auth?.user?.id)}
                  onPick={(p) => setSymbol(p.symbol)} commitTyped onClear={() => setSymbol("")} className="w-full" />
              </div>
              <Button type="submit" className="h-11 md:h-9">Show strikes</Button>
            </form>
          )}
          {data.items.length > 0 && (
            <ul className="space-y-space-3" aria-label="Your option settings on this symbol">
              {data.items.map((item) => (
                <li key={item.id} className="rounded-control bg-surface-2 p-space-3" data-testid="strikes-item">
                  <p className="text-t14 font-medium text-fg">{item.settings}</p>
                  <p className="text-t12 text-fg-muted">{item.symbol} · {item.mode}{item.expiry ? ` · expiry ${expiryText(item.expiry)}` : ""}</p>
                  {item.legs && item.legs.length > 0 ? (
                    <table className="mt-space-2 w-full text-left text-t14">
                      <thead><tr className="text-t12 text-fg-muted"><th className="py-space-1 font-medium">Leg</th><th className="font-medium">Type</th>
                        <th className="text-right font-medium">Strike</th></tr></thead>
                      <tbody>
                        {item.legs.map((leg, i) => (
                          <tr key={i} className="border-t border-subtle">
                            <td className="py-space-1 text-fg">{leg.label}</td>
                            <td className="text-fg-muted">{RIGHT[leg.right] ?? leg.right}</td>
                            <td className="text-right font-tabular text-fg">{data.reference.approximate ? "≈ " : ""}{strikeText(leg.strike)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : item.note ? <p className="mt-space-2 text-t12 text-fg-muted">{item.note}</p> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
