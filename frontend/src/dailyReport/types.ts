/** M88-R: the shared daily market report as the server stores it (`/api/daily-report/latest`). */
export interface ReportArea { where: "inside" | "above" | "below"; kind: string; low: number; high: number; timeframe: string; state: string }
export interface ReportStructure { direction: string; words: string; last_event?: string | null; swing_high: number | null; swing_low: number | null }
export interface ReportRow {
  symbol: string; name: string; kind: "INDEX" | "EQ"; sector: string | null; missing: boolean;
  open?: number | null; high?: number | null; low?: number | null; close?: number | null; prev_close?: number | null; change_pct?: number | null;
  volume?: number | null; volume_ratio?: number | null; delivery_pct?: number | null;
  recent_high?: number | null; recent_low?: number | null; new_recent_high?: boolean; new_recent_low?: boolean; range_position_pct?: number | null;
  structure_d?: ReportStructure; structure_w?: ReportStructure; swing_high_pct?: number | null; swing_low_pct?: number | null;
  areas?: ReportArea[]; fut_oi?: number | null; fut_oi_change_pct?: number | null; pcr?: number | null; read?: string | null;
}
export interface ReportBreadth {
  advances: number; declines: number; unchanged: number; stocks: number; new_recent_high: number; new_recent_low: number;
  new_recent_high_pct: number | null; new_recent_low_pct: number | null;
}
export interface ReportSector { sector: string; stocks: number; avg_change_pct: number | null; advances: number; declines: number }
export type SectionKey = "index_read" | "breadth_read" | "sectors_read" | "leaders_laggards" | "notable_structures" | "conclusion";
export interface DailyReport {
  id: number; trade_date: string; info_line: string; generated_at: string;
  index: ReportRow[]; stocks: ReportRow[]; breadth: ReportBreadth; sectors: ReportSector[];
  leaders: string[]; laggards: string[]; missing: string[]; sections: Record<SectionKey, string>;
}

export interface ReportRun {
  id: number; trade_date: string; status: "running" | "ok" | "failed"; error: string | null; attempts: number; ai_requests: number;
  input_tokens: number; output_tokens: number; tokens: number; cost_usd: number; cost_inr: number; symbols: number;
  trigger: "schedule" | "run_now"; stored: boolean; next_try_at: string | null; updated_at: string | null;
}
export interface ReportAdmin {
  settings: { symbols: string[]; recent_bars: number }; flag_on: boolean; key: { connected: boolean; message: string | null };
  runs: ReportRun[]; examples: string[];
}
