import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { StrategyInfo } from "../types";

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listStrategies().then(setStrategies).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-violet-400">Strategy Library</h1>
        <p className="text-sm font-semibold text-violet-400/60">Every inbuilt auto-executable scalping strategy, straight from the registry.</p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      <div className="grid md:grid-cols-2 gap-4">
        {strategies.map((s) => (
          <Card key={s.id}>
            <div className="flex items-center justify-between mb-2">
              <div className="font-semibold text-slate-100">{s.name}</div>
              <span className="text-[11px] uppercase tracking-wide text-muted border border-border rounded px-2 py-0.5">
                {s.category === "multi_timeframe" ? "Multi-timeframe" : "Indicator-based"}
              </span>
            </div>
            <div className="text-sm text-muted mb-3">{s.description}</div>
            <div className="text-xs text-slate-400 mb-2">Timeframes: {s.timeframes.join(" / ")}</div>
            <details className="text-xs text-muted">
              <summary className="cursor-pointer text-slate-300">Default parameters</summary>
              <pre className="mt-2 overflow-x-auto rounded bg-panel2 p-2 text-[11px]">
                {JSON.stringify(s.default_params, null, 2)}
              </pre>
            </details>
          </Card>
        ))}
      </div>
    </div>
  );
}
