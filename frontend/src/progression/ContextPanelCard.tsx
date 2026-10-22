import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { ContextPanel } from "../types";
import { TILE_ORDER, tileLines } from "./context";
import { useProgressionT } from "./i18n";

/**
 * N2-5 (flag `context_panel`): the market context panel - regime, breadth, India VIX, FII / DII, global cues and this
 * week's events - one tile each, with its source and time; a tile that cannot be read says why. Hidden while the flag
 * is off (the API answers 404). Information only.
 */
export default function ContextPanelCard() {
  const [panel, setPanel] = useState<ContextPanel | null>(null);
  const [busy, setBusy] = useState(false);
  function load() {
    setBusy(true);
    api.contextPanel().then(setPanel).catch(() => setPanel(null)).finally(() => setBusy(false));
  }
  useEffect(load, []);
  if (!panel) return null;
  return <ContextPanelView panel={panel} busy={busy} onRefresh={load} />;
}

export function ContextPanelView({ panel, busy, onRefresh }: { panel: ContextPanel; busy: boolean; onRefresh: () => void }) {
  const t = useProgressionT();
  const keys = (panel.order?.length ? panel.order : [...TILE_ORDER]).filter((k) => panel.tiles[k]);
  return (
    <Card title={t("context.title")} className="space-y-3">
      <div className="space-y-3 text-xs" data-testid="context-panel">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-fg-muted">{t("context.intro")}</p>
          <button type="button" disabled={busy} onClick={onRefresh}
                  className="min-h-[44px] rounded border border-border px-2 py-1 text-fg hover:bg-surface-2 disabled:opacity-50 sm:min-h-0">
            {t("context.refresh")}
          </button>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {keys.map((key) => {
            const tile = panel.tiles[key];
            const lines = tileLines(key, tile, t);
            return (
              <section key={key} className="space-y-1 rounded-lg border border-border p-2" data-testid={`context-${key}`} data-status={tile.status}
                       aria-label={t(`context.tile.${key}`, { defaultValue: key })}>
                <div className="flex flex-wrap items-baseline justify-between gap-1">
                  <h3 className="font-semibold text-fg">{t(`context.tile.${key}`, { defaultValue: key })}</h3>
                  {tile.as_of && <span className="text-fg-muted">{t("context.asOf", { date: tile.as_of.slice(0, 16).replace("T", " ") })}</span>}
                </div>
                {tile.status === "stale" && <p className="text-warn">{t("context.stale")}</p>}
                {tile.status === "na"
                  ? <p className="text-fg-muted">{t("context.na", { reason: tile.reason ?? "" })}</p>
                  : <ul className="space-y-0.5 text-fg">{lines.map((line, i) => <li key={i}>{line}</li>)}</ul>}
                <p className="text-xs text-fg-muted">{t("context.source", { source: tile.source })}</p>
              </section>
            );
          })}
        </div>
        <p className="text-xs text-fg-muted">{panel.note}</p>
      </div>
    </Card>
  );
}
