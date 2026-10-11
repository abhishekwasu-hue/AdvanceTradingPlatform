import { describe, expect, it } from "vitest";
import type { VisionReport, VisionReportsResponse } from "../types";
import { splitJudgement, visionView } from "./visionReport";

const base: VisionReport = {
  id: 7, ai_action_id: 3, status: "ok", provider: "anthropic", model: "m", reason: null, report: null, lines: [], line: null,
  image_sha256: "a".repeat(64), image_stored: false, cost_usd: 0.001, telegram_status: "none", created_at: null, finished_at: null,
};

function resp(reports: VisionReport[], extra: Partial<VisionReportsResponse> = {}): VisionReportsResponse {
  return { reports, vision_line: "Vision off - add your API key in Settings", in_app: true, ...extra };
}

describe("visionView", () => {
  it("hides everything when the flag is off (no response) or in-app is off", () => {
    expect(visionView(null)).toEqual({ kind: "hidden" });
    expect(visionView(resp([], { in_app: false }))).toEqual({ kind: "hidden" });
  });

  it("shows the off line without a report", () => {
    expect(visionView(resp([]))).toEqual({ kind: "line", text: "Vision off - add your API key in Settings", reportId: null });
  });

  it("shows the report, at most 12 lines", () => {
    const lines = ["Vision report - X 5min", "Zones: ✅ ok", ...Array.from({ length: 20 }, (_, i) => `line ${i}`)];
    const view = visionView(resp([{ ...base, lines, report: lines.join("\n") }]));
    expect(view.kind).toBe("report");
    if (view.kind === "report") expect(view.lines).toHaveLength(12);
  });

  it("shows the skipped line with its id (deletable) and the pending line without one", () => {
    expect(visionView(resp([{ ...base, status: "skipped", line: "Vision skipped: daily cap reached" }])))
      .toEqual({ kind: "line", text: "Vision skipped: daily cap reached", reportId: 7 });
    expect(visionView(resp([{ ...base, status: "pending", line: "Vision report pending" }])))
      .toEqual({ kind: "line", text: "Vision report pending", reportId: null });
  });
});

describe("splitJudgement", () => {
  it("splits heading, mark and the rest", () => {
    expect(splitJudgement("Zones: ✅ clean support (range 1.00 - 2.00)")).toEqual({ heading: "Zones", mark: "✅", rest: "clean support (range 1.00 - 2.00)" });
    expect(splitJudgement("Pullback & trigger: ⚠️ shallow")).toEqual({ heading: "Pullback & trigger", mark: "⚠️", rest: "shallow" });
    expect(splitJudgement("Comment only - Vision never approves")).toBeNull();
  });
});
