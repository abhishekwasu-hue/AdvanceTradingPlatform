import { useEffect, useState } from "react";
import { request } from "../../api/client";
import { guardDeep, guardText } from "../../chartAnalyze/signalLanguage";
import { cx } from "../../components/primitives/cx";
import { Details } from "../../ui/Details";

/**
 * M75 W-4 (owner order M75 CW-1 / CW-2; M75Q-5): the workspace's AI card. It starts by itself once the code reading is
 * there - one server call per (symbol, timeframe) opened; the server runs the model at most once per closed bar and
 * reuses that card on every later open, inside his plan's limits and his AI key. A cap, a missing key or part: one line,
 * the code reading stands on its own. Every text passes the signal-language guard again here; no order button.
 */
export interface WorkspaceCard {
  status: "ready" | "code_only" | "off" | "no_data" | "limit" | "running" | "unavailable" | "failed";
  message: string | null;
  card: null | {
    story?: string[];
    trend?: { side: string; text: string };
    current_state?: { phase: string; text: string };
    rsi?: { divergence: string; text: string };
    evidence?: { name: string; present: boolean }[];
    scenarios?: { primary?: string; alternative?: string };
    what_to_wait_for?: { condition: string | null } | null;
  };
  notes: string[];
  as_of: string | null;
  cached: boolean;
  disclaimer: string;
}

/** The AI card's timeframe for a chart's server timeframe; null where the hybrid reading is not offered. */
const CARD_TF: Record<string, string> = { "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d" };
export const cardTimeframe = (tf: string | null | undefined): string | null => (tf ? CARD_TF[tf] ?? null : null);

export const workspaceCardApi = {
  card: (symbol: string, timeframe: string) =>
    request<WorkspaceCard>("/chart-analyze/workspace", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, timeframe }) }),
};

export type CardState = { status: "idle" | "loading" | "failed" } | { status: "done"; card: WorkspaceCard };

/** Asks once per (symbol, timeframe) while `on` (the code reading is ready). */
export function useWorkspaceCard(symbol: string | null, timeframe: string | null, on: boolean): CardState {
  const [state, setState] = useState<CardState>({ status: "idle" });
  useEffect(() => {
    if (!on || !symbol || !timeframe) { setState({ status: "idle" }); return undefined; }
    let live = true;
    setState({ status: "loading" });
    workspaceCardApi.card(symbol, timeframe).then(
      (card) => { if (live) setState({ status: "done", card }); },
      () => { if (live) setState({ status: "failed" }); },
    );
    return () => { live = false; };
  }, [symbol, timeframe, on]);
  return state;
}

const TONE: Record<string, string> = { up: "text-up", down: "text-down" };
const KEEP = new Set(["disclaimer"]);

export function AiCardV2({ state, offered }: { state: CardState; offered: boolean }) {
  const muted = "text-t13 text-fg-muted";
  const done = state.status === "done" ? state.card : null;
  let body;
  if (!offered) body = <p className={muted}>The AI part is offered on the 5-minute, 15-minute, 1-hour and Daily charts.</p>;
  else if (state.status === "idle") return null;
  else if (state.status === "loading") body = <p role="status" className={muted}>The AI is reading this chart…</p>;
  else if (state.status === "failed") body = <p role="status" className={muted}>The AI part could not run this time; the code reading above is complete on its own.</p>;
  else if (!done) return null;
  else if (!done.card) body = <p role="status" className={muted} data-ai-card-line>{guardText(done.message ?? "")}</p>;
  else {
    const c = guardDeep(done.card, KEEP);
    const rows = [
      c.trend && { key: "trend", label: "Trend", value: c.trend.text, tone: TONE[c.trend.side] },
      c.current_state && { key: "state", label: "Now", value: c.current_state.text },
      c.rsi && { key: "rsi", label: "RSI", value: c.rsi.text, tone: c.rsi.divergence === "bullish" ? "text-up" : c.rsi.divergence === "bearish" ? "text-down" : undefined },
      c.what_to_wait_for?.condition && { key: "watch", label: "What to watch", value: c.what_to_wait_for.condition },
    ].filter((r): r is { key: string; label: string; value: string; tone?: string } => !!r && !!r.value);
    body = (
      <>
        {(c.story ?? []).slice(0, 2).map((p, i) => <p key={i} className="text-t13 text-fg">{p}</p>)}
        {rows.length > 0 && (
          <table className="w-full table-fixed text-t13">
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} data-ai-row={r.key} className="border-b border-subtle last:border-b-0 align-top">
                  <th scope="row" className="w-2/5 py-space-1 pr-space-2 text-left font-normal text-fg-muted">{r.label}</th>
                  <td className={cx("break-words py-space-1", r.tone ?? "text-fg")}>{r.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {(c.scenarios?.primary || (c.evidence ?? []).length > 0) && (
          <Details id="copilot.workspace.ai" label="Scenarios and evidence">
            {c.scenarios?.primary && <p className="text-fg">{c.scenarios.primary}</p>}
            {c.scenarios?.alternative && <p>{c.scenarios.alternative}</p>}
            {(c.evidence ?? []).length > 0 && (
              <ul className="space-y-space-1">
                {(c.evidence ?? []).map((e) => <li key={e.name} className={e.present ? "text-fg" : "text-fg-muted"}>{e.present ? "✓" : "–"} {e.name}</li>)}
              </ul>
            )}
          </Details>
        )}
        <p className="text-t12 text-fg-muted">{done.disclaimer}</p>
      </>
    );
  }
  return (
    <div data-ai-card={done ? done.status : state.status} className="space-y-space-2 border-t border-subtle pt-space-3">
      <h4 className="text-t13 font-semibold text-fg">AI reading</h4>
      {body}
    </div>
  );
}

export default AiCardV2;
