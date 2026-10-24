import type { ReactNode } from "react";
import { HelpPopover } from "./HelpPopover";

/**
 * UI-H Wave 0 (H6, bug C1): the frame around every chart.
 * - `empty`: when set, a plain message sits ON TOP of the canvas (an overlay inside the frame), so a chart with no bars
 *   never shows a silent blank box. The canvas stays mounted underneath (its size and state are kept, and it appears
 *   the moment data arrives).
 * - `legend`: the legend slot under the title (what each colour / line is).
 * - `axis`: axis guidance - one short line on what the axes show ("Price on the right, from the lowest to the highest
 *   bar shown"), kept beside the legend instead of inside the canvas.
 * - `help`: the method text, behind a (?) (H3).
 * The frame does not draw the chart; the child (lightweight-charts, an SVG) does.
 */
export interface ChartFrameProps {
  title?: string;
  /** Title-row extras (a StatusBadge, a timeframe SubTabs). */
  actions?: ReactNode;
  legend?: ReactNode;
  axis?: ReactNode;
  help?: ReactNode;
  /** The empty / unavailable message; null or undefined when the chart has data. */
  empty?: ReactNode;
  /** The canvas height (a Tailwind height class); the default suits a page chart. */
  heightClass?: string;
  children: ReactNode;
  className?: string;
}

export function ChartFrame({ title, actions, legend, axis, help, empty, heightClass = "h-72 md:h-96", children, className = "" }: ChartFrameProps) {
  const hasEmpty = empty !== null && empty !== undefined && empty !== false;
  return (
    <figure data-chart-frame data-empty={hasEmpty ? "true" : undefined} aria-label={title}
      className={`m-0 min-w-0 rounded-panel border border-subtle bg-surface-1 ${className}`}>
      {(title || actions || help) && (
        <div className="flex flex-wrap items-center justify-between gap-space-2 border-b border-subtle px-space-3 py-space-2">
          {title && (
            <figcaption className="flex min-w-0 items-center gap-space-1 break-words text-t14 font-semibold text-fg">
              {title}{help && <HelpPopover title={title}>{help}</HelpPopover>}
            </figcaption>
          )}
          {actions && <div className="flex flex-wrap items-center gap-space-2">{actions}</div>}
        </div>
      )}
      {(legend || axis) && (
        <div className="flex flex-wrap items-center gap-x-space-4 gap-y-space-1 px-space-3 pt-space-2 text-t14 text-fg-muted" data-chart-legend>
          {legend}
          {axis && <span data-chart-axis className="min-w-0 break-words">{axis}</span>}
        </div>
      )}
      <div className={`relative min-w-0 ${heightClass}`}>
        <div className="absolute inset-0" data-chart-canvas aria-hidden={hasEmpty || undefined}>{children}</div>
        {hasEmpty && (
          <div role="status" data-chart-empty
            className="absolute inset-0 z-10 flex items-center justify-center bg-surface-1/85 p-space-4 text-center text-t14 text-fg">
            <div className="max-w-sm">{empty}</div>
          </div>
        )}
      </div>
    </figure>
  );
}

/** One legend entry: a swatch in a token colour class (`bg-oi-call`, `bg-brand`) and its words. */
export function LegendItem({ swatch, children }: { swatch: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-space-1">
      <span aria-hidden className={`inline-block h-2 w-3 rounded-pill ${swatch}`} />{children}
    </span>
  );
}
