import { cx } from "../../components/primitives/cx";
import { Details } from "../../ui/Details";
import { formatDateIST, formatNumber, formatTimeIST } from "../../ui/format";
import { dryLines, dryOrderLine, dryTone, type DryRow, type DryRunView } from "./triggerPlans";

/**
 * M76 MP-12 (owner order M76 D-2; amendment: one compact card, facts only): the dry run of a trigger plan, in place.
 * Numbers first: the sessions replayed, how many would have triggered, then at most VISIBLE lines (the triggered sessions
 * first) and the rest behind "More"; what each order would send now (the ticket's own checks). Nothing is placed: the
 * server's dry run writes nothing and this card has no action.
 */
const VISIBLE = 3;
const TONE = { up: "text-up", warn: "text-warn", muted: "text-fg-muted" } as const;

function Line({ r }: { r: DryRow }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-space-2 text-t13" data-dry-row={r.state}>
      <span className="font-tabular text-fg">{formatDateIST(r.session)}</span>
      <span className={cx("font-semibold", TONE[dryTone(r.state)])}>{r.state_words}</span>
      {r.at && <span className="font-tabular text-fg">{formatTimeIST(r.at)}</span>}
      {r.price != null && <span className="font-tabular text-fg">{formatNumber(r.price, 2)}</span>}
      {r.words && <span className="min-w-0 text-t12 text-fg-muted">{r.words}</span>}
    </li>
  );
}

export function DryRunCardV2({ view }: { view: DryRunView }) {
  const lines = dryLines(view);
  const sent = view.on_trigger;
  return (
    <section aria-label="Dry run" className="space-y-space-2 rounded-panel border border-subtle p-space-3" data-trigger="dry-card">
      <div className="flex flex-wrap items-baseline gap-x-space-2 text-t13">
        <span className="font-semibold text-fg">Dry run</span>
        <span className="font-tabular text-fg" data-dry="count">{view.sessions.length} of {view.sessions_asked} sessions</span>
        <span className={cx("font-tabular font-semibold", view.triggered ? "text-up" : "text-fg-muted")} data-dry="triggered">
          {view.triggered} would have triggered
        </span>
        <span className="text-t12 text-fg-muted">Nothing placed (PAPER)</span>
      </div>
      <p className="text-t12 text-fg-muted" data-dry="condition">{view.condition_words}</p>
      {view.note && <p className="text-t12 text-fg-muted" data-dry="note">{view.note}</p>}
      {lines.length > 0 && <ul className="space-y-space-1">{lines.slice(0, VISIBLE).map((r) => <Line key={r.session} r={r} />)}</ul>}
      {sent && (
        <ul className="space-y-space-1" data-dry="orders">
          {sent.orders.map((o, i) => {
            const line = dryOrderLine(o);
            return (
              <li key={i} className="flex flex-wrap items-baseline gap-x-space-2 text-t13">
                <span className="font-tabular text-fg">{o.side} {o.lots} · {o.label}</span>
                {o.fill_price != null && <span className="font-tabular text-fg">{formatNumber(o.fill_price, 2)}</span>}
                <span className={line.ok ? "text-up" : "text-warn"}>{line.text}</span>
              </li>
            );
          })}
          <li className="text-t12 text-fg-muted" data-dry="on-trigger">{sent.words}</li>
        </ul>
      )}
      {(lines.length > VISIBLE || view.cut) && (
        <Details id="copilot.trigger.dry" label="More" className="mt-0">
          <ul className="space-y-space-1">{lines.slice(VISIBLE).map((r) => <Line key={r.session} r={r} />)}</ul>
          <p>{view.once} Candles read: {view.reads}.</p>
        </Details>
      )}
    </section>
  );
}

export default DryRunCardV2;
