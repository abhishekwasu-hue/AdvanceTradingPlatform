/**
 * U5 D3a: the result board with its view switch - Table (the D1 board), Heatmap (sectors sized by market cap, tiles
 * coloured by the day's change, matched symbols ringed in the signal colour) and Chart grid (the matched symbols: the
 * evidence chart with the scan's own conditions when the run stored one, else the shared daily closes). The switch
 * appears only when the organisation has `screener_board_views` (the context call answers; 404 -> the table alone).
 * Keyboard: the switch is a tablist with a roving tab stop (arrows / Home / End move between views) and each view is its
 * tabpanel; every tile and mini chart names its symbol, the change and whether it matched. The context covers the first
 * CONTEXT_MAX symbols in one call; the heatmap lays out only those and says "first N of M shown".
 */
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ApiError } from "../api/client";
import { cx } from "../components/primitives";
import type { RunResult } from "./api";
import {
  BOARD_VIEWS, CONTEXT_MAX, RRG_MAX, boardApi, gridSymbols, heatmapLayout, heatmapResults, nextTab, rrgScale, sparkPath, tileFill, type BoardContext,
  type BoardView, type ContextRow, type RrgResponse,
} from "./boardViews";
import { EvidenceChart } from "./EvidenceChart";
import { evidenceApi, visibleDrawables, type EvidencePack } from "./evidence";
import { ResultBoard, type ResultBoardProps } from "./ResultBoard";

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
            <text x={g.rect.x + 3} y={g.rect.y + 10} fontSize={9} fill="rgb(var(--fg-muted))">{g.sector} · {g.matched}/{g.tiles.length}</text>
            {g.tiles.map((t) => {
              const f = tileFill(t.change);
              return (
                <g key={t.symbol} role="img" aria-label={`${t.symbol}: ${t.matched ? "matched" : t.unread ? "could not be read" : "did not match"}, day change ${pct(t.change)}`}>
                  <rect x={t.rect.x + 0.5} y={t.rect.y + 0.5} width={Math.max(0, t.rect.w - 1)} height={Math.max(0, t.rect.h - 1)} fill={f.color} fillOpacity={f.opacity}
                        stroke={t.matched ? "rgb(var(--signal))" : "none"} strokeWidth={t.matched ? 2 : 0} data-matched={t.matched || undefined} />
                  {t.rect.w > 34 && t.rect.h > 16 && (
                    <text x={t.rect.x + 3} y={t.rect.y + 12} fontSize={9} fontFamily="var(--font-mono)" fill="rgb(var(--fg))">{t.symbol}</text>
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

export function ChartGridView({ results, context, packs }: { results: RunResult[]; context: ContextRow[]; packs: Record<number, EvidencePack> }) {
  const byS = new Map(context.map((c) => [c.symbol, c]));
  const rows = gridSymbols(results);
  if (!rows.length) return <p className="px-4 py-8 text-center text-t13 text-fg-muted">No symbol matched: the grid shows matched symbols.</p>;
  return (
    <ul className="grid grid-cols-2 gap-2 p-2" data-testid="chart-grid">
      {rows.map((r) => {
        const pack = r.evidence_id != null ? packs[r.evidence_id] : undefined;
        return (
          <li key={r.symbol} className="rounded-control border border-border bg-surface-1 p-1.5">
            <p className="flex items-baseline justify-between text-t12"><span className="font-mono text-fg">{r.symbol}</span>
              <span className="text-fg-muted">{pct(byS.get(r.symbol)?.change_pct ?? null)}</span></p>
            {pack ? <EvidenceChart pack={pack} drawables={visibleDrawables(pack, new Set(), false)} width={320} height={180} />
              : <Spark row={byS.get(r.symbol)} symbol={r.symbol} />}
          </li>
        );
      })}
    </ul>
  );
}

const QUAD_COLOR = { Leading: "rgb(var(--up))", Weakening: "rgb(var(--warn))", Lagging: "rgb(var(--down))", Improving: "rgb(var(--info))" } as const;

export function RrgView({ rrg, width = 360, height = 300 }: { rrg: RrgResponse; width?: number; height?: number }) {
  const sc = rrgScale(rrg.symbols, width, height);
  const c = { x: sc.x(100), y: sc.y(100) };
  const plotted = rrg.symbols.filter((r) => r.tail.length);
  const formingShown = plotted.some((r) => r.tail[r.tail.length - 1].forming);
  return (
    <figure className="m-0" data-testid="rrg-view">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img"
           aria-label={`Relative rotation against ${rrg.benchmark}: ${plotted.map((r) => `${r.symbol} ${r.quadrant}`).join(", ") || "no symbol has enough history"}${formingShown ? " (last point: the forming week)" : ""}`}>
        <line x1={c.x} x2={c.x} y1={0} y2={height} stroke="rgb(var(--border))" />
        <line x1={0} x2={width} y1={c.y} y2={c.y} stroke="rgb(var(--border))" />
        {(["Improving", "Leading", "Lagging", "Weakening"] as const).map((q, i) => (
          <text key={q} x={i % 2 ? width - 4 : 4} y={i < 2 ? 12 : height - 4} textAnchor={i % 2 ? "end" : "start"} fontSize={10} fill={QUAD_COLOR[q]}>{q}</text>
        ))}
        {plotted.map((r) => {
          const pts = r.tail.map((p) => `${sc.x(p.rs_ratio).toFixed(1)},${sc.y(p.rs_momentum).toFixed(1)}`);
          const last = r.tail[r.tail.length - 1];
          const col = r.quadrant ? QUAD_COLOR[r.quadrant] : "rgb(var(--fg-muted))";
          return (
            <g key={r.symbol} data-symbol={r.symbol} data-quadrant={r.quadrant}>
              <polyline points={pts.join(" ")} fill="none" stroke={col} strokeWidth={1.2} opacity={0.7} />
              <circle cx={sc.x(last.rs_ratio)} cy={sc.y(last.rs_momentum)} r={3} fill={last.forming ? "rgb(var(--surface-1))" : col} stroke={col}
                      strokeWidth={1.2} data-forming={last.forming || undefined} />
              <text x={sc.x(last.rs_ratio) + 4} y={sc.y(last.rs_momentum) - 4} fontSize={9} fontFamily="var(--font-mono)" fill="rgb(var(--fg))">{r.symbol}</text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-t12 text-fg-muted">
        x: RS-Ratio, y: RS-Momentum against {rrg.benchmark}, weekly, with a {rrg.symbols[0]?.tail.length ?? 0}-week tail.
        {formingShown ? ` The hollow last point is the forming week (${rrg.forming_week}, to date): it moves until Friday's close.` : ""}
        {rrg.reason ? ` ${rrg.reason}.` : ""}
      </figcaption>
      <table className="mt-1 w-full text-t12">
        <caption className="sr-only">Quadrant of each symbol</caption>
        <thead><tr><th scope="col" className="text-left font-medium text-fg-muted">Symbol</th><th scope="col" className="text-left font-medium text-fg-muted">Quadrant</th></tr></thead>
        <tbody>{rrg.symbols.map((r) => <tr key={r.symbol}><td className="font-mono text-fg">{r.symbol}</td><td className="text-fg">{r.quadrant ?? "not enough history"}</td></tr>)}</tbody>
      </table>
    </figure>
  );
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
  const [rrg, setRrg] = useState<RrgResponse | null>(null);
  const [rrgError, setRrgError] = useState<string | null>(null);
  const tabs = useRef<Partial<Record<BoardView, HTMLButtonElement | null>>>({});
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
    if (view !== "rrg" || !results || !symbols.length) return;
    let live = true;
    setRrg(null);
    setRrgError(null);
    boardApi.rrg(symbols.slice(0, RRG_MAX)).then(
      (r) => { if (live) setRrg(r); },
      (e: unknown) => { if (live) setRrgError(e instanceof Error && e.message ? e.message : "the request failed"); },
    );
    return () => { live = false; };
  }, [view, results, reload]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (view !== "grid" || !results) return;
    let live = true;
    for (const r of gridSymbols(results)) {
      if (r.evidence_id == null || packs[r.evidence_id]) continue;
      const id = r.evidence_id;
      evidenceApi.get(id).then((p) => { if (live) setPacks((m) => ({ ...m, [id]: p })); }, () => undefined);
    }
    return () => { live = false; };
  }, [view, results]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!available || !results) return <ResultBoard {...props} />;
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const next = nextTab(BOARD_VIEWS.map((v) => v.id), view, e.key);
    if (next == null) return;
    e.preventDefault();
    setView(next);
    tabs.current[next]?.focus();
  };
  const label = BOARD_VIEWS.find((v) => v.id === view)?.label;
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div role="tablist" aria-label="Result view" className="flex gap-1">
        {BOARD_VIEWS.map((v) => (
          <button key={v.id} ref={(el) => { tabs.current[v.id] = el; }} type="button" role="tab" id={tabId(v.id)} aria-controls={panelId(v.id)}
                  aria-selected={view === v.id} tabIndex={view === v.id ? 0 : -1} onClick={() => setView(v.id)} onKeyDown={onKey}
                  className={cx("rounded-control px-2 py-1 text-t12", view === v.id ? "bg-surface-2 font-medium text-fg" : "text-fg-muted hover:text-fg")}>
            {v.label}
          </button>
        ))}
      </div>
      {view === "table" && <div role="tabpanel" id={panelId("table")} aria-labelledby={tabId("table")} className="flex min-h-0 flex-col"><ResultBoard {...props} /></div>}
      {view !== "table" && (
        <section role="tabpanel" id={panelId(view)} aria-labelledby={tabId(view)}
                 className={cx("rounded-panel border border-border bg-surface-1 p-2", props.stale && "opacity-50")}>
          {(view === "rrg" ? rrgError : ctxError) ? (
            <p role="alert" className="px-4 py-8 text-center text-t13 text-fg-muted">
              The {view === "rrg" ? "RRG" : label?.toLowerCase()} could not be loaded: {view === "rrg" ? rrgError : ctxError}.{" "}
              <button type="button" className="underline" onClick={() => setReload((n) => n + 1)}>Retry</button>
            </p>
          ) : view === "rrg" ? (rrg ? <><RrgView rrg={rrg} /><p className="mt-1 text-t12 text-fg-muted">{rrg.note}</p></>
                                    : <p className="px-4 py-8 text-center text-t13 text-fg-muted">Loading…</p>)
            : ctx == null ? <p className="px-4 py-8 text-center text-t13 text-fg-muted">Loading…</p>
              : view === "heatmap" ? <HeatmapView results={results} context={ctx.symbols} />
                : <ChartGridView results={results} context={ctx.symbols} packs={packs} />}
          {ctx && view !== "rrg" && <p className="mt-1 text-t12 text-fg-muted">{ctx.note}</p>}
        </section>
      )}
    </div>
  );
}

export default ResultViews;
