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
  rows: OiBannerRow[];
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
