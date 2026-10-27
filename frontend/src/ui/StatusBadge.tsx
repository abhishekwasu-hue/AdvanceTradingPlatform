import { formatDateTimeIST, formatTimeIST } from "./format";
import { StatusChip, type Status } from "./StatusChip";

/**
 * UI-H Wave 0 (H7): ONE status mark per page or note - not a "Live" badge plus a "Market closed" chip plus an as-of
 * line. Built on StatusChip (the one chip look and colours).
 * - live: the market is open and the figures move;
 * - closed: the market is closed - "Closed · as of <last close>", where `asOf` is the LAST SESSION CLOSE (from the
 *   market calendar / the data), never the time of the request;
 * - delayed: the figures are older than they should be ("Delayed · as of 10:30 IST");
 * - paper: the paper-trading account;
 * - unavailable: the data could not be read (pair it with UnavailableState for the reason).
 * `asOf` is shown in IST: a time on the same IST day as `now`, else the date and time.
 */
export type BadgeState = "live" | "closed" | "delayed" | "paper" | "unavailable";

const LOOK: Record<BadgeState, { status: Status; word: string }> = {
  live: { status: "live", word: "Live" },
  closed: { status: "info", word: "Closed" },
  delayed: { status: "needs-action", word: "Delayed" },
  paper: { status: "paper", word: "Paper" },
  unavailable: { status: "error", word: "Not available" },
};

export interface StatusBadgeProps {
  state: BadgeState;
  /** The time the figures are from (the last session close when closed). */
  asOf?: Date | string | number | null;
  /** Replaces the default word ("Live", "Closed" ...). */
  label?: string;
  /** For tests: "now", to decide whether `asOf` is today (IST). */
  now?: Date;
  className?: string;
}

/** "as of 15:30 IST" when `asOf` is on today's IST date, else "as of 23 Oct 2026, 15:30 IST"; "" without a time. */
export function asOfText(asOf: StatusBadgeProps["asOf"], now: Date = new Date()): string {
  if (asOf === null || asOf === undefined || asOf === "") return "";
  const full = formatDateTimeIST(asOf);
  if (full === "–") return "";
  const sameDay = full.split(",")[0] === formatDateTimeIST(now).split(",")[0];
  return `as of ${sameDay ? formatTimeIST(asOf) : full}`;
}

export function StatusBadge({ state, asOf, label, now, className }: StatusBadgeProps) {
  const look = LOOK[state];
  const when = asOfText(asOf, now);
  return (
    <span data-status-badge={state} className={className}>
      <StatusChip status={look.status}>{label ?? look.word}{when ? ` · ${when}` : ""}</StatusChip>
    </span>
  );
}
