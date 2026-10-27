import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { RuinCalc, WhatIfResult, WhatIfSubject } from "../types";
import { chartGeometry, pnl, ruinBody, ruinPrefill, type RuinInputs } from "./decision";
import ExposureSection from "./ExposureSection";
import { pct, probability } from "./evidence";
import { useProgressionT } from "./i18n";
import { Details, TextOnDemand } from "../ui/numbersFirst";
import { useUiV2 } from "../ui/useFeatures";

/**
 * N2-3 (flag `decision_tools`): the risk-of-ruin / Kelly calculator, prefilled from the user's own figures (N2-1, when
 * the ladder sends them), and the what-if for the user's own open positions. Hidden while the flag is off (the API
 * answers 404). Information only: no control here sizes, places or closes a trade.
 */
export default function DecisionToolsCard() {
  const t = useProgressionT();
  const v2 = useUiV2();                       // UX-11: the intro and method notes under Details
  const [subjects, setSubjects] = useState<WhatIfSubject[] | null>(null);
  const [inputs, setInputs] = useState<RuinInputs | null>(null);
  const [prefilled, setPrefilled] = useState(false);
  const [calc, setCalc] = useState<RuinCalc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.decisionPositions().then((r) => setSubjects(r.subjects)).catch(() => setSubjects(null));
    api.paperLadder().then((l) => l.evidence).catch(() => undefined).then((ev) => {
      const p = ruinPrefill(ev);
      setInputs(p.inputs); setPrefilled(p.prefilled);
    });
  }, []);
  if (subjects === null || inputs === null) return null;

  const body = ruinBody(inputs);
  async function run() {
    if (!body) return;
    setError(null);
    try { setCalc(await api.decisionRuin(body)); } catch (e) { setError(String(e)); }
  }
  const field = (key: keyof RuinInputs, label: string) => (
    <label className="flex flex-col gap-1 text-fg-muted">
      {label}
      <input type="number" inputMode="decimal" value={inputs[key]} onChange={(e) => setInputs({ ...inputs, [key]: e.target.value })}
             className="min-h-[44px] w-full rounded border border-border bg-surface-2 px-2 py-1 text-fg sm:min-h-0" />
    </label>
  );

  return (
    <Card title={t("decision.title")} className="space-y-4">
      <div className="space-y-4 text-xs" data-testid="decision-tools">
        {!v2 && <p className="text-fg-muted">{t("decision.intro")}</p>}
        <section className="space-y-2" aria-label={t("decision.ruin.title")}>
          <h3 className="font-semibold text-fg">{t("decision.ruin.title")}</h3>
          {prefilled && !v2 && <p className="text-fg-muted">{t("decision.ruin.prefilled")}</p>}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {field("winRatePct", t("decision.ruin.winRate"))}
            {field("payoff", t("decision.ruin.payoff"))}
            {field("riskPct", t("decision.ruin.risk"))}
            {field("ruinPct", t("decision.ruin.ruinAt"))}
          </div>
          <button type="button" disabled={!body} onClick={() => void run()}
                  className="min-h-[44px] rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">
            {t("decision.ruin.run")}
          </button>
          {calc && <RuinResultView calc={calc} />}
        </section>
        <WhatIfSection subjects={subjects} />
        <ExposureSection />
        {error && <p role="status" className="text-down">{error}</p>}
        {/* UX-11 (v2 pages): what these tools are, under Details. */}
        {v2 && (
          <Details id="mytrades.decision-tools">
            <p>{t("decision.intro")}</p>
            {prefilled && <p>{t("decision.ruin.prefilled")}</p>}
          </Details>
        )}
      </div>
    </Card>
  );
}

export function RuinResultView({ calc }: { calc: RuinCalc }) {
  const t = useProgressionT();
  const v2 = useUiV2();
  return (
    <div className="space-y-2" data-testid="ruin-result">
      <p className="text-fg">{t("decision.ruin.expectancy", { r: calc.expectancy_r.toFixed(2) })}</p>
      <p className="text-fg">{t("decision.ruin.kelly", { pct: pct(calc.kelly.fraction), formula: calc.kelly.formula })}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left tabular-nums" aria-label={t("decision.ruin.tableLabel")}>
          <thead className="text-fg-muted">
            <tr><th className="py-1 pr-2 font-normal">{t("decision.ruin.colRisk")}</th><th className="py-1 pr-2 font-normal">{t("decision.ruin.colRuin")}</th>
                <th className="py-1 pr-2 font-normal">{t("decision.ruin.colLosses")}</th><th className="py-1 font-normal">{t("decision.ruin.colGrowth")}</th></tr>
          </thead>
          <tbody>
            {calc.rows.map((r) => (
              <tr key={r.risk_pct} className={r.chosen ? "bg-info/10 text-fg" : "text-fg"}>
                <td className="py-1 pr-2">{r.risk_pct}%{r.chosen ? ` (${t("decision.ruin.chosen")})` : ""}</td>
                <td className="py-1 pr-2">{r.ruin_status === "certain" ? t("decision.ruin.certain") : probability(r.ruin_probability)}</td>
                <td className="py-1 pr-2">{r.losses_to_ruin}</td>
                <td className={`py-1 ${r.growth_per_trade_pct != null && r.growth_per_trade_pct < 0 ? "text-down" : ""}`}>
                  {r.growth_per_trade_pct == null ? "-" : `${r.growth_per_trade_pct > 0 ? "+" : ""}${r.growth_per_trade_pct.toFixed(3)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TextOnDemand id="mytrades.decision-ruin" on={v2}><p className="text-xs text-fg-muted">{calc.note}.</p></TextOnDemand>
    </div>
  );
}

function WhatIfSection({ subjects }: { subjects: WhatIfSubject[] }) {
  const t = useProgressionT();
  const [underlying, setUnderlying] = useState(subjects[0]?.underlying ?? "");
  const [form, setForm] = useState({ spot: "", iv: "14", days: "0", shift: "0", range: "10" });
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const subject = subjects.find((s) => s.underlying === underlying);

  async function run() {
    if (!subject) return;
    setError(null);
    try {
      setResult(await api.decisionWhatIf({ underlying, spot: Number(form.spot), iv: subject.has_options ? Number(form.iv) / 100 : undefined,
                                           days_forward: Number(form.days), iv_shift: Number(form.shift) / 100, range_pct: Number(form.range) }));
    } catch (e) { setError(String(e)); }
  }
  const input = (key: keyof typeof form, label: string, hidden = false) => hidden ? null : (
    <label className="flex flex-col gap-1 text-fg-muted">
      {label}
      <input type="number" inputMode="decimal" value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })}
             className="min-h-[44px] w-full rounded border border-border bg-surface-2 px-2 py-1 text-fg sm:min-h-0" />
    </label>
  );
  return (
    <section className="space-y-2" aria-label={t("decision.whatIf.title")}>
      <h3 className="font-semibold text-fg">{t("decision.whatIf.title")}</h3>
      {!subjects.length ? <p className="text-fg-muted">{t("decision.whatIf.none")}</p> : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-fg-muted">
              {t("decision.whatIf.subject")}
              <select value={underlying} onChange={(e) => { setUnderlying(e.target.value); setResult(null); }}
                      className="min-h-[44px] rounded border border-border bg-surface-2 px-2 py-1 text-fg sm:min-h-0">
                {subjects.map((s) => <option key={s.underlying} value={s.underlying}>{s.underlying} · {t("decision.whatIf.legs", { count: s.legs.length })}</option>)}
              </select>
            </label>
            {input("spot", t("decision.whatIf.spot"))}
            {input("iv", t("decision.whatIf.iv"), !subject?.has_options)}
            {input("days", t("decision.whatIf.days"))}
            {input("shift", t("decision.whatIf.ivShift"), !subject?.has_options)}
            {input("range", t("decision.whatIf.range"))}
          </div>
          {subject?.skipped.map((s) => <p key={s.trade_id} className="text-warn">{t("decision.whatIf.skipped", { symbol: s.symbol, reason: s.reason })}</p>)}
          <button type="button" disabled={!subject || !(Number(form.spot) > 0)} onClick={() => void run()}
                  className="min-h-[44px] rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">
            {t("decision.whatIf.run")}
          </button>
          {error && <p role="status" className="text-down">{error}</p>}
          {result && <WhatIfView result={result} />}
        </>
      )}
    </section>
  );
}

const W = 600, H = 220;
const STROKE = { now: "stroke-info", scenario: "stroke-signal", expiry: "stroke-fg-muted" } as const;

export function WhatIfView({ result }: { result: WhatIfResult }) {
  const t = useProgressionT();
  const v2 = useUiV2();
  const g = chartGeometry(result, W, H);
  const shift = `${result.iv_shift >= 0 ? "+" : ""}${(result.iv_shift * 100).toFixed(1)}`;
  const label = { now: t("decision.whatIf.now"), scenario: t("decision.whatIf.scenario", { days: result.days_forward, shift }),
                  expiry: t("decision.whatIf.expiry", { date: result.expiry ?? "" }) };
  const s = result.at_spot;
  return (
    <div className="space-y-2" data-testid="whatif-result">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={t("decision.whatIf.chartLabel")}>
        {g.zeroY != null && <line x1={0} x2={W} y1={g.zeroY} y2={g.zeroY} className="stroke-fg-muted/60" strokeDasharray="2 3" />}
        <line x1={g.spotX} x2={g.spotX} y1={0} y2={H} className="stroke-info/50" />
        {g.paths.map((p) => (
          <path key={p.key} d={p.d} fill="none" className={STROKE[p.key]} strokeWidth={p.key === "scenario" ? 2 : 1.5}
                strokeDasharray={p.key === "expiry" ? "6 4" : undefined} />
        ))}
      </svg>
      <ul className="flex flex-wrap gap-3 text-fg-muted">
        {g.paths.map((p) => <li key={p.key} data-curve={p.key}>{label[p.key]}</li>)}
      </ul>
      <p className="text-fg tabular-nums">
        {t("decision.whatIf.atSpot", { spot: result.spot.toLocaleString("en-IN"), now: pnl(s.now), scenario: pnl(s.scenario) })}
        {s.expiry != null ? t("decision.whatIf.atSpotExpiry", { expiry: pnl(s.expiry) }) : ""}
      </p>
      <p className="text-fg-muted tabular-nums">{t("decision.whatIf.greeks", {
        delta: result.greeks.delta.toFixed(2), gamma: result.greeks.gamma.toFixed(4), theta: result.greeks.theta.toFixed(2), vega: result.greeks.vega.toFixed(2) })}</p>
      {result.skipped.map((x) => <p key={x.trade_id} className="text-warn">{t("decision.whatIf.skipped", { symbol: x.symbol, reason: x.reason })}</p>)}
      <TextOnDemand id="mytrades.decision-whatif" on={v2}><p className="text-xs text-fg-muted">{result.note}</p></TextOnDemand>
    </div>
  );
}
