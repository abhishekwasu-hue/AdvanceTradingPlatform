/**
 * N1 F3 (flag `portfolio_risk`): the Positions page's portfolio risk card - the user's own PAPER book: exposure, open
 * risk at the stops by bucket (all indices in one), NIFTY 50 beta-weighted exposure, the one-sided warning, and the
 * user's own limits on new entries (F2, when on). Numbers for the person's own decision - never advice, and nothing here
 * places or closes an order. Every string comes from the `portfolioRisk` i18n namespace.
 */
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { Card, StatTile } from "../components/ui";
import { usePortfolioT } from "./i18n";
import { Details, Signed } from "../ui/numbersFirst";
import { useUiV2 } from "../ui/useFeatures";
import { bucketLabel, isRiskView, limitLines, money, overLimit, pct, signed, type PortfolioRiskView } from "./model";

function Sub({ main, sub }: { main: ReactNode; sub: string }) {
  return <><span>{main}</span><span className="block text-xs font-normal text-fg-muted">{sub}</span></>;
}

export function PortfolioRiskView({ view }: { view: PortfolioRiskView }) {
  const t = usePortfolioT();
  const v2 = useUiV2();                       // UX-11: numbers first; the as-of line, notes and footer under Details
  const limits = limitLines(view.limits, t);
  const notes = [
    view.beta.unknown.length ? t("notes.betaUnknown", { names: view.beta.unknown.join(", ") }) : "",
    view.beta.not_weighted.length ? t("notes.notWeighted", { names: view.beta.not_weighted.join(", ") }) : "",
    view.priced_at_entry.length ? t("notes.atEntry", { names: view.priced_at_entry.join(", ") }) : "",
  ].filter(Boolean);
  return (
    <div className="space-y-3" data-testid="portfolio-risk">
      {!v2 && <p className="text-xs text-fg-muted">{t("asOf", { day: view.as_of, capital: money(view.capital), source: view.capital_source })}</p>}
      {view.positions === 0 ? <p className="text-sm text-fg-muted">{t("empty")}</p> : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatTile label={t("tiles.exposure")} value={<Sub main={money(view.exposure.gross)} sub={t("ofCapital", { pct: pct(view.exposure.gross_pct) })} />} />
            <StatTile label={t("tiles.net")} value={<Sub main={v2 ? <Signed value={view.exposure.net} kind="count" className="text-t20 sm:text-2xl" /> : signed(view.exposure.net)} sub={t("ofCapital", { pct: pct(view.exposure.net_pct) })} />} />
            <StatTile label={t("tiles.openRisk")} value={<Sub main={money(view.open_risk.total)} sub={t("ofCapital", { pct: pct(view.open_risk.pct) })} />} />
            <StatTile label={t("tiles.beta")} value={<Sub main={v2 ? <Signed value={view.beta.net} kind="count" className="text-t20 sm:text-2xl" /> : signed(view.beta.net)} sub={t("ofCapital", { pct: pct(view.beta.net_pct) })} />} />
          </div>
          {view.direction.warning && (
            <p role="note" className="text-xs text-warn" data-testid="one-sided"><strong>{t("notes.oneSided")}:</strong> {view.direction.warning}</p>
          )}
          <div className="overflow-x-auto">
            <div className="text-xs font-medium text-fg">{t("buckets.title")}</div>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-fg-muted"><th className="py-1">{t("buckets.bucket")}</th><th>{t("buckets.positions")}</th>
                <th>{t("buckets.exposure")}</th><th>{t("buckets.risk")}</th></tr></thead>
              <tbody>
                {view.buckets.map((row) => {
                  const over = overLimit(row, view.limits);
                  return (
                    <tr key={row.bucket} className="border-t border-border align-top" data-bucket={row.bucket}>
                      <td className="py-1"><div className="font-medium">{bucketLabel(row.bucket, t)}</div><div className="text-fg-muted">{row.symbols.join(", ")}</div></td>
                      <td className="font-tabular">{row.positions}</td>
                      <td className={`font-tabular ${over.sector ? "text-warn" : ""}`} data-over={over.sector ? "sector" : undefined}>
                        {money(row.gross_notional)} ({pct(row.gross_pct)}){over.sector ? ` · ${t("limits.over")}` : ""}
                      </td>
                      <td className={`font-tabular ${over.risk ? "text-warn" : ""}`} data-over={over.risk ? "risk" : undefined}>
                        {money(row.open_risk)} ({pct(row.risk_pct)}){over.risk ? ` · ${t("limits.over")}` : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {limits.length > 0 && (
        <div className="space-y-0.5 text-xs">
          <div className="font-medium text-fg">{t("limits.title")}</div>
          {limits.map((line) => <div key={line} className="text-fg-muted">{line}</div>)}
        </div>
      )}
      {v2 ? (
        <Details id="mytrades.portfolio-risk">
          <p>{t("asOf", { day: view.as_of, capital: money(view.capital), source: view.capital_source })}</p>
          {notes.map((n) => <p key={n}>{n}</p>)}
          <p>{t("footer")}</p>
        </Details>
      ) : (
        <>
          {notes.map((n) => <p key={n} className="text-xs text-fg-muted">{n}</p>)}
          <p className="text-xs text-fg-muted">{t("footer")}</p>
        </>
      )}
    </div>
  );
}

/** The card: loads GET /api/portfolio/risk (render it only while `portfolio_risk` is on). */
export default function PortfolioRiskCard() {
  const t = usePortfolioT();
  const [view, setView] = useState<PortfolioRiskView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.portfolioRisk().then((v) => (isRiskView(v) ? setView(v) : setError("unexpected answer"))).catch((e) => setError(String(e)));
  }, []);
  return (
    <Card title={t("title")}>
      {error ? <p role="alert" className="text-xs text-down">{t("failed", { error })}</p>
        : view ? <PortfolioRiskView view={view} /> : <p className="text-xs text-fg-muted">{t("loading")}</p>}
    </Card>
  );
}
