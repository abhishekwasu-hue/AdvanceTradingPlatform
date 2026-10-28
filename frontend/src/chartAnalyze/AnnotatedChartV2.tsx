import { useState } from "react";
import { HALO, HALO_ALPHA, legendFor, shapesFor, type Shape, type Token } from "./shapes";
import type { Annotations } from "./types";
import { SubTabs } from "../ui/SubTabs";

/**
 * Analyze a chart, block 1: the annotated chart. Image only: the overlay is drawn on the user's own image (an SVG in
 * the image's pixel space, scaled with it), next to the original on a desktop and as two tabs on a phone.
 */

const colour = (token: Token, alpha = 1) => `rgb(var(--${token}) / ${alpha})`;
const halo = colour(HALO, HALO_ALPHA);
const label = (token: Token) => ({ fill: colour(token), stroke: halo, strokeWidth: 3, paintOrder: "stroke" as const });

function ShapeSvg({ s }: { s: Shape }) {
  const dash = "dashed" in s && s.dashed ? "6 4" : undefined;
  if (s.kind === "rect") {
    return (
      <g>
        <rect x={s.x} y={s.y} width={s.w} height={s.h} style={{ fill: colour(s.token, s.fillAlpha), stroke: colour(s.token, 0.9) }}
          strokeWidth={1} strokeDasharray={dash} />
        {s.label && <text x={s.x + 4} y={s.y + 13} fontSize={12} style={label(s.token)}>{s.label}</text>}
      </g>
    );
  }
  if (s.kind === "line") {
    return (
      <g>
        <line x1={s.x0} y1={s.y0} x2={s.x1} y2={s.y1} style={{ stroke: halo }} strokeWidth={(s.width ?? 1.5) + 3} />
        <line x1={s.x0} y1={s.y0} x2={s.x1} y2={s.y1} style={{ stroke: colour(s.token) }} strokeWidth={s.width ?? 1.5} strokeDasharray={dash} />
        {s.label && <text x={Math.min(s.x0, s.x1) + 4} y={Math.min(s.y0, s.y1) - 4} fontSize={12} style={label(s.token)}>{s.label}</text>}
      </g>
    );
  }
  if (s.kind === "dot") {
    return (
      <g>
        <circle cx={s.x} cy={s.y} r={s.r} style={{ fill: "none", stroke: halo }} strokeWidth={5} />
        <circle cx={s.x} cy={s.y} r={s.r} style={{ fill: "none", stroke: colour(s.token) }} strokeWidth={2} />
        {s.label && <text x={s.x + s.r + 3} y={s.y - s.r} fontSize={12} style={label(s.token)}>{s.label}</text>}
      </g>
    );
  }
  return <text x={s.x - 8} y={s.y} fontSize={12} fontWeight={700} style={label(s.token)}>{s.text}</text>;
}

export function Overlay({ src, width, height, annotations, alt }: { src: string; width: number; height: number; annotations: Annotations; alt: string }) {
  const shapes = shapesFor(annotations, width);
  return (
    <div className="relative w-full overflow-hidden rounded-control border border-subtle bg-surface-2" data-testid="annotated-chart">
      <img src={src} alt={alt} width={width} height={height} className="block h-auto w-full" />
      <svg viewBox={`0 0 ${width} ${height}`} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden
        preserveAspectRatio="none" data-shapes={shapes.length}>
        {shapes.map((s, i) => <ShapeSvg key={i} s={s} />)}
      </svg>
    </div>
  );
}

export function Legend({ annotations }: { annotations: Annotations }) {
  const rows = legendFor(annotations);
  if (!rows.length) return <p className="text-t12 text-fg-muted">The chart is clean.</p>;
  return (
    <ul className="flex flex-wrap gap-x-space-4 gap-y-space-1 text-t12 text-fg-muted" aria-label="What the drawing shows">
      {rows.map((r) => (
        <li key={r.text} className="inline-flex items-center gap-space-1">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-pill" style={{ background: colour(r.token) }} />{r.text}
        </li>
      ))}
    </ul>
  );
}

/** Original and annotated: side by side from md up, two tabs on a phone. */
export default function AnnotatedChartV2({ src, width, height, annotations }: { src: string; width: number; height: number; annotations: Annotations }) {
  const [tab, setTab] = useState<"annotated" | "original">("annotated");
  return (
    <div className="space-y-space-3">
      <div className="md:hidden">
        <SubTabs label="Chart view" value={tab} onChange={setTab} full
          items={[{ key: "annotated", label: "Annotated" }, { key: "original", label: "Original" }] as const} />
      </div>
      <div className="grid gap-space-4 md:grid-cols-2">
        <figure className={`${tab === "original" ? "block" : "hidden"} space-y-space-1 md:block`}>
          <figcaption className="text-t12 font-medium text-fg-muted">Your chart</figcaption>
          <img src={src} alt="Your chart as uploaded" width={width} height={height} className="block h-auto w-full rounded-control border border-subtle" />
        </figure>
        <figure className={`${tab === "annotated" ? "block" : "hidden"} space-y-space-1 md:block`}>
          <figcaption className="text-t12 font-medium text-fg-muted">What the method sees</figcaption>
          <Overlay src={src} width={width} height={height} annotations={annotations} alt="Your chart with the reading drawn on it" />
        </figure>
      </div>
      <Legend annotations={annotations} />
    </div>
  );
}

/**
 * The shared copy (share link): the drawing alone over a plain background, in the stored image's pixel space. The
 * user's picture is never part of a shared copy - it shows the chart's name and its price axis (CLAUDE.md, SEBI).
 */
export function StructureSketch({ width, height, annotations }: { width: number; height: number; annotations: Annotations }) {
  const shapes = shapesFor(annotations, width);
  return (
    <div className="space-y-space-3">
      <div className="relative w-full overflow-hidden rounded-control border border-subtle bg-surface-2" data-testid="structure-sketch"
        style={{ aspectRatio: `${Math.max(1, width)} / ${Math.max(1, height)}` }}>
        <svg viewBox={`0 0 ${width} ${height}`} className="absolute inset-0 h-full w-full" role="img"
          aria-label="The structure the method sees, drawn without the chart" preserveAspectRatio="none" data-shapes={shapes.length}>
          {shapes.map((s, i) => <ShapeSvg key={i} s={s} />)}
        </svg>
      </div>
      <Legend annotations={annotations} />
    </div>
  );
}
