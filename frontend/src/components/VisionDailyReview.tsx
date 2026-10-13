import type { VisionReportsResponse } from "../types";
import { VisionReportView } from "./VisionReportPanel";
import { dailyGroups, single } from "./visionReport";

/**
 * BYOK-V3: the caller's own after-close Vision reviews (their own symbols, their own key), newest day first. Comment
 * only - nothing here approves, places or changes anything; each report can be deleted by its owner.
 */
export default function VisionDailyReview({ resp, onChanged }: { resp: VisionReportsResponse; onChanged: () => void }) {
  const groups = dailyGroups(resp);
  if (groups.length === 0) {
    return <p className="py-4 text-center text-sm text-fg-muted">{resp.vision_line || "No daily review yet."}</p>;
  }
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <section key={g.date} aria-label={`Daily review ${g.date}`} className="space-y-2">
          <h3 className="text-xs font-semibold text-fg">{g.date}</h3>
          {g.reports.map((r) => <VisionReportView key={r.id} resp={single(resp, r)} onChanged={onChanged} />)}
        </section>
      ))}
    </div>
  );
}
