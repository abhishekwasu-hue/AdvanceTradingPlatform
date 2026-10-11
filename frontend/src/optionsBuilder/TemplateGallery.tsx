/**
 * P1-c2: the template gallery - families as tabs, each template a card with its expiry shape (premiums left out: the
 * shape, not the money). Calendars and diagonals say "two expiries" instead of a misleading shape.
 */
import { useState } from "react";
import { cx } from "../components/primitives";
import { thumbnailShape, type TemplateInfo } from "./model";

function Thumb({ t }: { t: TemplateInfo }) {
  const ys = thumbnailShape(t);
  if (!ys) return <span className="flex h-10 items-center justify-center text-[10px] text-fg-muted">two expiries</span>;
  const lo = Math.min(...ys, 0);
  const hi = Math.max(...ys, 0);
  const span = hi - lo || 1;
  const pts = ys.map((y, i) => `${(i / (ys.length - 1)) * 100},${36 - ((y - lo) / span) * 32}`).join(" ");
  const zero = 36 - ((0 - lo) / span) * 32;
  return (
    <svg viewBox="0 0 100 40" className="h-10 w-full" aria-hidden>
      <line x1={0} x2={100} y1={zero} y2={zero} className="stroke-fg-muted/40" strokeDasharray="2 2" />
      <polyline points={pts} className="fill-none stroke-brand" strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function TemplateGallery({ families, templates, onPick, disabled }: {
  families: Record<string, string[]>; templates: Record<string, TemplateInfo>; onPick: (name: string) => void; disabled?: boolean;
}) {
  const names = Object.keys(families);
  const [family, setFamily] = useState(names[0] ?? "");
  return (
    <section aria-label="Templates" className="rounded-xl border border-border bg-surface-1 p-3">
      <div role="tablist" aria-label="Families" className="flex flex-wrap gap-1">
        {names.map((f) => (
          <button key={f} role="tab" type="button" aria-selected={family === f} onClick={() => setFamily(f)}
                  className={cx("rounded-md px-3 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-brand", family === f ? "bg-surface-3 text-fg" : "text-fg-muted hover:text-fg")}>
            {f}
          </button>
        ))}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {(families[family] ?? []).map((n) => (
          <button key={n} type="button" disabled={disabled} onClick={() => onPick(n)} title={templates[n]?.what}
                  className="rounded-lg border border-border bg-surface p-2 text-left hover:border-brand/60 focus:outline-none focus:ring-2 focus:ring-brand disabled:opacity-50"
                  data-testid="template-card">
            {templates[n] && <Thumb t={templates[n]} />}
            <span className="mt-1 block truncate text-xs text-fg">{n}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
