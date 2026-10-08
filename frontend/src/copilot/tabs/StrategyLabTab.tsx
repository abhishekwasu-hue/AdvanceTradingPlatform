import { FlaskConical, Gauge, Layers, MessageSquareText } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { DataSourceBar, useCandleSource } from "../../components/DataSource";
import { Badge, Button, Input, Select } from "../../components/primitives";
import type { OHLCVBar, Regime, StrategistRequestParsed, StrategistResult } from "../../types";
import { useAiTask } from "../aiTask";
import AiProgress from "../components/AiProgress";
import CostChip from "../components/CostChip";
import PrivacyNote from "../components/PrivacyNote";
import TiltCard from "../components/TiltCard";
import { useCostEstimate } from "../cost";
import { useCopilotT } from "../i18n";
import CandidateCard from "./lab/CandidateCard";
import DraftsPanel from "./lab/DraftsPanel";
import StudyView from "./lab/StudyView";
import { Panel, Stagger, StaggerItem } from "./shared";

/**
 * Strategy Lab: plain-language idea -> white-box rules (in words, DSL on request) -> an automatic server-side backtest
 * on tuning sessions and on unseen ones -> save with the risk acceptance -> PAPER. Below, the AI drafts (edit,
 * re-test, approve). The Copilot never places an order; every step that changes anything is a button the trader presses.
 */
const SYMBOLS = ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "RELIANCE", "HDFCBANK", "INFY"];

/** Sample one-minute bars arranged into NSE sessions (09:15-15:29 IST, weekdays) so the study sees real-looking days. */
export function sessionize(bars: OHLCVBar[], now = new Date()): OHLCVBar[] {
  const perDay = 375;
  const days: Date[] = [];
  const d = new Date(now);
  d.setUTCHours(0, 0, 0, 0);
  while (days.length * perDay < bars.length) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) days.unshift(new Date(d));
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return bars.map((b, i) => {
    const day = days[Math.floor(i / perDay)];
    return { ...b, timestamp: new Date(day.getTime() + (3 * 60 + 45 + (i % perDay)) * 60_000).toISOString() };
  });
}

/** The regime classifier a deployment's regime filter uses, on the chosen symbol's 5-minute candles (sample or broker). */
function RegimeCheck({ source, symbol }: { source: ReturnType<typeof useCandleSource>; symbol: string }) {
  const t = useCopilotT();
  const task = useAiTask(t, 60_000);
  const [regime, setRegime] = useState<Regime | null>(null);
  async function run() {
    const sym = symbol.trim().toUpperCase();
    const r = await task.run([t("lab.regime.step")], async (signal) => {
      const data = await source.fetch([sym], "5min", { count: 600, startPriceFor: () => 100, seedFor: () => 11 });
      const candles = data.candles[sym];
      if (!candles?.length) throw new Error(t("lab.drafts.noCandles", { symbol: sym }));
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      return api.aiRegime(candles);
    });
    if (r) setRegime(r);
  }
  return (
    <Panel title={t("lab.regime.title")} icon={<Gauge size={15} />} testId="lab-regime"
           action={<Button size="sm" disabled={task.busy || !symbol.trim()} onClick={() => void run()}>{t("lab.regime.run", { symbol: symbol.trim().toUpperCase() })}</Button>}>
      <p className="text-xs text-fg-muted">{t("lab.regime.intro")}</p>
      <div className="mt-2"><AiProgress state={task.state} onCancel={task.cancel} /></div>
      {regime && (
        <div className="mt-2 text-sm">
          <div className="font-semibold text-fg">{t("lab.regime.result", { kind: regime.kind.replace(/_/g, " ").toLowerCase(), confidence: regime.confidence })}</div>
          <ul className="mt-1 list-disc pl-4 text-xs text-fg-muted">{regime.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
    </Panel>
  );
}

export default function StrategyLabTab() {
  const t = useCopilotT();
  const source = useCandleSource(30);
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [style, setStyle] = useState<"intraday" | "scalping">("intraday");
  const [direction, setDirection] = useState<"long" | "short" | "both">("both");
  const [idea, setIdea] = useState("");
  const [parsed, setParsed] = useState<StrategistRequestParsed | null>(null);
  const [result, setResult] = useState<StrategistResult | null>(null);
  const task = useAiTask(t, 120_000);
  const estimate = useCostEstimate("strategist");

  // The idea in words fills the fields (the trader can still correct them by hand before running).
  useEffect(() => {
    const text = idea.trim();
    if (!text) { setParsed(null); return; }
    let live = true;
    const h = setTimeout(() => {
      api.aiStrategistParse(text, symbol, "en").then((p) => {
        if (!live) return;
        setParsed(p);
        if (p.matched.symbol) setSymbol(p.symbol);
        if (p.matched.style) setStyle(p.style);
        if (p.matched.direction) setDirection(p.direction === "auto" ? "both" : p.direction);
      }).catch(() => { if (live) setParsed(null); });
    }, 400);
    return () => { live = false; clearTimeout(h); };
  }, [idea]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    const sym = symbol.trim().toUpperCase();
    const out = await task.run([t("lab.step.read"), t("lab.step.write"), t("lab.step.tune"), t("lab.step.unseen")], async (signal) => {
      let candles: OHLCVBar[] | undefined;
      if (source.mode === "sample") {
        const r = await source.fetch([sym], "1min", { count: 375 * 12, startPriceFor: () => (sym.includes("BANK") ? 52_000 : sym.includes("NIFTY") ? 24_500 : 1_500), seedFor: () => 7 });
        candles = sessionize(r.candles[sym] ?? []);
      }
      return api.aiStrategistBuild({ symbol: sym, candles, broker: source.mode === "broker" ? source.broker || undefined : undefined, style, direction, language: "en" }, signal);
    });
    if (out) setResult(out);
  }

  return (
    <div className="space-y-4" data-testid="tab-panel-strategy-lab">
      <DataSourceBar source={source} />
      <TiltCard tilt={false} className="space-y-3" data-testid="lab-idea">
        <div className="flex items-center gap-2">
          <span className="rounded-lg bg-ai/15 p-1.5 text-ai"><FlaskConical size={18} /></span>
          <div>
            <h2 className="text-base font-semibold text-fg">{t("lab.title")}</h2>
            <p className="text-xs text-fg-muted">{t("lab.intro")}</p>
          </div>
        </div>
        <div>
          <Input label={t("lab.ideaLabel")} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder={t("lab.ideaPlaceholder")} />
          {parsed && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px]">
              <MessageSquareText size={12} className="text-ai" /><span className="text-fg-muted">{parsed.summary}</span>
              {(["symbol", "style", "direction"] as const).map((k) => parsed.matched[k] && <Badge key={k} tone="brand">{parsed.matched[k]}</Badge>)}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-44"><Input label={t("lab.symbol")} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></div>
          <div className="flex flex-wrap gap-1 pb-1">
            {SYMBOLS.map((s) => (
              <button key={s} onClick={() => setSymbol(s)} aria-pressed={symbol === s}
                      className={`rounded-full border px-2 py-0.5 text-[11px] ${symbol === s ? "border-ai/60 bg-ai/15 text-fg" : "border-border text-fg-muted hover:text-fg"}`}>{s}</button>
            ))}
          </div>
          <div className="w-64"><Select label={t("lab.style")} value={style} onChange={(v) => setStyle(v as typeof style)}
            options={[{ value: "intraday", label: t("lab.styleIntraday") }, { value: "scalping", label: t("lab.styleScalping") }]} /></div>
          <div className="w-40"><Select label={t("lab.side")} value={direction} onChange={(v) => setDirection(v as typeof direction)}
            options={[{ value: "long", label: t("lab.direction.LONG") }, { value: "short", label: t("lab.direction.SHORT") }, { value: "both", label: t("lab.direction.BOTH") }]} /></div>
          <Button variant="primary" icon={<Layers size={15} />} disabled={task.busy || !symbol.trim()} onClick={() => void run()} data-testid="lab-run">{t("lab.run")}</Button>
          {estimate && <CostChip tokens={estimate.tokens} inr={estimate.inr} estimate />}
        </div>
        {source.mode === "sample" && <p className="text-[11px] text-warn">{t("lab.sampleNote")}</p>}
        <AiProgress state={task.state} onCancel={task.cancel} onRetry={() => void run()} />
        <PrivacyNote compact />
      </TiltCard>

      {result && (
        <>
          <StudyView s={result.study} source={result.data_source} />
          <Panel title={t("lab.templates", { n: result.tested, ai: result.ai_candidates })} icon={<Layers size={15} />}>
            <ul className="mb-3 space-y-0.5 text-xs text-fg-muted">{result.notes.map((n) => <li key={n}>· {n}</li>)}</ul>
            <Stagger className="space-y-3">
              {result.candidates.map((c) => (
                <StaggerItem key={c.id + c.direction}><CandidateCard c={c} symbol={result.study.symbol} sample={!result.data_source.startsWith("broker")} /></StaggerItem>
              ))}
            </Stagger>
            {result.candidates.length === 0 && <p className="text-sm text-fg-muted">{t("lab.noTrades")}</p>}
            <p className="mt-3 text-[11px] text-fg-muted">{t("lab.method")}</p>
          </Panel>
        </>
      )}

      <DraftsPanel source={source} symbol={symbol} />
      <RegimeCheck source={source} symbol={symbol} />
    </div>
  );
}
