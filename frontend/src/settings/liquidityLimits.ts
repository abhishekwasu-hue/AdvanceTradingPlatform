/**
 * FNO-8B-28 (owner decision 23 Oct): "My own liquidity limits" for F&O - an optional override, OFF by default.
 *
 * The default stays the comparison with the chain on screen (no fixed number). When he turns his own limits on - the
 * widest bid-ask gap he accepts (% of the price) and the least open interest he accepts - the "Before you place"
 * liquidity line also says whether the strike is inside them (src/fno/riskParts.ts limitWords). Information only: no
 * button reads it, it never holds back an order and exits are never touched.
 *
 * Stored on the server per user (`/api/me/prefs`, namespace `fno`, backend app/me/prefs.py FnoPrefs / LiquidityLimits).
 * Set on Settings → My trading defaults (src/settings/TradingDefaultsV2.tsx), beside the trading defaults.
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";

export const FNO_NAMESPACE = "fno";
export const MAX_MIN_OI = 1_000_000_000;

export interface LiquidityLimits {
  on: boolean;
  /** The widest bid-ask gap he accepts, as a percent of the price (above 0, up to 100). */
  max_spread_pct: number | null;
  /** The least open interest he accepts (a whole number from 1). */
  min_oi: number | null;
}

export const LIMITS_OFF: LiquidityLimits = { on: false, max_spread_pct: null, min_oi: null };

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The server's value read defensively: a value out of range is taken as "not set"; on without a limit is off. */
export function readLimits(raw: unknown): LiquidityLimits {
  const r = (raw && typeof raw === "object" ? (raw as Record<string, unknown>).liquidity : null) as Record<string, unknown> | null | undefined;
  if (!r || typeof r !== "object") return LIMITS_OFF;
  const spread = num(r.max_spread_pct) && r.max_spread_pct > 0 && r.max_spread_pct <= 100 ? r.max_spread_pct : null;
  const oi = num(r.min_oi) && Number.isInteger(r.min_oi) && r.min_oi >= 1 && r.min_oi <= MAX_MIN_OI ? r.min_oi : null;
  return { on: r.on === true && (spread !== null || oi !== null), max_spread_pct: spread, min_oi: oi };
}

/** The limits a confirm step uses: null while off (the default comparison alone). */
export function activeLimits(l: LiquidityLimits | null | undefined): LiquidityLimits | null {
  return l && l.on && (l.max_spread_pct !== null || l.min_oi !== null) ? l : null;
}

// ---------------------------------------------------------------- the settings page's own fields

export interface LimitsForm { on: boolean; max_spread_pct: string; min_oi: string }

export const limitsFormOf = (l: LiquidityLimits): LimitsForm => ({
  on: l.on, max_spread_pct: l.max_spread_pct === null ? "" : String(l.max_spread_pct), min_oi: l.min_oi === null ? "" : String(l.min_oi),
});

/** The fields as limits, with a plain sentence per value that cannot be saved. While off, the fields are hidden, so a
 * value that cannot be saved is simply dropped (never a problem he cannot see). */
export function limitsOf(f: LimitsForm): { value: LiquidityLimits; problems: string[] } {
  const problems: string[] = [];
  const s = f.max_spread_pct.replace(/,/g, "").trim();
  const o = f.min_oi.replace(/,/g, "").trim();
  let spread: number | null = null;
  let oi: number | null = null;
  if (s) {
    const v = Number(s);
    if (Number.isFinite(v) && v > 0 && v <= 100) spread = v;
    else if (f.on) problems.push("Max bid-ask gap: a number above 0, up to 100.");
  }
  if (o) {
    const v = Number(o);
    if (Number.isInteger(v) && v >= 1 && v <= MAX_MIN_OI) oi = v;
    else if (f.on) problems.push("Min open interest: a whole number from 1.");
  }
  if (f.on && !problems.length && spread === null && oi === null) problems.push("Set a max bid-ask gap or a min open interest to use your own limits.");
  return { value: { on: f.on, max_spread_pct: spread, min_oi: oi }, problems };
}

// ---------------------------------------------------------------- reading and saving

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

export async function loadLimits(fetcher: typeof request = request): Promise<{ value: LiquidityLimits; version: number }> {
  const r = await fetcher<PrefsRead>("/me/prefs");
  return { value: readLimits(r.prefs?.[FNO_NAMESPACE]), version: r.versions?.[FNO_NAMESPACE] ?? 0 };
}

export async function saveLimits(value: LiquidityLimits, version: number | null, fetcher: typeof request = request):
  Promise<{ value: LiquidityLimits; version: number }> {
  const q = version === null ? "" : `?version=${version}`;
  const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/${FNO_NAMESPACE}${q}`, {
    method: "PUT", body: JSON.stringify({ liquidity: value }),
  });
  const saved = { value: readLimits(r.value), version: r.version };
  cached = Promise.resolve(saved.value);
  return saved;
}

let cached: Promise<LiquidityLimits | null> | null = null;

/** For tests: forget the copy this tab read. */
export function resetLimitsCache(): void {
  cached = null;
}

/** His limits for a confirm step (read once per tab, refreshed by a save). Null while not known, when off (`enabled`
 * false) or on a failed read: the liquidity line is then the default comparison alone. */
export function useLiquidityLimits(enabled: boolean, fetcher: typeof request = request): LiquidityLimits | null {
  const [value, setValue] = useState<LiquidityLimits | null>(null);
  useEffect(() => {
    if (!enabled) { setValue(null); return undefined; }
    let live = true;
    if (!cached) cached = loadLimits(fetcher).then((r) => r.value).catch(() => { cached = null; return null; });
    void cached.then((v) => { if (live) setValue(v); });
    return () => { live = false; };
  }, [enabled, fetcher]);
  return value;
}
