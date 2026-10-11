/**
 * U7 LG1: one Legends card in full - the disclaimer naming the person, the five card lines (who, style, what the screen
 * looks for, timeframe, source), what the screen approximates, the ScreenQL source with its editable parameters and
 * their documented ranges, and the public sources the card cites. Every word comes from the server's reviewed data
 * files (GET /api/screener/legends); nothing here is written in code.
 *
 * Keyboard and screen readers: the panel is an article named by its heading, which takes focus when the panel opens
 * so the reader starts at the card; the lines are a description list; the parameters are a table with a caption and
 * column headers; "Back to screens" returns to the grid.
 */
import { ArrowLeft } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button } from "../components/primitives";
import { cardLines, filledSource, rangeText, type LegendCardData } from "./legends";

export interface LegendCardProps {
  card: LegendCardData;
  onBack?: () => void;
  /** Whether this page can load the screen into the builder (OPEN_QUESTIONS TX1-7: not yet for Legends). */
  canOpen?: boolean;
  onOpen?: () => void;
  /** Move focus to the heading on mount (the gallery does this when the card opens). */
  focusOnMount?: boolean;
}

export function LegendCard({ card, onBack, canOpen = false, onOpen, focusOnMount = false }: LegendCardProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusOnMount) heading.current?.focus(); }, [focusOnMount]);
  const headingId = `legend-${card.id}`;

  return (
    <article className="rounded-panel border border-border bg-surface-1 p-4" aria-labelledby={headingId} data-legend-id={card.id}>
      {onBack && (
        <Button size="sm" variant="ghost" onClick={onBack} className="mb-2"><ArrowLeft size={14} aria-hidden /> Back to screens</Button>
      )}
      <p className="text-t12 text-fg-muted">Legends · {card.legend}</p>
      <h4 id={headingId} ref={heading} tabIndex={-1} className="mt-1 text-t15 font-semibold text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        {card.name}
      </h4>
      <p role="note" className="mt-2 rounded-control border border-border bg-surface-2 px-2 py-1.5 text-t12 text-fg" data-testid="legend-disclaimer">
        {card.disclaimer}
      </p>

      <dl className="mt-3 grid gap-x-3 gap-y-1.5 text-t13 sm:grid-cols-[10rem_1fr]" aria-label="About the method">
        {cardLines(card).map((l) => (
          <div key={l.key} className="contents">
            <dt className="text-fg-muted">{l.label}</dt>
            <dd className="text-fg">{l.text}</dd>
          </div>
        ))}
      </dl>

      <section className="mt-4" aria-labelledby={`${headingId}-screen`}>
        <h5 id={`${headingId}-screen`} className="text-t13 font-medium text-fg">The screen</h5>
        <p className="mt-1 text-t13 text-fg-muted">An approximation of the published rules. {card.approximation}</p>
        <p className="mt-2 text-t12 text-fg-muted">
          Timeframe <span className="font-mono text-fg">{card.screen.base_tf}</span> · shown with the default parameters
        </p>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded-control border border-border bg-surface-inset p-2 font-mono text-t12 text-fg"
             aria-label="ScreenQL source" data-testid="legend-source"><code>{filledSource(card)}</code></pre>
        {card.screen.params.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full border-collapse text-t12">
              <caption className="text-left text-t12 text-fg-muted">Editable parameters (default and documented range)</caption>
              <thead>
                <tr className="border-b border-border">
                  <th scope="col" className="py-1 pr-2 text-left font-medium text-fg-muted">Parameter</th>
                  <th scope="col" className="py-1 pr-2 text-right font-medium text-fg-muted">Default</th>
                  <th scope="col" className="py-1 pr-2 text-left font-medium text-fg-muted">Range</th>
                  <th scope="col" className="py-1 text-left font-medium text-fg-muted">Meaning</th>
                </tr>
              </thead>
              <tbody>
                {card.screen.params.map((p) => (
                  <tr key={p.name} className="border-b border-border last:border-0">
                    <th scope="row" className="py-1 pr-2 text-left font-mono font-normal text-fg">${p.name}</th>
                    <td className="py-1 pr-2 text-right font-mono font-tabular text-fg">{p.default}</td>
                    <td className="py-1 pr-2 font-mono font-tabular text-fg-muted">{rangeText(p)}</td>
                    <td className="py-1 text-fg-muted">{p.doc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="mt-4" aria-labelledby={`${headingId}-sources`}>
        <h5 id={`${headingId}-sources`} className="text-t13 font-medium text-fg">Public sources</h5>
        <ul className="mt-1 space-y-1 text-t12 text-fg-muted">
          {card.sources.map((s) => (
            <li key={s.id}>
              <cite className="not-italic text-fg">{s.title}</cite> ({s.year}), {s.authors.join(" and ")}{s.publisher ? `, ${s.publisher}` : ""}.
            </li>
          ))}
        </ul>
      </section>

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button size="sm" onClick={onOpen} disabled={!canOpen || !onOpen} aria-label={`Open ${card.name} in the builder`}
                title={canOpen ? undefined : "The builder cannot load this screen yet; its ScreenQL source is shown above"}>Open</Button>
      </div>
    </article>
  );
}
