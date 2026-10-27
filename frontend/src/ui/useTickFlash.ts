import { useEffect, useRef, useState } from "react";

/**
 * UX-7 (owner order UX-2, 25 Oct): the "live feel" - a price that changes shows a short soft tint, up or down, then
 * returns to normal (the number itself never moves or jumps). Only a change of the same value counts: the first value,
 * an empty one or the same number again never flashes. With reduced motion the tint still shows, without the fade
 * (the duration tokens are 0 then).
 */
export type Flash = "up" | "down" | null;

/** How long the tint stays (ms). A UI timing, not a trading number. */
export const FLASH_MS = 700;

/** The flash for a change from `prev` to `next`: up / down, or none (first value, no value, unchanged). Pure. */
export function flashFor(prev: number | null | undefined, next: number | null | undefined): Flash {
  if (prev == null || next == null || !Number.isFinite(prev) || !Number.isFinite(next) || prev === next) return null;
  return next > prev ? "up" : "down";
}

/** The soft tint classes for a flash (tokens only); "" when none. */
export function flashClass(flash: Flash): string {
  return flash === "up" ? "bg-up-tint" : flash === "down" ? "bg-down-tint" : "";
}

/** The flash for a value as it changes over time: set on each change, cleared after FLASH_MS. */
export function useTickFlash(value: number | null | undefined): Flash {
  const last = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<Flash>(null);
  useEffect(() => {
    const next = flashFor(last.current, value);
    if (value != null) last.current = value;
    if (!next) return undefined;
    setFlash(next);
    const t = setTimeout(() => setFlash(null), FLASH_MS);
    return () => clearTimeout(t);
  }, [value]);
  return flash;
}
