/**
 * U5 D3a: the result board with its view switch - Table (the D1 board), Heatmap (sectors sized by market cap, tiles
 * coloured by the day's change, matched symbols ringed in the signal colour) and Chart grid (the matched symbols: the
 * evidence chart with the scan's own conditions when the run stored one, else the shared daily closes). The switch
 * appears only when the organisation has `screener_board_views` (the context call answers; 404 -> the table alone).
 * Keyboard: the switch is a tablist with a roving tab stop (arrows / Home / End move between views) and each view is its
 * tabpanel; every tile and mini chart names its symbol, the change and whether it matched. The context covers the first
 * CONTEXT_MAX symbols in one call; the heatmap lays out only those and says "first N of M shown".
 */
import { useEffect, useId, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { cx, SkeletonRows } from "../components/primitives";
import type { RunResult } from "./api";
import {
  BOARD_VIEWS, CONTEXT_MAX, boardApi, gridSymbols, heatmapLayout, heatmapResults, sparkPath, tileFill, type BoardContext, type BoardView,
  type ContextRow,
} from "./boardViews";
import { EvidenceChartLive } from "./EvidenceChart";
import { evidenceApi, visibleDrawables, type EvidencePack } from "./evidence";
import { ResultBoard, type ResultBoardProps } from "./ResultBoard";
import { SubTabs } from "../ui/SubTabs";

/** SCR-FIX C5: the heatmap's labels in viewBox units - the 380-wide box is drawn about 1:1 on a phone, so 12 reads as
 * about 12 px there (it was 9: about 8 px on a phone). A tile too small for its symbol shows none (its name is in its label). */
const HEAT_FONT = 12;

const pct = (v: number | null) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(2)} %`);

export function HeatmapView({ results, context, width = 380, height = 300 }: { results: RunResult[]; context: ContextRow[]; width?: number; height?: number }) {
  const shown = useMemo(() => heatmapResults(results, context), [results, context]);
  const groups = useMemo(() => heatmapLayout(shown, context, { x: 0, y: 0, w: width, h: height }), [shown, context, width, height]);
  return (
    <figure className="m-0" data-testid="heatmap-view">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="group" aria-label="Heatmap of the scanned symbols by sector">
        {groups.map((g) => (
          <g key={g.sector} data-sector={g.sector}>
            <rect x={g.rect.x} y={g.rect.y} width={g.rect.w} height={g.rect.h} fill="none" stroke="rgb(var(--border))" />
            <text x={g.rect.x + 3} y={g.rect.y + 13} fontSize={HEAT_FONT} fill="rgb(var(--fg-muted))">{g.sector} · {g.matched}/{g.tiles.length}</text>
            {g.tiles.map((t) => {
              const f = tileFill(t.change);
              return (
                <g key={t.symbol} role="img" aria-label={`${t.symbol}: ${t.matched ? "matched" : t.unread ? "could not be read" : "did not match"}, day change ${pct(t.change)}`}>
                  <rect x={t.rect.x + 0.5} y={t.rect.y + 0.5} width={Math.max(0, t.rect.w - 1)} height={Math.max(0, t.rect.h - 1)} fill={f.color} fillOpacity={f.opacity}
                        stroke={t.matched ? "rgb(var(--signal))" : "none"} strokeWidth={t.matched ? 2 : 0} data-matched={t.matched || undefined} />
                  {t.rect.w > HEAT_FONT * 4 && t.rect.h > HEAT_FONT + 6 && (
                    <text x={t.rect.x + 3} y={t.rect.y + HEAT_FONT + 3} fontSize={HEAT_FONT} fontFamily="var(--font-mono)" fill="rgb(var(--fg))">{t.symbol}</text>
                  )}
                </g>
              );
            })}
          </g>
        ))}
      </svg>
      <figcaption className="mt-1 text-t12 text-fg-muted">
        {shown.length < results.length && <>First {shown.length} of {results.length} shown · </>}
        Sized by market cap (a symbol without one is shown at the median) · colour: the last day's change · ringed: matched
      </figcaption>
    </figure>
  );
}

function Spark({ row, symbol }: { row: ContextRow | undefined; symbol: string }) {
  const d = row ? sparkPath(row.closes, 160, 48) : null;
  return d ? (
    <svg viewBox="0 0 160 48" width="100%" role="img" aria-label={`${symbol}: the last ${row?.closes.length} daily closes, day change ${pct(row?.change_pct ?? null)}`}>
      <path d={d} fill="none" stroke="rgb(var(--chart-text))" strokeWidth={1.5} />
    </svg>
  ) : <p className="text-t12 text-fg-muted">No shared daily history.</p>;
}

const NO_HIDDEN: ReadonlySet<string> = new Set();

/** One grid card's chart: the drawables worked out once per pack (not on every render of the board). */
function GridChart({ pack }: { pack: EvidencePack }) {
  const drawables = useMemo(() => visibleDrawables(pack, NO_HIDDEN, false), [pack]);
  return <EvidenceChartLive pack={pack} drawables={drawables} className="h-64" compact />;
}

/** SCR-FIX C5: one column on a phone (two from `sm`), each matched symbol on the Charts page's own chart at a readable
 * size; a card whose evidence did not load says so (with the shared daily closes when there are some). */
export function ChartGridView({ results, context, packs, failed = {} }: {
  results: RunResult[]; context: ContextRow[]; packs: Record<number, EvidencePack>; failed?: Record<number, string>;
}) {
  const byS = new Map(context.map((c) => [c.symbol, c]));
  const rows = gridSymbols(results);
  if (!rows.length) return <p className="px-4 py-8 text-center text-t13 text-fg-muted">No symbol matched: the grid shows matched symbols.</p>;
  return (
    <ul className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2" data-testid="chart-grid">
      {rows.map((r) => {
        const pack = r.evidence_id != null ? packs[r.evidence_id] : undefined;
        const problem = r.evidence_id != null ? failed[r.evidence_id] : undefined;
        return (
          <li key={r.symbol} className="min-w-0 rounded-control border border-border bg-surface-1 p-2" data-symbol={r.symbol}>
            <p className="flex items-baseline justify-between text-t13"><span className="font-mono text-fg">{r.symbol}</span>
              <span className="text-fg-muted">{pct(byS.get(r.symbol)?.change_pct ?? null)}</span></p>
            {pack ? <GridChart pack={pack} />
              : (
                <>
                  {problem && <p role="alert" className="text-t13 text-warn" data-testid="grid-evidence-error">The chart for {r.symbol} could not be loaded: {problem}</p>}
                  <Spark row={byS.get(r.symbol)} symbol={r.symbol} />
                </>
              )}
          </li>
        );
      })}
    </ul>
  );
}

/** SCR-FIX C5: a grid card's evidence read failed - in words, never a raw status. */
export function evidenceProblem(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) return "this evidence is no longer available.";
  return e instanceof Error && e.message ? e.message : "the request failed.";
}

function withoutKey(m: Record<number, string>, id: number): Record<number, string> {
  if (!(id in m)) return m;
  const next = { ...m };
  delete next[id];
  return next;
}

/** The board with its views: fetches the context once per run (its 404 means the flag is off: the table alone; any
 * other failure says so with a retry instead of loading forever). */
export function ResultViews(props: ResultBoardProps) {
  const { results } = props;
  const symbols = useMemo(() => (results ?? []).map((r) => r.symbol).slice(0, CONTEXT_MAX), [results]);
  const [view, setView] = useState<BoardView>("table");
  const [ctx, setCtx] = useState<BoardContext | null>(null);
  const [ctxError, setCtxError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [available, setAvailable] = useState(false);
  const [packs, setPacks] = useState<Record<number, EvidencePack>>({});
  const [packErrors, setPackErrors] = useState<Record<number, string>>({});
  const uid = useId();
  const tabId = (v: BoardView) => `${uid}-tab-${v}`;
  const panelId = (v: BoardView) => `${uid}-panel-${v}`;
  const key = symbols.join(",");
  useEffect(() => {
    setCtx(null);
    setCtxError(null);
    if (!results || !symbols.length) return;
    let live = true;
    boardApi.context(symbols).then(
      (c) => { if (live) { setCtx(c); setAvailable(true); } },
      (e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) { setAvailable(false); setView("table"); return; }
        setCtxError(e instanceof Error && e.message ? e.message : "the request failed");
      },
    );
    return () => { live = false; };
  }, [results, key, reload]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (view !== "grid" || !results) return;
    let live = true;
    for (const r of gridSymbols(results)) {
      if (r.evidence_id == null || packs[r.evidence_id]) continue;
      const id = r.evidence_id;
      // SCR-FIX C5: a failed read is shown on its card (it was swallowed: the card looked as if it had no evidence)
      evidenceApi.get(id).then(
        (p) => { if (live) { setPacks((m) => ({ ...m, [id]: p })); setPackErrors((m) => withoutKey(m, id)); } },
        (e: unknown) => { if (live) setPackErrors((m) => ({ ...m, [id]: evidenceProblem(e) })); },
      );
    }
    return () => { live = false; };
  }, [view, results]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!available || !results) return <ResultBoard {...props} />;
  const label = BOARD_VIEWS.find((v) => v.id === view)?.label;
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <SubTabs label="Result view" idPrefix={uid} value={view} onChange={setView} size="sm"
        items={BOARD_VIEWS.map((v) => ({ key: v.id, label: v.label }))} />
      {view === "table" && <div role="tabpanel" id={panelId("table")} aria-labelledby={tabId("table")} className="flex min-h-0 flex-col"><ResultBoard {...props} /></div>}
      {view !== "table" && (
        <section role="tabpanel" id={panelId(view)} aria-labelledby={tabId(view)}
                 className={cx("rounded-panel border border-border bg-surface-1 p-2", props.stale && "opacity-50")}>
          {ctxError ? (
            <p role="alert" className="px-4 py-8 text-center text-t13 text-fg-muted">
              The {label?.toLowerCase()} could not be loaded: {ctxError}.{" "}
              <button type="button" className="underline" onClick={() => setReload((n) => n + 1)}>Retry</button>
            </p>
          ) : ctx == null ? <SkeletonRows rows={2} label="Loading" />
            : view === "heatmap" ? <HeatmapView results={results} context={ctx.symbols} />
              : <ChartGridView results={results} context={ctx.symbols} packs={packs} failed={packErrors} />}
          {ctx && <p className="mt-1 text-t12 text-fg-muted">{ctx.note}</p>}
        </section>
      )}
    </div>
  );
}

export default ResultViews;
