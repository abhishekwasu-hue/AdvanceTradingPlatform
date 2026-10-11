/**
 * U9 TX1: the screen gallery - a left rail of methods (how a screen reasons), top chips of setup types (what it looks
 * for) and a grid of screen cards, with a search over names and definitions and the full method × setup matrix. Every
 * label, definition and count comes from the server's taxonomy (data, not code). An empty cell is shown as empty and
 * says so; nothing here ranks, scores or recommends a screen.
 *
 * Keyboard: the rail and the chips are toggle buttons (aria-pressed) in reading order; the result count is a polite
 * live region; every card's "Open" names its screen.
 */
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Button, cx } from "../components/primitives";
import {
  cellCount, chipCount, emptyText, filterScreens, NO_FILTER, placeText, railCount, tagLabel,
  type GalleryFilter, type TaxAxis, type Taxonomy, type TaxScreen,
} from "./taxonomy";

export interface ScreenGalleryProps {
  taxonomy: Taxonomy;
  /** Whether this page can load the screen into the builder (a source whose builder is not here yet cannot). */
  canOpen: (screen: TaxScreen) => boolean;
  onOpen: (screen: TaxScreen) => void;
  initial?: Partial<GalleryFilter>;
}

export function ScreenGallery({ taxonomy, canOpen, onOpen, initial }: ScreenGalleryProps) {
  const [filter, setFilter] = useState<GalleryFilter>({ ...NO_FILTER, ...initial });
  const shown = useMemo(() => filterScreens(taxonomy, filter), [taxonomy, filter]);
  const pick = (next: Partial<GalleryFilter>) => setFilter((f) => ({ ...f, ...next }));
  const methods = taxonomy.methods;
  const setups = taxonomy.setups;

  return (
    <div className="screen-gallery" data-testid="screen-gallery">
      <nav aria-label="Method" className="screen-gallery-rail">
        <h3 className="mb-2 text-t12 font-medium text-fg-muted">Method</h3>
        <ul className="screen-gallery-rail-list">
          <li><RailItem label="All methods" count={railCount(taxonomy, null, filter)} active={filter.method === null} onClick={() => pick({ method: null })} /></li>
          {methods.map((m) => (
            <li key={m.id}>
              <RailItem axis={m} label={m.label} count={railCount(taxonomy, m.id, filter)} active={filter.method === m.id}
                        onClick={() => pick({ method: m.id })} note={m.user_built ? "Yours" : undefined} />
            </li>
          ))}
        </ul>
      </nav>

      <div className="min-w-0">
        <div role="group" aria-label="Setup type" className="flex flex-wrap gap-1.5">
          <Chip label="All setups" count={chipCount(taxonomy, null, filter)} active={filter.setup === null} onClick={() => pick({ setup: null })} />
          {setups.map((s) => (
            <Chip key={s.id} axis={s} label={s.label} count={chipCount(taxonomy, s.id, filter)} active={filter.setup === s.id} onClick={() => pick({ setup: s.id })} />
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <label className="relative block w-full max-w-xs">
            <span className="sr-only">Search screens by name or definition</span>
            <Search aria-hidden size={14} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-fg-muted" />
            <input type="search" value={filter.query} onChange={(e) => pick({ query: e.target.value })} placeholder="Search names and definitions"
                   className="h-8 w-full rounded-control border border-border bg-surface-inset pl-7 pr-2 text-t13 text-fg placeholder:text-fg-muted/70 focus:outline-none focus:ring-2 focus:ring-brand" />
          </label>
          <p className="text-t12 text-fg-muted" aria-live="polite" aria-atomic="true" data-testid="gallery-count">
            <span className="font-mono font-tabular text-fg">{shown.length}</span> {shown.length === 1 ? "screen" : "screens"} · {placeText(taxonomy, filter)}
          </p>
        </div>

        {shown.length === 0 ? (
          <p className="mt-3 rounded-control border border-dashed border-border px-4 py-8 text-center text-t13 text-fg-muted" data-testid="gallery-empty">
            {emptyText(taxonomy, filter)}
          </p>
        ) : (
          <ul aria-label="Screens" className="screen-gallery-cards mt-3">
            {shown.map((s) => <li key={s.id}><ScreenCard taxonomy={taxonomy} screen={s} canOpen={canOpen(s)} onOpen={() => onOpen(s)} /></li>)}
          </ul>
        )}

        <details className="mt-4 rounded-control border border-border">
          <summary className="cursor-pointer px-3 py-2 text-t13 text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">Method × setup matrix</summary>
          <Matrix taxonomy={taxonomy} filter={filter} onPick={(method, setup) => pick({ method, setup })} />
        </details>
        <p className="mt-3 text-t12 text-fg-muted">{taxonomy.note}</p>
      </div>
    </div>
  );
}

function RailItem({ axis, label, count, active, note, onClick }: { axis?: TaxAxis; label: string; count: number; active: boolean; note?: string; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={active} title={axis?.definition} onClick={onClick}
            className={cx("flex h-9 w-full items-center gap-2 rounded-control px-2 text-left text-t13 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand",
              active ? "bg-surface-2 font-medium text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg")}>
      {axis && <span aria-hidden className="w-4 font-mono text-t12 text-fg-muted">{axis.code}</span>}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {note && <span className="text-t12 text-fg-muted">{note}</span>}
      <Count n={count} />
    </button>
  );
}

function Chip({ axis, label, count, active, onClick }: { axis?: TaxAxis; label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={active} title={axis?.definition} onClick={onClick}
            className={cx("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-t12 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand",
              active ? "border-signal bg-signal/10 text-fg" : count === 0 ? "border-dashed border-border text-fg-muted" : "border-border text-fg hover:bg-surface-2")}>
      {label}
      <Count n={count} />
    </button>
  );
}

/** A count; zero reads "0" in muted type (and "(none)" to a screen reader), never hidden. */
function Count({ n }: { n: number }) {
  return (
    <span className={cx("font-mono font-tabular text-t12", n === 0 ? "text-fg-muted/70" : "text-fg-muted")}>
      <span aria-hidden>{n}</span><span className="sr-only">{n === 0 ? "(none)" : `(${n} ${n === 1 ? "screen" : "screens"})`}</span>
    </span>
  );
}

function ScreenCard({ taxonomy, screen, canOpen, onOpen }: { taxonomy: Taxonomy; screen: TaxScreen; canOpen: boolean; onOpen: () => void }) {
  const method = taxonomy.methods.find((m) => m.id === screen.method);
  const setup = taxonomy.setups.find((s) => s.id === screen.setup);
  const t = screen.tags;
  return (
    <article className="flex h-full flex-col rounded-panel border border-border bg-surface-1 p-3" aria-labelledby={`screen-${screen.id}`} data-screen-id={screen.id}>
      <p className="text-t12 text-fg-muted">{method?.label ?? screen.method} → {setup?.label ?? screen.setup}</p>
      <h4 id={`screen-${screen.id}`} className="mt-1 text-t15 font-semibold text-fg">{screen.name}</h4>
      <p className="mt-1 flex-1 text-t13 text-fg-muted">{screen.definition}</p>
      <ul aria-label="Tags" className="mt-2 flex flex-wrap gap-1">
        {t.timeframe_family.map((tf) => <Tag key={tf}>{tagLabel(taxonomy, "timeframe_family", tf)}</Tag>)}
        <Tag>{tagLabel(taxonomy, "direction", t.direction)}</Tag>
        {t.data_tier.map((d) => <Tag key={d} strong>{tagLabel(taxonomy, "data_tier", d)}</Tag>)}
        <Tag>{tagLabel(taxonomy, "difficulty", t.difficulty)}</Tag>
        {screen.evidence_ready && <Tag>Evidence chart</Tag>}
        {screen.fitness_available && <Tag>Fitness</Tag>}
      </ul>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-t12 text-fg-muted">{tagLabel(taxonomy, "universe_default", t.universe_default)}</span>
        <Button size="sm" onClick={onOpen} disabled={!canOpen} aria-label={`Open ${screen.name} in the builder`}
                title={canOpen ? undefined : "This screen's family is not available on this page yet"}>Open</Button>
      </div>
    </article>
  );
}

function Tag({ children, strong = false }: { children: string; strong?: boolean }) {
  return <li className={cx("rounded-control border px-1.5 text-t12", strong ? "border-border bg-surface-2 text-fg" : "border-border text-fg-muted")}>{children}</li>;
}

/** Every cell of method × setup, with its count; an empty cell shows "–" and says "none" to a screen reader. */
function Matrix({ taxonomy, filter, onPick }: { taxonomy: Taxonomy; filter: GalleryFilter; onPick: (method: string, setup: string) => void }) {
  return (
    <div className="overflow-x-auto px-3 pb-3">
      <table className="w-full border-collapse text-t12">
        <caption className="sr-only">Screens filed by method (rows) and setup type (columns); {taxonomy.total} in all</caption>
        <thead>
          <tr>
            <th scope="col" className="py-1 pr-2 text-left font-medium text-fg-muted">Method</th>
            {taxonomy.setups.map((s) => (
              <th key={s.id} scope="col" title={s.label} className="px-1 py-1 text-center font-medium text-fg-muted">
                <span aria-hidden className="font-mono">{s.code}</span><span className="sr-only">{s.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {taxonomy.methods.map((m) => (
            <tr key={m.id} className="border-t border-border">
              <th scope="row" className="py-1 pr-2 text-left font-normal text-fg">{m.label}</th>
              {taxonomy.setups.map((s) => {
                const n = cellCount(taxonomy, m.id, s.id);
                const here = filter.method === m.id && filter.setup === s.id;
                return (
                  <td key={s.id} className="px-1 py-1 text-center">
                    {n === 0 ? (
                      <span className="font-mono text-fg-muted/70" aria-label={`${m.label}, ${s.label}: none`}>–</span>
                    ) : (
                      <button type="button" aria-pressed={here} onClick={() => onPick(m.id, s.id)} aria-label={`${m.label}, ${s.label}: ${n} ${n === 1 ? "screen" : "screens"}`}
                              className={cx("min-w-6 rounded-control px-1 font-mono font-tabular focus:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                                here ? "bg-signal/15 text-fg" : "text-fg hover:bg-surface-2")}>{n}</button>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-t12 text-fg-muted">
        Columns: {taxonomy.setups.map((s) => `${s.code} ${s.label}`).join(" · ")}
      </p>
    </div>
  );
}
