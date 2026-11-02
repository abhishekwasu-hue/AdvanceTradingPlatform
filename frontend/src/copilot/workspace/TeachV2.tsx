import { useEffect, useState } from "react";
import type { ChartEngine } from "../../charting/engine";
import type { Annotations } from "./annotations";
import { teachArea, teachAt, type Teach } from "./teach";

/**
 * M76 MP-14 L-2 (owner order M76 L-2; flag `copilot_workspace`): teach-on-chart. Tap an area, a swing label, a BOS /
 * CHoCH or a divergence end on the workspace's chart: its one-line meaning shows under the chart (the one glossary; an
 * area's state in the reading's own words). The areas are also listed as buttons, for a keyboard or a screen reader.
 * No advice; nothing placed or changed. Rules: `teach.ts` (teach.test.tsx).
 */
const CHIP = "inline-flex min-h-11 items-center rounded-full border px-space-2 text-t12 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8";

export function TeachV2({ engine, annotations }: { engine: Pick<ChartEngine, "on"> | null; annotations: Annotations | null }) {
  const [teach, setTeach] = useState<Teach | null>(null);
  useEffect(() => { setTeach(null); }, [annotations]);
  useEffect(() => {
    if (!engine || !annotations) return undefined;
    return engine.on((e) => {
      if (e.type === "click") setTeach(teachAt(annotations, e.time, e.price));
    });
  }, [engine, annotations]);
  if (!annotations) return null;
  return (
    <div className="space-y-space-1" data-teach={teach ? teach.kind : "none"}>
      {teach ? (
        <div role="status" className="rounded-control border border-subtle bg-surface-2 px-space-3 py-space-2 text-t13" data-teach-line>
          <p className="font-semibold text-fg font-tabular">{teach.title}</p>
          <p className="text-fg">{teach.meaning}</p>
          {teach.state && <p className="text-fg-muted">{teach.state}</p>}
        </div>
      ) : <p className="text-t12 text-fg-muted">Tap an area or a label on the chart for its meaning.</p>}
      {annotations.areas.length > 0 && (
        <ul className="flex flex-wrap gap-space-1" aria-label="What each area means">
          {annotations.areas.map((a, i) => (
            <li key={`${a.role}-${a.low}-${a.high}-${i}`}>
              <button type="button" className={`${CHIP} ${a.side === "buyer" ? "border-up/50" : "border-down/50"} text-fg`} data-teach-area={i}
                onClick={() => setTeach(teachArea(a))}>{a.side === "buyer" ? "Buyer" : "Seller"} {a.low_text} - {a.high_text}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default TeachV2;
