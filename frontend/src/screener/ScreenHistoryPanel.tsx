/**
 * FN2-j (behind `ui_v2`): "When did this screen hit" (who matched on a date; how the matches moved afterwards) and the
 * rolling "Backtest" - each shown only while its flag is on. Daily screens on end-of-day bars; a past-data description,
 * never a promise: nothing here places, changes or proposes an order.
 */
import { useState, type ReactNode } from "react";
import { ApiError } from "../api/errors";
import { Button, SkeletonRows } from "../components/primitives";
import { fieldClass } from "../components/primitives/Input";
import { cx } from "../components/primitives/cx";
import { Details } from "../ui/Details";
import { formatINR, formatNumber, meaningOf } from "../ui/format";
import { sparkPath } from "../ui/Sparkline";
import {
  HISTORY_MAX_SYMBOLS, STEPS, benchmarkLabel, isAsOf, isBacktest, isFitness, pctOf, rangeProblem, screenHistoryApi, shareOf,
  type AsOfReply, type BacktestReply, type FitnessReply, type ScreenHistoryApi,
} from "./screenHistory";

export interface ScreenHistoryPanelProps {
  api?: ScreenHistoryApi;
  /** The scan's ScreenQL text ("" while there is none). */
  source: string;
  symbols: readonly string[];
  /** Name of the list the scan runs on, for the sentence under the title. */
  listName?: string;
  /** The scan's base timeframe: history runs daily screens only. */
  scanTf: string;
  /** Flag `screener_history`. */
  historyOn: boolean;
  /** Flag `screener_backtest` (it needs `screener_history` as well). */
  backtestOn: boolean;
}

const FAILED = "This could not be loaded. Check your connection, then try again.";
const tone = (v: number | null | undefined) => { const m = meaningOf(v == null ? null : v * 100); return m === "up" ? "text-up" : m === "down" ? "text-down" : "text-fg"; };
const word = (e: unknown) => (e instanceof ApiError && e.message ? e.message : FAILED);

function DateField({ label, value, onChange, id }: { label: string; value: string; onChange: (v: string) => void; id: string }) {
  return (
    <div className="min-w-[9rem] flex-1 space-y-1">
      <label htmlFor={id} className="block text-t12 font-medium text-fg-muted">{label}</label>
      <input id={id} type="date" value={value} onChange={(e) => onChange(e.target.value)} data-testid={id}
             className={cx(fieldClass, "min-h-11 md:min-h-9")} />
    </div>
  );
}

function StepField({ value, onChange, id }: { value: string; onChange: (v: string) => void; id: string }) {
  return (
    <div className="min-w-[9rem] flex-1 space-y-1">
      <label htmlFor={id} className="block text-t12 font-medium text-fg-muted">Check</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} data-testid={id} className={cx(fieldClass, "min-h-11 md:min-h-9")}>
        {STEPS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
    </div>
  );
}

function Stat({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className="rounded-panel border border-border bg-surface-1 p-3">
      <p className="text-t12 text-fg-muted">{label}</p>
      <p className={cx("font-tabular text-t15 font-semibold", className ?? "text-fg")}>{value}</p>
    </div>
  );
}

function Notes({ notes, disclaimer }: { notes: readonly string[]; disclaimer: string }) {
  return (
    <>
      {notes.length > 0 && <ul className="space-y-1 text-t12 text-fg-muted">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      <Details id="screener.history.about" label="About these figures"><p>{disclaimer}</p></Details>
    </>
  );
}

function AsOfSection({ api, screen, ready }: { api: ScreenHistoryApi; screen: { source: string; universe: { symbols: string[] } }; ready: boolean }) {
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [out, setOut] = useState<AsOfReply | null>(null);
  const go = async () => {
    if (!date) { setProblem("Pick a date."); return; }
    setBusy(true); setProblem(null);
    try {
      const r = await api.asof(screen, date);
      if (isAsOf(r)) setOut(r); else setProblem(FAILED);
    } catch (e) { setOut(null); setProblem(word(e)); } finally { setBusy(false); }
  };
  return (
    <section className="space-y-3" data-testid="history-asof" aria-labelledby="history-asof-title">
      <h3 id="history-asof-title" className="text-t15 font-semibold text-fg">When did this screen hit</h3>
      <div className="flex flex-wrap items-end gap-2">
        <DateField id="history-asof-date" label="On this date" value={date} onChange={setDate} />
        <Button size="v2" variant="primary" onClick={() => void go()} loading={busy} disabled={!ready} data-testid="history-asof-run">Show who matched</Button>
      </div>
      {problem && <p role="alert" className="text-t13 text-warn" data-testid="history-asof-problem">{problem}</p>}
      {busy && <SkeletonRows rows={2} label="Reading that date" />}
      {out && !busy && (
        <div className="space-y-2" data-testid="history-asof-result">
          <p className="text-t13 text-fg"><span className="font-tabular font-semibold">{formatNumber(out.matched.length)}</span> matched of{" "}
            <span className="font-tabular">{formatNumber(out.traded_that_day)}</span> that traded on {out.date}</p>
          {out.matched.length > 0
            ? <ul className="flex flex-wrap gap-2" aria-label="Matched symbols">{out.matched.map((s) => <li key={s} className="rounded-md border border-border bg-surface-2 px-2 py-1 text-t13 font-medium text-fg">{s}</li>)}</ul>
            : <p className="text-t13 text-fg-muted">None of the symbols matched on that date.</p>}
          <Notes notes={out.notes} disclaimer={out.disclaimer} />
        </div>
      )}
    </section>
  );
}

function FitnessSection({ api, screen, ready }: { api: ScreenHistoryApi; screen: { source: string; universe: { symbols: string[] } }; ready: boolean }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [every, setEvery] = useState("weekly");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [out, setOut] = useState<FitnessReply | null>(null);
  const go = async () => {
    const bad = rangeProblem(start, end);
    if (bad) { setProblem(bad); return; }
    setBusy(true); setProblem(null);
    try {
      const r = await api.fitness(screen, start, end, every);
      if (isFitness(r)) setOut(r); else setProblem(FAILED);
    } catch (e) { setOut(null); setProblem(word(e)); } finally { setBusy(false); }
  };
  return (
    <section className="space-y-3" data-testid="history-fitness" aria-labelledby="history-fitness-title">
      <h3 id="history-fitness-title" className="text-t15 font-semibold text-fg">How the matches moved afterwards</h3>
      <div className="flex flex-wrap items-end gap-2">
        <DateField id="history-fitness-start" label="From" value={start} onChange={setStart} />
        <DateField id="history-fitness-end" label="To" value={end} onChange={setEnd} />
        <StepField id="history-fitness-every" value={every} onChange={setEvery} />
        <Button size="v2" variant="primary" onClick={() => void go()} loading={busy} disabled={!ready} data-testid="history-fitness-run">Look back</Button>
      </div>
      {problem && <p role="alert" className="text-t13 text-warn" data-testid="history-fitness-problem">{problem}</p>}
      {busy && <SkeletonRows rows={3} label="Reading the window" />}
      {out && !busy && (
        <div className="space-y-2" data-testid="history-fitness-result">
          <p className="text-t13 text-fg-muted">
            {formatNumber(out.dates)} dates checked · on average <span className="font-tabular">{out.avg_passers == null ? "–" : formatNumber(out.avg_passers, 1)}</span> matched at a time
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[28rem] text-left text-t13">
              <caption className="sr-only">Matches against the whole list, by sessions afterwards</caption>
              <thead className="text-t12 text-fg-muted">
                <tr><th className="py-1 pr-2 font-medium">Sessions after</th><th className="py-1 pr-2 text-right font-medium">Matches moved</th>
                  <th className="py-1 pr-2 text-right font-medium">List moved</th><th className="py-1 pr-2 text-right font-medium">Difference</th>
                  <th className="py-1 text-right font-medium">Ahead of list</th></tr>
              </thead>
              <tbody>
                {out.horizons.map((h) => (
                  <tr key={h.horizon} className="border-t border-border-subtle" data-testid={`history-horizon-${h.horizon}`}>
                    <td className="py-1 pr-2 font-tabular text-fg">{h.horizon}</td>
                    <td className={cx("py-1 pr-2 text-right font-tabular", tone(h.passers_mean))}>{pctOf(h.passers_mean)}</td>
                    <td className={cx("py-1 pr-2 text-right font-tabular", tone(h.universe_mean))}>{pctOf(h.universe_mean)}</td>
                    <td className={cx("py-1 pr-2 text-right font-tabular", tone(h.excess_mean))}>{pctOf(h.excess_mean)}</td>
                    <td className="py-1 text-right font-tabular text-fg">{shareOf(h.hit_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-t13 text-fg-muted" data-testid="history-fitness-confidence">{out.confidence}</p>
          <Notes notes={out.notes} disclaimer={out.disclaimer} />
        </div>
      )}
    </section>
  );
}

function BacktestSection({ api, screen, ready }: { api: ScreenHistoryApi; screen: { source: string; universe: { symbols: string[] } }; ready: boolean }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [every, setEvery] = useState("monthly");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [out, setOut] = useState<BacktestReply | null>(null);
  const go = async () => {
    const bad = rangeProblem(start, end);
    if (bad) { setProblem(bad); return; }
    setBusy(true); setProblem(null);
    try {
      const r = await api.backtest(screen, start, end, every);
      if (isBacktest(r)) setOut(r); else setProblem(FAILED);
    } catch (e) { setOut(null); setProblem(word(e)); } finally { setBusy(false); }
  };
  const line = out ? sparkPath(out.equity.map((p) => p.equity), 300, 60, 0) : null;
  return (
    <section className="space-y-3" data-testid="history-backtest" aria-labelledby="history-backtest-title">
      <h3 id="history-backtest-title" className="text-t15 font-semibold text-fg">Backtest</h3>
      <p className="text-t13 text-fg-muted">Past-data simulation of the matches. It places nothing.</p>
      <div className="flex flex-wrap items-end gap-2">
        <DateField id="history-backtest-start" label="From" value={start} onChange={setStart} />
        <DateField id="history-backtest-end" label="To" value={end} onChange={setEnd} />
        <StepField id="history-backtest-every" value={every} onChange={setEvery} />
        <Button size="v2" variant="primary" onClick={() => void go()} loading={busy} disabled={!ready} data-testid="history-backtest-run">Run backtest</Button>
      </div>
      {problem && <p role="alert" className="text-t13 text-warn" data-testid="history-backtest-problem">{problem}</p>}
      {busy && <SkeletonRows rows={3} label="Running the simulation" />}
      {out && !busy && (
        <div className="space-y-3" data-testid="history-backtest-result">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="Total change" value={pctOf(out.strategy.total_return)} className={tone(out.strategy.total_return)} />
            <Stat label="Largest fall" value={pctOf(out.strategy.max_drawdown)} className={tone(out.strategy.max_drawdown)} />
            <Stat label="Re-checks" value={formatNumber(out.rebalances)} />
            <Stat label="Costs paid" value={formatINR(out.costs)} />
          </div>
          {line && (
            <svg viewBox="0 0 300 60" preserveAspectRatio="none" role="img" aria-label="Value of the simulation over the window"
                 className="h-16 w-full text-brand" data-testid="history-backtest-curve">
              <path d={line} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
            </svg>
          )}
          <p className="text-t13 text-fg-muted">
            Started with {formatINR(out.capital)}, ended with {formatINR(out.final_equity)} · on average{" "}
            <span className="font-tabular">{out.avg_holdings == null ? "–" : formatNumber(out.avg_holdings, 1)}</span> holdings
          </p>
          {Object.keys(out.benchmarks).length > 0 && (
            <ul className="space-y-1 text-t13" aria-label="Compared with">
              {Object.entries(out.benchmarks).map(([k, b]) => (
                <li key={k} className="flex flex-wrap justify-between gap-2" data-testid={`history-bench-${k}`}>
                  <span className="text-fg">{benchmarkLabel(k)}</span>
                  <span className="font-tabular text-fg-muted">
                    <span className={tone(b.total_return)}>{pctOf(b.total_return)}</span> · difference{" "}
                    <span className={tone(out.excess_total_return[k])}>{pctOf(out.excess_total_return[k])}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Notes notes={out.notes} disclaimer={out.disclaimer} />
          <Details id="screener.history.fills" label="How the simulation fills"><p>{out.fills}</p></Details>
        </div>
      )}
    </section>
  );
}

export default function ScreenHistoryPanel({ api = screenHistoryApi, source, symbols, scanTf, historyOn, backtestOn, listName }: ScreenHistoryPanelProps) {
  const screen = { source, universe: { symbols: symbols.slice(0, HISTORY_MAX_SYMBOLS) } };
  const blocked = scanTf !== "1d" ? "Screen history reads daily screens only. Set the scan to the daily timeframe first."
    : !source.trim() ? "Add a condition to the scan first; then you can look back." : null;
  const ready = !blocked && screen.universe.symbols.length > 0;
  if (!historyOn && !backtestOn) return null;
  return (
    <div className="space-y-5 text-t13" data-testid="screen-history">
      {blocked
        ? <p className="text-t13 text-fg-muted" data-testid="history-blocked">{blocked}</p>
        : <p className="text-t13 text-fg-muted">Looks back over {listName || "your list"} (up to {HISTORY_MAX_SYMBOLS} symbols) on end-of-day data.</p>}
      {historyOn && <AsOfSection api={api} screen={screen} ready={ready} />}
      {historyOn && <FitnessSection api={api} screen={screen} ready={ready} />}
      {historyOn && backtestOn && <BacktestSection api={api} screen={screen} ready={ready} />}
    </div>
  );
}
