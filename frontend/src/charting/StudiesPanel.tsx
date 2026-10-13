/**
 * N4 CH6: the study list on a chart - add a ScreenQL expression ("SMA(close, 20)", "RSI(14)", "close > EMA(close, 9)"),
 * see each study's value or why it was refused, remove it. The values are the server's (the screener runtime, as of
 * `at`); the WebWorker only turns them into chart series. Flag `chart_studies`: while it is off nothing is shown.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChartEngine } from "./engine";
import { layerTf } from "./priceAction";
import { windowStart } from "./replay";
import { createStudyMath, type StudyMath } from "./studyClient";
import type { LinePoint } from "./studyMath";
import {
  MAX_STUDIES, STUDY_COLORS, StudiesLoader, loadStudyList, saveStudyList, studiesApi, type StudiesResponse, type StudiesState,
} from "./studies";

const btn = "rounded px-1.5 py-0.5 text-[10px] font-semibold";

/** The list and its results (pure render, tested without a browser). */
export function StudiesList({ exprs, result, onRemove }: { exprs: readonly string[]; result: StudiesResponse | null; onRemove: (i: number) => void }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1" data-testid="studies-list">
      {exprs.map((e, i) => {
        const st = result?.studies[i];
        const value = st?.ok ? (st.kind === "bool" ? (st.last ? "true" : "false") : st.last_text ?? "–") : null;
        return (
          <li key={`${i}-${e}`} className="inline-flex items-center gap-1" data-ok={st ? String(st.ok) : "pending"}>
            <span aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: STUDY_COLORS[i % STUDY_COLORS.length] }} />
            <span className="font-mono">{st?.text ?? e}</span>
            {value != null && <span className="opacity-70">= {value}{st?.kind === "bool" && st.true_bars != null ? ` · true on ${st.true_bars} bars` : ""}</span>}
            {st && !st.ok && <span className="text-warn" title={st.problems.map((p) => p.message).join("; ")}>{st.problems[0]?.message}</span>}
            <button type="button" className={`${btn} text-fg-muted hover:text-down`} aria-label={`Remove ${e}`} onClick={() => onRemove(i)}>×</button>
          </li>
        );
      })}
    </ul>
  );
}

export function StudiesPanel({ engine, instrument, timeframe, at = null, start = null, refreshKey = null, candleTimes = [], closes = [], math }: {
  engine: ChartEngine | null; instrument: string; timeframe: string; at?: string | null; start?: string | null; refreshKey?: string | number | null;
  candleTimes?: readonly number[]; closes?: readonly number[]; math?: StudyMath;
}) {
  const tf = layerTf(timeframe);
  const [exprs, setExprs] = useState<string[]>(() => loadStudyList());
  const [draft, setDraft] = useState("");
  const [state, setState] = useState<StudiesState>({ result: null, failed: null, available: null });
  const loader = useMemo(() => new StudiesLoader((q) => studiesApi.get(q.instrument, q.exprs, q), setState), []);
  const ownMath = useMemo(() => math ?? createStudyMath(), [math]);
  useEffect(() => () => { if (!math) ownMath.dispose(); }, [math, ownMath]);
  const drawn = useRef<string[]>([]);

  useEffect(() => loader.load(instrument && tf ? { instrument, tf, exprs, at, start: windowStart(at, start), } : null),
            [loader, instrument, tf, exprs, at, start, refreshKey]);

  // draw: every new answer replaces what was drawn; no answer (loading, failed, off) draws nothing
  const { result } = state;
  useEffect(() => {
    if (!engine) return;
    let live = true;
    const clear = () => {
      for (const id of drawn.current) { engine.setStudy?.(id, null); engine.setLayer(id, null); }
      drawn.current = [];
    };
    clear();
    if (result) {
      result.studies.forEach((st, i) => {
        if (!st.ok) return;
        const color = STUDY_COLORS[i % STUDY_COLORS.length];
        const id = `study-${i}`;
        drawn.current.push(id);
        if (st.kind === "num" && st.values) {
          void ownMath.run({ op: "line", times: result.times, values: st.values }).then((data) => {
            if (live) engine.setStudy?.(id, { data: data as LinePoint[], color, pane: st.pane, title: st.text });
          });
        } else if (st.kind === "bool" && st.fires) {
          void ownMath.run({ op: "fires", fires: st.fires, candleTimes: [...candleTimes], closes: [...closes] }).then((marks) => {
            if (live) engine.setLayer(id, { asOf: result.as_of ?? undefined, labels: (marks as LinePoint[]).map((m) => ({ time: m.time, price: m.value, text: "▲", color, position: "below" as const })) });
          });
        }
      });
    }
    return () => { live = false; clear(); };
  }, [engine, result, ownMath, candleTimes, closes]);

  const update = (next: string[]) => { setExprs(next); saveStudyList(next); };
  const add = () => {
    const e = draft.trim();
    if (!e || exprs.length >= MAX_STUDIES) return;
    update([...exprs, e]);
    setDraft("");
  };

  if (state.available === false) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted" data-testid="studies-panel" title={result?.note}>
      <span className="font-semibold text-fg">Studies</span>
      <StudiesList exprs={exprs} result={result} onRemove={(i) => update(exprs.filter((_, k) => k !== i))} />
      {exprs.length < MAX_STUDIES && (
        <form className="inline-flex gap-1" onSubmit={(ev) => { ev.preventDefault(); add(); }}>
          <input value={draft} onChange={(ev) => setDraft(ev.target.value)} placeholder="ScreenQL, e.g. SMA(close, 20)" maxLength={300}
                 aria-label="Add a study (ScreenQL)" className="w-48 rounded border border-border bg-surface px-1 py-0.5 font-mono text-[11px]" />
          <button type="submit" className={`${btn} text-fg-muted hover:text-fg`}>Add</button>
        </form>
      )}
      {state.failed && <span className="text-warn">Studies could not be loaded: {state.failed}</span>}
    </div>
  );
}

export default StudiesPanel;
