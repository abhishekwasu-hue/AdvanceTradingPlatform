// BYOK-V2: what the proposal card shows of the caller's own Vision report. Pure, so it is unit-tested. The report only
// comments - nothing here (or on the server) approves, rejects or changes a proposal.
import type { VisionReportsResponse } from "../types";

export const MAX_REPORT_LINES = 12;
export const VISION_MARKS = ["✅", "⚠️", "❌"] as const;

export type VisionView =
  | { kind: "hidden" }
  | { kind: "line"; text: string; reportId: number | null }
  | { kind: "report"; lines: string[]; reportId: number; telegram: "none" | "sent" | "failed" };

/** null (flag off, error) or the response -> what to render. Out-of-app only (in_app off) renders nothing. */
export function visionView(resp: VisionReportsResponse | null): VisionView {
  if (!resp || !resp.in_app) return { kind: "hidden" };
  const report = resp.reports[0];
  if (!report) return { kind: "line", text: resp.vision_line, reportId: null };
  if (report.status === "ok" && report.lines.length > 0) {
    return { kind: "report", lines: report.lines.slice(0, MAX_REPORT_LINES), reportId: report.id, telegram: report.telegram_status };
  }
  return { kind: "line", text: report.line ?? resp.vision_line, reportId: report.status === "pending" || report.status === "running" ? null : report.id };
}

/** Splits "Zones: ✅ note (range ...)" into the heading, the mark and the rest; null for lines without a mark. */
export function splitJudgement(line: string): { heading: string; mark: string; rest: string } | null {
  const mark = VISION_MARKS.find((m) => line.includes(m));
  const colon = line.indexOf(":");
  if (!mark || colon < 0) return null;
  const rest = line.slice(line.indexOf(mark) + mark.length).trim();
  return { heading: line.slice(0, colon).trim(), mark, rest };
}
