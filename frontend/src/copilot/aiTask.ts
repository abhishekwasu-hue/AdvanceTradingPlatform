import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";

/**
 * Every Copilot call that waits on the AI or a backtest runs through `useAiTask`: named progress steps, a Cancel
 * button (AbortController), a timeout, and a friendly error - never a raw `String(e)`.
 */
export type TaskPhase = "idle" | "running" | "done" | "error" | "cancelled";

export interface AiTaskState {
  phase: TaskPhase;
  /** Index into the steps passed to `run` while running. */
  step: number;
  steps: string[];
  error: string | null;
  startedAt: number | null;
}

export const DEFAULT_TIMEOUT_MS = 90_000;

class TimeoutError extends Error {
  constructor() { super("timeout"); this.name = "TimeoutError"; }
}

/** A sentence for any failure of an AI call. ApiError messages are already written for a trader (api/errors.ts). */
export function friendlyError(e: unknown, t: (key: string) => string): string {
  if (e instanceof TimeoutError) return t("states.timeout");
  if (e instanceof DOMException && e.name === "AbortError") return t("states.cancelled");
  if (e instanceof ApiError) {
    if (e.status === 402) return t("states.plan");
    if (e.status === 503) return e.detail || t("states.unavailable");
    if (e.status >= 500) return t("states.server");
    return e.message;
  }
  if (e instanceof Error && e.message && !/^[A-Z][a-zA-Z]*Error\b|undefined|null|\{/.test(e.message)) return e.message;
  return t("states.generic");
}

export function useAiTask(t: (key: string) => string, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const [state, setState] = useState<AiTaskState>({ phase: "idle", step: 0, steps: [], error: null, startedAt: null });
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<number | null>(null);
  const stepTimer = useRef<number | null>(null);

  const clearTimers = () => {
    if (timer.current != null) window.clearTimeout(timer.current);
    if (stepTimer.current != null) window.clearInterval(stepTimer.current);
    timer.current = stepTimer.current = null;
  };
  useEffect(() => () => { clearTimers(); controller.current?.abort(); }, []);

  /** Runs `fn` with an AbortSignal. The steps advance on a clock (the server does not stream its progress) and the
   * last one stays until the answer arrives; `advance` lets the caller move to a step when it knows. */
  const run = useCallback(async <T,>(steps: string[], fn: (signal: AbortSignal, advance: (step: number) => void) => Promise<T>): Promise<T | undefined> => {
    controller.current?.abort();
    clearTimers();
    const ctl = new AbortController();
    controller.current = ctl;
    setState({ phase: "running", step: 0, steps, error: null, startedAt: Date.now() });
    const advance = (step: number) => setState((s) => (s.phase === "running" ? { ...s, step: Math.min(step, steps.length - 1) } : s));
    stepTimer.current = window.setInterval(() => setState((s) => (s.phase === "running" && s.step < steps.length - 1 ? { ...s, step: s.step + 1 } : s)), 2500);
    let timedOut = false;
    timer.current = window.setTimeout(() => { timedOut = true; ctl.abort(); }, timeoutMs);
    // A step that does not listen to the signal (a candle fetch) must not keep the task "running" past a Cancel or
    // the timeout: the abort wins the race.
    const aborted = new Promise<never>((_, reject) => ctl.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true }));
    try {
      const out = await Promise.race([fn(ctl.signal, advance), aborted]);
      if (controller.current !== ctl) return undefined;           // a newer run replaced this one: it owns the state
      if (ctl.signal.aborted) throw timedOut ? new TimeoutError() : new DOMException("Aborted", "AbortError");
      setState((s) => ({ ...s, phase: "done", error: null }));
      return out;
    } catch (e) {
      if (controller.current !== ctl) return undefined;
      const err = timedOut ? new TimeoutError() : e;
      const cancelled = !timedOut && ctl.signal.aborted;
      setState((s) => ({ ...s, phase: cancelled ? "cancelled" : "error", error: cancelled ? null : friendlyError(err, t) }));
      return undefined;
    } finally {
      if (controller.current === ctl) clearTimers();
    }
  }, [t, timeoutMs]);

  const cancel = useCallback(() => { controller.current?.abort(); }, []);
  const reset = useCallback(() => setState({ phase: "idle", step: 0, steps: [], error: null, startedAt: null }), []);
  return { state, run, cancel, reset, busy: state.phase === "running" };
}
