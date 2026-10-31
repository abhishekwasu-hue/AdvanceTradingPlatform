/**
 * M49 U6 (owner-approved mockup docs/ui/U6_SCREENER.html): ONE way to start a scan on Find setups - one block with one
 * chooser (Ready scan / Template / Own words) instead of three separate starts (the ready-scans card, the empty
 * canvas's templates and the "From an idea" dialog). Nothing here is new behaviour: a ready scan opens and runs as
 * before, a template fills the Conditions as the empty canvas did, "Own words" is the same idea panel inline.
 *
 * Research only: every start lists symbols that meet the trader's own conditions - never a recommendation.
 */
import { ChevronRight, Play, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "../components/primitives";
import { SubTabs } from "../ui/SubTabs";
import type { StarterTemplate } from "./FunnelCanvas";
import type { TaxScreen } from "./taxonomy";

export type StartWay = "ready" | "template" | "words";

export const START_LABEL: Record<StartWay, string> = { ready: "Ready scan", template: "Template", words: "Own words" };
/** On a phone the three tabs keep one row: "Ready" is "Ready scan" (its full name stays the accessible name). */
const START_SHORT: Record<StartWay, string> = { ready: "Ready", template: "Template", words: "Own words" };

/** The ways this account can start: Ready scan only when there are ready scans, Own words only with the idea panel. */
export function startWays({ ready, words }: { ready: number; words: boolean }): StartWay[] {
  return [...(ready > 0 ? ["ready" as const] : []), "template" as const, ...(words ? ["words" as const] : [])];
}

export interface StartScanProps {
  way: StartWay;
  onWay: (way: StartWay) => void;
  ways: readonly StartWay[];
  /** Ready scan: up to three screens that open and run at once on the trader's list. */
  ready: readonly TaxScreen[];
  /** How many screens the library holds ("See all ready scans (24)"); null: no library to open. */
  libraryCount: number | null;
  onRunReady: (s: TaxScreen) => void;
  onSeeAll: () => void;
  /** Template: the starting points; each fills the Conditions (nothing runs). */
  templates: readonly StarterTemplate[];
  onTemplate: (t: StarterTemplate) => void;
  /** "Blank: I add conditions": opens Conditions with nothing in it. */
  onBlank: () => void;
  /** Own words: the idea panel (rendered only while that tab is open). */
  words?: ReactNode;
  /** The trader's list in words ("NIFTY 50"), for the one line under the chooser. */
  listName: string;
}

const ROW = "flex w-full min-h-11 items-center gap-space-3 rounded-control border border-subtle bg-surface px-space-3 py-space-2 text-left hover:border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export function StartScan(props: StartScanProps) {
  const { way, ways } = props;
  return (
    <section aria-labelledby="start-scan-title" className="rounded-panel border border-border bg-surface-1 p-space-4" data-testid="start-scan">
      <h2 id="start-scan-title" className="text-t15 font-semibold text-fg">Start a scan</h2>
      <SubTabs<StartWay> className="mt-space-3" label="Start a scan" value={way} onChange={props.onWay} idPrefix="start-scan"
                         items={ways.map((k) => ({ key: k, label: START_LABEL[k], short: START_SHORT[k], ariaLabel: START_LABEL[k] }))} />
      <div className="mt-space-3" role="tabpanel" id={`start-scan-panel-${way}`} aria-labelledby={`start-scan-tab-${way}`}>
        {way === "ready" && (
          <div data-testid="ready-scans">
            <p className="text-t13 text-fg-muted">One tap runs it on {props.listName || "your list"}. You can change every condition afterwards.</p>
            <ul className="mt-space-2 space-y-space-2">
              {props.ready.map((s) => (
                <li key={s.id} className="flex items-center gap-space-2 rounded-control border border-subtle bg-surface px-space-3 py-space-2">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-t14 font-medium text-fg">{s.name}</span>
                    {s.definition && <span className="line-clamp-1 block text-t12 text-fg-muted">{s.definition}</span>}
                  </span>
                  <Button size="sm" variant="primary" edge onClick={() => props.onRunReady(s)} aria-label={`Run ${s.name}`} data-testid="ready-scan-run">
                    <Play size={14} aria-hidden /> Run
                  </Button>
                </li>
              ))}
            </ul>
            {props.libraryCount != null && props.libraryCount > 0 && (
              <Button size="sm" variant="ghost" className="mt-space-2" onClick={props.onSeeAll} data-testid="ready-see-all">
                <span className="font-tabular">See all ready scans ({props.libraryCount})</span> <ChevronRight size={14} aria-hidden />
              </Button>
            )}
          </div>
        )}
        {way === "template" && (
          <ul className="space-y-space-2" data-testid="start-templates">
            {props.templates.map((t) => (
              <li key={t.id}>
                <button type="button" className={ROW} onClick={() => props.onTemplate(t)}>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-t14 font-medium text-fg">{t.name}</span>
                    <span className="line-clamp-1 block text-t12 text-fg-muted">{t.summary}</span>
                  </span>
                  <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted" />
                </button>
              </li>
            ))}
            <li>
              <button type="button" className={`${ROW} border-dashed`} onClick={props.onBlank} data-testid="start-blank">
                <Plus size={16} aria-hidden className="shrink-0 text-fg-muted" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-t14 font-medium text-fg">Blank: I add conditions</span>
                  <span className="line-clamp-1 block text-t12 text-fg-muted">Build it yourself, one condition at a time</span>
                </span>
              </button>
            </li>
          </ul>
        )}
        {way === "words" && props.words}
      </div>
    </section>
  );
}

export default StartScan;
