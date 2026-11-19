/**
 * M86 W3: the chart reader's answers (backend app/chart_reader/routes.py). The UI shows only what the API returns; the
 * reading method itself is not in this repository.
 */

/** Owner 18 Nov: a book strategy's own rules, shown as bullets (the book's text with its page numbers). */
export interface StrategyRules {
  summary: string;
  sides: string[];
  conditions: { id: string; text: string; page: string; two_chart: boolean }[];
  trigger: { text: string; page: string; stated: boolean };
  invalidation: { text: string; page: string }[];
  not_checked: string[];
}

/** Owner 18 Nov: the book read's levels drawn on ATP's chart of the stored candles (bands tagged L1, L2 ...). */
export interface BookChart {
  png_base64: string;
  width: number;
  height: number;
  label: string;
  tags: string[];
}

export interface ReaderStrategy {
  id: string;
  name: string;
  book: string;
  author: string;
  edition: string;
  two_chart: boolean;
  sides: string[];
  rules?: StrategyRules;
}

/** GET /api/chart-reader/strategies. For the platform owner `own_method` is true and `default` is his own method (listed
 * first, name "My method"); for everyone else it is omitted from the list and `default` is the first book strategy. */
export interface ReaderStrategies {
  available: boolean;
  /** One plain line when the reader is not available (not installed / not connected), else null. */
  message: string | null;
  /** The private reader is installed on this server / the caller has his own Claude key (owner 7 Nov: own key only). */
  installed?: boolean;
  connected?: boolean;
  default: string | null;
  /** Owner 15 Nov: the caller may use the owner's own method (the platform owner only). Missing = no. */
  own_method?: boolean;
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
  book_chart?: BookChart | null;
  error?: string;
  category?: string;
}

/** CA-CHAT (owner 18 / 19 Nov): one condition the trader wrote, judged on his analysis's chart. */
export interface ChatAnswer {
  condition: string;
  status: ConditionStatus;
  evidence: string;
  /** The price area it points at, when the chart has a price scale (else null). */
  price_low: number | null;
  price_high: number | null;
}

export interface ChatTurn { question: string; answers: ChatAnswer[]; cost_usd: number; at: string; unreadable?: boolean }
export interface ChatThread { turns: ChatTurn[]; max_turns: number; estimate_usd: number }
export interface ChatAsk { turn: ChatTurn; turns: number; estimate_usd: number }
