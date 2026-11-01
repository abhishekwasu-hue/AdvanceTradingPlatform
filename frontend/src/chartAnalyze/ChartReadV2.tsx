import { PenLine, RotateCcw, ScanText } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "../components/primitives";
import { Combobox } from "../ui/Combobox";
import type { ChartRead, HybridNeed, HybridOverride, HybridResult, PickField } from "./types";

/**
 * M84 (owner order): Advanced analysis starts from the uploaded picture alone. The symbol and timeframe are read from
 * what is printed on the chart (the server's AI picture read, mapped to ATP's instruments and timeframes); the user's
 * own value, when he types one, wins. This file holds the three places the user sees or changes them, in place:
 * - `PickFields`: the optional fields (the form's "Change symbol / timeframe", the ask, the result's "Change");
 * - `NeedsCard`: a value that could not be read or does not map - its exact reason, and the one field to fill;
 * - `ChartReadLine`: "Read from your chart: NIFTY 50 · 15m" on the result, with "Change" and "Re-run" in place.
 * No price is ever read from the picture: every number comes from ATP's stored candles.
 */

export const CHANGE_LABEL = "Change symbol / timeframe";
export const OPTIONAL_NOTE = "ATP reads the symbol and timeframe from your chart. Set them here only to change what it reads.";
export const PRICES_NOTE = "No price is read from your picture: every number comes from ATP's stored candles for this symbol and timeframe.";
const input = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 uppercase text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";
const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";

export type TfOption = { id: string; label: string };

/** "Read from your chart: NIFTY 50 · 15m"; a value the user typed is named as his. */
export function readLine(pick: { symbol: string | null; timeframe: string | null }, source?: { symbol: string | null; timeframe: string | null } | null): string {
  const s = pick.symbol ?? "", tf = pick.timeframe ?? "";
  const from = { symbol: source?.symbol ?? "chart", timeframe: source?.timeframe ?? "chart" };
  if (from.symbol === "chart" && from.timeframe === "chart") return `Read from your chart: ${[s, tf].filter(Boolean).join(" · ")}`;
  if (from.symbol === "you" && from.timeframe === "you") return `Your symbol and timeframe: ${s} · ${tf}`;
  const word = (f: "symbol" | "timeframe") => (from[f] === "you" ? "yours" : "read from your chart");
  return `${s} (${word("symbol")}) · ${tf} (${word("timeframe")})`;
}

export function PickFields({ symbol, timeframe, onSymbol, onTimeframe, timeframes, fields = ["symbol", "timeframe"], invalid = [] }: {
  symbol: string; timeframe: string; onSymbol: (v: string) => void; onTimeframe: (v: string) => void; timeframes: TfOption[];
  fields?: PickField[]; invalid?: PickField[];
}) {
  const id = useId();
  return (
    <div className="grid gap-space-3 sm:grid-cols-2" data-testid="hybrid-pick">
      {fields.includes("symbol") && (
        <div>
          <label htmlFor={`${id}-symbol`} className="mb-space-1 block text-t12 font-medium text-fg-muted">The chart&apos;s symbol</label>
          <input id={`${id}-symbol`} value={symbol} onChange={(e) => onSymbol(e.target.value)} placeholder="For example NIFTY or RELIANCE"
            autoComplete="off" spellCheck={false} maxLength={40} aria-invalid={invalid.includes("symbol") || undefined} className={input} />
        </div>
      )}
      {fields.includes("timeframe") && (
        <div>
          <label htmlFor={`${id}-tf`} className="mb-space-1 block text-t12 font-medium text-fg-muted">The chart&apos;s timeframe</label>
          <Combobox id={`${id}-tf`} label="The chart's timeframe" value={timeframe} onChange={onTimeframe} className="w-full"
            invalid={invalid.includes("timeframe") || undefined} options={timeframes.map((t) => ({ value: t.id, label: t.label }))} />
        </div>
      )}
    </div>
  );
}

/** What the chart showed, for the ask: the values that mapped. */
function shown(read: ChartRead | null | undefined): string | null {
  const parts = [read?.symbol, read?.timeframe].filter(Boolean);
  return parts.length ? `Read from your chart: ${parts.join(" · ")}` : null;
}

export function NeedsCard({ needs, read, timeframes, running, onRun }: {
  needs: HybridNeed[]; read?: ChartRead | null; timeframes: TfOption[]; running: boolean; onRun: (change: Partial<HybridOverride>) => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const fields = needs.map((n) => n.field);
  const ready = fields.every((f) => (f === "symbol" ? symbol.trim() : timeframe));
  const line = shown(read);
  return (
    <section className={card} aria-labelledby="hy-needs" data-testid="hybrid-needs">
      <h2 id="hy-needs" className="flex items-center gap-space-2 text-t15 font-semibold text-fg">
        <ScanText size={18} aria-hidden className="shrink-0 text-brand" />{needs.length === 1 ? needs[0].ask : "Tell ATP the chart's symbol and timeframe to continue."}
      </h2>
      {line && <p className="mt-space-1 text-t13 text-fg" data-testid="chart-read">{line}</p>}
      <ul className="mt-space-2 space-y-space-1">
        {needs.map((n) => <li key={n.field} className="text-t13 text-fg-muted" data-need={n.field}>{n.reason}</li>)}
      </ul>
      <div className="mt-space-3">
        <PickFields symbol={symbol} timeframe={timeframe} onSymbol={setSymbol} onTimeframe={setTimeframe} timeframes={timeframes} fields={fields} invalid={fields} />
      </div>
      <Button variant="primary" className="mt-space-3 h-11 w-full sm:w-auto md:h-9" loading={running} disabled={!ready}
        onClick={() => onRun({ ...(fields.includes("symbol") ? { symbol: symbol.trim().toUpperCase() } : {}), ...(fields.includes("timeframe") ? { timeframe } : {}) })}>
        Analyze
      </Button>
    </section>
  );
}

export function ChartReadLine({ result, timeframes, running, onRun }: {
  result: HybridResult; timeframes: TfOption[]; running: boolean; onRun: (change: Partial<HybridOverride>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState(result.pick.symbol);
  const [timeframe, setTimeframe] = useState(result.pick.timeframe);
  const changed = { ...(symbol.trim().toUpperCase() !== result.pick.symbol ? { symbol: symbol.trim().toUpperCase() } : {}),
    ...(timeframe !== result.pick.timeframe ? { timeframe } : {}) };
  const source = result.pick_source ?? { symbol: "you", timeframe: "you" };
  return (
    <div className="mt-space-2" data-testid="chart-read-line">
      <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
        <ScanText size={16} aria-hidden className="shrink-0 text-brand" />
        <p className="min-w-0 break-words text-t13 font-medium text-fg" data-testid="chart-read">{readLine(result.pick, source)}</p>
        {!open && (
          <Button variant="ghost" className="h-11 md:h-8" icon={<PenLine size={14} aria-hidden />} onClick={() => setOpen(true)}>Change</Button>
        )}
      </div>
      {open && (
        <div className="mt-space-2 rounded-control border border-subtle bg-surface-2 p-space-3" data-testid="chart-read-change">
          <PickFields symbol={symbol} timeframe={timeframe} onSymbol={setSymbol} onTimeframe={setTimeframe} timeframes={timeframes} />
          <div className="mt-space-3 flex flex-col gap-space-2 sm:flex-row">
            <Button variant="primary" className="h-11 md:h-9" icon={<RotateCcw size={16} aria-hidden />} loading={running}
              disabled={!symbol.trim() || !timeframe || Object.keys(changed).length === 0} onClick={() => onRun(changed)}>Re-run</Button>
            <Button className="h-11 md:h-9" onClick={() => { setOpen(false); setSymbol(result.pick.symbol); setTimeframe(result.pick.timeframe); }}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
}
