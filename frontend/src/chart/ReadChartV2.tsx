import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { LightweightEngine } from "../charting/lightweight";
import { Details } from "../ui/Details";
import { READ_LAYER_ID, readChartApi, readLayer, readSummary, type ReadChart } from "./readChart";

/**
 * AUD-T5: "Read this chart" (flags `ui_v2` + `read_chart`). While the toolbar button is on, the open chart is read on
 * the server as of its last closed bar and the reading is drawn as layers; below the chart one line stays open (the
 * structure) and the rest - pullback state, each area with its state, the commitment candle, how it is read - sits in
 * the closed Details. A new symbol or timeframe reads again; turning the button off removes the layers.
 */
export type ReadState = { status: "idle" | "loading" | "failed" | "off" } | { status: "ready"; read: ReadChart };

export function useReadChart(engine: LightweightEngine | null, instrument: string, tf: string | null, on: boolean): ReadState {
  const [state, setState] = useState<ReadState>({ status: "idle" });
  useEffect(() => {
    if (!on || !tf || !instrument) { setState({ status: "idle" }); return undefined; }
    let live = true;
    setState({ status: "loading" });
    readChartApi.read(instrument, tf).then(
      (read) => { if (live) setState({ status: "ready", read }); },
      (e: unknown) => { if (live) setState({ status: e instanceof ApiError && e.status === 404 ? "off" : "failed" }); },
    );
    return () => { live = false; };
  }, [instrument, tf, on]);

  const read = state.status === "ready" ? state.read : null;
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(READ_LAYER_ID, on && read ? readLayer(read) : null);
    return () => engine.setLayer(READ_LAYER_ID, null);
  }, [engine, on, read]);
  return state;
}

function asOfText(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function ReadChartBody({ read }: { read: ReadChart }) {
  const asOf = asOfText(read.as_of);
  return (
    <div data-testid="read-chart" className="text-t13 text-fg">
      <p>{readSummary(read)}</p>
      <Details id="charts.read">
        <ul className="list-disc space-y-space-1 pl-space-4">
          {read.lines.slice(read.structure ? 1 : 0).map((line, i) => <li key={i}>{line}</li>)}
        </ul>
        {read.answer_key && <p>{read.answer_key.words}</p>}
        <p>
          Read from the last closed bar{asOf ? ` (${asOf} IST)` : ""}; a bar still forming is not read. Areas are the stored areas near
          the price, each from its own timeframe. The pullback state is the Daily trend as of the previous close. The commitment candle
          is the newest one at an area since the areas were stored.
        </p>
        <p>{read.note}</p>
      </Details>
    </div>
  );
}

/** CT-1 C10: `onHide` (inside the shell, where the switch is in Ask AI) puts a "Hide the reading" button beside it. */
export function ReadChartPanel({ state, onHide }: { state: ReadState; onHide?: () => void }) {
  if (state.status === "idle" || state.status === "off") return null;
  return (
    <div role="status" className="flex items-start gap-space-2 border-t border-subtle px-space-3 py-space-2 text-t13 text-fg-muted">
      <div className="min-w-0 flex-1">
        {state.status === "loading" && <span>Reading this chart…</span>}
        {state.status === "failed" && <span>{onHide ? "This chart could not be read. Hide the reading and read it again from Ask AI." : "This chart could not be read. Turn Read this chart off and on again to retry."}</span>}
        {state.status === "ready" && <ReadChartBody read={state.read} />}
      </div>
      {onHide && (
        <button type="button" onClick={onHide} data-testid="read-chart-hide"
          className="inline-flex min-h-11 shrink-0 items-center rounded-control px-space-2 text-t13 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
          Hide the reading
        </button>
      )}
    </div>
  );
}

export default ReadChartPanel;
