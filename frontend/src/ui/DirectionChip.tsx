import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { cx } from "../components/primitives/cx";

/**
 * Colour v2 (owner, 23 Oct): the direction of the user's own signal or position - Long is a green chip with an up
 * arrow, Short a red chip with a down arrow. Icon plus word, never colour alone (in the colour-blind mode the chips are
 * blue and orange). A direction label only: no buy / sell wording, no price.
 */
export type Side = "long" | "short";

/** "LONG" / "Long" / "Buy" -> long; "SHORT" / "Short" / "Sell" -> short; anything else -> null. */
export function sideOf(value: string | null | undefined): Side | null {
  const v = (value ?? "").trim().toLowerCase();
  if (v === "long" || v === "buy") return "long";
  if (v === "short" || v === "sell") return "short";
  return null;
}

export function DirectionChip({ side, className }: { side: Side; className?: string }) {
  const long = side === "long";
  const Icon = long ? ArrowUpRight : ArrowDownRight;
  return (
    <span data-direction={side}
      className={cx("inline-flex h-6 shrink-0 items-center gap-px rounded-pill px-space-2 text-t13 font-semibold",
        long ? "bg-up-tint text-up" : "bg-down-tint text-down", className)}>
      <Icon size={14} aria-hidden />{long ? "Long" : "Short"}
    </span>
  );
}
