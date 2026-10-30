import { useEffect, useState } from "react";

/**
 * REV-1 M3: fewer reads. `debounce` runs `fn` once, `ms` after the last call of a burst (typing, a run of live events);
 * `useDebouncedValue` is the same for a value (a search box: one read when the typing stops, not one per key).
 */
export interface Debounced<A extends unknown[]> {
  (...args: A): void;
  cancel: () => void;
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number): Debounced<A> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const call = ((...args: A) => {
    if (timer !== undefined) clearTimeout(timer);
    timer = setTimeout(() => { timer = undefined; fn(...args); }, ms);
  }) as Debounced<A>;
  call.cancel = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };
  return call;
}

/** The value, `ms` after it last changed (the first value at once). */
export function useDebouncedValue<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}
