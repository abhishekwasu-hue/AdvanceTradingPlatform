import { useState } from "react";
import { builderApi, wireLegs, type AdjustResult } from "../optionsBuilder/api";
import type { Leg } from "../optionsBuilder/model";
import { ADJUST_DEFAULTS, adjustFields, changeLine, compareRows, curvePaths, moneyLine, type AdjustForm } from "./adjust";
import { useProgressionT } from "./i18n";

/**
 * N2-7 (flag `builder_adjustments`): the builder's adjustment drawer - roll a leg, add a hedge, convert to an iron
 * condor or close one side, with the changes at model prices and the payoff and Greeks before and after. Models only:
 * "Use the legs after" only replaces the legs on this page; nothing is placed, staged or proposed.
 */
export default function AdjustDrawer({ legs, spot, step, asOf, onUse }: {
  legs: Leg[]; spot: number | null; step: number | null; asOf: string; onUse: (legs: AdjustResult["after_legs"]) => void;
}) {
  const t = useProgressionT();
  const [form, setForm] = useState<AdjustForm>(ADJUST_DEFAULTS);
  const [result, setResult] = useState<AdjustResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fields = adjustFields(form);
  const ready = legs.length > 0 && spot != null && step != null && fields != null;

  async function run() {
    if (!ready || !fields || spot == null || step == null) return;
    setBusy(true); setError(null);
    try { setResult(await builderApi.adjust({ legs: wireLegs(legs), spot, as_of: asOf, strike_step: step, kind: form.kind, ...fields })); }
    catch (e) { setError(String(e)); setResult(null); }
    finally { setBusy(false); }
  }
  const set = (patch: Partial<AdjustForm>) => { setForm({ ...form, ...patch }); setResult(null); };
  const input = "min-h-[44px] rounded border border-border bg-surface-2 px-2 py-1 text-fg sm:min-h-0";
  return (
    <div className="space-y-3 border-t border-border px-4 py-3 text-xs" data-testid="adjust-panel">
      <p className="text-fg-muted">{t("adjust.intro")}</p>
      {step == null && <p className="text-warn">{t("adjust.needStep")}</p>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.kind")}
          <select className={input} value={form.kind} onChange={(e) => set({ kind: e.target.value as AdjustForm["kind"] })}>
            {(["roll", "add_hedge", "iron_condor", "close_side"] as const).map((k) => <option key={k} value={k}>{t(`adjust.kinds.${k}`)}</option>)}
          </select>
        </label>
        {(form.kind === "roll" || form.kind === "add_hedge") && (
          <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.leg")}
            <select className={input} value={form.index} onChange={(e) => set({ index: e.target.value })}>
              {legs.map((l, i) => <option key={l.id} value={i}>{`${l.direction} ${l.option_type} ${l.strike} (${l.expiry})`}</option>)}
            </select>
          </label>
        )}
        {form.kind === "roll" && <>
          <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.steps")}
            <input className={input} type="number" value={form.steps} onChange={(e) => set({ steps: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.expiry")}
            <input className={input} type="date" value={form.expiry} onChange={(e) => set({ expiry: e.target.value })} />
          </label>
        </>}
        {(form.kind === "add_hedge" || form.kind === "iron_condor") && (
          <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.width")}
            <input className={input} type="number" min={1} value={form.width} onChange={(e) => set({ width: e.target.value })} />
          </label>
        )}
        {form.kind === "close_side" && (
          <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.side")}
            <select className={input} value={form.side} onChange={(e) => set({ side: e.target.value as "CE" | "PE" })}>
              <option value="CE">CE</option><option value="PE">PE</option>
            </select>
          </label>
        )}
        {form.kind !== "close_side" && (
          <label className="flex flex-col gap-1 text-fg-muted">{t("adjust.newIv")}
            <input className={input} type="number" value={form.newIv} onChange={(e) => set({ newIv: e.target.value })} />
          </label>
        )}
      </div>
      <button type="button" disabled={!ready || busy} onClick={() => void run()}
              className="min-h-[44px] rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">
        {t("adjust.run")}
      </button>
      {error && <p role="status" className="text-down">{error}</p>}
      {result && <AdjustResultView result={result} onUse={() => onUse(result.after_legs)} />}
    </div>
  );
}

const W = 600, H = 180;

export function AdjustResultView({ result, onUse }: { result: AdjustResult; onUse: () => void }) {
  const t = useProgressionT();
  const paths = curvePaths(result, W, H);
  return (
    <div className="space-y-2" data-testid="adjust-result">
      <p className="font-semibold text-fg">{result.description}</p>
      <div>
        <p className="text-fg-muted">{t("adjust.changes")}</p>
        <ul className="text-fg">{result.changes.map((c, i) => <li key={i}>{changeLine(c, t)}</li>)}</ul>
        <p className="text-fg">{moneyLine(result, t)}</p>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={t("adjust.chartLabel")}>
        {paths.zeroY != null && <line x1={0} x2={W} y1={paths.zeroY} y2={paths.zeroY} className="stroke-fg-muted/60" strokeDasharray="2 3" />}
        <path d={paths.before} fill="none" className="stroke-fg-muted" strokeWidth={1.5} strokeDasharray="6 4" />
        <path d={paths.after} fill="none" className="stroke-signal" strokeWidth={2} />
      </svg>
      <div className="overflow-x-auto">
        <table className="w-full text-left tabular-nums" aria-label={t("adjust.compare")}>
          <thead className="text-fg-muted"><tr><th className="py-1 pr-2 font-normal" /><th className="py-1 pr-2 font-normal">{t("adjust.colBefore")}</th>
            <th className="py-1 font-normal">{t("adjust.colAfter")}</th></tr></thead>
          <tbody>
            {compareRows(result, t).map(([label, before, after]) => (
              <tr key={label} className="text-fg"><td className="py-1 pr-2 text-fg-muted">{label}</td><td className="py-1 pr-2">{before}</td><td className="py-1">{after}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" onClick={onUse}
              className="min-h-[44px] rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 sm:min-h-0">{t("adjust.use")}</button>
      <p className="text-[11px] text-fg-muted">{result.why_not} {result.disclaimer}</p>
    </div>
  );
}
