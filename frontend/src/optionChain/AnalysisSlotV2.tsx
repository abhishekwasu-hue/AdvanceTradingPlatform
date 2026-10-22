/**
 * OC1: the place below the chain where the Analysis tabs go (OC2, `frontend/src/optionChain/AnalysisTabsV2.tsx`, built
 * on another branch). A follow-up replaces this placeholder's body with
 * `<AnalysisTabs symbol={symbol} expiry={expiry} refreshKey={refreshKey} />` once both have merged. Until then it renders
 * an empty marker only (no text, no data).
 */
export interface AnalysisSlotProps {
  symbol: string;
  /** The expiry on screen (ISO date), or null while not known. */
  expiry: string | null;
  /** Changes with every chain answer (its `fetched_at`), so the analyses can refresh with the chain. */
  refreshKey: string | null;
}

export default function AnalysisSlotV2({ symbol, expiry, refreshKey }: AnalysisSlotProps) {
  return <div data-analysis-mount="option-chain" data-underlying={symbol} data-expiry={expiry ?? ""} data-refresh-key={refreshKey ?? ""} />;
}
