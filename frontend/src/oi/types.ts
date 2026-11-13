/** OI Banner (O3): the shapes served by /api/option-chain/{underlying}/banner|history|strikes|settings. */
export type OiDirection = "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL";

export interface OiBannerRow {
  slot: string;
  data_as_of: string;
  direction: OiDirection;
  message: string;
  put_class: string;
  call_class: string;
  signal: string;
  diff: number;
  delta_diff: number;
  total_call_oi: number;
  total_put_oi: number;
  pcr: number | null;
  pcr_band: string | null;
  pcr_label: string;
  underlying_price: number;
  atm_strike: number;
  strike_step: number;
  max_pain: number | null;
  expiry: string | null;
  dte: number | null;
  first_of_day: boolean;
  /** M96 L1: the Trade banner's message in neutral fact words (backend oi_words.trade_fact_message). */
  fact_message?: string;
  /** M61 PCR-1 (history rows): the reading's session phase on the exchange calendar, its strike count (strikes with OI
   * on both sides) and whether it is partial (below the session's usual count). Absent on older answers. */
  phase?: "session" | "pre_open" | "after_close" | "closed_day";
  strikes?: number;
  partial?: boolean;
}

export interface OiFreshness {
  market_open: boolean;
  stale: boolean;
  age_minutes: number | null;
  stale_after_minutes: number;
}

export interface OiBannerResponse extends OiFreshness {
  underlying: string;
  date: string;
  banner: OiBannerRow | null;
  message?: string;
}

export interface OiHistoryResponse extends OiFreshness {
  underlying: string;
  date: string;
  interval: number;
  /** Owner 13 Nov: `bucket` = Trade's History "Time", the start of the row's 5 / 10 / 15 minute bucket (IST ISO). */
  rows: (OiBannerRow & { bucket?: string; opposite?: OiOppositeMark })[];
}

export interface OiStrikeSeries {
  strike: number;
  points: { slot: string; call_oi: number | null; put_oi: number | null }[];
  baseline_call_oi: number | null;
  baseline_put_oi: number | null;
}

export interface OiStrikesResponse extends OiFreshness {
  underlying: string;
  date: string;
  strikes: OiStrikeSeries[];
  data_as_of: string | null;
}

export interface OiSettingsResponse {
  underlying: string;
  enabled: boolean;
  exchange: string;
  overrides: Record<string, unknown>;
  effective: Record<string, unknown>;
  updated_at: string | null;
}

export const OI_ALERT_TYPES = ["DIRECTION_CHANGE", "STABLE_FLIP", "STRENGTH_CHANGE", "PCR_BAND", "MAX_PAIN_MOVE", "OI_WALL", "DTE", "COLLECTOR_STALE"] as const;

export interface OiAlertSettings {
  enabled: boolean;
  types: string[];
  cooldown_minutes: number;
  max_pain_strikes: number;
  dte_milestones: number[];
  quiet_start: string | null;
  quiet_end: string | null;
  digest_time: string | null;
  /** M96 N1: the user's own per-strike OI change levels (none until he adds one). */
  strike_rules?: OiStrikeRule[];
  /** M96 N1: the user's own PCR level (none until he types one). */
  pcr_level?: number | null;
  /** Owner 13 Nov: the opposite call / put OI move alert (OPPOSITE_OI in `types`, per symbol from the bell). */
  opposite_window_minutes?: 5 | 10 | 15;
  opposite_min_change_pct?: number;
  opposite_relative_k?: number;
  opposite_relative_windows?: number;
}

/** M96 N1: notify when this strike's call (CE) or put (PE) OI change since the day's first reading reaches `threshold`. */
export interface OiStrikeRule { strike: number; side: "CE" | "PE"; threshold: number }

/** M96 N1: the threshold alert types (managed in their own card, src/optionChain/OiThresholdAlertsV2.tsx). */
export const OI_THRESHOLD_ALERT_TYPES = ["STRIKE_OI_CHANGE", "PCR_CROSS", "OPPOSITE_OI"] as const;

/** Owner 13 Nov: a History row whose window closed with an opposite call / put OI move (his own levels). */
export interface OiOppositeMark { call_change: number; put_change: number; window: number }

export interface OiAlertLogResponse {
  underlying: string;
  snoozed_until: string | null;
  alerts: { alert_type: string; old_state: string | null; new_state: string; status: string; slot: string; at: string; notification_id: number | null }[];
}
