import { ArrowDownRight, ArrowRight, ArrowUpRight, Eye, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../../api/client";
import { Badge, Button, fieldClass } from "../../../components/primitives";
import type { MarketThesis, ThesisHistory } from "../../../types";
import { friendlyError } from "../../aiTask";
import { useCopilotT } from "../../i18n";
import { Panel } from "../shared";

/**
 * The market thesis of one watched symbol, in neutral words (P0.8): "the data reads ..." with the factor agreement,
 * index scenarios, and the SHADOW size multiplier - recorded and scored, never applied. A stale read hides its levels.
 */
const DIR_ICON = { BULLISH: ArrowUpRight, BEARISH: ArrowDownRight, NEUTRAL: ArrowRight } as const;

/** NSE cash session 09:15-15:30 IST on a weekday (holidays are known only server-side). */
function marketOpenNow(): boolean {
  const ist = new Date(Date.now() + 330 * 60_000);
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  return ist.getUTCDay() >= 1 && ist.getUTCDay() <= 5 && minutes >= 9 * 60 + 15 && minutes < 15 * 60 + 30;
}

export default function ThesisPanel() {
  const t = useCopilotT();
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [thesis, setThesis] = useState<MarketThesis | null>(null);
  const [history, setHistory] = useState<ThesisHistory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [off, setOff] = useState(false);

  const latest = useRef(0);
  async function load(refresh = false) {
    const id = ++latest.current;               // switching symbols quickly: only the newest answer is shown
    setBusy(true); setError(null);
    try {
      const th = await api.aiThesis(symbol, "en", refresh);
      if (id !== latest.current) return;
      setThesis(th); setOff(false);
      const hist = await api.aiThesisHistory(symbol);
      if (id !== latest.current) return;
      setHistory(hist);
    } catch (e) {
      if (id !== latest.current) return;
      if (e instanceof ApiError && e.status === 503 && e.body.includes("market_thesis")) { setOff(true); setThesis(null); }
      else { setError(friendlyError(e, t)); setThesis(null); }
    } finally { if (id === latest.current) setBusy(false); }
  }
  useEffect(() => { void load(); }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api.aiMarketMemory().then((m) => {
      const list = m.watchlist ?? [];
      setWatchlist(list);
      if (list.length && !list.includes(symbol)) setSymbol(list[0]);
    }).catch(() => undefined);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const Icon = thesis ? DIR_ICON[thesis.direction] ?? Eye : Eye;
  const dirTone = thesis?.direction === "BULLISH" ? "up" : thesis?.direction === "BEARISH" ? "down" : "neutral";
  const readAt = thesis ? (thesis.inputs as { read_at?: string | null }).read_at ?? null : null;
  const ageH = readAt ? (Date.now() - new Date(readAt).getTime()) / 3_600_000 : null;
  const stale = ageH != null && ageH > (marketOpenNow() ? 0.75 : 20);
  const sb = history?.scoreboard;

  return (
    <Panel title={t("thesis.title")} icon={<Eye size={15} />} testId="pulse-thesis"
           action={(
             <>
               {/* a native select: the default tab stays light (the Radix Select is ~17 KB) */}
               <select aria-label={t("thesis.symbol")} className={`${fieldClass} w-40`} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                 {(watchlist.length ? watchlist : [symbol]).map((s) => <option key={s} value={s}>{s}</option>)}
               </select>
               <Button size="sm" variant="ghost" icon={<RefreshCw size={13} className={busy ? "animate-spin" : ""} />} disabled={busy} onClick={() => void load(true)}>{t("thesis.rebuild")}</Button>
             </>
           )}>
      <p className="mb-2 text-xs text-fg-muted">{t("thesis.intro")}</p>
      {off && <p className="text-sm text-fg-muted">{t("thesis.off")}</p>}
      {error && <p className="text-sm text-down" role="alert">{error}</p>}
      {thesis && (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={dirTone}><Icon size={13} className="mr-1 inline" />{t("thesis.reads", { symbol: thesis.symbol, direction: t(`thesis.dir.${thesis.direction}`) })}</Badge>
            {stale && <Badge tone="warn" className="font-bold uppercase">{t("thesis.stale", { hours: Math.round(ageH ?? 0) })}</Badge>}
            {thesis.confidence != null && !stale && <span className="text-xs text-fg" title={t("thesis.scoreHint")}>{t("thesis.score", { score: thesis.confidence })}</span>}
            <span className="text-xs text-fg-muted">{t("thesis.agree", { agreeing: thesis.agreement.agreeing, total: thesis.agreement.with_opinion, coverage: Math.round(thesis.agreement.coverage * 100) })}</span>
            <span className="ml-auto rounded-md border border-border px-2 py-0.5 text-xs text-fg-muted" title={thesis.shadow.reasons.join("; ") || t("thesis.noReduction")}>
              {t("thesis.shadow", { mult: thesis.shadow.size_multiplier.toFixed(2) })} <span className="text-warn">{t("thesis.notApplied")}</span>
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {thesis.agreement.matrix.map((r) => (
              <span key={r.factor} className={`rounded-md border px-1.5 py-0.5 ${!r.available ? "border-border text-fg-muted line-through" : r.direction > 0 ? "border-up/40 text-up" : r.direction < 0 ? "border-down/40 text-down" : "border-border text-fg"}`}>
                {t(`thesis.factor.${r.factor}`, { defaultValue: r.factor })} {!r.available ? "?" : r.direction > 0 ? "↑" : r.direction < 0 ? "↓" : "→"} <span className="text-fg-muted">×{r.weight.toFixed(2)}</span>
              </span>
            ))}
          </div>
          {thesis.detail_shown === false && <p className="text-xs text-fg-muted">{t("thesis.noScenarios")}</p>}
          {stale ? <p className="text-xs text-warn">{t("thesis.staleHint", { hours: Math.round(ageH ?? 0) })}</p> : (
            <div className="grid gap-2 md:grid-cols-3">
              {(["bull", "base", "bear"] as const).map((k) => thesis.scenarios[k] && (
                <div key={k} className={`rounded-xl border p-2.5 text-xs ${k === "bull" ? "border-up/30 bg-up/5" : k === "bear" ? "border-down/30 bg-down/5" : "border-border bg-surface-2/50"}`}>
                  <div className="font-semibold uppercase text-fg">{t(`thesis.scenario.${k}`)}</div>
                  <div className="mt-0.5 text-fg-muted">{thesis.scenarios[k]?.text}</div>
                </div>
              ))}
            </div>
          )}
          <details className="text-xs text-fg-muted">
            <summary className="cursor-pointer hover:text-fg">{t("thesis.inWords")}{thesis.narrative_source === "model" ? ` ${t("thesis.aiChecked")}` : ""}</summary>
            <ul className="mt-1 list-disc pl-4">{thesis.lines.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
          {sb && sb.scored > 0 && (
            <p className="text-xs text-fg-muted">
              {t("thesis.scoreboard", { scored: sb.scored, hits: sb.hits, misses: sb.misses, flat: sb.flat })}
              {sb.hit_rate != null && <> · <b className="text-fg">{t("thesis.hitRate", { pct: Math.round(sb.hit_rate * 100) })}</b></>}
            </p>
          )}
          <p className="text-[11px] text-fg-muted">{t("thesis.footer")}</p>
        </div>
      )}
    </Panel>
  );
}
