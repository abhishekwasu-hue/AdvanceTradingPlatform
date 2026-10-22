import { useState } from "react";
import { api } from "../api/client";
import type { ExposureView } from "../types";
import { rupees0, strongestPairs } from "./exposure";
import { useProgressionT } from "./i18n";

/**
 * N2-4 (flag `decision_tools`): the caller's own open positions by underlying and sector, beta to the NIFTY 50 and the
 * pairs whose daily returns moved together. Loaded on demand (it reads the daily lake for every underlying).
 */
export default function ExposureSection() {
  const t = useProgressionT();
  const [view, setView] = useState<ExposureView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setBusy(true); setError(null);
    try { setView(await api.decisionExposure()); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return (
    <section className="space-y-2" aria-label={t("decision.exposure.title")}>
      <h3 className="font-semibold text-fg">{t("decision.exposure.title")}</h3>
      <p className="text-fg-muted">{t("decision.exposure.intro")}</p>
      <button type="button" disabled={busy} onClick={() => void load()}
              className="min-h-[44px] rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">
        {t("decision.exposure.load")}
      </button>
      {error && <p role="status" className="text-down">{error}</p>}
      {view && <ExposureViewPanel view={view} />}
    </section>
  );
}

export function ExposureViewPanel({ view }: { view: ExposureView }) {
  const t = useProgressionT();
  if (!view.underlyings.length) return <p className="text-fg-muted">{t("decision.exposure.none")}</p>;
  const pairs = strongestPairs(view);
  return (
    <div className="space-y-2" data-testid="exposure-view">
      <p className="text-fg">{t("decision.exposure.totals", { count: view.totals.underlyings, legs: view.totals.legs, risk: rupees0(view.totals.risk_at_stop) })}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left tabular-nums" aria-label={t("decision.exposure.tableLabel")}>
          <thead className="text-fg-muted">
            <tr>
              <th className="py-1 pr-2 font-normal">{t("decision.exposure.colUnderlying")}</th>
              <th className="py-1 pr-2 font-normal">{t("decision.exposure.colSector")}</th>
              <th className="py-1 pr-2 font-normal">{t("decision.exposure.colLegs")}</th>
              <th className="py-1 pr-2 font-normal">{t("decision.exposure.colNotional")}</th>
              <th className="py-1 pr-2 font-normal">{t("decision.exposure.colPremium")}</th>
              <th className="py-1 pr-2 font-normal">{t("decision.exposure.colRisk")}</th>
              <th className="py-1 font-normal">{t("decision.exposure.colBeta", { market: view.market })}</th>
            </tr>
          </thead>
          <tbody>
            {view.underlyings.map((u) => (
              <tr key={u.underlying} className="text-fg">
                <td className="py-1 pr-2">{u.underlying}</td>
                <td className="py-1 pr-2">{u.sector}</td>
                <td className="py-1 pr-2">{u.legs}</td>
                <td className="py-1 pr-2">{rupees0(u.notional)}</td>
                <td className="py-1 pr-2">{rupees0(u.option_premium)}</td>
                <td className="py-1 pr-2">
                  {rupees0(u.risk_at_stop)}
                  {u.legs_without_stop.length > 0 && <span className="ml-1 text-warn">({t("decision.exposure.noStop", { count: u.legs_without_stop.length })})</span>}
                </td>
                <td className="py-1">{u.beta == null ? t("decision.exposure.noBeta") : u.beta.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="space-y-0.5 text-fg-muted" aria-label={t("decision.exposure.sectorsLabel")}>
        {view.sectors.map((s) => <li key={s.sector}>{t("decision.exposure.sectorRow", { sector: s.sector, count: s.underlyings, risk: rupees0(s.risk_at_stop) })}</li>)}
      </ul>
      {pairs.length ? (
        <div>
          <p className="text-fg">{t("decision.exposure.together", { threshold: view.move_together_threshold, days: view.lookback_days })}</p>
          <ul className="space-y-0.5 text-fg">
            {pairs.map((p) => <li key={`${p.a}|${p.b}`}>{t("decision.exposure.pair", { a: p.a, b: p.b, r: (p.r ?? 0).toFixed(2), days: p.days })}</li>)}
          </ul>
        </div>
      ) : <p className="text-fg-muted">{t("decision.exposure.togetherNone", { threshold: view.move_together_threshold })}</p>}
      {!!view.not_computed?.length && (
        <p className="text-warn">{t("decision.exposure.notComputed", { max: view.max_computed ?? 0, list: view.not_computed.join(", ") })}</p>
      )}
      <p className="text-xs text-fg-muted">{view.note}</p>
    </div>
  );
}
