import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AiProviderConfig } from "../types";

/**
 * BYOK cost visibility: the estimated tokens and rupees of one typical call of a task on the organisation's own
 * provider (P0.9 per-task models and prices). Null when answers come from the rules (no outside provider) - then
 * nothing is spent and no chip is shown. The Ask tab shows the metered figure of each answer instead.
 */
export interface CostEstimate { tokens: number; inr: number }

let cached: Promise<AiProviderConfig | null> | null = null;
function providerConfig(): Promise<AiProviderConfig | null> {
  cached ??= api.aiProvider().catch(() => null);
  return cached;
}

export function estimateFor(cfg: AiProviderConfig | null, task: string): CostEstimate | null {
  if (!cfg || cfg.provider === "rule_based" || !cfg.enabled || !cfg.configured) return null;
  const row = cfg.task_models?.[cfg.provider]?.find((r) => r.task === task);
  if (!row) return null;
  const size = cfg.typical_call_tokens?.[row.tier];
  return { tokens: size ? size.input + size.output : 0, inr: row.est_inr_per_call };
}

export function useCostEstimate(task: string): CostEstimate | null {
  const [est, setEst] = useState<CostEstimate | null>(null);
  useEffect(() => {
    let live = true;
    void providerConfig().then((cfg) => { if (live) setEst(estimateFor(cfg, task)); });
    return () => { live = false; };
  }, [task]);
  return est;
}

/** Tests reset the shared provider read. */
export function resetCostCache(): void { cached = null; }
