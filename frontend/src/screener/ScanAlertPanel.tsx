/**
 * Handover 11j step 7 "Tell me when it matches" (flag `screener_copilot`, behind `ui_v2`): the trader's scan becomes an
 * ordinary screen alert rule (POST /api/alerts/rules, kind "screen") on the symbols in his Universe column. The
 * existing bar-close engine checks it on every closed bar (the default; "while the bar is forming" only with the
 * `screener_intrabar` flag) and the existing delivery sends it to the app and to the Telegram / e-mail he connected.
 * The scan is saved first, so the alert checks exactly what he sees. A rule only notifies: it never places, blocks or
 * changes an order.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { friendlyError } from "../api/errors";
import { Button, cx } from "../components/primitives";
import { Details } from "../ui/Details";
import { PageFootnote } from "../ui/PageFootnote";
import { scanAlertsApi, type ScanAlertInput, type ScanAlertRule } from "./api";

export interface ScanAlertApi {
  rules: () => Promise<ScanAlertRule[]>;
  create: (body: ScanAlertInput) => Promise<ScanAlertRule>;
  setStatus: (id: number, action: "pause" | "resume") => Promise<ScanAlertRule>;
}

export interface ScanAlertPanelProps {
  name: string;
  symbols: string[];
  scanTf: string;
  screenId?: number;
  /** Saves the scan as it is now and gives its id (null when it could not be saved; the page says why). */
  save: () => Promise<number | null>;
  intrabar?: boolean;
  /** Owner order 23 Oct (ui_v2): the universe the scan runs on, and how many stocks it holds (an alert watches up to 50). */
  universeName?: string;
  universeTotal?: number;
  api?: ScanAlertApi;
  initialRules?: ScanAlertRule[];
}

const TF_WORDS: Record<string, string> = {
  "1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour",
  "1d": "daily", "1w": "weekly", "1M": "monthly",
};
export const DONE = "Alert on. ATP tells you when a symbol matches this scan.";

/** "checks each time a daily bar closes" / "checks while each 15-minute bar is forming". */
export function whenLine(tf: string, fireOn: "bar_close" | "intrabar"): string {
  const bar = TF_WORDS[tf] ?? tf;
  return fireOn === "intrabar" ? `checks while each ${bar} bar is forming` : `checks each time a ${bar} bar closes`;
}

/** One line for an alert already on this scan. */
export function ruleLine(r: ScanAlertRule): string {
  const n = r.symbols?.length ?? 0;
  return `${r.status === "active" ? "On" : "Paused"} · ${whenLine(r.base_tf, r.fire_on ?? "bar_close")} · ${n} ${n === 1 ? "symbol" : "symbols"}`;
}

export default function ScanAlertPanel({ name, symbols, scanTf, screenId, save, intrabar = false, universeName, universeTotal, api = scanAlertsApi,
  initialRules }: ScanAlertPanelProps) {
  const [rules, setRules] = useState<ScanAlertRule[]>(initialRules ?? []);
  const [fireOn, setFireOn] = useState<"bar_close" | "intrabar">("bar_close");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [id, setId] = useState<number | undefined>(screenId);

  useEffect(() => {
    if (initialRules) return undefined;
    let alive = true;
    api.rules().then((all) => { if (alive) setRules(all); }).catch(() => undefined);   // no list is not an error here
    return () => { alive = false; };
  }, [api, initialRules]);

  const mine = rules.filter((r) => r.kind === "screen" && id != null && r.screen_id === id);

  const turnOn = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await save();
      if (saved == null) return;
      setId(saved);
      const rule = await api.create({ name: name.trim() || "My scan", screen_id: saved, symbols, fire_on: fireOn });
      setRules((all) => [rule, ...all.filter((r) => r.id !== rule.id)]);
      setDone(true);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (r: ScanAlertRule) => {
    api.setStatus(r.id, r.status === "active" ? "pause" : "resume")
      .then((next) => setRules((all) => all.map((x) => (x.id === next.id ? { ...x, ...next } : x))))
      .catch((e: unknown) => setError(friendlyError(e)));
  };

  return (
    <div className="space-y-4 text-t13" data-testid="scan-alert">
      {symbols.length === 0 ? (
        <p className="text-warn" data-testid="scan-alert-no-symbols">Add the symbols to watch in the Universe column first (up to 50).</p>
      ) : (
        <>
          <p className="text-fg">
            ATP {whenLine(scanTf, fireOn)} for “{name.trim() || "My scan"}” on {universeName ? <>{universeName} ({symbols.length} {symbols.length === 1 ? "stock" : "stocks"})</>
              : <>your {symbols.length} {symbols.length === 1 ? "symbol" : "symbols"}</>}, and tells you when one matches.
          </p>
          {universeName && universeTotal != null && universeTotal > symbols.length && (
            <p className="text-t12 text-fg-muted" data-testid="scan-alert-first">
              An alert watches up to {symbols.length} stocks, so this one watches the first {symbols.length} of the {universeTotal} in {universeName}.
            </p>
          )}
          <fieldset className="space-y-2">
            <legend className="text-t12 text-fg-muted">When to check</legend>
            <label className="flex items-start gap-2">
              <input type="radio" name="scan-alert-when" checked={fireOn === "bar_close"} onChange={() => setFireOn("bar_close")} className="mt-0.5 accent-[rgb(var(--signal))]" />
              <span><span className="text-fg">When the bar closes</span> <span className="text-t12 text-fg-muted">(recommended: the bar is final)</span></span>
            </label>
            {intrabar && (
              <label className="flex items-start gap-2">
                <input type="radio" name="scan-alert-when" checked={fireOn === "intrabar"} onChange={() => setFireOn("intrabar")} className="mt-0.5 accent-[rgb(var(--signal))]" />
                <span><span className="text-fg">While the bar is forming</span> <span className="text-t12 text-fg-muted">(earlier, but it can change before the close)</span></span>
              </label>
            )}
          </fieldset>
          <Details id="screener.alert.how" label="How you are told" className="mt-0">
            <p>
              In the app, and on Telegram or e-mail if you connected them in{" "}
              <Link to="/settings/alerts" className="underline">Settings › Alerts</Link>. Your scan is saved first, so the alert checks exactly what you see.
            </p>
          </Details>
          {done ? (
            <p role="status" className="rounded-panel border border-border bg-surface-2 p-3 text-fg" data-testid="scan-alert-done">{DONE}</p>
          ) : (
            <Button variant="primary" onClick={() => void turnOn()} loading={busy} data-testid="scan-alert-create">Turn on the alert</Button>
          )}
        </>
      )}
      {error && <p role="alert" className="text-t12 text-warn">{error}</p>}

      {mine.length > 0 && (
        <div className="space-y-2" data-testid="scan-alert-rules">
          <p className="text-t12 font-semibold text-fg">Alerts on this scan</p>
          <ul className="space-y-2">
            {mine.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-panel border border-border bg-surface-1 p-2">
                <span className={cx("text-t12", r.status === "active" ? "text-fg" : "text-fg-muted")}>{ruleLine(r)}</span>
                <Button size="sm" onClick={() => toggle(r)}>{r.status === "active" ? "Pause" : "Turn back on"}</Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {/* SCR-FIX E1: the dialog's one "not advice" line, with its own short words */}
      <PageFootnote>An alert only tells you; it never places or changes an order.</PageFootnote>
    </div>
  );
}
