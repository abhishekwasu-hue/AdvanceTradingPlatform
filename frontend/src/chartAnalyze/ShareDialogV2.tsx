import { Copy, Link2Off } from "lucide-react";
import { useEffect, useState } from "react";
import { friendlyError } from "../api/errors";
import { Button, Dialog } from "../components/primitives";
import { chartAnalyzeApi } from "./api";
import type { AnalysisItem } from "./types";

/**
 * Analyze a chart (PR-A4, flag chart_analyze_share): "Share with my organisation". A link to the user's own analysis
 * that only logged-in members of the same organisation can open, for a limited time, stoppable at once (CA-5). What it
 * opens is a structure-only copy: no chart name, no prices, no dates and not the picture (backend share.py) - said here
 * before the link is made, so nobody is surprised.
 */

export function shareUrl(token: string, origin: string = typeof window !== "undefined" ? window.location.origin : ""): string {
  return `${origin}/chart-analyze?shared=${encodeURIComponent(token)}`;
}

function day(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export const SHARE_EXPLAINER = "Members of your organisation who are logged in can open the link. It shows the structure only: the chart's name, "
  + "its prices, its dates and your picture are left out. Nobody outside your organisation can open it.";

export default function ShareDialogV2({ item, onClose, onChanged }: { item: AnalysisItem | null; onClose: () => void; onChanged: (item: AnalysisItem) => void }) {
  const [busy, setBusy] = useState<"make" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => { setError(null); setCopied(false); }, [item?.id]);
  if (!item) return null;
  const link = item.share ? shareUrl(item.share.token) : null;

  const run = async (what: "make" | "stop") => {
    setBusy(what);
    setError(null);
    setCopied(false);
    try {
      onChanged(what === "make" ? await chartAnalyzeApi.share(item.id) : await chartAnalyzeApi.unshare(item.id));
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  };

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setError("The link could not be copied. Select it and copy it by hand.");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title="Share with my organisation" description={SHARE_EXPLAINER}
      footer={link ? <>
        <Button className="h-11 md:h-9" onClick={() => void run("make")} loading={busy === "make"}>Make a new link</Button>
        <Button variant="primary" className="h-11 md:h-9" icon={<Copy size={16} aria-hidden />} onClick={() => void copy()}>{copied ? "Copied" : "Copy link"}</Button>
      </> : <>
        <Button className="h-11 md:h-9" onClick={onClose}>Cancel</Button>
        <Button variant="primary" className="h-11 md:h-9" loading={busy === "make"} onClick={() => void run("make")}>Create link</Button>
      </>}>
      <div className="space-y-space-3" data-testid="share-dialog">
        {link ? (
          <>
            <label className="block">
              <span className="mb-space-1 block text-t12 font-medium text-fg-muted">The link</span>
              <input readOnly value={link} onFocus={(e) => e.currentTarget.select()} data-testid="share-link"
                className="h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 font-mono text-t12 text-fg md:h-9" />
            </label>
            <p className="text-t12 text-fg-muted">Open until {day(item.share?.expires_at ?? "")}. Stop sharing ends it at once; a new link ends the old one.</p>
            <Button className="h-11 md:h-9" variant="ghost" icon={<Link2Off size={16} aria-hidden />} loading={busy === "stop"} onClick={() => void run("stop")}>Stop sharing</Button>
          </>
        ) : (
          <p className="text-t14 text-fg">Not shared. Create a link to show this reading to your organisation.</p>
        )}
        {error && <p role="alert" className="text-t12 text-warn">{error}</p>}
      </div>
    </Dialog>
  );
}
