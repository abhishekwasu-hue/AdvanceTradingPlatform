/**
 * M86 W3: the chart reader's answers (backend app/chart_reader/routes.py). The UI shows only what the API returns; the
 * reading method itself is not in this repository.
 */

export interface ReaderStrategy {
  id: string;
  name: string;
  book: string;
  author: string;
  edition: string;
  two_chart: boolean;
  sides: string[];
}

/** GET /api/chart-reader/strategies. `default` is "ABHI" (listed first, name "Abhi's method"). */
export interface ReaderStrategies {
  available: boolean;
  /** One plain line when the reader is not available (not installed / not connected), else null. */
  message: string | null;
  default: string;
  model: string;
  effort: string;
  estimate_usd: number;
  /** The estimate when the paired chart is attached (a strategy with two-chart conditions, stored candles). */
  estimate_usd_two_chart?: number;
  /** The estimate for one uploaded chart picture. */
  estimate_usd_upload?: number;
  strategies: ReaderStrategy[];
}

export type ReaderSource = "lake" | "upload";

export interface ReaderRequest {
  symbol?: string;
  strategy: string;
  source: ReaderSource;
  item_id?: number;
}

export type Tri = "yes" | "no" | "unclear";

export interface AbhiArea {
  side: "buyer" | "seller";
  price_low: number | null;
  price_high: number | null;
  chart: string;
  evidence: string;
}

export interface AbhiRead {
  trend: { direction: "up" | "down" | "sideways" | "unclear" | string; evidence: string };
  areas: AbhiArea[];
  correction: { present: boolean; area_index: number | null; momentum_weakening: Tri; evidence: string };
  commitment_candle: { present: boolean; evidence: string };
  area_of_interest_statement: string;
  watch_next: string;
  cannot_read: string | null;
}

export type ConditionStatus = "met" | "not_met" | "unclear";

export interface BookRead {
  side_scored: "long" | "short";
  conditions: { id: string; status: ConditionStatus; evidence: string }[];
  levels: { kind: string; price_low: number | null; price_high: number | null; chart: string; evidence: string }[];
  trigger_seen: { status: Tri; bar: number | null; evidence: string };
  invalidated: { status: Tri; evidence: string };
  statement: string;
  cannot_read: string | null;
}

export interface ReaderUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  model?: string;
}

/** POST /api/chart-reader/read. `read` is an AbhiRead for "ABHI" and a BookRead for a book strategy. */
export interface ReaderResult<R = AbhiRead | BookRead> {
  status: "ok" | "refused" | "error";
  strategy: { id: string; name: string; independent_book_strategy?: boolean };
  prompt_version: string;
  read: R | null;
  removed: string[];
  time_flags: string[];
  two_chart_conditions: string[];
  second_chart_missing: boolean;
  usage: ReaderUsage;
  cached: boolean;
  estimate_usd: number;
  cost_usd: number;
  label: "Advanced analysis (code + AI)" | "AI only" | string;
  error?: string;
  category?: string;
}
