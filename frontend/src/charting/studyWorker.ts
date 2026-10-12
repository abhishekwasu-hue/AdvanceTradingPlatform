/// <reference lib="webworker" />
/**
 * N4 CH6: the study-math WebWorker. Messages: { id, req: MathRequest } -> { id, result } or { id, error }. All the math
 * is `studyMath.ts` (pure, tested on the main thread too).
 */
import { runMath, type MathRequest } from "./studyMath";

self.onmessage = (e: MessageEvent<{ id: number; req: MathRequest }>) => {
  const { id, req } = e.data;
  try {
    (self as unknown as Worker).postMessage({ id, result: runMath(req) });
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: err instanceof Error ? err.message : String(err) });
  }
};
