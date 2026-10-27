/**
 * Analyze a chart: the shapes the API returns (backend app/chart_analyze/contract.py, docs/design/CHART_ANALYZE.md §4).
 * Generic names only: the analysis engine fills them; this app renders them. Image-only coordinates are pixels of the
 * stored image (x right, y down) and every price is approximate.
 */

export type SetupStatus = "formed" | "forming" | "none";
export type AreaSide = "buyer" | "seller";
export type AreaState = "fresh" | "tested" | "broken" | "flipped";
export type SwingLabel = "HH" | "HL" | "LH" | "LL";

export interface Point { x: number; y: number }
export interface Segment { x0: number; y0: number; x1: number; y1: number }
export interface PriceRef { text: string; price: number | null }

export interface AlertSpec { kind: "above" | "below" | "between"; price?: number; low?: number; high?: number }

export interface Annotations {
  swings: (Point & { label: SwingLabel; price: number | null })[];
  areas: (Segment & { id: string; side: AreaSide; state: AreaState; price_low: number | null; price_high: number | null; note: string })[];
  lines: (Segment & { id: string; name: string; touches: Point[]; note: string })[];
  rsi_divergence: { kind: "bullish" | "bearish"; price: Segment; rsi: Segment | null; note: string } | null;
  commitment_candle: (Point & { note: string }) | null;
  ltf_choch: (Point & { note: string }) | null;
  setup_lines: { kind: "trigger" | "invalidation" | "next_area"; y: number; price: number | null; note?: string }[];
}

export interface AnalysisResult {
  contract_version: number;
  mode: "image_only" | "hybrid";
  label: string | null;
  disclaimer: string;
  approximate_prices: boolean;
  coordinate_space: "image_pixels" | "time_price";
  image: { width: number; height: number };
  recognised: { symbol: string | null; exchange: string | null; timeframe: string | null; first_bar: string | null; last_bar: string | null };
  story: string[];
  trend: { side: "up" | "down" | "sideways" | "unclear"; text: string };
  current_state: { phase: string; text: string };
  rsi: { divergence: "bullish" | "bearish" | "none"; text: string };
  setup: {
    side: "bull_put" | "bear_call" | "none"; label: "A" | "B" | null; status: SetupStatus; waiting_for: string | null;
    trigger: PriceRef; invalidation: PriceRef; next_areas: PriceRef[];
  };
  evidence: { name: string; present: boolean; reason: string }[];
  scenarios: { primary: string; alternative: string; primary_invalidated_at: PriceRef };
  what_to_wait_for: { condition: string; alert: AlertSpec | null } | null;
  htf_context: { timeframe: string | null; trend: string; nearest_areas: { side: AreaSide | null; state: AreaState | null; price_low: number | null; price_high: number | null }[] }[];
  key_levels: { kind: "area" | "line"; name: string; side: AreaSide | null; state: AreaState | null; price_low: number | null; price_high: number | null }[];
  annotations: Annotations;
  /** Set on the structure-only copy a share link opens (backend share.py): no name, no price, no date, no picture. */
  shared?: boolean;
  shared_note?: string;
}

/** The owner's live share link (null: not shared, stopped or expired). */
export interface ItemShare { token: string; expires_at: string }

/** GET /chart-analyze/shared/{token}: the structure-only copy a member of the owner's organisation opens. */
export interface SharedAnalysis {
  result: AnalysisResult;
  mode: "image_only" | "hybrid";
  expires_at: string;
  is_yours: boolean;
  disclaimer: string;
}

export type ItemStatus = "ready" | "running" | "done" | "failed" | "deleted";

export interface AnalysisItem {
  id: number;
  mode: "image_only" | "hybrid";
  source: "upload" | "link";
  source_host: string | null;
  status: ItemStatus;
  image: { width: number; height: number; url: string };
  error: string | null;
  provider: string | null;
  model: string | null;
  engine_version: string | null;
  billed_to_user_key: boolean;
  cost_usd: number;
  created_at: string | null;
  analysed_at: string | null;
  summary: { setup_status: SetupStatus; trend: string; symbol: string | null } | null;
  result: AnalysisResult | null;
  /** The live share link (organisation only), when the owner made one. Absent on older answers. */
  share?: ItemShare | null;
  label: string | null;
  disclaimer: string;
}

export interface AnalyzeStatus {
  enabled: boolean;
  /** A1: `reason` and `owner_steps` come only to SUPER_ADMIN, and only while the engine is not installed. */
  engine: { installed: boolean; message?: string; name?: string; version?: string; reason?: string; owner_steps?: string };
  modes: { image_only: boolean; hybrid: boolean };
  hybrid_note: string;
  limits: { tier: string; daily_limit: number; monthly_limit: number; daily_used: number; monthly_used: number; cost_cap_usd: number; cost_used_usd: number; resets: string };
  upload: { types: string[]; max_bytes: number; max_width: number; max_height: number };
  retention_days: number;
  label: string;
  disclaimer: string;
  link_message: string;
}

/** GET /chart-analyze/items/{id}/strikes: the option strikes the user's OWN deployment settings work out to at the
 * setup's trigger level (backend app/chart_analyze/strikes.py). Always flagged "from your settings". */
export interface StrikeLeg { label: string; right: "CE" | "PE"; strike: number; ratio: number }

export interface StrikesItem {
  id: number;
  mode: string;
  symbol: string;
  settings: string;
  expiry: string | null;
  legs: StrikeLeg[] | null;
  note: string | null;
}

export interface StrikesAnswer {
  source: "your_settings";
  side: "bull_put" | "bear_call" | "none";
  symbol: string | null;
  reference: { text: string; price: number | null; approximate: boolean };
  approximate_note: string | null;
  items: StrikesItem[];
  message: string | null;
}
