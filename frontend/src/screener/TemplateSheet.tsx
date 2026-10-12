/**
 * U5 D3b: the template sheet - the screens this page can start from (the starters, and the Legends cards with their
 * ScreenQL), grouped by setup, each with its one-line definition, the organisation's last run of that same screen
 * ("3 of 10 matched · 12 Oct, 15:30 IST") and a live thumbnail: the evidence chart of that run's first matched symbol
 * (U8, the screen's own conditions), never an icon. A template never run says so. Flag `screener_board_views`.
 * Research only: a template lists symbols; it never recommends one.
 */
import { useEffect, useMemo, useState } from "react";
import { Button } from "../components/primitives";
import { boardApi, lastRunText, type TemplateRun } from "./boardViews";
import { EvidenceChart } from "./EvidenceChart";
import { evidenceApi, visibleDrawables, type EvidencePack } from "./evidence";
import type { Taxonomy } from "./taxonomy";

export interface SheetTemplate { id: string; name: string; definition: string; setup: string; source: string; canOpen: boolean }

/** The templates with a ScreenQL text, grouped by the taxonomy's setup labels (in its order). */
export function sheetGroups(taxonomy: Taxonomy, templates: SheetTemplate[]): { label: string; items: SheetTemplate[] }[] {
  return taxonomy.setups
    .map((s) => ({ label: s.label, items: templates.filter((t) => t.setup === s.id) }))
    .filter((g) => g.items.length > 0);
}

export function TemplateCard({ t, run, pack, onOpen }: { t: SheetTemplate; run: TemplateRun | null | undefined; pack?: EvidencePack; onOpen: () => void }) {
  const last = lastRunText(run);
  return (
    <li className="flex flex-col rounded-panel border border-border bg-surface-1 p-2" data-template={t.id}>
      <div className="aspect-[16/9] overflow-hidden rounded-control bg-surface-inset">
        {pack ? <EvidenceChart pack={pack} drawables={visibleDrawables(pack, new Set(), false)} width={320} height={180} />
          : <p className="flex h-full items-center justify-center px-2 text-center text-t12 text-fg-muted">
              {run ? "No chart stored for this run" : "Not run yet - run it to see a preview from live data"}</p>}
      </div>
      <h4 className="mt-2 text-t13 font-semibold text-fg">{t.name}</h4>
      <p className="text-t12 text-fg-muted">{t.definition}</p>
      <p className="mt-1 text-t12 text-fg" data-testid="template-last-run">{last ? `Last run: ${last}` : "Never run in your organisation"}</p>
      <div className="mt-2 flex justify-end">
        <Button size="sm" onClick={onOpen} disabled={!t.canOpen} aria-label={`Open ${t.name}`}
                title={t.canOpen ? undefined : "The builder cannot load this screen yet"}>Open</Button>
      </div>
    </li>
  );
}

export function TemplateSheet({ taxonomy, templates, onOpen }: { taxonomy: Taxonomy; templates: SheetTemplate[]; onOpen: (id: string) => void }) {
  const [runs, setRuns] = useState<Record<string, TemplateRun | null>>({});
  const [packs, setPacks] = useState<Record<number, EvidencePack>>({});
  const groups = useMemo(() => sheetGroups(taxonomy, templates), [taxonomy, templates]);
  const key = templates.map((t) => t.id).join(",");
  useEffect(() => {
    if (!templates.length) return;
    let live = true;
    boardApi.templateRuns(templates.map((t) => ({ id: t.id, source: t.source }))).then((r) => {
      if (!live) return;
      setRuns(r.runs);
      for (const run of Object.values(r.runs)) {
        const id = run?.evidence_id;
        if (id != null) evidenceApi.get(id).then((p) => { if (live) setPacks((m) => ({ ...m, [id]: p })); }, () => undefined);
      }
    }, () => undefined);
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-4" data-testid="template-sheet">
      {groups.map((g) => (
        <section key={g.label} aria-label={g.label}>
          <h3 className="text-t13 font-medium text-fg">{g.label}</h3>
          <ul className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {g.items.map((t) => {
              const run = runs[t.id];
              return <TemplateCard key={t.id} t={t} run={run} pack={run?.evidence_id != null ? packs[run.evidence_id] : undefined} onOpen={() => onOpen(t.id)} />;
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

export default TemplateSheet;
