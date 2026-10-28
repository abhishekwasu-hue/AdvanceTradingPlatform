/**
 * Trading Terminal T1: the status bar's pure parts (unit-tested) - the money as the bar shows it (or hidden behind the
 * privacy mask), the feed state in plain words with its tone, and the privacy mask itself, kept per device.
 */
import { formatINR, formatSignedINR } from "../ui/format";
import type { FeedState, TerminalStatus } from "./api";

export const MASK = "₹••••";
export const MASK_KEY = "atp_terminal_mask";

/** An amount on the bar: the privacy mask hides every one of them (the same four dots, so nothing about the size shows). */
export function money(v: number | null | undefined, masked: boolean, signed = false): string {
  if (masked) return MASK;
  return signed ? formatSignedINR(v ?? null) : formatINR(v ?? null);
}

export type Tone = "up" | "down" | "warn" | "neutral";

export function signTone(v: number | null | undefined): Tone {
  if (typeof v !== "number" || !Number.isFinite(v) || Math.abs(v) < 0.5) return "neutral";
  return v > 0 ? "up" : "down";
}

/** The feed chip: a short word and its tone; the full sentence from the server goes in its title. */
export function feedChip(state: FeedState | undefined): { label: string; tone: Tone } {
  switch (state) {
    case "live": return { label: "Live prices", tone: "up" };
    case "connecting": return { label: "Connecting", tone: "neutral" };
    case "reconnecting": return { label: "Reconnecting", tone: "warn" };
    case "renew_needed": return { label: "Log in to your broker", tone: "warn" };
    case "polling": return { label: "Prices every few seconds", tone: "neutral" };
    case "none": return { label: "Waiting for live prices", tone: "warn" };
    case "off":
    default: return { label: "Prices every few seconds", tone: "neutral" };
  }
}

/** The mode chip: shown, never changed (T9). */
export function modeChip(s: TerminalStatus | null): { label: string; title: string } {
  if (!s || s.mode.mode === "PAPER") return { label: "Paper", title: s?.mode.message ?? "Practice mode: no real order is sent and no money moves." };
  return { label: "Live", title: s.mode.message };
}

/** The margin line: always labelled an estimate; the positions it could not estimate are said, never guessed. */
export function marginNote(s: TerminalStatus): string {
  const n = s.margin.not_estimated;
  return n > 0 ? `Estimate. ${n} position${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} your broker's margin and ${n === 1 ? "is" : "are"} not counted.` : "Estimate.";
}

/** The open MTM's caption: how many open positions have a price yet. */
export function openCaption(s: TerminalStatus): string | null {
  if (s.open.count === 0) return "No open positions";
  if (s.open.priced < s.open.count) return `${s.open.priced} of ${s.open.count} priced`;
  return null;
}

/** The privacy mask, per device (this browser only): on or off. Storage may be unavailable - then it is off. */
export function readMask(storage: Pick<Storage, "getItem"> | null = safeStorage()): boolean {
  try { return storage?.getItem(MASK_KEY) === "1"; } catch { return false; }
}

export function writeMask(on: boolean, storage: Pick<Storage, "setItem" | "removeItem"> | null = safeStorage()): void {
  try {
    if (on) storage?.setItem(MASK_KEY, "1");
    else storage?.removeItem(MASK_KEY);
  } catch { /* storage unavailable: the mask lasts until the page closes */ }
}

function safeStorage(): Storage | null {
  try { return typeof window !== "undefined" ? window.localStorage : null; } catch { return null; }
}
