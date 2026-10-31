// Z5: text for the short strikes' breach chances on a proposal card (numbers from the server's model).
import type { StrikeBreachBlock } from "../types";
import { formatNumber } from "../ui/format";

function pct(p: number | null | undefined): string {
  return p == null ? "-" : `${formatNumber(p * 100, 1)}%`;
}

/** One line per short strike: "Short PE 19700 (3 DTE): 12.3% at expiry, 30.1% touch". */
export function breachRows(block: StrikeBreachBlock | null | undefined): { key: string; text: string }[] {
  if (!block || !block.available) return [];
  return block.legs.map((leg) => {
    const head = `Short ${leg.option_type} ${leg.strike} (${leg.dte ?? "-"} DTE)`;
    if (leg.status === "in_the_money") return { key: `${leg.leg}`, text: `${head}: already beyond the strike` };
    if (leg.status === "expired") return { key: `${leg.leg}`, text: `${head}: expired` };
    return { key: `${leg.leg}`, text: `${head}: breach at expiry ${pct(leg.prob_close)}, touch before ${pct(leg.prob_touch)}${leg.extrapolated ? " (outside the fitted range)" : ""}` };
  });
}
