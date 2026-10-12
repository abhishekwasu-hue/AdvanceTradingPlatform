import { useEffect, useState } from "react";
import { builderApi, wireLegs, type SavedSummary } from "../optionsBuilder/api";
import { legId, type Evaluation, type Leg } from "../optionsBuilder/model";
import { COMPARE_POINTS, COMPARE_RANGE_PCT, addSlot, compareTable, overlay, type CompareSlot } from "./compare";
import { useProgressionT } from "./i18n";

const STROKES = ["stroke-info", "stroke-signal", "stroke-warn"];
const W = 600, H = 200;

/**
 * N2-8 (flag `builder_compare`): up to three strategies side by side - the page's current legs, or saved strategies
 * when saving is on - each evaluated by the engine at the same spot, date and range. Models only; nothing is placed.
 */
export default function CompareSection({ legs, spot, asOf, savedOn }: { legs: Leg[]; spot: number | null; asOf: string; savedOn: boolean }) {
  const t = useProgressionT();
  const [slots, setSlots] = useState<CompareSlot[]>([]);
  const [evals, setEvals] = useState<Evaluation[] | null>(null);
  const [saved, setSaved] = useState<SavedSummary[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (savedOn) builderApi.saved().then(setSaved).catch(() => setSaved([])); }, [savedOn]);

  function add(slot: CompareSlot) {
    const out = addSlot(slots, slot);
    setNote(out.refused ? t(out.refused === "full" ? "compare.full" : "compare.same") : null);
    setSlots(out.slots); setEvals(null);
  }
  async function addSaved(id: number) {
    try {
      const s = await builderApi.load(id);
      add({ key: `saved-${id}`, name: s.name, legs: s.legs.map((l) => ({ ...l, id: legId(), iv: l.iv ?? null } as Leg)) });
    } catch (e) { setNote(String(e)); }
  }
  async function run() {
    if (spot == null || !slots.length) return;
    setBusy(true); setNote(null);
    try {
      setEvals(await Promise.all(slots.map((s) => builderApi.evaluate({ legs: wireLegs(s.legs), spot, as_of: asOf, days_forward: 0, iv_shift: 0,
                                                                         range_pct: COMPARE_RANGE_PCT, points: COMPARE_POINTS }))));
    } catch (e) { setNote(String(e)); setEvals(null); } finally { setBusy(false); }
  }
  const btn = "min-h-[44px] rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0";
  return (
    <section className="space-y-2 border-t border-border px-4 py-3 text-xs" aria-label={t("compare.title")} data-testid="compare-panel">
      <h3 className="font-semibold text-fg">{t("compare.title")}</h3>
      <p className="text-fg-muted">{t("compare.intro")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btn} disabled={!legs.length}
                onClick={() => add({ key: `page-${Date.now()}`, name: t("compare.current", { n: slots.length + 1 }), legs: legs.map((l) => ({ ...l })) })}>
          {t("compare.add")}
        </button>
        {savedOn && saved.length > 0 && (
          <label className="flex items-center gap-1 text-fg-muted">{t("compare.saved")}
            <select className="min-h-[44px] rounded border border-border bg-surface-2 px-1 py-0.5 text-fg sm:min-h-0" value=""
                    onChange={(e) => { if (e.target.value) void addSaved(Number(e.target.value)); }}>
              <option value="">{t("compare.savedPick")}</option>
              {saved.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.underlying})</option>)}
            </select>
          </label>
        )}
        <button type="button" className={btn} disabled={!slots.length || spot == null || busy} onClick={() => void run()}>{t("compare.run")}</button>
      </div>
      {spot == null && <p className="text-warn">{t("compare.needSpot")}</p>}
      <ul className="flex flex-wrap gap-2">
        {slots.map((s, i) => (
          <li key={s.key} className="flex items-center gap-1 rounded bg-surface-2 px-2 py-0.5 text-fg">
            <span aria-hidden className={`inline-block h-0.5 w-4 ${["bg-info", "bg-signal", "bg-warn"][i]}`} />
            {s.name} · {t("compare.legs", { count: s.legs.length })}
            <button type="button" aria-label={t("compare.remove", { name: s.name })} className="px-1 text-fg-muted hover:text-fg"
                    onClick={() => { setSlots(slots.filter((x) => x.key !== s.key)); setEvals(null); }}>×</button>
          </li>
        ))}
      </ul>
      {note && <p role="status" className="text-fg-muted">{note}</p>}
      {evals && <CompareView names={slots.map((s) => s.name)} evals={evals} />}
    </section>
  );
}

export function CompareView({ names, evals }: { names: string[]; evals: Evaluation[] }) {
  const t = useProgressionT();
  const chart = overlay(evals, W, H);
  return (
    <div className="space-y-2" data-testid="compare-result">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={t("compare.chartLabel")}>
        {chart.zeroY != null && <line x1={0} x2={W} y1={chart.zeroY} y2={chart.zeroY} className="stroke-fg-muted/60" strokeDasharray="2 3" />}
        {chart.paths.map((d, i) => <path key={i} d={d} fill="none" className={STROKES[i]} strokeWidth={1.75} />)}
      </svg>
      <div className="overflow-x-auto">
        <table className="w-full text-left tabular-nums" aria-label={t("compare.tableLabel")}>
          <thead className="text-fg-muted"><tr><th className="py-1 pr-2 font-normal" />{names.map((n) => <th key={n} className="py-1 pr-2 font-normal">{n}</th>)}</tr></thead>
          <tbody>
            {compareTable(evals, t).map(([label, ...values]) => (
              <tr key={label} className="text-fg"><td className="py-1 pr-2 text-fg-muted">{label}</td>{values.map((v, i) => <td key={i} className="py-1 pr-2">{v}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-fg-muted">{evals[0]?.disclaimer}</p>
    </div>
  );
}
