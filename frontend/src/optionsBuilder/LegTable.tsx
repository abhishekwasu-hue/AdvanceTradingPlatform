/**
 * P1-c2 / Z5: the legs, edited in place - direction, type, strike (on the instrument's grid), expiry, lots, premium and
 * IV, with each leg's Greeks (delta, gamma, theta per day, vega) from the last evaluation. On a phone each leg is a card
 * of labelled fields below the payoff. Hedge legs are listed first. A premium the model filled in
 * is marked "model" until the trader types the real one.
 */
import { useEffect, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button, cx } from "../components/primitives";
import { formatNumber } from "../ui/format";
import { snapStrike, type Evaluation, type Leg, type OptionKind } from "./model";

const cell = "h-8 rounded-md border border-border bg-surface-2 px-2 font-mono text-xs text-fg tabular-nums focus:outline-none focus:ring-2 focus:ring-brand";

export interface LegTableProps {
  legs: Leg[];
  /** each leg's evaluated row by leg id - absent while that leg is being re-evaluated */
  rows: Record<string, Evaluation["legs"][number]>;
  step: number;
  onChange: (legs: Leg[]) => void;
  onAdd: () => void;
  /** phones: one card per leg (labelled fields) instead of the wide table */
  compact?: boolean;
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

export function LegTable({ legs, rows, step, onChange, onAdd, compact = false }: LegTableProps) {
  const set = (id: string, patch: Partial<Leg>) => onChange(legs.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  // Each field once; the table (wide screens) and the cards (phones) lay the same controls out differently.
  const fields = (l: Leg, i: number) => {
    const g = rows[l.id];
    const n = i + 1;
    const w = (cls: string) => (compact ? cn(cell, "w-full") : cn(cell, cls));
    return {
      side: (
        <button type="button" aria-label={`Leg ${n} side: ${l.direction}. Switch`} onClick={() => set(l.id, { direction: l.direction === "BUY" ? "SELL" : "BUY" })}
                className={cx("h-8 rounded-md border font-mono text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-brand",
                  compact ? "w-full" : "w-14", l.direction === "BUY" ? "border-up/50 text-up" : "border-down/50 text-down")}>
          {l.direction}
        </button>
      ),
      type: (
        <select aria-label={`Leg ${n} type`} className={w("w-[4.5rem]")} value={l.option_type} onChange={(e) => set(l.id, { option_type: e.target.value as OptionKind })}>
          <option value="CE">CE</option><option value="PE">PE</option><option value="FUT">FUT</option>
        </select>
      ),
      strike: (
        <NumberCell aria-label={`Leg ${n} strike`} className={w("w-24")} step={step || 1} value={l.strike} disabled={l.option_type === "FUT"} commitOn="blur"
                    valid={(v) => v > 0} onCommit={(v) => v != null && set(l.id, { strike: snapStrike(v, step) })} />
      ),
      expiry: <input aria-label={`Leg ${n} expiry`} className={w("w-36")} type="date" value={l.expiry} onChange={(e) => set(l.id, { expiry: e.target.value })} />,
      lots: (
        <NumberCell aria-label={`Leg ${n} lots`} className={w("w-16")} min={1} step={1} value={l.lots}
                    valid={(v) => Number.isInteger(v) && v >= 1 && v <= 1000} onCommit={(v) => v != null && set(l.id, { lots: v })} />
      ),
      premium: (
        <div className="flex items-center gap-1">
          <NumberCell aria-label={`Leg ${n} premium`} className={w("w-24")} min={0} step={0.05} value={l.premium}
                      valid={(v) => v >= 0} onCommit={(v) => v != null && set(l.id, { premium: v, premium_source: "manual" })} />
          {l.premium_source === "model" && <span className="rounded-full border border-warn/50 px-1.5 text-xs text-warn" title="Priced by the model - type the real price">model</span>}
        </div>
      ),
      iv: (
        <NumberCell aria-label={`Leg ${n} IV percent`} className={w("w-20")} step={0.1} disabled={l.option_type === "FUT"}
                    value={l.iv == null ? null : +(l.iv * 100).toFixed(2)} placeholder={g?.iv_source ? formatNumber(g.iv * 100, 1) : "solve"}
                    valid={(v) => v > 0 && v < 500} empty={null} onCommit={(v) => set(l.id, { iv: v == null ? null : v / 100 })} />
      ),
      delta: formatNumber(g?.greeks.delta, 1),
      gamma: formatNumber(g?.greeks.gamma, 4),
      theta: formatNumber(g?.greeks.theta, 0),
      vega: formatNumber(g?.greeks.vega, 1),
      remove: (
        <button type="button" aria-label={`Remove leg ${n}`} onClick={() => onChange(legs.filter((x) => x.id !== l.id))}
                className="rounded-md p-1 text-fg-muted hover:text-down focus:outline-none focus:ring-2 focus:ring-brand"><Trash2 size={14} /></button>
      ),
    };
  };
  return (
    <section aria-label="Legs" className="rounded-xl border border-border bg-surface-1">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-fg">Legs</h2>
        <Button size="sm" onClick={onAdd}><Plus size={14} /> Add leg</Button>
      </header>
      {legs.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-fg-muted">Pick a template above, or add a leg.</p>
      ) : compact ? (
        <ol className="divide-y divide-border/60">
          {legs.map((l, i) => {
            const f = fields(l, i);
            const label = (text: string, node: ReactNode) => (
              <div className="min-w-0"><div className="mb-1 text-xs text-fg-muted">{text}</div>{node}</div>
            );
            return (
              <li key={l.id} className="space-y-2 px-4 py-3" data-testid="leg-row">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-fg-muted">Leg {i + 1}</span>
                  {f.remove}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {label("Side", f.side)}{label("Type", f.type)}{label("Lots", f.lots)}
                  {label("Strike", f.strike)}{label("Premium", f.premium)}{label("IV %", f.iv)}
                </div>
                {label("Expiry", f.expiry)}
                <dl className="grid grid-cols-4 gap-2 font-mono text-xs tabular-nums">
                  {(["delta", "gamma", "theta", "vega"] as const).map((k) => (
                    <div key={k}><dt className="text-fg-muted">{k === "theta" ? "Theta/day" : k[0].toUpperCase() + k.slice(1)}</dt><dd className="text-fg">{f[k]}</dd></div>
                  ))}
                </dl>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-xs">
            <thead className="text-fg-muted">
              <tr className="border-b border-border">
                {["#", "Side", "Type", "Strike", "Expiry", "Lots", "Premium", "IV %", "Delta", "Gamma", "Theta/day", "Vega", ""].map((h) => (
                  <th key={h} scope="col" className="px-2 py-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {legs.map((l, i) => {
                const f = fields(l, i);
                return (
                  <tr key={l.id} className="border-b border-border/60 last:border-b-0" data-testid="leg-row">
                    <td className="px-2 font-mono text-fg-muted">{i + 1}</td>
                    <td className="px-2 py-1.5">{f.side}</td>
                    <td className="px-2">{f.type}</td>
                    <td className="px-2">{f.strike}</td>
                    <td className="px-2">{f.expiry}</td>
                    <td className="px-2">{f.lots}</td>
                    <td className="px-2">{f.premium}</td>
                    <td className="px-2">{f.iv}</td>
                    {(["delta", "gamma", "theta", "vega"] as const).map((k) => <td key={k} className="px-2 font-mono tabular-nums text-fg">{f[k]}</td>)}
                    <td className="px-2">{f.remove}</td>
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
