import { useCopilotT } from "../i18n";

/**
 * The market sentiment score (-100 risk-off .. +100 risk-on, from the deterministic sentiment read) as a CSS-3D
 * gauge: a tilted dial face with depth, a needle and the number. Colour carries the meaning only: the up token for
 * risk-on, down for risk-off, muted when neutral or unknown. No three.js - it stays in the page's own chunk.
 */
export function sentimentAngle(score: number): number {
  const s = Math.max(-100, Math.min(100, score));
  return (s / 100) * 90;           // -90deg (far left) .. +90deg (far right)
}

export default function SentimentGauge3D({ score, label, coverage }: { score: number | null; label?: string | null; coverage?: number | null }) {
  const t = useCopilotT();
  const known = score != null && label !== "UNKNOWN";
  const angle = known ? sentimentAngle(score) : 0;
  const tone = !known ? "text-fg-muted" : score > 10 ? "text-up" : score < -10 ? "text-down" : "text-fg";
  const word = !known ? t("pulse.sentiment.unknown") : label === "RISK_ON" ? t("pulse.sentiment.riskOn") : label === "RISK_OFF" ? t("pulse.sentiment.riskOff") : t("pulse.sentiment.neutral");
  return (
    <figure className="copilot-gauge" aria-label={t("pulse.sentiment.aria", { value: known ? Math.round(score) : "-", word })} role="img">
      <div className="copilot-gauge-stage">
        <div className="copilot-gauge-face">
          <svg viewBox="0 0 200 110" className="h-full w-full" aria-hidden="true">
            <defs>
              <linearGradient id="gauge-arc" x1="0" x2="1" y1="0" y2="0">
                <stop offset="0%" stopColor="rgb(var(--down))" />
                <stop offset="50%" stopColor="rgb(var(--fg-muted))" stopOpacity="0.6" />
                <stop offset="100%" stopColor="rgb(var(--up))" />
              </linearGradient>
            </defs>
            <path d="M 15 100 A 85 85 0 0 1 185 100" fill="none" stroke="rgb(var(--surface-3))" strokeWidth="16" strokeLinecap="round" />
            <path d="M 15 100 A 85 85 0 0 1 185 100" fill="none" stroke="url(#gauge-arc)" strokeWidth="10" strokeLinecap="round" opacity={known ? 0.9 : 0.25} />
            {[-90, -45, 0, 45, 90].map((a) => {
              const r1 = 70, r2 = 62, rad = ((a - 90) * Math.PI) / 180;
              return <line key={a} x1={100 + r1 * Math.cos(rad)} y1={100 + r1 * Math.sin(rad)} x2={100 + r2 * Math.cos(rad)} y2={100 + r2 * Math.sin(rad)}
                           stroke="rgb(var(--fg-muted))" strokeOpacity="0.6" strokeWidth="1.5" />;
            })}
          </svg>
          <div className="copilot-gauge-needle" style={{ transform: `rotate(${angle}deg)` }} />
          <div className="copilot-gauge-hub" />
        </div>
      </div>
      <figcaption className="mt-1 text-center">
        <div className={`font-tabular text-2xl font-bold ${tone}`}>{known ? `${score > 0 ? "+" : ""}${Math.round(score)}` : "-"}</div>
        <div className="text-xs text-fg-muted">{word}{known && coverage != null ? ` · ${t("pulse.sentiment.coverage", { pct: Math.round(coverage * 100) })}` : ""}</div>
      </figcaption>
    </figure>
  );
}
