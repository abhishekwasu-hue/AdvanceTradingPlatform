import { ChevronRight, CircleAlert, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { CARD, LINK_SECONDARY } from "../dashboard/CardV2";
import type { ReadinessChecklist } from "../types";
import { checksOk, goLiveView, type GoLiveRow, type GoLiveView } from "./goLive";

/**
 * P3: the Go-live checklist, under Settings → Advanced and reached from "Prepare for LIVE" (both shells). Read-only:
 * it shows `GET /api/readiness` for LIVE (or paper trading), and changes nothing. The passing rows fold into "N checks
 * OK", the optional ones into "N optional"; only the blockers and warnings are open, each with one sentence and one
 * button (src/setup/goLive.ts). No developer footer and no internal numbers.
 */
export default function GoLiveChecklistV2() {
  const titleId = useId();
  const [target, setTarget] = useState<"LIVE" | "PAPER">("LIVE");
  // The answer with the target it was asked for, so switching Live / Paper never shows the other one's rows.
  const [data, setData] = useState<{ target: "LIVE" | "PAPER"; list: ReadinessChecklist } | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setBusy(true);
    setFailed(false);
    const asked = target;
    api.readiness(asked).then((list) => setData({ target: asked, list }), () => setFailed(true)).finally(() => setBusy(false));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [target]);

  const view = data && data.target === target ? goLiveView(data.list) : null;

  return (
    <section aria-labelledby={titleId} data-block="go-live" aria-busy={busy || undefined} className={`${CARD} p-space-4 md:p-space-5`}>
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <h2 id={titleId} className="text-t15 font-semibold text-fg">Go-live checklist</h2>
        <div role="group" aria-label="Check for" className="inline-flex rounded-control border border-border p-px">
          {(["LIVE", "PAPER"] as const).map((t) => (
            <button key={t} type="button" onClick={() => setTarget(t)} aria-pressed={target === t}
              className={`h-11 rounded-control px-space-3 md:h-9 text-t13 font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${target === t ? "bg-surface-2 text-fg" : "text-fg-muted hover:text-fg"}`}>
              {t === "LIVE" ? "Live" : "Paper"}
            </button>
          ))}
        </div>
      </div>

      {failed && <p className="mt-space-3 text-t14 text-fg-muted">We could not load the checklist. Try again in a moment.</p>}
      {!view && !failed && <p className="mt-space-3 text-t14 text-fg-muted">Checking…</p>}
      {view && <GoLiveListV2 view={view} onRecheck={load} busy={busy} />}
    </section>
  );
}

/** The checklist's body (rendered by the unit tests): the headline, the open rows, then the two folds. */
export function GoLiveListV2({ view, onRecheck, busy = false }: { view: GoLiveView; onRecheck: () => void; busy?: boolean }) {
  return (
    <>
      <p data-headline className={`mt-space-2 text-t14 font-medium ${view.ready ? "text-up" : "text-down"}`}>{view.headline}</p>
      {view.open.length > 0 && (
        <ul className="mt-space-3 divide-y divide-subtle" aria-label="Needs doing">
          {view.open.map((r) => <OpenRow key={r.key} row={r} onRecheck={onRecheck} busy={busy} />)}
        </ul>
      )}
      <div className="mt-space-3 space-y-space-1">
        {view.ok.length > 0 && <Fold label={checksOk(view.ok.length)} items={view.ok} kind="ok" />}
        {view.optional.length > 0 && <Fold label={`${view.optional.length} optional`} items={view.optional} kind="optional" />}
      </div>
    </>
  );
}

function OpenRow({ row, onRecheck, busy }: { row: GoLiveRow; onRecheck: () => void; busy: boolean }) {
  const Icon = row.status === "todo" ? CircleAlert : TriangleAlert;
  return (
    <li data-row={row.key} data-status={row.status} className="flex items-start gap-space-3 py-space-3">
      <Icon size={18} aria-hidden className={`mt-px shrink-0 ${row.status === "todo" ? "text-down" : "text-warn"}`} />
      <div className="flex min-w-0 flex-1 flex-col gap-space-2 sm:flex-row sm:items-center sm:justify-between sm:gap-space-3">
        <div className="min-w-0">
          <p className="text-t14 font-medium text-fg">
            {row.title}
            <span className="sr-only">{row.status === "todo" ? " (blocker)" : " (warning)"}</span>
          </p>
          <p className="text-t13 text-fg-muted">{row.sentence}</p>
        </div>
        {"to" in row.action ? (
          <Link to={row.action.to} className={`self-start sm:self-auto ${LINK_SECONDARY}`}>{row.action.label}</Link>
        ) : (
          <button type="button" onClick={onRecheck} disabled={busy} className={`self-start sm:self-auto ${LINK_SECONDARY} disabled:opacity-50`}>
            <RefreshCw size={14} aria-hidden className={busy ? "animate-spin" : ""} /> {row.action.label}
          </button>
        )}
      </div>
    </li>
  );
}

function Fold({ label, items, kind }: { label: string; items: string[]; kind: "ok" | "optional" }) {
  return (
    <details data-fold={kind} className="group/fold">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-space-2 rounded-control text-t14 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} aria-hidden className="shrink-0 transition-transform duration-fast group-open/fold:rotate-90" />
        {label}
      </summary>
      <ul className="mb-space-2 ml-space-6 space-y-space-1">
        {items.map((t) => <li key={t} className="text-t13 text-fg-muted">{t}</li>)}
      </ul>
    </details>
  );
}
