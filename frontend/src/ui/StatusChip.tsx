import { Check, CircleAlert, FlaskConical, Info, Radio, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Chip, type ChipTone, type ChipVariant } from "./Chip";

/**
 * Chart + colour order, Part 3 (CC-1, CC-2): the status chip - the word, an icon and a colour by meaning, the same on
 * every page. It never reuses the P&L colours for a state:
 * - connected / done: neutral with a check (a success is not green - green means profit only);
 * - needs action: warn, with a warning icon;
 * - error: warn - or down only when money or an order is at stake (`atStake`);
 * - info: info, with an info icon;
 * - paper: the neutral outline chip with the word "Paper" (CC-2: no orange - orange is LIVE / armed, and loss in the
 *   colour-blind mode);
 * - live: the solid chip (the text colour; UI v2 §8).
 */
export type Status = "connected" | "needs-action" | "error" | "info" | "paper" | "live";

const LOOK: Record<Status, { tone: ChipTone; variant: ChipVariant; icon: ReactNode; word: string }> = {
  connected: { tone: "neutral", variant: "tint", icon: <Check size={13} />, word: "Connected" },
  "needs-action": { tone: "warn", variant: "tint", icon: <TriangleAlert size={13} />, word: "Needs action" },
  error: { tone: "warn", variant: "tint", icon: <CircleAlert size={13} />, word: "Error" },
  info: { tone: "info", variant: "tint", icon: <Info size={13} />, word: "Info" },
  paper: { tone: "neutral", variant: "outline", icon: <FlaskConical size={13} />, word: "Paper" },
  live: { tone: "neutral", variant: "solid", icon: <Radio size={13} />, word: "Live" },
};

export interface StatusChipProps {
  status: Status;
  /** The words shown; each status has a plain default ("Connected", "Needs action" ...). */
  children?: ReactNode;
  /** Error only: money or an order is at stake (an order rejected, a position not protected), so the chip is red. */
  atStake?: boolean;
  className?: string;
}

/** The tone a status takes (exported for tests and for pieces that colour a matching icon). */
export function statusTone(status: Status, atStake = false): ChipTone {
  return status === "error" && atStake ? "down" : LOOK[status].tone;
}

export function StatusChip({ status, children, atStake = false, className }: StatusChipProps) {
  const look = LOOK[status];
  return (
    <Chip name={status} tone={statusTone(status, atStake)} variant={look.variant} icon={look.icon} className={className}>
      {children ?? look.word}
    </Chip>
  );
}
