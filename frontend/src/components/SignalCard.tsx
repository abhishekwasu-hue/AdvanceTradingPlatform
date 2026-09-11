import type { EnrichedSignal } from "../types";
import { DirectionBadge, GradeBadge, ProgressBar } from "./ui";

export default function SignalCard({ result }: { result: EnrichedSignal }) {
  const { signal, composite_score, grade, breakdown, confirmations } = result;

  if (signal.direction === "NO_TRADE") {
    return (
      <div className="rounded-lg border border-border bg-panel p-5">
        <div className="text-sm text-muted mb-1">{signal.symbol}</div>
        <div className="text-lg font-semibold text-slate-200">NO TRADE</div>
        <ul className="mt-3 space-y-1 text-sm text-muted list-disc list-inside">
          {signal.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-panel p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="text-sm text-muted">{signal.symbol}</div>
        <div className="flex items-center gap-2">
          <DirectionBadge direction={signal.direction} />
          <GradeBadge grade={grade} />
        </div>
      </div>
      <div className="text-xs text-muted mb-4">
        {signal.strategy_name} · {signal.timeframe_combo}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Field label="Entry" value={signal.entry} />
        <Field label="Stop Loss" value={signal.stop_loss} tone="down" />
        <Field label="Target 1" value={signal.target1} tone="up" />
        <Field label="Target 2" value={signal.target2} tone="up" />
      </div>

      <div className="flex items-center justify-between text-sm mb-1">
        <span className="text-muted">Composite Score</span>
        <span className="font-semibold">{composite_score}/100</span>
      </div>
      <ProgressBar pct={composite_score} />
      <div className="text-xs text-muted mt-1 mb-4">Risk/Reward 1:{signal.risk_reward?.toFixed(2) ?? "-"}</div>

      <div className="text-xs uppercase tracking-wide text-muted mb-2">Why this trade</div>
      <div className="space-y-2 mb-2">
        {Object.entries(breakdown).map(([key, comp]) => (
          <div key={key}>
            <div className="flex justify-between text-xs mb-0.5">
              <span className="capitalize text-slate-300">{key.replace(/_/g, " ")}</span>
              <span className="text-muted">
                {comp.pct.toFixed(0)}% · weight {comp.weight}
              </span>
            </div>
            <ProgressBar pct={comp.pct} />
          </div>
        ))}
      </div>

      <ul className="mt-3 space-y-1 text-xs text-muted list-disc list-inside">
        {confirmations.map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>
    </div>
  );
}

function Field({ label, value, tone }: { label: string; value: number | null; tone?: "up" | "down" }) {
  const color = tone === "up" ? "text-accent" : tone === "down" ? "text-danger" : "text-slate-100";
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`text-sm font-semibold ${color}`}>{value?.toFixed(2) ?? "-"}</div>
    </div>
  );
}
