import { TrendingDown, TrendingUp, Waves, Zap } from "lucide-react";
import { Badge } from "../../../components/primitives";
import type { MarketStudy } from "../../../types";
import TiltCard from "../../components/TiltCard";
import { useCopilotT } from "../../i18n";
import { num } from "../shared";

/** The market study the templates were tested on: the read, the trend by timeframe and today's levels. */
export default function StudyView({ s, source }: { s: MarketStudy; source: string }) {
  const t = useCopilotT();
  const sample = !source.startsWith("broker");
  const CharIcon = s.character === "RANGE" ? Waves : s.character === "VOLATILE" ? Zap : s.bias === "BEARISH" ? TrendingDown : TrendingUp;
  const biasTone = s.bias === "BULLISH" ? "text-up" : s.bias === "BEARISH" ? "text-down" : "text-fg";
  const pct = Math.round(((s.bias_score + 1) / 2) * 100);
  const rows = [...s.ladder];
  const at = rows.findIndex((r) => r.price < s.last_price);
  rows.splice(at === -1 ? rows.length : at, 0, { name: t("lab.study.last"), key: "_last", price: s.last_price, distance_pct: 0 });
  return (
    <div className="grid gap-4 lg:grid-cols-3" data-testid="lab-study">
      <TiltCard>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("lab.study.title", { symbol: s.symbol })}</div>
        <div className="mt-1 flex items-baseline justify-between gap-2">
          <div className="font-tabular text-2xl font-bold text-fg">{num(s.last_price)}{sample && <Badge tone="warn" className="ml-2 align-middle">{t("lab.study.samplePrice")}</Badge>}</div>
          <div className={`text-right text-sm font-semibold ${biasTone}`}>{t("lab.study.reads", { bias: t(`thesis.dir.${s.bias}`) })}</div>
        </div>
        {s.confidence != null && <div className="text-[11px] text-fg-muted">{t("lab.study.score", { score: s.confidence })}</div>}
        <div className="my-3">
          <div className="mb-1 flex justify-between text-[11px] text-fg-muted"><span>{t("thesis.dir.BEARISH")}</span><span>{t("thesis.dir.NEUTRAL")}</span><span>{t("thesis.dir.BULLISH")}</span></div>
          <div className="relative h-2 rounded-full" style={{ background: "linear-gradient(90deg, rgb(var(--down) / 0.6), rgb(var(--surface-3)), rgb(var(--up) / 0.6))" }}>
            <div className="absolute -top-1 h-4 w-1.5 rounded bg-fg shadow" style={{ left: `calc(${Math.min(98, Math.max(1, pct))}% - 3px)` }} />
          </div>
        </div>
        <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-fg"><CharIcon size={15} className="text-ai" />{t(`lab.study.char.${s.character}`)}{s.vix != null && <span className="ml-auto text-xs text-fg-muted">VIX {s.vix.toFixed(1)}</span>}</div>
        <ul className="space-y-1 text-xs text-fg-muted">{s.lines.map((l) => <li key={l}>· {l}</li>)}</ul>
      </TiltCard>

      <TiltCard>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("lab.study.byTf")}</div>
        <table className="w-full text-xs">
          <thead><tr className="text-left text-fg-muted"><th className="py-1 font-normal">TF</th><th className="font-normal">{t("lab.study.trend")}</th><th className="text-right font-normal">RSI</th><th className="text-right font-normal">ADX</th></tr></thead>
          <tbody>
            {s.timeframes.map((tf) => (
              <tr key={tf.timeframe} className="border-t border-border/50">
                <td className="py-1.5 font-semibold text-fg">{tf.timeframe}</td>
                <td className={tf.trend === "UP" ? "text-up" : tf.trend === "DOWN" ? "text-down" : "text-fg"}>{tf.trend_text}</td>
                <td className="text-right font-tabular text-fg">{num(tf.rsi, 0)}</td>
                <td className="text-right font-tabular text-fg">{num(tf.adx, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 space-y-1.5">
          {s.detail_shown === false && <p className="text-[11px] text-fg-muted">{t("thesis.noScenarios")}</p>}
          {s.scenarios.map((sc) => (
            <div key={sc.id} className={`rounded-lg border px-2 py-1.5 text-xs text-fg ${sc.id === "bull" ? "border-up/30" : sc.id === "bear" ? "border-down/30" : "border-border"}`}>{sc.text}</div>
          ))}
        </div>
      </TiltCard>

      <TiltCard>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("lab.study.levels")}</div>
        <div className="space-y-0.5 text-xs">
          {rows.map((r) => (
            <div key={r.key + r.price} className={`flex items-center justify-between rounded px-2 py-0.5 ${r.key === "_last" ? "bg-ai/15 font-semibold text-fg" : "text-fg-muted"}`}>
              <span>{r.name}</span>
              <span className="font-tabular">{num(r.price)}{r.key !== "_last" && r.distance_pct != null && <span className={`ml-2 ${r.distance_pct > 0 ? "text-up" : r.distance_pct < 0 ? "text-down" : ""}`}>{r.distance_pct > 0 ? "+" : ""}{r.distance_pct}%</span>}</span>
            </div>
          ))}
        </div>
      </TiltCard>
    </div>
  );
}
