/**
 * U7 LG4a: actions on a Legends card (flag `screener_legends_actions`) - the card's screen, with its default parameters
 * filled in, run on the user's own list (POST /api/screener/run), set as a bar-close alert rule on one symbol where a
 * rule can hold it (POST /api/alerts/rules), or copied as ScreenQL. Research only: a match is a list entry or a
 * notification, never an order; every refusal shows the server's own reason.
 */
import { useState } from "react";
import { ApiError, api } from "../api/client";
import { Button } from "../components/primitives";
import { screenerApi, type RunResponse } from "./api";
import { filledSource, parseSymbols, type CardActions, type LegendCardData } from "./legends";

const why = (e: unknown) => (e instanceof ApiError ? e.message : "The request failed.");

export function LegendActions({ card, actions }: { card: LegendCardData; actions: CardActions }) {
  const source = filledSource(card);
  const [list, setList] = useState("");
  const [run, setRun] = useState<{ busy: boolean; result: RunResponse | null; error: string | null }>({ busy: false, result: null, error: null });
  const [symbol, setSymbol] = useState("");
  const [alert, setAlert] = useState<{ busy: boolean; done: string | null; error: string | null }>({ busy: false, done: null, error: null });
  const [copied, setCopied] = useState(false);
  const symbols = parseSymbols(list);
  const one = parseSymbols(symbol, 1)[0] ?? "";

  const doRun = async () => {
    setRun({ busy: true, result: null, error: null });
    try { setRun({ busy: false, result: await screenerApi.run(source, card.screen.base_tf, symbols), error: null }); }
    catch (e) { setRun({ busy: false, result: null, error: why(e) }); }
  };
  const doAlert = async () => {
    setAlert({ busy: true, done: null, error: null });
    try {
      await api.createAlertRule({ name: card.name, kind: "instrument", symbol: one, condition: source, base_tf: card.screen.base_tf, priority: "normal",
                                  cooldown_minutes: 60, mode: "instant", digest_every: "hourly" });
      setAlert({ busy: false, done: `Alert rule created for ${one} (bar close, ${card.screen.base_tf}).`, error: null });
    } catch (e) { setAlert({ busy: false, done: null, error: why(e) }); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(source); setCopied(true); } catch { setCopied(false); }
  };

  return (
    <section className="mt-4 space-y-3 border-t border-border pt-3" aria-label="Use this screen" data-testid="legend-actions">
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (symbols.length) void doRun(); }}>
        <label className="flex min-w-[14rem] flex-1 flex-col text-t12 text-fg-muted">
          Run on my list (up to 50 symbols)
          <input value={list} onChange={(e) => setList(e.target.value)} placeholder="RELIANCE, TCS, INFY"
                 className="mt-1 h-8 rounded-control border border-border bg-surface-inset px-2 text-t13 text-fg" />
        </label>
        <Button size="sm" type="submit" disabled={!actions.run || !symbols.length || run.busy}>{run.busy ? "Running…" : `Run on ${symbols.length || "my"} symbols`}</Button>
        <Button size="sm" variant="ghost" type="button" onClick={copy}>{copied ? "Copied" : "Copy ScreenQL"}</Button>
      </form>
      {run.error && <p role="alert" className="text-t12 text-warn">{run.error}</p>}
      {run.result && (
        <p role="status" className="text-t12 text-fg" data-testid="legend-run-result">
          {run.result.matched.length ? `Matched: ${run.result.matched.join(", ")}` : "No symbol matched"} · {run.result.scanned} scanned · {run.result.disclaimer}
        </p>
      )}
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (one && actions.alert) void doAlert(); }}>
        <label className="flex flex-col text-t12 text-fg-muted">
          Alert me on one symbol (bar close)
          <input value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={!actions.alert} placeholder="RELIANCE"
                 className="mt-1 h-8 w-40 rounded-control border border-border bg-surface-inset px-2 text-t13 text-fg disabled:opacity-60" />
        </label>
        <Button size="sm" type="submit" disabled={!actions.alert || !one || alert.busy}
                title={actions.alert_reason ?? undefined}>{alert.busy ? "Creating…" : "Create alert rule"}</Button>
      </form>
      {!actions.alert && actions.alert_reason && <p className="text-t12 text-fg-muted" data-testid="legend-alert-reason">{actions.alert_reason}</p>}
      {alert.error && <p role="alert" className="text-t12 text-warn">{alert.error}</p>}
      {alert.done && <p role="status" className="text-t12 text-signal">{alert.done}</p>}
    </section>
  );
}

export default LegendActions;
