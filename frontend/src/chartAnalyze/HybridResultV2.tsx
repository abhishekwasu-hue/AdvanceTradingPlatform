import { CheckCircle2, Download, Eye, Trash2 } from "lucide-react";
import { Fragment, lazy, Suspense, type ReactNode } from "react";
import { Button, Skeleton } from "../components/primitives";
import { cx } from "../components/primitives/cx";
import { formatNumber } from "../ui/format";
import { guardDeep, guardText } from "./signalLanguage";
import type { HybridArea, HybridResult, HybridRow, HybridTrend } from "./types";
import type { LevelLine } from "../chart/chartCore";
import type { TimeframeId } from "../chart/timeframes";
import type { LayerBand } from "../charting/engine";
import { Details } from "../ui/Details";
import { HelpPopover } from "../ui/HelpPopover";

// Owner CA-Q1: the chart core loads only where the live chart is shown (the pages), never in a static render.
const EmbeddedChartV2 = lazy(() => import("../chart/EmbeddedChartV2"));

/**
 * M59 CR-4 / ADV-2 C+D (docs/design/ADV2_SPEC.md): the hybrid result - "Advanced analysis (code + AI)", or "(code)" when
 * the engine has no AI part. Layout = the owner's approved sample: the header (title, label chip + state chip, last
 * close and the candles read), ATP's reading drawn on ATP's own chart beside "Your chart", Structure (trend, swings,
 * protected level + %, last break; the higher degree behind (?); the other timeframes that are ready), Areas of
 * interest (above / below, band, tag, distance %), Scenarios (main / other), What to watch, the checklist's present
 * facts and the disclaimer. A section with nothing in it is not rendered and a missing value is left out (never a
 * placeholder); results stored before ADV-2 render with the sections they have. Every text passes the signal-language
 * check; no order button.
 */

const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";
const CHIP = "inline-flex min-h-7 items-center whitespace-nowrap rounded-pill px-space-2 text-t12 font-semibold";
const TAG = "inline-flex min-h-6 items-center whitespace-nowrap rounded-control bg-surface-2 px-space-2 text-t12 font-medium text-fg";
const TONE = { up: "bg-up-tint text-up", down: "bg-down-tint text-down", sideways: "bg-surface-2 text-fg" } as const;
const TEXT_TONE = { up: "text-up", down: "text-down", sideways: "text-fg" } as const;
const TREND_WORD = { up: "Up", down: "Down", sideways: "Sideways" } as const;
const SOURCE_WORD = { stored: "ATP's stored candles", broker: "your broker's candles", "stored+broker": "ATP's stored and your broker's candles" } as const;
const ARROW = { up: "↑", down: "↓" } as const;
const KEEP = new Set(["disclaimer"]);
const ROW_BASE = "grid min-h-11 items-center gap-x-space-3 border-t border-subtle py-space-2 first:border-t-0";
const ROW = `${ROW_BASE} grid-cols-[auto_minmax(0,1fr)]`;
const num = (v: number | null | undefined) => (v == null ? "" : formatNumber(v, 2));
const band = (lo: number | null | undefined, hi: number | null | undefined) => (lo != null && hi != null && formatNumber(lo, 2) === formatNumber(hi, 2) ? num(lo) : `${num(lo)} – ${num(hi)}`);
const pct = (v: number) => `${formatNumber(v, 2)}%`;

/** The trend word of a structure row; "" when the row has no trend (the cell is then left empty). */
export function trendText(row: HybridRow): string {
  if (!row.trend) return "";
  return TREND_WORD[row.trend] + (row.weak ? " (weak)" : "");
}

/** ADV-2 B: an area's distance - "2.30%" (distance_pct); a result stored before ADV-2: "2.90 points". */
export function areaDistance(a: HybridArea): string {
  if (a.where === "inside") return "at the price";
  if (a.distance_pct != null) return pct(a.distance_pct);
  return a.distance == null ? "" : `${formatNumber(a.distance, 2)} points`;
}

/** The reading's trend tone: the picked timeframe's row, else the state text's first word. */
function readingTrend(r: HybridResult["reading"], row: HybridRow | undefined): HybridTrend | null {
  if (row?.trend) return row.trend;
  const first = r.state?.text.trim().split(/\s/)[0]?.toLowerCase();
  return first === "up" || first === "down" || first === "sideways" ? first : null;
}

function Block({ title, id, children, help }: { title: string; id: string; children: ReactNode; help?: ReactNode }) {
  return (
    <section aria-labelledby={id} className={card} data-block={id}>
      <div className="mb-space-2 flex items-center justify-between gap-space-2">
        <h3 id={id} className="text-t15 font-semibold text-fg md:text-t18">{title}</h3>
        {help}
      </div>
      {children}
    </section>
  );
}

function Fact({ label, children, id }: { label: ReactNode; children: ReactNode; id: string }) {
  return (
    <div className={ROW} data-fact={id}>
      <dt className="text-t14 text-fg">{label}</dt>
      <dd className="ml-auto min-w-0 text-right font-tabular text-t14 text-fg">{children}</dd>
    </div>
  );
}

function Legend() {
  const item = "inline-flex items-center gap-space-1 text-t12 text-fg-muted";
  const swatch = "inline-block h-3 w-4 rounded-sm";
  return (
    <ul className="mt-space-2 flex flex-wrap gap-x-space-3 gap-y-space-1" aria-label="What the drawing shows" data-legend>
      <li className={item}><span className={cx(swatch, "bg-down-tint ring-1 ring-down")} aria-hidden />Area of interest above</li>
      <li className={item}><span className={cx(swatch, "bg-up-tint ring-1 ring-up")} aria-hidden />Area of interest below</li>
      <li className={item}><span className={cx(swatch, "h-0.5 bg-brand")} aria-hidden />Protected level</li>
      <li className={item}><span className={cx(swatch, "border border-warn")} aria-hidden />Trigger candle</li>
    </ul>
  );
}

export interface HybridResultProps {
  result: HybridResult;
  userImageSrc: string | null;
  readingSrc: string | null;
  imageNote?: string | null;
  onDownload?: () => void;
  onDelete?: () => void;
  /** M72 PR-5: false in the Copilot thread - the Download / Delete buttons stay on the Analyze a chart page. */
  actions?: boolean;
  downloading?: boolean;
  deleting?: boolean;
  /** M84: "Read from your chart: NIFTY 50 · 15m", with Change and Re-run in place (ChartReadV2). */
  readLine?: ReactNode;
  /** Owner CA-Q1 (3 Nov): ATP's reading drawn on ATP's own chart (zoom, scroll, price axis) - the pages pass true;
   * the drawn picture stays for the download, behind Details. */
  liveChart?: boolean;
}

const LIVE_TF: Record<string, TimeframeId> = { "5m": "5min", "15m": "15min", "1h": "60min", "1d": "day" };

/** Owner CA-Q1: the reading's lines and areas in the chart core's own shapes (the same values the picture draws). */
export function readingOverlay(r: HybridResult["reading"]): { levels: LevelLine[]; areas: LayerBand[] } {
  const levels: LevelLine[] = [];
  if (r.protected != null) levels.push({ price: r.protected, title: "Protected", tone: "neutral" });
  if (r.last_break) levels.push({ price: r.last_break.price, title: `Break${r.last_break.label ? ` (${r.last_break.label})` : ""}`, tone: r.last_break.direction === "down" ? "down" : r.last_break.direction === "up" ? "up" : "neutral" });
  for (const lv of r.levels) levels.push({ price: lv.price, title: lv.kind === "swing high" ? "Swing high" : "Swing low", tone: "neutral" });
  const areas: LayerBand[] = r.areas.map((a) => ({ low: a.low, high: a.high, role: a.where === "above" ? "supply" : a.where === "below" ? "demand" : "neutral",
    label: `Area ${band(a.low, a.high)}`, opacity: 0.16 }));
  return { levels, areas };
}

/** ADV-2 C: the picture's note, worded positively; it sits behind Details with the download. */
export function pictureNote(title: string): string {
  return `ATP draws its reading on its own chart of ${title}, with its own price scale; your picture stays exactly as you added it.`;
}

/** The checklist's count line behind Details: "4 of 7 checks present". */
export function checksLine(list: { present: boolean }[]): string {
  return `${list.filter((c) => c.present).length} of ${list.length} checks present`;
}

function Structure({ r, row }: { r: HybridResult["reading"]; row: HybridRow | undefined }) {
  const trend = readingTrend(r, row);
  const trendWords = r.state?.text ? guardText(r.state.text) : row?.ready && row.trend ? trendText(row) : "";
  const swings = (r.swings ?? []).slice(-6);
  const prot = r.protected ?? (row?.ready ? row.protected : null);
  const close = r.last_close;
  const protPct = r.protected_distance_pct ?? (prot != null && close ? Math.abs(prot - close) / close * 100 : null);
  const protWhere = prot != null && close != null ? (prot > close ? "above" : prot < close ? "below" : null) : null;
  const brk = r.last_break ?? (row?.ready ? row.last_break : null);
  const others = r.table.filter((t) => t.ready && t.tf !== r.timeframe);
  const hd = r.higher_degree;
  if (!trendWords && swings.length === 0 && prot == null && !brk && others.length === 0) return null;
  const help = hd?.text ? (
    <HelpPopover title="Higher degree" align="end">
      <p data-testid="hybrid-higher-degree"><span className={cx("font-semibold", hd.trend ? TEXT_TONE[hd.trend] : "text-fg")}>{hd.trend ? TREND_WORD[hd.trend] : ""}</span>{hd.trend ? " · " : ""}{guardText(hd.text)}</p>
    </HelpPopover>
  ) : undefined;
  return (
    <Block title="Structure" id="hy-structure" help={help}>
      <dl data-testid="hybrid-structure">
        {trendWords && <Fact id="trend" label="Trend"><span className={cx("font-sans font-semibold", trend ? TEXT_TONE[trend] : "text-fg")}>{trendWords}</span></Fact>}
        {swings.length > 0 && (
          <Fact id="swings" label="Swings">
            <span className="inline-flex flex-wrap justify-end gap-x-space-1">
              {swings.map((s, i) => <span key={`${s.label}-${s.price}-${i}`} className="whitespace-nowrap">{i > 0 && <span aria-hidden className="text-fg-muted">→ </span>}{s.label} {num(s.price)}</span>)}
            </span>
          </Fact>
        )}
        {prot != null && (
          <Fact id="protected" label={protWhere === "above" ? "Protected high" : protWhere === "below" ? "Protected low" : "Protected level"}>
            <span className="font-semibold">{num(prot)}</span>
            {close != null && protWhere && (
              <span className="text-fg-muted"> {protWhere === "above" ? "+" : "−"}{num(Math.abs(prot - close))}{protPct != null ? ` · ${pct(protPct)} ${protWhere}` : ""}</span>
            )}
          </Fact>
        )}
        {brk && (
          <Fact id="break" label="Last break">
            {brk.label} {brk.direction ? <span aria-hidden>{ARROW[brk.direction]} </span> : null}{num(brk.price)}
          </Fact>
        )}
      </dl>
      {others.length > 0 && (
        <div className="mt-space-3" data-card-block="timeframes">
          <h4 className="text-t12 font-medium uppercase text-fg-muted">Other timeframes</h4>
          <table className="mt-space-1 w-full min-w-0 border-collapse text-left" data-testid="hybrid-table">
            <thead>
              <tr className="text-t12 font-medium uppercase text-fg-muted">
                <th scope="col" className="px-space-1 py-space-1 font-medium">Timeframe</th>
                <th scope="col" className="px-space-1 py-space-1 font-medium">Trend</th>
                <th scope="col" className="px-space-1 py-space-1 text-right font-medium">Protected level</th>
                <th scope="col" className="hidden px-space-1 py-space-1 font-medium md:table-cell">Last break</th>
              </tr>
            </thead>
            <tbody>
              {others.map((o) => (
                <Fragment key={o.tf}>
                  <tr className="border-t border-subtle align-middle" data-tf={o.tf}>
                    <th scope="row" className="px-space-1 py-space-2 text-t13 font-semibold text-fg">{o.label}</th>
                    <td className="px-space-1 py-space-2">
                      {o.trend && <span className={cx(CHIP, TONE[o.trend])} data-trend={o.trend}>{trendText(o)}</span>}
                    </td>
                    <td className="px-space-1 py-space-2 text-right font-tabular text-t13 text-fg">{num(o.protected)}</td>
                    <td className="hidden px-space-1 py-space-2 text-t13 text-fg-muted md:table-cell">
                      {o.last_break && <>{o.last_break.label} <span className="font-tabular text-fg">{num(o.last_break.price)}</span></>}
                    </td>
                  </tr>
                  {o.last_break && (
                    <tr className="md:hidden">
                      <td colSpan={3} className="px-space-1 pb-space-2 text-t12 text-fg-muted">
                        Last break: {o.last_break.label} <span className="font-tabular text-fg">{num(o.last_break.price)}</span>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Block>
  );
}

const WHERE = { above: { word: "Above", tone: "text-down" }, inside: { word: "At the price", tone: "text-fg" }, below: { word: "Below", tone: "text-up" } } as const;

function Areas({ areas }: { areas: HybridArea[] }) {
  const ordered = [...areas.filter((a) => a.where === "above"), ...areas.filter((a) => a.where === "inside"), ...areas.filter((a) => a.where === "below")];
  return (
    <Block title="Areas of interest" id="hy-areas">
      <ul data-testid="hybrid-areas">
        {ordered.map((a) => {
          const dist = areaDistance(a);
          return (
            <li key={`${a.where}-${a.low}-${a.high}`} className={ROW} data-area={a.where}>
              <span className={cx("text-t14", WHERE[a.where].tone)}>{WHERE[a.where].word}</span>
              <span className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-space-2 gap-y-space-1 font-tabular">
                <span className="whitespace-nowrap text-t14 font-semibold text-fg">{band(a.low, a.high)}</span>
                {a.state && <span className={TAG} data-area-tag>{guardText(a.state)}</span>}
                {dist && <span className="whitespace-nowrap text-t13 text-fg-muted" data-area-distance>{dist}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </Block>
  );
}

export default function HybridResultV2({ result, userImageSrc, readingSrc, imageNote, onDownload, onDelete, downloading, deleting, actions = true, readLine, liveChart = false }: HybridResultProps) {
  const r = result.reading;
  const ai = result.ai ? guardDeep(result.ai, KEEP) : null;
  const title = `${r.symbol} · ${r.timeframe_label}`;
  const row = r.table.find((t) => t.tf === r.timeframe);
  const trend = readingTrend(r, row);
  const stateChip = r.state?.text ? guardText(r.state.text) : null;
  const live = liveChart && Boolean(LIVE_TF[r.timeframe]);
  const scenarios = r.scenarios && (r.scenarios.main || r.scenarios.other) ? r.scenarios : null;
  const watch = (r.watch ?? []).filter((w) => w.when);
  const present = r.checklist.filter((c) => c.present);
  const source = r.source ? SOURCE_WORD[r.source] : "the stored candles";
  const showYours = Boolean(userImageSrc || imageNote);
  const picture = readingSrc ? <img src={readingSrc} alt={`ATP's reading of ${title}, drawn on the candles`} className="w-full rounded-control border border-subtle" /> : null;
  return (
    <div className="space-y-space-4" data-testid="hybrid-result" data-method={result.method}>
      <section className={card} aria-labelledby="hy-charts">
        <h2 id="hy-charts" className="text-t18 font-semibold text-fg">{title}</h2>
        <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
          <span className={cx(CHIP, "bg-brand-tint text-brand-strong")} data-testid="hybrid-label">{result.label}</span>
          {stateChip && <span className={cx(CHIP, trend ? TONE[trend] : TONE.sideways)} data-testid="hybrid-state">{stateChip}</span>}
        </div>
        {readLine}
        {r.last_close != null && (
          <p className="mt-space-2 text-t13 text-fg-muted" data-testid="hybrid-close">
            Close <span className="font-tabular font-semibold text-fg">{num(r.last_close)}</span>
            {r.bars_read ? <> · <span className="font-tabular">{formatNumber(r.bars_read, 0)}</span> {r.timeframe_label.toLowerCase()} candles read</> : null}
            {` · from ${source}`}
          </p>
        )}
        <div className={cx("mt-space-3 grid gap-space-3", showYours && "md:grid-cols-2")}>
          {showYours && (
            <figure className="min-w-0" data-figure="yours">
              <figcaption className="mb-space-1 text-t13 font-semibold text-fg">Your chart</figcaption>
              {userImageSrc ? <img src={userImageSrc} alt="The chart you added" className="w-full rounded-control border border-subtle" />
                : <p className="rounded-control border border-subtle p-space-3 text-t13 text-fg-muted">{imageNote}</p>}
            </figure>
          )}
          <figure className="min-w-0" data-figure="atp">
            <figcaption className="mb-space-1 text-t13 font-semibold text-fg">ATP&apos;s reading of {title}</figcaption>
            {live ? (
              <Suspense fallback={<Skeleton className="aspect-video w-full" label="Loading ATP's chart" />}>
                <EmbeddedChartV2 symbol={r.symbol} timeframe={LIVE_TF[r.timeframe]} className="h-72 md:h-80" {...readingOverlay(r)} />
              </Suspense>
            ) : picture ?? <Skeleton className="aspect-video w-full" label="Loading ATP's chart" />}
            {!live && <Legend />}
          </figure>
        </div>
        <Details id="chart-analyze.reading-picture" label="The picture and the download">
          {live && picture && <>{picture}<Legend /></>}
          <p data-testid="side-by-side-note">{pictureNote(title)}</p>
          {actions && onDownload && (
            <Button className="h-11 md:h-9" icon={<Download size={16} aria-hidden />} loading={downloading} onClick={onDownload}>Download image</Button>
          )}
        </Details>
        {actions && onDelete && (
          <div className="mt-space-2 flex">
            <Button className="h-11 md:h-9" icon={<Trash2 size={16} aria-hidden />} loading={deleting} onClick={onDelete}>Delete</Button>
          </div>
        )}
      </section>

      <Structure r={r} row={row} />

      {r.areas.length > 0 && <Areas areas={r.areas} />}

      {scenarios && (
        <Block title="Scenarios" id="hy-scenarios">
          <div className="space-y-space-2 text-t14 text-fg" data-testid="hybrid-scenarios">
            {scenarios.main && <p><span className={cx("font-semibold", trend === "down" ? "text-down" : trend === "up" ? "text-up" : "text-fg")}>Main: </span>{guardText(scenarios.main)}</p>}
            {scenarios.other && <p><span className={cx("font-semibold", trend === "down" ? "text-up" : trend === "up" ? "text-down" : "text-fg")}>Other: </span>{guardText(scenarios.other)}</p>}
          </div>
        </Block>
      )}

      {watch.length > 0 && (
        <Block title="What to watch" id="hy-watch">
          <dl data-testid="hybrid-watch">
            {watch.map((w) => (
              <div key={w.when} className={cx(ROW_BASE, "grid-cols-[minmax(0,3fr)_minmax(0,2fr)]")} data-watch>
                <dt className="text-t14 text-fg">{guardText(w.when)}</dt>
                {w.meaning && <dd className="ml-auto text-right text-t13 text-fg-muted">{guardText(w.meaning)}</dd>}
              </div>
            ))}
          </dl>
        </Block>
      )}

      {present.length > 0 && (
        <Block title="Checklist" id="hy-checklist">
          <ul className="divide-y divide-subtle" data-testid="hybrid-checklist">
            {present.map((c) => (
              <li key={c.name} className="flex items-start gap-space-3 py-space-2" data-present="true">
                <CheckCircle2 size={18} aria-label="Present" className="mt-0.5 shrink-0 text-up" />
                <span className="min-w-0">
                  <span className="block text-t14 font-medium text-fg">{c.name}</span>
                  <span className="block font-tabular text-t12 text-fg-muted">{guardText(c.reason)}</span>
                </span>
              </li>
            ))}
          </ul>
          {present.length < r.checklist.length && (
            <Details id="chart-analyze.checks" label="Details">
              <p data-testid="hybrid-checks-count">{checksLine(r.checklist)}</p>
            </Details>
          )}
        </Block>
      )}

      {ai && (
        <Block title="AI reading of these facts" id="hy-ai">
          <div className="space-y-space-2 text-t14 text-fg">{ai.story.map((p, i) => <p key={i}>{p}</p>)}</div>
          {ai.what_to_wait_for?.condition && (
            <p className="mt-space-3 flex items-start gap-space-2 text-t14 text-fg">
              <Eye size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
              <span><span className="font-semibold">What to watch: </span>{ai.what_to_wait_for.condition}</span>
            </p>
          )}
          <details className="mt-space-3 text-t14 text-fg">
            <summary className="cursor-pointer text-t13 font-medium text-fg-muted">Scenarios</summary>
            <p className="mt-space-2">{ai.scenarios.primary}</p>
            <p className="mt-space-1 text-fg-muted">{ai.scenarios.alternative}</p>
          </details>
        </Block>
      )}
      {result.notes.length > 0 && (
        <ul className="space-y-space-1 text-t12 text-fg-muted" data-testid="hybrid-notes">{result.notes.map((n) => <li key={n}>{guardText(n)}</li>)}</ul>
      )}
      <p className="text-t12 text-fg-muted" data-testid="hybrid-disclaimer">{result.disclaimer}</p>
    </div>
  );
}
