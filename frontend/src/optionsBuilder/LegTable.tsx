/**
 * P1-c2: the legs, edited in place - direction, type, strike (on the instrument's grid), expiry, lots, premium and IV,
 * with each leg's delta and theta from the last evaluation. Hedge legs are listed first. A premium the model filled in
 * is marked "model" until the trader types the real one.
 */
import { useEffect, useState, type InputHTMLAttributes } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button, cx } from "../components/primitives";
import { snapStrike, type Evaluation, type Leg, type OptionKind } from "./model";

const cell = "h-8 rounded-md border border-border bg-surface-2 px-2 font-mono text-xs text-fg tabular-nums focus:outline-none focus:ring-2 focus:ring-brand";

export interface LegTableProps {
  legs: Leg[];
  /** each leg's evaluated row by leg id - absent while that leg is being re-evaluated */
  rows: Record<string, Evaluation["legs"][number]>;
  step: number;
  onChange: (legs: Leg[]) => void;
  onAdd: () => void;
}

/**
 * A number cell that keeps what is typed: clearing it, or typing a half-finished number, is editing - not 0 sent to
 * the server. A value is committed only when it parses and `valid` takes it; leaving the cell shows the leg's value
 * again. `empty` (when given) is what an empty cell commits. `commitOn="blur"` (the strike) commits on Enter or on
 * leaving the cell only, so "22100" typed slowly never sends strikes 2, 22, 221. Until the trader types, the cell
 * follows the leg (a re-priced premium shows even while the cell has focus).
 */
function NumberCell({ value, valid, onCommit, empty, commitOn = "change", ...rest }: {
  value: number | null; valid: (n: number) => boolean; onCommit: (n: number | null) => void; empty?: null;
  commitOn?: "change" | "blur";
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const shown = value == null ? "" : String(value);
  const [text, setText] = useState(shown);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!dirty) setText(shown); }, [shown, dirty]);
  const commit = (t: string) => {
    if (t.trim() === "") { if (empty !== undefined) onCommit(empty); return; }
    const n = Number(t);
    if (Number.isFinite(n) && valid(n)) onCommit(n);
  };
  return (
    <input {...rest} type="number" value={text}
           onBlur={() => { if (dirty && commitOn === "blur") commit(text); setDirty(false); setText(shown); }}
           onKeyDown={(e) => { if (e.key === "Enter" && commitOn === "blur") { commit(text); setDirty(false); } }}
           onChange={(e) => {
             const t = e.target.value;
             setText(t);
             setDirty(true);
             if (commitOn === "change") commit(t);
           }} />
  );
}

export function LegTable({ legs, rows, step, onChange, onAdd }: LegTableProps) {
  const set = (id: string, patch: Partial<Leg>) => onChange(legs.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  return (
    <section aria-label="Legs" className="rounded-xl border border-border bg-surface-1">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-fg">Legs</h2>
        <Button size="sm" onClick={onAdd}><Plus size={14} /> Add leg</Button>
      </header>
      {legs.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-fg-muted">Pick a template above, or add a leg.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-fg-muted">
              <tr className="border-b border-border">
                {["#", "Side", "Type", "Strike", "Expiry", "Lots", "Premium", "IV %", "Delta", "Theta/day", ""].map((h) => (
                  <th key={h} scope="col" className="px-2 py-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {legs.map((l, i) => {
                const g = rows[l.id];
                return (
                  <tr key={l.id} className="border-b border-border/60 last:border-b-0" data-testid="leg-row">
                    <td className="px-2 font-mono text-fg-muted">{i + 1}</td>
                    <td className="px-2 py-1.5">
                      <button type="button" aria-label={`Leg ${i + 1} side: ${l.direction}. Switch`} onClick={() => set(l.id, { direction: l.direction === "BUY" ? "SELL" : "BUY" })}
                              className={cx("h-8 w-14 rounded-md border font-mono text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-brand",
                                l.direction === "BUY" ? "border-up/50 text-up" : "border-down/50 text-down")}>
                        {l.direction}
                      </button>
                    </td>
                    <td className="px-2"><select aria-label={`Leg ${i + 1} type`} className={cn(cell, "w-[4.5rem]")} value={l.option_type} onChange={(e) => set(l.id, { option_type: e.target.value as OptionKind })}>
                      <option value="CE">CE</option><option value="PE">PE</option><option value="FUT">FUT</option>
                    </select></td>
                    <td className="px-2"><NumberCell aria-label={`Leg ${i + 1} strike`} className={cn(cell, "w-24")} step={step || 1} value={l.strike} disabled={l.option_type === "FUT"} commitOn="blur"
                                                     valid={(n) => n > 0} onCommit={(n) => n != null && set(l.id, { strike: snapStrike(n, step) })} /></td>
                    <td className="px-2"><input aria-label={`Leg ${i + 1} expiry`} className={cn(cell, "w-36")} type="date" value={l.expiry} onChange={(e) => set(l.id, { expiry: e.target.value })} /></td>
                    <td className="px-2"><NumberCell aria-label={`Leg ${i + 1} lots`} className={cn(cell, "w-16")} min={1} step={1} value={l.lots}
                                                     valid={(n) => Number.isInteger(n) && n >= 1 && n <= 1000} onCommit={(n) => n != null && set(l.id, { lots: n })} /></td>
                    <td className="px-2">
                      <div className="flex items-center gap-1">
                        <NumberCell aria-label={`Leg ${i + 1} premium`} className={cn(cell, "w-24")} min={0} step={0.05} value={l.premium}
                                    valid={(n) => n >= 0} onCommit={(n) => n != null && set(l.id, { premium: n, premium_source: "manual" })} />
                        {l.premium_source === "model" && <span className="rounded-full border border-warn/50 px-1.5 text-[10px] text-warn" title="Priced by the model - type the real price">model</span>}
                      </div>
                    </td>
                    <td className="px-2"><NumberCell aria-label={`Leg ${i + 1} IV percent`} className={cn(cell, "w-20")} step={0.1} disabled={l.option_type === "FUT"}
                                                     value={l.iv == null ? null : +(l.iv * 100).toFixed(2)} placeholder={g?.iv_source ? `${(g.iv * 100).toFixed(1)}` : "solve"}
                                                     valid={(n) => n > 0 && n < 500} empty={null} onCommit={(n) => set(l.id, { iv: n == null ? null : n / 100 })} /></td>
                    <td className="px-2 font-mono tabular-nums text-fg">{g ? g.greeks.delta.toFixed(1) : "–"}</td>
                    <td className="px-2 font-mono tabular-nums text-fg">{g ? g.greeks.theta.toFixed(0) : "–"}</td>
                    <td className="px-2">
                      <button type="button" aria-label={`Remove leg ${i + 1}`} onClick={() => onChange(legs.filter((x) => x.id !== l.id))}
                              className="rounded-md p-1 text-fg-muted hover:text-down focus:outline-none focus:ring-2 focus:ring-brand"><Trash2 size={14} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function cn(...parts: string[]): string {
  return cx(...parts);
}
