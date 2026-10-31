import { Eye } from "lucide-react";
import { Fragment } from "react";
import { cx } from "../../components/primitives/cx";
import { HelpPopover } from "../../ui/HelpPopover";
import { EMPTY, formatNumber } from "../../ui/format";
import { areaNote, cardAreaText, cardTrendText, notReadyText, swingText, type CardArea, type StructureCard } from "./model";

/**
 * M59 CR-1 (owner order ATP_ORDER_M59): the Copilot's chart reading as a card, not prose - the structure per timeframe
 * as a table (Timeframe | Trend | Protected level | Last break) with trend chips, "Areas nearby" as two short lists
 * (above / below, distance in points), the swing levels as chips and ONE plain "what to watch" line. A timeframe whose
 * history is not stored yet is one "data not ready" chip, never a bar count. Numbers: 2 decimals, Indian grouping.
 * Phone: the table, the areas and the line are the first screen; the swing levels sit behind a (?) there.
 * Every word and number comes from the server (backend copilot_chat.structure_card); nothing here is a call to act.
 */
const num = (v: number) => formatNumber(v, 2);
const TONE = { up: "bg-up-tint text-up", down: "bg-down-tint text-down", sideways: "bg-surface-2 text-fg" } as const;
const CHIP = "inline-flex min-h-7 items-center whitespace-nowrap rounded-pill px-space-2 text-t12 font-semibold";

function AreaList({ title, areas, tone }: { title: string; areas: CardArea[]; tone: "up" | "down" }) {
  return (
    <div className="min-w-0" data-areas={title.toLowerCase()}>
      <h4 className="text-t12 font-medium uppercase text-fg-muted">{title}</h4>
      {areas.length === 0 ? <p className="mt-space-1 text-t13 text-fg-muted">{EMPTY}</p> : (
        <ul className="mt-space-1 space-y-space-1">
          {areas.map((a) => (
            <li key={`${a.from}-${a.to}`} className="font-tabular" data-area={a.where} aria-label={cardAreaText(a, num)}>
              <span className={cx("block whitespace-nowrap text-t12 font-semibold md:text-t13", tone === "up" ? "text-up" : "text-down")}>{num(a.from)} – {num(a.to)}</span>
              <span className="block text-t12 text-fg-muted">{areaNote(a, num)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SwingChips({ levels }: { levels: StructureCard["swing_levels"] }) {
  return (
    <ul className="flex flex-wrap gap-space-2">
      {levels.map((lv) => (
        <li key={`${lv.side}-${lv.price}`} data-swing={lv.side ?? "level"}
          className="inline-flex min-h-7 items-center rounded-pill border border-border bg-surface-2 px-space-3 font-tabular text-t13 text-fg">
          {swingText(lv, num)}
        </li>
      ))}
    </ul>
  );
}

export function StructureCardV2({ card }: { card: StructureCard }) {
  const areas = card.areas_above.length > 0 || card.areas_below.length > 0;
  const swings = card.swing_levels.length > 0;
  // phone: the swing levels sit behind a (?) beside "Areas nearby" (no extra block on the first screen)
  const swingHelp = swings && <span className="md:hidden"><HelpPopover title="Swing levels" align="end"><SwingChips levels={card.swing_levels} /></HelpPopover></span>;
  return (
    <section className="space-y-space-3 rounded-control border border-subtle p-space-3" data-structure-card={card.symbol} aria-label={`${card.symbol} structure`}>
      <div className="-mx-space-1 overflow-x-auto md:max-w-3xl" data-card-block="table">
        <table className="w-full min-w-0 border-collapse text-left">
          <thead>
            <tr className="text-t12 font-medium uppercase text-fg-muted">
              <th scope="col" className="px-space-1 py-space-1 font-medium">Timeframe</th>
              <th scope="col" className="px-space-1 py-space-1 font-medium">Trend</th>
              <th scope="col" className="px-space-1 py-space-1 text-right font-medium">Protected level</th>
              <th scope="col" className="hidden px-space-1 py-space-1 font-medium md:table-cell">Last break</th>
            </tr>
          </thead>
          <tbody>
            {card.timeframes.map((row) => (
              <Fragment key={row.tf}>
                <tr className="border-t border-subtle align-middle" data-tf={row.tf} data-ready={row.ready}>
                  {row.ready ? (
                    <>
                      <th scope="row" className="px-space-1 py-space-2 text-t13 font-semibold text-fg">{row.label}</th>
                      <td className="px-space-1 py-space-2">
                        <span className={cx(CHIP, row.trend ? TONE[row.trend] : "bg-surface-2 text-fg-muted")} data-trend={row.trend ?? "none"}
                          data-weak={row.weak || undefined}>{cardTrendText(row)}</span>
                      </td>
                      <td className="px-space-1 py-space-2 text-right font-tabular text-t13 text-fg">{row.protected === null ? EMPTY : num(row.protected)}</td>
                      <td className="hidden px-space-1 py-space-2 text-t13 text-fg-muted md:table-cell" data-last-break>
                        {row.last_break ? <>{row.last_break.label} <span className="font-tabular text-fg">{num(row.last_break.price)}</span></> : EMPTY}
                      </td>
                    </>
                  ) : (
                    <td colSpan={4} className="px-space-1 py-space-2">
                      <span className={cx(CHIP, "bg-surface-2 text-fg-muted")} data-not-ready={row.tf}>{notReadyText(row)}</span>
                    </td>
                  )}
                </tr>
                {/* phone: the last break is a short line under its row (the table keeps three columns there) */}
                {row.ready && row.last_break && (
                  <tr className="md:hidden" data-last-break-phone={row.tf}>
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
      {areas && (
        <div data-card-block="areas">
          <div className="flex items-center justify-between gap-space-2">
            <h3 className="text-t13 font-semibold text-fg">Areas nearby</h3>
            {swingHelp && <span className="flex items-center gap-space-1 text-t12 text-fg-muted md:hidden">Swing levels {swingHelp}</span>}
          </div>
          <div className="mt-space-1 grid grid-cols-2 gap-space-3 md:max-w-3xl">
            <AreaList title="Above" areas={card.areas_above} tone="down" />
            <AreaList title="Below" areas={card.areas_below} tone="up" />
          </div>
        </div>
      )}
      {swings && (
        <>
          <div className="hidden md:block" data-card-block="swings">
            <h3 className="mb-space-1 text-t13 font-semibold text-fg">Swing levels</h3>
            <SwingChips levels={card.swing_levels} />
          </div>
          {!areas && <div className="flex items-center gap-space-1 text-t13 font-semibold text-fg md:hidden" data-card-block="swings-phone">Swing levels {swingHelp}</div>}
        </>
      )}
      {card.watch && (
        <p className="flex items-start gap-space-2 text-t14 text-fg" data-card-block="watch">
          <Eye size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
          <span><span className="font-semibold">What to watch: </span>{card.watch}</span>
        </p>
      )}
    </section>
  );
}

export default StructureCardV2;
