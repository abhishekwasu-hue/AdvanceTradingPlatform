/**
 * N4 CH6: a promise API over the study-math WebWorker, with the same math on the main thread where a Worker cannot
 * start (tests, old browsers, a blocked script). Answers come back by request id, so concurrent calls never mix.
 */
import { runMath, type LinePoint, type MathRequest, type Num } from "./studyMath";

export interface StudyMath {
  run(req: MathRequest): Promise<LinePoint[] | Num[]>;
  readonly inWorker: boolean;
  dispose(): void;
}

interface WorkerLike {
  postMessage(msg: unknown): void;
  terminate(): void;
  onmessage: ((e: MessageEvent) => void) | null;
  onerror: ((e: unknown) => void) | null;
}

export function mainThreadMath(): StudyMath {
  return { inWorker: false, run: async (req) => runMath(req), dispose: () => {} };
}

/** The worker-backed API; `make` builds the worker (default: the bundled studyWorker), and any failure to start falls
 * back to the main thread. A worker error rejects the pending calls, and later calls run on the main thread. */
export function createStudyMath(make?: () => WorkerLike): StudyMath {
  let worker: WorkerLike | null = null;
  try {
    worker = make ? make() : typeof Worker !== "undefined"
      ? (new Worker(new URL("./studyWorker.ts", import.meta.url), { type: "module" }) as unknown as WorkerLike) : null;
  } catch {
    worker = null;
  }
  if (!worker) return mainThreadMath();
  const pending = new Map<number, { resolve: (r: LinePoint[] | Num[]) => void; reject: (e: Error) => void }>();
  let seq = 0;
  let broken = false;
  const w = worker;
  w.onmessage = (e: MessageEvent) => {
    const { id, result, error } = e.data as { id: number; result?: LinePoint[] | Num[]; error?: string };
    const p = pending.get(id);
    if (!p) return;
    pending.delete(id);
    if (error != null) p.reject(new Error(error)); else p.resolve(result ?? []);
  };
  w.onerror = () => {
    broken = true;
    for (const p of pending.values()) p.reject(new Error("the study worker stopped"));
    pending.clear();
  };
  return {
    get inWorker() { return !broken; },
    run(req) {
      if (broken) return Promise.resolve(runMath(req));
      const id = ++seq;
      return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); w.postMessage({ id, req }); });
    },
    dispose() { w.terminate(); pending.clear(); },
  };
}
