import { Eye } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { VisionReportsResponse } from "../types";
import { splitJudgement, visionView } from "./visionReport";

/**
 * BYOK-V2: the caller's own Vision report on one proposal (made with the caller's own AI key), or the line that says
 * why there is none. Renders nothing while the operator flag `vision_byok` is off (the API answers 404). Comment only:
 * there is no control here that approves, rejects or changes the proposal - only "Delete report" for the user's own copy.
 */
export default function VisionReportPanel({ actionId }: { actionId: number }) {
  const [resp, setResp] = useState<VisionReportsResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => { api.byokReports(actionId).then(setResp).catch(() => setResp(null)); };
  useEffect(load, [actionId]);

  const view = visionView(resp);
  if (view.kind === "hidden") return null;

  async function remove(id: number) {
    setBusy(true);
    try { await api.byokDeleteReport(id); load(); } catch { /* the list reload shows the truth */ } finally { setBusy(false); }
  }
  const deleteButton = (id: number | null) => id == null ? null : (
    <button type="button" className="ml-auto text-[11px] text-fg-muted hover:text-down hover:underline disabled:opacity-50" disabled={busy}
            onClick={() => void remove(id)}>Delete report</button>
  );

  if (view.kind === "line") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-xs text-fg-muted" data-testid="vision-report">
        <Eye size={12} aria-hidden /><span>{view.text}</span>{deleteButton(view.reportId)}
      </div>
    );
  }
  const [header, ...rest] = view.lines;
  return (
    <div className="space-y-1 rounded-lg border border-ai/30 bg-ai/5 px-2 py-1.5 text-xs" data-testid="vision-report">
      <div className="flex items-center gap-2 font-semibold text-fg"><Eye size={12} aria-hidden /><span>{header}</span>{deleteButton(view.reportId)}</div>
      <ul className="space-y-0.5">
        {rest.map((line, i) => {
          const part = splitJudgement(line);
          return part ? (
            <li key={i} className="text-fg"><span aria-hidden>{part.mark}</span> <b>{part.heading}</b> {part.rest}</li>
          ) : <li key={i} className="text-[11px] text-fg-muted">{line}</li>;
        })}
      </ul>
      {view.telegram === "sent" && <p className="text-[11px] text-fg-muted">Also sent to your linked Telegram chat.</p>}
    </div>
  );
}
