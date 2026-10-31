import type { OiBannerResponse, OiBannerRow, OiHistoryResponse, OiStrikesResponse } from "../oi/types";

/**
 * M61: a made-up OI day for the OI tab's tests and screenshots (round invented numbers, no market data): an underlying
 * at 23,050 on 30 Oct 2026, two pre-open readings (06:45, 06:50, yesterday's chain), session readings every 5 minutes
 * from 09:15 whose OI stops changing at 15:25, a partial reading at 11:50 (fewer strikes), and after-close readings
 * that repeat the last one. The history is newest first, as the server sends it.
 */
const DAY = "2026-10-30";
const ist = (hh: number, mm: number) => `${DAY}T${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:00+05:30`;

function row(hh: number, mm: number, call: number, put: number, over: Partial<OiBannerRow> = {}): OiBannerRow {
  return {
    slot: ist(hh, mm), data_as_of: ist(hh, mm), direction: "NEUTRAL", message: "", put_class: "", call_class: "", signal: "",
    diff: put - call, delta_diff: 0, total_call_oi: call, total_put_oi: put, pcr: Math.round((put / call) * 100) / 100, pcr_band: null,
    pcr_label: "", underlying_price: 23_050, atm_strike: 23_050, strike_step: 50, max_pain: 23_000, expiry: "2026-11-03", dte: 4,
    first_of_day: false, phase: "session", strikes: 31, partial: false, ...over,
  };
}

function day(): OiBannerRow[] {
  const out: OiBannerRow[] = [row(6, 45, 4_80_00_000, 5_10_00_000, { phase: "pre_open" }), row(6, 50, 4_80_00_000, 5_10_00_000, { phase: "pre_open" })];
  let call = 4_85_00_000, put = 4_95_00_000;
  for (let m = 9 * 60 + 15; m <= 15 * 60 + 30; m += 5) {
    const hh = Math.floor(m / 60), mm = m % 60;
    if (m <= 15 * 60 + 25 && m % 15 !== 5) { call += 4_10_000 + (m % 7) * 35_000; put += 4_90_000 + (m % 11) * 41_000; }
    if (hh === 11 && mm === 50) { out.push(row(hh, mm, call - 1_10_00_000, put - 40_00_000, { partial: true, strikes: 24 })); continue; }
    out.push(row(hh, mm, call, put));
  }
  for (let m = 15 * 60 + 35; m <= 16 * 60; m += 5) out.push(row(Math.floor(m / 60), m % 60, call, put, { phase: "after_close" }));
  return out.reverse();
}

export const OI_HISTORY: OiHistoryResponse = {
  underlying: "NIFTY", date: DAY, interval: 5, rows: day(), market_open: false, stale: false, age_minutes: 420, stale_after_minutes: 15,
};

const LAST = OI_HISTORY.rows.find((r) => r.phase === "session")!;

export const OI_BANNER: OiBannerResponse = {
  underlying: "NIFTY", date: DAY, banner: { ...LAST, data_as_of: ist(16, 0), slot: ist(16, 0) }, market_open: false, stale: false,
  age_minutes: 400, stale_after_minutes: 15,
};

const STRIKES = [22_800, 22_850, 22_900, 22_950, 23_000, 23_050, 23_100, 23_150, 23_200, 23_250, 23_300];
const CALL = [6_10_000, 9_40_000, 12_75_000, 16_20_000, 21_30_000, 18_45_000, 22_60_000, 19_80_000, 27_49_000, 17_10_000, 24_35_000];
const PUT = [29_80_000, 22_15_000, 31_60_000, 26_40_000, 49_44_000, 35_20_000, 21_75_000, 14_30_000, 9_85_000, 6_20_000, 4_10_000];

export const OI_STRIKES: OiStrikesResponse = {
  underlying: "NIFTY", date: DAY, data_as_of: ist(15, 30), market_open: false, stale: false, age_minutes: 420, stale_after_minutes: 15,
  strikes: STRIKES.map((strike, i) => ({
    strike, baseline_call_oi: CALL[i] - 2_00_000, baseline_put_oi: PUT[i] - 3_00_000,
    points: [{ slot: ist(9, 15), call_oi: CALL[i] - 2_00_000, put_oi: PUT[i] - 3_00_000 }, { slot: ist(15, 30), call_oi: CALL[i], put_oi: PUT[i] }],
  })),
};
