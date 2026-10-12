// N2-4: pure helpers for the exposure view and the expected-move layer. The server computes every figure; this shapes
// them for display and for the chart engine (LayerData bands). Facts only.
import type { LayerData } from "../charting/engine";
import type { ExpectedMove, ExposureView } from "../types";

export const CONE_LAYER_ID = "expected_move";
const CONE_COLOR = "#2563eb";

/** Rupees with no decimals: "₹1,440". */
export function rupees0(n: number): string {
  return `${n < 0 ? "-" : ""}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
}

/** The pairs to show: those moving together first (strongest |r| first). */
export function strongestPairs(v: ExposureView): ExposureView["move_together"] {
  return [...v.move_together].sort((x, y) => Math.abs(y.r ?? 0) - Math.abs(x.r ?? 0));
}

/** Two bands around the close for the chosen horizon: ±2 sd (faint, dotted) under ±1 sd (stronger, dashed). */
export function coneLayer(m: ExpectedMove, label1: string, label2: string): LayerData {
  const s = m.selected;
  return {
    bands: [
      { low: s.low_2sd, high: s.high_2sd, color: CONE_COLOR, opacity: 0.06, style: "dotted", label: label2 },
      { low: s.low_1sd, high: s.high_1sd, color: CONE_COLOR, opacity: 0.12, style: "dashed", label: label1 },
    ],
    lines: [{ price: m.close, color: CONE_COLOR, style: "dotted", title: "close" }],
  };
}
