/**
 * M97 PR-2: the Supply & Demand scanners as the server sends them (GET /api/screener/supply-demand - flag `sd_pack`, off
 * by default: the call then fails and the gallery shows no settings for them). The payload has the intraday scanners'
 * shape (the same Param / Scanner on the server), so the gallery's settings table and its helpers are shared.
 *
 * Signals only: a scanner lists matches; it never says what to trade.
 */
export type { IntradayLibrary as SdLibrary, IntradayScanner as SdScanner } from "./intraday";

/** The library source the pack's screens are filed under, and the gallery chip that shows only them. */
export const SD_SOURCE = "supply_demand";
export const SD_CHIP = "Supply & Demand";
