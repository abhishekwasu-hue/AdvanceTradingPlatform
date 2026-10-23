/**
 * The marker and price-line shapes the charts take as props (ProChart, the strategies panel). They lived in the old
 * `CandleChart` component, which nothing rendered any more; the component is gone and its types live here (chart
 * review R1 (b), docs/design/CHART_COLOUR_REVIEW.md).
 */
import type { ChartTone } from "../theme";

export interface PriceLineSpec {
  price: number;
  color: ChartTone | string; // a meaning ("up" / "down" / "fg") follows the theme; anything else is a literal colour
  title: string;
}

export interface ChartMarker {
  timestamp: string; // must match one candle's timestamp
  position: "aboveBar" | "belowBar";
  color: ChartTone | string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
}

/** Convenience builder for the common case: an entry marker derived from a signal's direction. */
export function directionMarker(timestamp: string, direction: "LONG" | "SHORT", text: string): ChartMarker {
  return {
    timestamp,
    position: direction === "LONG" ? "belowBar" : "aboveBar",
    color: direction === "LONG" ? "up" : "down",
    shape: direction === "LONG" ? "arrowUp" : "arrowDown",
    text,
  };
}
