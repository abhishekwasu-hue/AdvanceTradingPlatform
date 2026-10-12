/**
 * N4 CH6: ScreenQL studies on the chart - `GET /api/charts/studies` (flag `chart_studies`; off -> 503 and nothing is
 * shown). The server computes every value with the screener's own runtime, as of `at` (replay: the replayed bar's end)
 * from the bars closed by then; the browser only draws them: a number study as a line (on the price pane when it lives
 * at the price, else in a pane of its own), a condition study as marks where it turns true.
 * Reference only - nothing here places, changes or proposes an order.
 */
import { ApiError, request } from "../api/client";
import { BUSY_RETRIES, BUSY_RETRY_MS, isBusy, type LayerTf } from "./priceAction";

export const MAX_STUDIES = 6;
export const STUDY_COLORS = ["#2563eb", "#d97706", "#7c3aed", "#0891b2", "#be185d", "#4d7c0f"] as const;

export interface StudyProblem { message: string; pos: number | null }
export interface StudyResult {
  id: number; expr: string; text: string; ok: boolean; problems: StudyProblem[]; kind: "num" | "bool" | null; unit: string | null;
  pane: "price" | "own"; lookback: number;
  values?: (number | null)[]; last?: number | boolean | null; last_text?: string | null;
  fires?: number[]; true_bars?: number;
}
export interface StudiesResponse {
  schema: string; instrument: string; tf: LayerTf; at: string; start: string; source: string | null; as_of: string | null; bars: number;
  warmup_bars: number; times: number[]; first_time: number | null; last_time: number | null; studies: StudyResult[];
  fingerprint: string; note: string; reason: string | null;
}

export const studiesApi = {
  get: (instrument: string, exprs: readonly string[], opts: { tf: LayerTf; at?: string | null; start?: string | null }) => {
    const q = new URLSearchParams({ instrument, tf: opts.tf });
    for (const e of exprs) q.append("expr", e);
    if (opts.at) q.set("at", opts.at);
    if (opts.start) q.set("start", opts.start);
    return request<StudiesResponse>(`/charts/studies?${q.toString()}`);
  },
};

const STORE_KEY = "atp.chart.studies";

/** The viewer's study list (per browser, a convenience - any read problem gives none). */
export function loadStudyList(storage: Pick<Storage, "getItem"> | null = safe()): string[] {
  try {
    const raw = storage?.getItem(STORE_KEY);
    const v = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim().length > 0).slice(0, MAX_STUDIES) : [];
  } catch {
    return [];
  }
}

export function saveStudyList(list: readonly string[], storage: Pick<Storage, "setItem"> | null = safe()): void {
  try { storage?.setItem(STORE_KEY, JSON.stringify(list.slice(0, MAX_STUDIES))); } catch { /* blocked storage: not kept */ }
}

function safe(): Storage | null {
  try { return typeof window !== "undefined" ? window.localStorage : null; } catch { return null; }
}

export interface StudiesQuery { instrument: string; tf: LayerTf; exprs: string[]; at: string | null; start: string | null }
export interface StudiesState { result: StudiesResponse | null; failed: string | null; available: boolean | null }

/** One request at a time, as the price-action layer's loader: a new question clears the old answer at once (never a
 * study as of another bar on screen), a late answer to an older question is dropped, a failure leaves nothing drawn
 * and says why, 503 (flag off) shows nothing; 503 "busy, retry" is tried again a few times (`isBusy`). */
export class StudiesLoader {
  private seq = 0;
  private available: boolean | null = null;

  constructor(private readonly fetcher: (q: StudiesQuery) => Promise<StudiesResponse>, private readonly emit: (s: StudiesState) => void) {}

  load(q: StudiesQuery | null): () => void {
    const my = ++this.seq;
    this.emit({ result: null, failed: null, available: q ? this.available : this.available });
    if (!q || q.exprs.length === 0) return () => {};
    let timer: ReturnType<typeof setTimeout> | null = null;
    const attempt = (left: number) => this.fetcher(q).then(
      (r) => { if (my === this.seq) { this.available = true; this.emit({ result: r, failed: null, available: true }); } },
      (e: unknown) => {
        if (my !== this.seq) return;
        // busy (the server's build slots are full): try again shortly; 503 without it is the flag being off
        if (isBusy(e) && left > 0) { timer = setTimeout(() => { if (my === this.seq) attempt(left - 1); }, BUSY_RETRY_MS); return; }
        if (e instanceof ApiError && e.status === 503 && !isBusy(e)) { this.available = false; this.emit({ result: null, failed: null, available: false }); return; }
        this.emit({ result: null, failed: e instanceof Error ? e.message : "Studies could not be loaded.", available: this.available });
      },
    );
    attempt(BUSY_RETRIES);
    return () => { if (timer) clearTimeout(timer); if (my === this.seq) this.seq++; };
  }
}
