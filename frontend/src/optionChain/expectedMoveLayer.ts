/**
 * M96 N1: "Expected move (from the option prices)" on the price chart - a layer in the Charts page's Indicators menu
 * (Layers), off until the user turns it on. Two bands around the spot to the nearest expiry:
 * - from the ATM straddle: spot ± (ATM call + put price);
 * - from the ATM IV: spot ± spot × IV × sqrt(calendar days to the expiry close / 365).
 * The prices are the user's own broker chain (the Option chain page's read, its cache and rate budget); with no broker,
 * the last close from NSE's F&O day file (straddle band only, labelled "last close"). A range the option prices imply,
 * not a forecast and not a level to act on. Same drawing path as the other layers (`engine.setLayer`).
 */
import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import { eodApi } from "../api/eod";
import type { ChartEngine, LayerData } from "../charting/engine";
import { formatDateIST, formatNumber, formatTimeIST } from "../ui/format";
import { daysToExpiryClose, eodStraddleOf, expectedMove, straddleOf, type ExpectedMoveRange } from "./history";

export const OPTIONS_EM_LAYER_ID = "options_expected_move";
export const OPTIONS_EM_LABEL = "Expected move (from the option prices)";
const COLOR = "#2563eb";
const REFRESH_MS = 60_000;

export interface OptionsMove { range: ExpectedMoveRange; spot: number; expiry: string | null; asOf: string | null; source: "live" | "last_close" }

/** The two bands (IV-based faint and dotted under the straddle one); null when neither can be priced. */
export function optionsMoveLayer(m: OptionsMove): LayerData | null {
  const bands = [];
  if (m.range.iv) bands.push({ low: m.range.iv.low, high: m.range.iv.high, color: COLOR, opacity: 0.06, style: "dotted" as const, label: "Expected move · IV" });
  if (m.range.straddle) bands.push({ low: m.range.straddle.low, high: m.range.straddle.high, color: COLOR, opacity: 0.12, style: "dashed" as const, label: "Expected move · straddle" });
  return bands.length ? { asOf: m.asOf ?? undefined, bands } : null;
}

/** The one line under the chart. */
export function optionsMoveText(m: OptionsMove | null, failed: string | null): string | null {
  if (failed) return failed;
  if (!m) return null;
  const head = `${OPTIONS_EM_LABEL}${m.expiry ? ` to ${formatDateIST(m.expiry, { year: false })}` : ""}:`;
  const parts: string[] = [];
  if (m.range.straddle) parts.push(`straddle ${formatNumber(m.range.straddle.low)}–${formatNumber(m.range.straddle.high)}`);
  if (m.range.iv) parts.push(`IV ${formatNumber(m.range.iv.low)}–${formatNumber(m.range.iv.high)}`);
  if (!m.range.straddle && !m.range.iv) parts.push("the ATM prices are not available");
  parts.push(m.source === "last_close" ? `last close ${m.asOf ? formatDateIST(m.asOf, { year: false }) : ""}`.trim() : m.asOf ? `as of ${formatTimeIST(m.asOf)}` : "");
  return `${head} ${parts.filter(Boolean).join(" · ")}`;
}

async function readMove(symbol: string): Promise<OptionsMove> {
  try {
    const live = await api.optionChainLive(symbol);
    const spot = live.summary.spot;
    if (spot === null) throw new Error("no spot");
    const asOf = live.data_as_of ?? live.fetched_at;
    const range = expectedMove({ spot, straddle: straddleOf(live), ivPct: live.summary.atm_iv, days: daysToExpiryClose(live.expiry, Date.parse(asOf)) });
    return { range, spot, expiry: live.expiry, asOf, source: "live" };
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 409)) throw e;          // 409: no broker session - the last close instead
  }
  const eod = await eodApi.chain(symbol);
  if (eod.spot === null) throw new Error("no spot");
  return { range: expectedMove({ spot: eod.spot, straddle: eodStraddleOf(eod), ivPct: null, days: null }), spot: eod.spot, expiry: eod.expiry,
    asOf: eod.date, source: "last_close" };
}

/** Reads and draws the layer while `on`; returns the line under the chart (null while off). */
export function useOptionsExpectedMove(engine: ChartEngine | null, symbol: string, on: boolean, marketOpen: boolean | null): string | null {
  const [move, setMove] = useState<OptionsMove | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(null);
    if (!on || !symbol) { setMove(null); return () => { live = false; }; }
    readMove(symbol).then((m) => { if (live) setMove(m); },
      () => { if (live) { setMove(null); setFailed(`${OPTIONS_EM_LABEL}: no option prices for ${symbol}.`); } });
    return () => { live = false; };
  }, [on, symbol, tick]);
  useEffect(() => {
    if (!on || marketOpen !== true) return undefined;
    const id = window.setInterval(() => setTick((t) => t + 1), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [on, marketOpen]);
  const data = useMemo(() => (on && move ? optionsMoveLayer(move) : null), [on, move]);
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(OPTIONS_EM_LAYER_ID, data);
    return () => engine.setLayer(OPTIONS_EM_LAYER_ID, null);
  }, [engine, data]);
  return on ? optionsMoveText(move, failed) : null;
}
