import { useEffect, useState } from "react";
import { api } from "../api/client";

/**
 * P3 (review): "in PAPER" on the front page means the trader has no LIVE deployment. Then the front page shows nothing
 * about LIVE: no "Live trading is paused" and no "Verify your email" notice in either top bar, and no such rows in the
 * v2 "Needs attention" block (dashboard/model.ts). Fail safe: only a deployments list that was actually read can hide
 * them - a failed or pending read keeps every LIVE notice visible.
 */
export function paperOnlyFrom(deployments: unknown): boolean {
  return Array.isArray(deployments) && !deployments.some((d) => (d as { mode?: unknown } | null)?.mode === "LIVE");
}

/** True only on the front page (`active`), once `GET /deployments?include_stopped=true` answered with no LIVE deployment
 * (a stopped LIVE deployment still makes the trader a LIVE trader). */
export function usePaperOnly(active: boolean): boolean {
  const [paperOnly, setPaperOnly] = useState(false);
  useEffect(() => {
    setPaperOnly(false);
    if (!active) return;
    let current = true;
    api.listDeployments(true).then((d) => { if (current) setPaperOnly(paperOnlyFrom(d)); }, () => { if (current) setPaperOnly(false); });
    return () => { current = false; };
  }, [active]);
  return paperOnly;
}
