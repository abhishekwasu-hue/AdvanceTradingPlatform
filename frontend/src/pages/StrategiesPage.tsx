import { Suspense, lazy, useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { StrategyInfo } from "../types";
import { PageHeader } from "../components/primitives";
import { useUiV2 } from "../ui/useFeatures";

// UI2-3 (UI v2 §3 M6): with `ui_v2` on, the signal generator lives in each strategy's drawer ("Try it on a symbol"),
// its own chunk; with the flag off this page renders exactly as before.
const TryOnSymbolV2 = lazy(() => import("../signals/TryOnSymbolV2"));

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const v2 = useUiV2();
  const [trying, setTrying] = useState<StrategyInfo | null>(null);

  useEffect(() => {
    api.listStrategies().then(setStrategies).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Strategy Library" description="Every inbuilt auto-executable scalping strategy, straight from the registry." />

      {error && <div className="text-sm text-down">{error}</div>}

      <div className="grid md:grid-cols-2 gap-4">
        {strategies.map((s) => (
          <Card key={s.id}>
            <div className="flex items-center justify-between mb-2">
              <div className="font-semibold text-fg">{s.name}</div>
              <span className="text-[11px] uppercase tracking-wide text-fg-muted border border-border rounded px-2 py-0.5">
                {s.category === "multi_timeframe" ? "Multi-timeframe" : "Indicator-based"}
              </span>
            </div>
            <div className="text-sm text-fg-muted mb-3">{s.description}</div>
            <div className="text-xs text-fg-muted mb-2">Timeframes: {s.timeframes.join(" / ")}</div>
            {v2 && (
              <button type="button" onClick={() => setTrying(s)}
                className="mb-2 inline-flex min-h-11 items-center rounded-control text-sm font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
                Try it on a symbol
              </button>
            )}
            <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer text-fg-muted">Default parameters</summary>
              <pre className="mt-2 overflow-x-auto rounded bg-surface-2 p-2 text-[11px]">
                {JSON.stringify(s.default_params, null, 2)}
              </pre>
            </details>
          </Card>
        ))}
      </div>
      {v2 && trying && <Suspense fallback={null}><TryOnSymbolV2 strategy={trying} onClose={() => setTrying(null)} /></Suspense>}
    </div>
  );
}
