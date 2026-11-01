import { Brain, Globe2 } from "lucide-react";
import { useEffect } from "react";
import { Badge } from "../../../components/primitives";
import { friendlyError } from "../../aiTask";
import { useCopilotData } from "../../data";
import { useCopilotT } from "../../i18n";
import { Panel } from "../shared";
import { formatNumber, formatPct } from "../../../ui/format";

/**
 * The market memory the Copilot reads from (the worker's read every 15 minutes): each watched symbol's direction,
 * regime and structure, the cues, the sentiment inputs and the global cues. A stale or placeholder read hides its
 * prices. M83: the memory comes from the tab's one store and is read by the tab's one "Read now" (the sticky header);
 * when a read stored nothing, the panel says exactly why - never a silent "empty".
 */
const GLOBAL_INVERSE = new Set(["BRENT", "DXY", "USDINR", "US10Y"]);
const GLOBAL_NEUTRAL = new Set(["GOLD"]);

function minutesAgo(iso: string | null | undefined): number | null {
  return iso ? Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000)) : null;
}

export default function MemoryPanel() {
  const t = useCopilotT();
  const { memory, memoryError, loadMemory, reading, readReport } = useCopilotData();
  useEffect(() => { if (memory === null) void loadMemory(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const error = readReport?.error ? friendlyError(readReport.error, t) : memoryError ? friendlyError(memoryError, t) : null;
  // The last press stored no symbol: its exact reasons, here on the panel (M83 A1).
  const missed = readReport && !readReport.error && readReport.symbols === 0 ? readReport.reasons : [];
  const globalsAt = (memory?.globals ?? []).map((g) => g.captured_at).filter((x): x is string => !!x).sort();

  const ago = (min: number | null) => (min == null ? "-" : min < 60 ? t("memory.minAgo", { n: min }) : min < 2880 ? t("memory.hAgo", { n: Math.round(min / 60) }) : t("memory.dAgo", { n: Math.round(min / 1440) }));
  const reads = (memory?.symbols ?? []).map((s) => s.captured_at).filter((x): x is string => !!x).sort();
  const age = minutesAgo(reads.length ? reads[reads.length - 1] : null);
  const stale = age != null && age > 3 * (memory?.interval_minutes ?? 15);
  const changes = (memory?.symbols ?? []).map((s) => s.change_pct).filter((c): c is number => c != null).map((c) => c.toFixed(2));
  const placeholder = changes.length >= 2 && new Set(changes).size === 1;
  const hide = stale || placeholder;
  const tone = (v: number | null | undefined) => ((v ?? 0) > 0 ? "text-up" : (v ?? 0) < 0 ? "text-down" : "text-fg");
  const sign = (v: number | null | undefined, d = 2) => formatPct(v ?? 0, d);

  return (
    <Panel title={t("memory.title")} icon={<Brain size={15} />} testId="pulse-memory" busy={reading || (memory === null && error === null)}
           action={(
             <>
               <span className="text-xs text-fg-muted" data-testid="pulse-memory-stamp">{age == null ? t("memory.noSymbolRead") : t("memory.updated", { ago: ago(age) })}</span>
               {stale && <Badge tone="warn">{t("memory.stale")}</Badge>}
             </>
           )}>
      <p className="mb-2 text-xs text-fg-muted">{t("memory.intro", { n: memory?.interval_minutes ?? 15 })}</p>
      {error && <p className="mb-2 text-sm text-down" role="alert">{error}</p>}
      {missed.length > 0 && (
        <div className="mb-2 rounded-lg border border-warn/40 bg-warn/5 px-2 py-1 text-xs text-warn" role="alert" data-testid="pulse-memory-why">
          <p>{t("memory.notStored")}</p>
          <ul className="mt-0.5 list-disc pl-4">{missed.slice(0, 8).map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      {stale && <p className="mb-2 rounded-lg border border-warn/40 bg-warn/5 px-2 py-1 text-xs text-warn">{t("memory.staleHint")}</p>}
      {!stale && placeholder && <p className="mb-2 rounded-lg border border-warn/40 bg-warn/5 px-2 py-1 text-xs text-warn">{t("memory.placeholderHint")}</p>}
      {!memory || memory.symbols.length === 0 ? (
        <p className="text-sm text-fg-muted">{missed.length > 0 ? t("memory.emptyAfterRead") : t("memory.empty")}{memory?.watchlist?.length ? ` ${t("memory.watchlist", { list: memory.watchlist.join(", ") })}` : ""}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className={`w-full text-xs ${stale ? "opacity-60" : ""}`}>
            <thead><tr className="text-left text-fg-muted">
              <th className="py-1 font-medium">{t("memory.col.symbol")}</th><th className="font-medium">{t("memory.col.price")}</th><th className="font-medium">{t("memory.col.today")}</th>
              <th className="font-medium">{t("memory.col.direction")}</th><th className="font-medium">{t("memory.col.regime")}</th><th className="font-medium">{t("memory.col.structure")}</th>
            </tr></thead>
            <tbody>
              {memory.symbols.map((s) => (
                <tr key={s.symbol} className="border-t border-border/60">
                  <td className="py-1.5 font-semibold text-fg">{s.symbol}</td>
                  <td className="font-tabular text-fg">{hide || s.last_price == null ? "-" : formatNumber(s.last_price, 3, { trim: true })}</td>
                  <td className={`font-tabular ${hide ? "text-fg-muted" : tone(s.change_pct)}`}>{hide ? "-" : sign(s.change_pct)}</td>
                  <td className={s.bias === "BULLISH" ? "text-up" : s.bias === "BEARISH" ? "text-down" : "text-fg"}>{t(`memory.bias.${s.bias ?? "NEUTRAL"}`)}</td>
                  <td className="text-fg-muted">{(s.regime ?? "-").replace(/_/g, " ").toLowerCase()} / {(s.higher_regime ?? "-").replace(/_/g, " ").toLowerCase()}</td>
                  <td className="text-fg-muted">{s.structure ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {memory?.sentiment && memory.sentiment.label !== "UNKNOWN" && (
        <div className="mt-3 border-t border-border/60 pt-2">
          <div className="mb-1 text-xs font-semibold text-fg">{t("memory.sentimentInputs")}</div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {Object.entries(memory.sentiment.components).map(([key, c]) => (
              <span key={key} className={`rounded-md border px-1.5 py-0.5 ${c.score == null ? "border-border text-fg-muted line-through" : "border-border text-fg"}`}>
                {t(`memory.component.${key}`, { defaultValue: key })} {c.score == null ? "-" : `${c.score > 0 ? "+" : ""}${formatNumber(c.score, 0)}`}{c.score != null && <span className="text-fg-muted"> ×{formatNumber(c.weight, 2)}</span>}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="mt-3 border-t border-border/60 pt-2">
        <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-fg"><Globe2 size={13} className="text-ai-2" />{t("memory.global")}
          <span className="ml-auto font-normal text-fg-muted" data-testid="pulse-global-stamp">{t("memory.updated", { ago: ago(minutesAgo(globalsAt.length ? globalsAt[globalsAt.length - 1] : null)) })}</span>
        </div>
        {memory?.globals && memory.globals.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {memory.globals.map((g) => {
              const ch = g.change_pct ?? 0;
              const supportive = GLOBAL_NEUTRAL.has(g.symbol) || Math.abs(ch) < 0.05 ? null : GLOBAL_INVERSE.has(g.symbol) ? ch < 0 : ch > 0;
              return (
                <span key={g.symbol} className="rounded-lg border border-border bg-surface-2/60 px-2 py-0.5 text-fg">
                  {t(`memory.globalName.${g.symbol}`, { defaultValue: g.symbol })} <b className={supportive == null ? "text-fg" : supportive ? "text-up" : "text-down"}>{sign(ch)}</b>
                </span>
              );
            })}
          </div>
        ) : <p className="text-xs text-fg-muted">{memory?.global_enabled === false ? t("memory.globalOff") : t("memory.globalEmpty")}</p>}
        <p className="mt-1 text-xs text-fg-muted">{t("memory.globalNote")}</p>
      </div>
    </Panel>
  );
}
