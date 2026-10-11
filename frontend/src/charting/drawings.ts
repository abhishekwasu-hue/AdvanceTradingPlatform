/**
 * CH1 (ADR-0023): the engine-neutral drawing schema (`drawing/1`, the same shape the backend stores) and the drawings
 * storage client. Anchors are time/price - never pixels or bar indexes - so every timeframe and every chart engine
 * shows the same drawing. Nothing here touches an order.
 */
import { ApiError } from "../api/errors";
import { request } from "../api/client";

export const DRAWING_SCHEMA = "drawing/1";
export const EXPORT_FORMAT = "atp-drawings/1";

export type DrawingKind =
  | "trendline" | "ray" | "measure" | "rectangle" | "fib_retracement" | "fib_extension" | "channel"
  | "hline" | "vline" | "text" | "long_position" | "short_position";

/** Per kind, which coordinates each anchor must carry: "tp" time and price, "p" price only, "t" time only. */
export const ANCHOR_RULES: Record<DrawingKind, ("tp" | "p" | "t")[]> = {
  trendline: ["tp", "tp"], ray: ["tp", "tp"], measure: ["tp", "tp"], rectangle: ["tp", "tp"],
  fib_retracement: ["tp", "tp"], fib_extension: ["tp", "tp", "tp"], channel: ["tp", "tp", "tp"],
  hline: ["p"], vline: ["t"], text: ["tp"],
  long_position: ["tp", "p", "p"], short_position: ["tp", "p", "p"],
};

export interface Anchor { t?: string; p?: number }
export interface DrawingStyle { color?: string; width?: number; line?: "solid" | "dashed" | "dotted"; extend?: "none" | "left" | "right" | "both" }

export interface DrawingV1 {
  schema?: typeof DRAWING_SCHEMA;
  kind: DrawingKind;
  anchors: Anchor[];
  style?: DrawingStyle;
  text?: string;
  levels?: number[];
}

export interface StoredDrawing {
  id: number;
  symbol: string;
  exchange: string;
  kind: DrawingKind;
  drawing: DrawingV1;
  version: number;
  locked: boolean;
  updated_at: string | null;
}

/** The backend's rules, checked before a request so the user sees the problem at once (the server checks again). */
export function drawingProblem(d: DrawingV1): string | null {
  const need = ANCHOR_RULES[d.kind];
  if (!need) return `unknown drawing kind ${String(d.kind)}`;
  if (d.anchors.length !== need.length) return `a ${d.kind} needs ${need.length} anchor(s), got ${d.anchors.length}`;
  for (let i = 0; i < need.length; i++) {
    const a = d.anchors[i];
    if (need[i].includes("t") && (!a.t || Number.isNaN(Date.parse(a.t)))) return `${d.kind} anchor ${i + 1} needs a time`;
    if (need[i].includes("p") && (a.p == null || !Number.isFinite(a.p))) return `${d.kind} anchor ${i + 1} needs a price`;
  }
  if (d.levels && !d.kind.startsWith("fib_")) return "levels apply to Fibonacci drawings only";
  if (d.kind === "text" && !(d.text ?? "").trim()) return "a text drawing needs text";
  if (d.text && /[<>]/.test(d.text)) return "text may not contain < or >";
  if (d.style?.color && !/^#[0-9a-fA-F]{6}$/.test(d.style.color)) return "colour must be #rrggbb";
  return null;
}

/** Thrown when the drawing changed elsewhere (another tab, another device) since this edit was made: 409 with the
 * current drawing, or 404 (deleted elsewhere) with `current` null. */
export class DrawingConflict extends Error {
  readonly current: StoredDrawing | null;

  constructor(current: StoredDrawing | null) {
    super("This drawing changed in another window. The latest version is shown.");
    this.name = "DrawingConflict";
    this.current = current;
  }
}

function conflictFrom(error: unknown): never {
  if (error instanceof ApiError && error.status === 404) throw new DrawingConflict(null);   // deleted elsewhere
  if (error instanceof ApiError && error.status === 409) {
    let current: StoredDrawing | null = null;
    try {
      const body = JSON.parse(error.body) as { detail?: { current?: StoredDrawing } };
      current = body.detail?.current ?? null;
    } catch {
      current = null;
    }
    throw new DrawingConflict(current);
  }
  throw error;
}

const base = "/charts/drawings";
const q = (symbol: string, exchange: string) => `symbol=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exchange)}`;

export const drawingsApi = {
  list: (symbol: string, exchange = "NSE") => request<StoredDrawing[]>(`${base}?${q(symbol, exchange)}`),
  create: (symbol: string, drawing: DrawingV1, exchange = "NSE") =>
    request<StoredDrawing>(base, { method: "POST", body: JSON.stringify({ symbol, exchange, drawing }) }),
  update: (stored: StoredDrawing, drawing: DrawingV1) =>
    request<StoredDrawing>(`${base}/${stored.id}`, { method: "PUT", body: JSON.stringify({ version: stored.version, drawing }) }).catch(conflictFrom),
  lock: (stored: StoredDrawing, locked: boolean) =>
    request<StoredDrawing>(`${base}/${stored.id}/lock`, { method: "POST", body: JSON.stringify({ locked }) }).catch(conflictFrom),
  remove: (stored: StoredDrawing) =>
    request<{ deleted: number }>(`${base}/${stored.id}?version=${stored.version}`, { method: "DELETE" }).catch(conflictFrom),
  exportDoc: (symbol: string, exchange = "NSE") =>
    request<{ format: typeof EXPORT_FORMAT; symbol: string; exchange: string; drawings: DrawingV1[] }>(`${base}/export?${q(symbol, exchange)}`),
  importDoc: (doc: { format: typeof EXPORT_FORMAT; symbol: string; exchange: string; drawings: DrawingV1[] }) =>
    request<{ imported: number; ids: number[] }>(`${base}/import`, { method: "POST", body: JSON.stringify(doc) }),
};
