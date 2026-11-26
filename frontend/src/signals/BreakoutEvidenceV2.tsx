import { InPlaceLink } from "../inplace/InPlaceLink";
import { chartTabPath } from "../shell/shellRoutes";
import { timeframeLabel, type TimeframeId } from "../chart/timeframes";
import { formatDateTimeIST, formatNumber, formatPrice } from "../ui/format";
import type { BreakoutEvidence, SignalFeedItem } from "./feed";
import { reasonWords } from "./signalText";

const CHART_TIMEFRAMES: Record<string, TimeframeId> = {
  "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day",
};
const FACTORS: Record<string, string> = {
  level: "Breakout level", candle: "Candle quality", structure: "Market structure", momentum: "Momentum",
  rsi: "RSI divergence", retest: "Retest", zones: "Room to the next zone", mtf: "Higher-timeframe alignment",
};
const ACCEPTANCE = {
  DIRECT_BREAKOUT: "Breakout close", FOLLOW_THROUGH: "Follow-through close", RETEST_BREAKOUT: "Retest confirmed",
} as const;
const VOLUME_SOURCE = { genuine: "Instrument volume", proxy: "Futures volume", unavailable: "Unavailable" } as const;
const LEVEL_SOURCE = {
  B1: "Price range", B2: "Confirmed swing", HORIZONTAL: "Support or resistance",
  ZONE: "Demand or supply boundary", SESSION: "Previous session high or low",
} as const;

function timeFrameText(value: string) {
  const chartTf = CHART_TIMEFRAMES[value];
  return chartTf ? timeframeLabel(chartTf) : value;
}

function ratio(value: number) {
  return `${formatNumber(value, 2, { trim: true })} ATR`;
}

function facts(evidence: BreakoutEvidence, direction: SignalFeedItem["direction"]): [string, string][] {
  const rows: [string, string][] = [];
  if (evidence.timeframe) rows.push(["Breakout timeframe", timeFrameText(evidence.timeframe)]);
  if (evidence.sources?.length) rows.push(["Breakout level source", evidence.sources.map((source) => LEVEL_SOURCE[source]).join(", ")]);
  if (evidence.detected_at) rows.push(["Detected", formatDateTimeIST(evidence.detected_at, { seconds: true })]);
  if (evidence.confirmed_at) rows.push(["Confirmed", formatDateTimeIST(evidence.confirmed_at, { seconds: true })]);
  if (evidence.available_at && evidence.available_at !== evidence.confirmed_at) {
    rows.push(["Available", formatDateTimeIST(evidence.available_at, { seconds: true })]);
  }
  if (evidence.published_at && evidence.published_at !== evidence.confirmed_at) {
    rows.push(["Published", formatDateTimeIST(evidence.published_at, { seconds: true })]);
  }
  if (evidence.breakout_level != null) rows.push(["Broken level", formatPrice(evidence.breakout_level)]);
  if (evidence.reference_price != null) rows.push(["Confirmation close", formatPrice(evidence.reference_price)]);
  if (evidence.previous_atr != null) rows.push(["Previous ATR", formatPrice(evidence.previous_atr)]);
  if (evidence.buffer != null) rows.push(["Breakout buffer", ratio(evidence.buffer)]);
  if (evidence.trigger_ohlc) {
    const { open, high, low, close } = evidence.trigger_ohlc;
    rows.push(["Breakout candle", `O ${formatPrice(open)} · H ${formatPrice(high)} · L ${formatPrice(low)} · C ${formatPrice(close)}`]);
  }
  if (evidence.clearance != null) rows.push(["Opposing zone distance", formatPrice(evidence.clearance)]);
  if (evidence.acceptance) rows.push(["Confirmation", ACCEPTANCE[evidence.acceptance]]);
  if (evidence.candle_body_fraction != null) {
    rows.push(["Candle body", `${formatNumber(evidence.candle_body_fraction * 100, 1, { trim: true })}% of the range`]);
  }
  if (evidence.close_from_edge_fraction != null) {
    rows.push(["Close from candle edge", `${formatNumber(evidence.close_from_edge_fraction * 100, 1, { trim: true })}% of the range`]);
  }
  if (evidence.extension_atr != null) rows.push(["Distance beyond the breakout", ratio(evidence.extension_atr)]);
  const area = evidence.opposing_area;
  if (area) {
    const label = direction === "LONG" ? "Nearest supply zone" : direction === "SHORT" ? "Nearest demand zone" : "Nearest opposing zone";
    rows.push([label, `${formatPrice(area.low)}–${formatPrice(area.high)} · ${timeFrameText(area.timeframe)}`]);
  } else if (evidence.zone_reason === "NO_OPPOSING_ZONE_IN_SCOPE") {
    rows.push(["Opposing zone", "None within the area checked"]);
  }
  if (evidence.clearance_atr != null) rows.push(["Room to the next zone", ratio(evidence.clearance_atr)]);
  if (evidence.divergence_conflict || evidence.opposing_divergence) {
    rows.push(["RSI divergence", evidence.divergence_conflict ? "Mixed readings" : "Opposing reading"]);
  } else if (evidence.compatible_divergence) {
    rows.push(["RSI divergence", "Supports the breakout direction"]);
  }
  if (evidence.volume_source) {
    const source = VOLUME_SOURCE[evidence.volume_source];
    rows.push(["Volume evidence", evidence.volume_confirmed && evidence.volume_source !== "unavailable" ? `${source} · confirmed` : source]);
  }
  const zoneTimeframes = direction === "LONG" ? evidence.supply_tfs : direction === "SHORT" ? evidence.demand_tfs : undefined;
  if (zoneTimeframes?.length) rows.push(["Opposing zones checked", zoneTimeframes.map(timeFrameText).join(", ")]);
  if (evidence.rsi_tfs?.length && (evidence.compatible_divergence || evidence.opposing_divergence)) {
    rows.push(["RSI timeframes checked", evidence.rsi_tfs.map(timeFrameText).join(", ")]);
  }
  return rows;
}

export function BreakoutEvidenceV2({ item }: { item: SignalFeedItem }) {
  const evidence = item.breakout;
  const rows = evidence ? facts(evidence, item.direction) : [];
  const reasons = reasonWords(item.reasons);
  const chartTf = evidence?.timeframe ? CHART_TIMEFRAMES[evidence.timeframe] : undefined;
  return (
    <div className="space-y-space-3" data-breakout-evidence>
      <h3 className="text-t15 font-semibold text-fg">Why this signal</h3>
      {evidence?.score != null && <p className="font-tabular text-t14 text-fg">Signal quality {formatNumber(evidence.score)} / 100</p>}
      {reasons.length > 0 && <ul className="list-disc space-y-space-1 pl-space-5 text-t14 text-fg">{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      {rows.length > 0 && (
        <dl className="grid gap-space-2 sm:grid-cols-2">
          {rows.map(([label, value]) => (
            <div key={label} className="min-w-0 rounded-control border border-subtle p-space-2">
              <dt className="text-t12 text-fg-muted">{label}</dt>
              <dd className="break-words text-t14 text-fg">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {evidence?.factors && evidence.factors.length > 0 && (
        <details className="rounded-control border border-subtle px-space-3">
          <summary className="flex min-h-11 cursor-pointer items-center text-t14 font-semibold text-brand">Score breakdown</summary>
          <dl className="space-y-space-1 pb-space-3">
            {evidence.factors.map(({ component, maximum, awarded }) => (
              <div key={component} className="flex justify-between gap-space-2 text-t14">
                <dt className="text-fg-muted">{FACTORS[component] ?? component}</dt>
                <dd className="shrink-0 font-tabular text-fg">{formatNumber(awarded, 2, { trim: true })} / {formatNumber(maximum)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {evidence?.gates && evidence.gates.length > 0 && (
        <details className="rounded-control border border-subtle px-space-3">
          <summary className="flex min-h-11 cursor-pointer items-center text-t14 font-semibold text-brand">Confirmation checks</summary>
          <dl className="space-y-space-1 pb-space-3">
            {evidence.gates.map(({ name, status }) => (
              <div key={name} className="flex justify-between gap-space-2 text-t14">
                <dt>{name}</dt>
                <dd className="text-fg-muted">{status.replace(/_/g, " ").toLowerCase()}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {!evidence && !reasons.length && <p className="text-t14 text-fg-muted">No explanation was saved for this signal.</p>}
      <InPlaceLink to={chartTabPath(item.symbol, item.exchange, chartTf)}
        className="inline-flex min-h-11 items-center rounded-control text-t14 font-semibold text-brand hover:underline">
        Open current chart
      </InPlaceLink>
    </div>
  );
}
