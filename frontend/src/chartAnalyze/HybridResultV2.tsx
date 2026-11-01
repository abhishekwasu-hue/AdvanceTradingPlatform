import { CheckCircle2, CircleSlash, Download, Eye, Trash2 } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Button, Skeleton } from "../components/primitives";
import { cx } from "../components/primitives/cx";
import { EMPTY, formatNumber } from "../ui/format";
import { guardDeep, guardText } from "./signalLanguage";
import type { HybridArea, HybridResult, HybridRow } from "./types";

/**
 * M59 CR-4: the hybrid result - "Advanced analysis (code + AI)", or "(code)" when the engine has no AI part.
 * ATP does not know the uploaded picture's price scale, so nothing is drawn on it: "Your chart" sits beside "ATP's
 * reading of <SYMBOL> <TF>", a chart the server drew from the stored candles with the reading's annotations (areas of
 * interest as bands, the protected level and swing levels as lines, the trigger candle marked). Below: the structure
 * table (CR-1 card style: Timeframe | Trend | Protected level | Last break), the areas nearby (above / below, distance
 * in points), the checklist of ATP's facts (present / absent, never scored) and, only with the engine's AI part, its
 * reading. Every number comes from the stored candles; every text passes the signal-language check; no order button.
 */

const card = "rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5";
const CHIP = "inline-flex min-h-7 items-center whitespace-nowrap rounded-pill px-space-2 text-t12 font-semibold";
const TONE = { up: "bg-up-tint text-up", down: "bg-down-tint text-down", sideways: "bg-surface-2 text-fg" } as const;
const TREND_WORD = { up: "Up", down: "Down", sideways: "Sideways" } as const;
const KEEP = new Set(["disclaimer"]);
const num = (v: number | null | undefined) => (v == null ? EMPTY : formatNumber(v, 2));

export function trendText(row: HybridRow): string {
  if (!row.trend) return "Not read yet";
  return TREND_WORD[row.trend] + (row.weak ? " (weak)" : "");
}

export function areaNote(a: HybridArea): string {
  if (a.where === "inside") return "The price is inside it";
  const away = a.distance == null ? "" : `${formatNumber(a.distance, 2)} points away`;
  return [away, a.state].filter(Boolean).join(" · ");
}

function Block({ title, id, children, help }: { title: string; id: string; children: ReactNode; help?: ReactNode }) {
  return (
    <section aria-labelledby={id} className={card} data-block={id}>
      <div className="mb-space-3 flex items-center justify-between gap-space-2">
        <h3 id={id} className="text-t15 font-semibold text-fg">{title}</h3>
        {help}
      </div>
      {children}
    </section>
  );
}

function AreaList({ title, areas, tone }: { title: string; areas: HybridArea[]; tone: "up" | "down" }) {
  return (
    <div className="min-w-0" data-areas={title.toLowerCase()}>
      <h4 className="text-t12 font-medium uppercase text-fg-muted">{title}</h4>
      {areas.length === 0 ? <p className="mt-space-1 text-t13 text-fg-muted">{EMPTY}</p> : (
        <ul className="mt-space-1 space-y-space-1">
          {areas.map((a) => (
            <li key={`${a.low}-${a.high}`} className="font-tabular" data-area={a.where}>
              <span className={cx("block break-words text-t12 font-semibold md:whitespace-nowrap md:text-t13", tone === "up" ? "text-up" : "text-down")}>{num(a.low)} – {num(a.high)}</span>
              <span className="block text-t12 text-fg-muted">{areaNote(a)}</span>
            </li>
          ))}
        </ul>
      )}
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
}

export default function HybridResultV2({ result, userImageSrc, readingSrc, imageNote, onDownload, onDelete, downloading, deleting, actions = true }: HybridResultProps) {
  const r = result.reading;
  const ai = result.ai ? guardDeep(result.ai, KEEP) : null;
  const title = `${r.symbol} ${r.timeframe_label}`;
  const above = r.areas.filter((a) => a.where === "above");
  const below = r.areas.filter((a) => a.where === "below");
  const inside = r.areas.filter((a) => a.where === "inside");
  return (
    <div className="space-y-space-4" data-testid="hybrid-result" data-method={result.method}>
      <section className={card} aria-labelledby="hy-charts">
        <div className="flex flex-wrap items-center gap-space-2">
          <h2 id="hy-charts" className="text-t15 font-semibold text-fg md:text-t18">{title}</h2>
          <span className={cx(CHIP, "bg-brand-tint text-brand-strong")} data-testid="hybrid-label">{result.label}</span>
        </div>
        {r.last_close != null && (
          <p className="mt-space-1 text-t12 text-fg-muted">Last close <span className="font-tabular text-fg">{num(r.last_close)}</span> · from the stored candles</p>
        )}
        <div className="mt-space-3 grid gap-space-3 md:grid-cols-2">
          <figure className="min-w-0" data-figure="yours">
            <figcaption className="mb-space-1 text-t13 font-semibold text-fg">Your chart</figcaption>
            {userImageSrc ? <img src={userImageSrc} alt="The chart you added" className="w-full rounded-control border border-subtle" />
              : <p className="rounded-control border border-subtle p-space-3 text-t13 text-fg-muted">{imageNote ?? "The chart image is not shown."}</p>}
          </figure>
          <figure className="min-w-0" data-figure="atp">
            <figcaption className="mb-space-1 text-t13 font-semibold text-fg">ATP&apos;s reading of {title}</figcaption>
            {readingSrc ? <img src={readingSrc} alt={`ATP's reading of ${title}, drawn on the stored candles`} className="w-full rounded-control border border-subtle" />
              : <Skeleton className="aspect-video w-full" label="Loading ATP's chart" />}
            <Legend />
          </figure>
        </div>
        <p className="mt-space-2 text-t12 text-fg-muted" data-testid="side-by-side-note">{result.side_by_side_note}</p>
        {actions && (
          <div className="mt-space-3 flex flex-col gap-space-2 sm:flex-row">
            <Button className="h-11 md:h-9" icon={<Download size={16} aria-hidden />} loading={downloading} onClick={onDownload}>Download image</Button>
            <Button className="h-11 md:h-9" icon={<Trash2 size={16} aria-hidden />} loading={deleting} onClick={onDelete}>Delete</Button>
          </div>
        )}
      </section>

      <Block title="Structure" id="hy-structure">
        <div className="-mx-space-1 overflow-x-auto">
          <table className="w-full min-w-0 border-collapse text-left" data-testid="hybrid-table">
            <thead>
              <tr className="text-t12 font-medium uppercase text-fg-muted">
                <th scope="col" className="px-space-1 py-space-1 font-medium">Timeframe</th>
                <th scope="col" className="px-space-1 py-space-1 font-medium">Trend</th>
                <th scope="col" className="px-space-1 py-space-1 text-right font-medium">Protected level</th>
                <th scope="col" className="hidden px-space-1 py-space-1 font-medium md:table-cell">Last break</th>
              </tr>
            </thead>
            <tbody>
              {r.table.map((row) => (
                <Fragment key={row.tf}>
                  <tr className="border-t border-subtle align-middle" data-tf={row.tf} data-ready={row.ready}>
                    {row.ready ? (
                      <>
                        <th scope="row" className="px-space-1 py-space-2 text-t13 font-semibold text-fg">{row.label}{row.tf === r.timeframe ? " · your chart" : ""}</th>
                        <td className="px-space-1 py-space-2">
                          <span className={cx(CHIP, row.trend ? TONE[row.trend] : "bg-surface-2 text-fg-muted")} data-trend={row.trend ?? "none"}>{trendText(row)}</span>
                        </td>
                        <td className="px-space-1 py-space-2 text-right font-tabular text-t13 text-fg">{num(row.protected)}</td>
                        <td className="hidden px-space-1 py-space-2 text-t13 text-fg-muted md:table-cell">
                          {row.last_break ? <>{row.last_break.label} <span className="font-tabular text-fg">{num(row.last_break.price)}</span></> : EMPTY}
                        </td>
                      </>
                    ) : (
                      <td colSpan={4} className="px-space-1 py-space-2">
                        <span className={cx(CHIP, "bg-surface-2 text-fg-muted")} data-not-ready={row.tf}>{row.label}: data not ready</span>
                      </td>
                    )}
                  </tr>
                  {row.ready && row.last_break && (
                    <tr className="md:hidden">
                      <td colSpan={3} className="px-space-1 pb-space-2 text-t12 text-fg-muted">
                        Last break: {row.last_break.label} <span className="font-tabular text-fg">{num(row.last_break.price)}</span>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-space-3" data-card-block="areas">
          <h4 className="text-t13 font-semibold text-fg">Areas of interest nearby</h4>
          {r.zones_note ? <p className="mt-space-1 text-t13 text-fg-muted">{r.zones_note}</p> : (
            <>
              {inside.length > 0 && <p className="mt-space-1 text-t13 text-fg">At the price: <span className="font-tabular">{num(inside[0].low)} – {num(inside[0].high)}</span></p>}
              <div className="mt-space-1 grid grid-cols-2 gap-space-3 md:max-w-3xl">
                <AreaList title="Above" areas={above} tone="down" />
                <AreaList title="Below" areas={below} tone="up" />
              </div>
            </>
          )}
        </div>
      </Block>

      <Block title="Checklist" id="hy-checklist">
        <ul className="divide-y divide-subtle" data-testid="hybrid-checklist">
          {r.checklist.map((c) => (
            <li key={c.name} className="flex items-start gap-space-3 py-space-2" data-present={c.present}>
              {c.present ? <CheckCircle2 size={18} aria-label="Present" className="mt-0.5 shrink-0 text-up" />
                : <CircleSlash size={18} aria-label="Not present" className="mt-0.5 shrink-0 text-fg-muted" />}
              <span className="min-w-0">
                <span className="block text-t14 font-medium text-fg">{c.name}</span>
                <span className="block font-tabular text-t12 text-fg-muted">{guardText(c.reason)}</span>
              </span>
            </li>
          ))}
        </ul>
      </Block>

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
