import { DOT_CLASS, DOT_TEXT, monogram, type BrokerDot } from "./brokerState";

/**
 * P2b: the broker tile and the status dot - tiny and dependency-free, because both navigations (in the first
 * download) show the dot. Semantic colour tokens only.
 */

/** A broker's tile: its initials on a plain square. No logo artwork (no trademarked images are copied). */
export function BrokerLogo({ name, size = "md" }: { name: string; size?: "sm" | "md" | "lg" }) {
  const box = size === "sm" ? "h-8 w-8 text-t12" : size === "lg" ? "h-12 w-12 text-t18" : "h-10 w-10 text-t15";
  return (
    <span aria-hidden data-broker-logo
      className={`flex shrink-0 items-center justify-center rounded-control border border-subtle bg-surface-2 font-semibold text-fg ${box}`}>
      {monogram(name)}
    </span>
  );
}

/** The status dot: green connected, amber needs login, red not working. `announce` adds the words for screen readers
 * (a navigation entry); next to visible status words it is left out. */
export function StatusDot({ dot, announce = false, className = "" }: { dot: BrokerDot; announce?: boolean; className?: string }) {
  return (
    <>
      <span aria-hidden data-broker-dot={dot} className={`inline-block h-2 w-2 shrink-0 rounded-pill ${DOT_CLASS[dot]} ${className}`} />
      {announce && <span className="sr-only">{`, ${DOT_TEXT[dot]}`}</span>}
    </>
  );
}
