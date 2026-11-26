import { ApiError } from "../api/errors";
import type { WatchItem } from "../chart/watchlistsV2";

export const MAX_BREAKOUT_INSTRUMENTS = 10;
const EQUITY_KEY = /^NSE:[A-Z0-9 &_.-]{1,50}$/;
const INDEX_KEY = /^NSE_INDEX:[A-Z0-9]{1,40}$/;
const INDEX_ALIASES: Record<string, string> = {
  NIFTY: "NIFTY50", NIFTY50: "NIFTY50",
  BANKNIFTY: "NIFTYBANK", NIFTYBANK: "NIFTYBANK",
  NIFTYFINSERVICE: "NIFTYFINSERVICE", FINNIFTY: "NIFTYFINSERVICE",
  NIFTYFINANCIALSERVICES: "NIFTYFINSERVICE",
  MIDCPNIFTY: "NIFTYMIDCAPSELECT", NIFTYMIDSELECT: "NIFTYMIDCAPSELECT",
};

export function breakoutInstrumentKey({ exchange, symbol }: WatchItem): string | null {
  const value = symbol.trim().toUpperCase().replace(/\s+/g, " ");
  if (!value) return null;
  const indexCode = value.replace(/[^A-Z0-9]/g, "").slice(0, 40);
  const venue = exchange.toUpperCase();
  const alias = INDEX_ALIASES[indexCode];
  const key = venue === "NSE_INDEX" || (venue === "NSE" && alias)
    ? `NSE_INDEX:${alias ?? indexCode}` : venue === "NSE" ? `NSE:${value}` : "";
  return EQUITY_KEY.test(key) || INDEX_KEY.test(key) ? key : null;
}

export function instrumentKeys(value: string): string[] {
  return value.split(",").map((key) => key.trim().toUpperCase()).filter(Boolean);
}

export function appendInstruments(existing: string[], picks: readonly WatchItem[]) {
  const keys = new Set(existing);
  let unsupported = 0;
  let overLimit = 0;
  for (const item of picks) {
    const key = breakoutInstrumentKey(item);
    if (!key) unsupported += 1;
    else if (!keys.has(key) && keys.size >= MAX_BREAKOUT_INSTRUMENTS) overLimit += 1;
    else keys.add(key);
  }
  return { keys: [...keys], unsupported, overLimit };
}

export type FieldErrors = Record<string, string[]>;

export function breakoutFieldErrors(error: unknown): FieldErrors {
  if (!(error instanceof ApiError) || error.status !== 422) return {};
  try {
    const detail: unknown = JSON.parse(error.body).detail;
    if (!Array.isArray(detail)) return {};
    const fields: FieldErrors = {};
    for (const issue of detail) {
      if (!Array.isArray(issue?.loc) || typeof issue.msg !== "string") continue;
      const loc = issue.loc.filter((part: unknown) => typeof part === "string" || typeof part === "number");
      const name = loc[0] === "instruments" ? "instruments" : loc.join(".");
      if (!name) continue;
      const message = issue.msg.replace(/^Value error,\s*/i, "");
      fields[name] = [...new Set([...(fields[name] ?? []), message])];
    }
    return fields;
  } catch {
    return {};
  }
}
