import { fetchAnalysesShared } from "./analyses";
import { AnalysisTabs } from "./AnalysisTabsV2";
import { MyAnalyses } from "./MyAnalysesV2";

/**
 * OC1 / OC4: the analyses below the v2 chain, for the symbol and expiry on screen:
 * - the built-in Analysis tabs (OC2: S/R from OI, OI build-up, PCR, Max Pain, expected move, IV smile / skew);
 * - Ask AI moved to the page header (OC-2: a side panel / phone sheet, OptionChainPageV2);
 * - My analyses (OC3: the saved ones, as tabs that update with the chain and use no AI).
 * Each refreshes with the chain (`refreshKey` = the chain answer's `fetched_at`) and shows the server's plain sentence
 * when there is no data, never sample numbers. Information only: nothing here places an order.
 */
export interface AnalysisSlotProps {
  symbol: string;
  /** The expiry on screen (ISO date), or null while not known. */
  expiry: string | null;
  /** Changes with every chain answer (its `fetched_at`), so the analyses can refresh with the chain. */
  refreshKey: string | null;
}

export default function AnalysisSlotV2({ symbol, expiry, refreshKey }: AnalysisSlotProps) {
  const key = refreshKey ?? undefined;
  return (
    <div className="space-y-space-4" data-analysis-mount="option-chain" data-underlying={symbol} data-expiry={expiry ?? ""}
      data-refresh-key={refreshKey ?? ""}>
      {/* MO4 slice 2: the shared read, so the summary on top of the page (ChainDishV2) costs no second chain read. */}
      <AnalysisTabs symbol={symbol} expiry={expiry} refreshKey={key} load={fetchAnalysesShared} />
      <MyAnalyses symbol={symbol} expiry={expiry} refreshKey={key} />
    </div>
  );
}
