import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { StrategyInfo } from "../types";
import { PageHeader } from "../components/primitives";
import { useUiV2 } from "../ui/useFeatures";
import { LibraryChips } from "../ui/LibraryChips";
import { timeframeWords } from "../ui/strategyLabels";
import StrategyLibraryV2 from "./StrategyLibraryV2";

// M49 U3: with `ui_v2` on, the Library is the owner-approved card design (StrategyLibraryV2: at most three settings per
// card, the rest under "More settings", "Try it on a symbol" in each card - UI2-3 M6); with the flag off this page
// renders exactly as before.
export default function StrategiesPage() {
  const v2 = useUiV2();
  return v2 ? <StrategyLibraryV2 /> : <StrategiesPageV1 />;
}

function StrategiesPageV1() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listStrategies().then(setStrategies).catch((e) => setError(errorSentence(e)));
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Strategy Library" description="The ready-made strategies you can run on paper." />

      {error && <div className="text-sm text-down">{error}</div>}

      <div className="grid md:grid-cols-2 gap-4">
        {strategies.map((s) => (
          <Card key={s.id}>
            <div className="flex items-center justify-between mb-2">
              <div className="font-semibold text-fg">{s.name}</div>
              <span className="text-xs uppercase tracking-wide text-fg-muted border border-border rounded px-2 py-0.5">
                {s.category === "multi_timeframe" ? "Multi-timeframe" : "Indicator-based"}
              </span>
            </div>
            <div className="text-sm text-fg-muted mb-3">{s.description}</div>
            <div className="text-xs text-fg-muted mb-2">Timeframes: {s.timeframes.map((t) => timeframeWords(t)).join(" / ")}</div>
            {/* SB-UX SU7: the strategy's settings as readable chips ("Fast EMA 9"), never raw JSON; REV-B2: no no-op R:R chip.
                STRAT-X: the stop and the targets are suggested values for the trader's own settings, shown apart. */}
            <LibraryChips params={s.default_params} />
          </Card>
        ))}
      </div>
    </div>
  );
}
