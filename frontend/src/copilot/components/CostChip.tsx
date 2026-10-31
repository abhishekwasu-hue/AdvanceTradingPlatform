import { Coins } from "lucide-react";
import { formatINR, formatNumber } from "../../ui/format";
import { useCopilotT } from "../i18n";

/** "≈ 1.2k tokens · ₹0.42" under an AI answer (BYOK: the trader's own key pays). `estimate` marks a figure that
 * comes from the typical-call size, not this call's metered usage. */
export function formatTokens(n: number): string {
  return n >= 10_000 ? `${formatNumber(n / 1000)}k` : n >= 1000 ? `${formatNumber(n / 1000, 1)}k` : formatNumber(n);
}

export function formatInr(v: number): string {
  // Paise below ₹10 (a call costs a few paise), whole rupees above.
  return v < 0.01 ? "<₹0.01" : formatINR(v, { paise: v < 10 });
}

export default function CostChip({ tokens, inr, estimate = false }: { tokens: number; inr: number; estimate?: boolean }) {
  const t = useCopilotT();
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs text-fg-muted"
          title={estimate ? t("cost.estimateHint") : t("cost.meteredHint")}>
      <Coins size={11} />≈ {t("cost.tokens", { n: formatTokens(tokens) })} · {formatInr(inr)}{estimate ? ` ${t("cost.est")}` : ""}
    </span>
  );
}
