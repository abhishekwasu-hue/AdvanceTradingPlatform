import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { cx } from "../components/primitives/cx";
import { CARD, LoadingRowsV2 } from "../dashboard/CardV2";
import { RhythmSectionV2 } from "../rhythm/DailyRhythmCardV2";
import { Details } from "../ui/Details";
import { growthApi } from "./api";
import { DISH_REFRESH_MS, dishShown, readDishOpen, writeDishOpen } from "./model";
import type { DishPage, PageDish } from "./types";

/**
 * MO4 slice 4 (ATP_SESSION_GROWTH_MASTER.md PART 2 items 3, 5, 9, 10, 11; `ui_v2` + `growth_v2`): the "ready-made
 * dish" at the top of Signals, My positions, the Journal, Charts and Find setups - prepared, personal lines the moment
 * the page opens, built by the server from his own records (backend app/growth/dishes.py). One line stands out (the
 * headline); the sections below it reuse the daily brief's section (`RhythmSectionV2`), and every line is checked
 * again for call words on render (`dishShown`). He can fold the details away; the choice is remembered on this device
 * only. Information only: nothing here places, changes or suggests a trade. With the flag off the server answers 404
 * and nothing shows.
 */

export type DishState = { status: "loading" } | { status: "off" } | { status: "error" } | { status: "ok"; data: PageDish };
type State = DishState;

/** What a read's answer means for the dish: shown; nothing (a flag off = 404, no symbol = 422, the journal off); or a
 * failed read, which keeps the lines already shown (`was`) and otherwise shows nothing. */
export function dishState(answer: { data: PageDish } | { error: unknown }, was: DishState = { status: "loading" }): DishState {
  if ("data" in answer) return answer.data.available ? { status: "ok", data: answer.data } : { status: "off" };
  const e = answer.error;
  if (e instanceof ApiError && (e.status === 404 || e.status === 422)) return { status: "off" };
  return was.status === "ok" ? was : { status: "error" };
}

export interface PageDishProps {
  page: DishPage;
  /** Charts: the symbol on screen (and its exchange). */
  symbol?: string | null;
  exchange?: string | null;
  /** Folded on first visit (Charts: the chart comes first). */
  folded?: boolean;
  className?: string;
  load?: typeof growthApi.dish;
}

export default function PageDishV2({ page, symbol, exchange, folded = false, className, load = growthApi.dish }: PageDishProps) {
  const [state, setState] = useState<State>({ status: "loading" });
  const loader = useRef(load);
  loader.current = load;
  const key = `${page}|${(symbol ?? "").toUpperCase()}|${(exchange ?? "").toUpperCase()}`;

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    const read = () => loader.current(page, symbol, exchange)
      .then((data) => { if (live) setState((was) => dishState({ data }, was)); })
      .catch((error: unknown) => { if (live) setState((was) => dishState({ error }, was)); });
    void read();
    const every = DISH_REFRESH_MS[page];
    const id = every ? window.setInterval(() => { if (document.visibilityState !== "hidden") void read(); }, every) : undefined;
    return () => { live = false; if (id !== undefined) window.clearInterval(id); };
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps -- `key` stands for the page, symbol and exchange

  if (state.status === "off" || state.status === "error") return null;   // the page below says what it can itself
  if (state.status === "loading") {
    return (
      <section aria-label="Loading your summary" className={cx(CARD, "p-space-4 md:p-space-5", className)}>
        <LoadingRowsV2 rows={folded ? 1 : 2} />
      </section>
    );
  }
  return <PageDishView dish={state.data} folded={folded} className={className} />;
}

/** The dish for an answer already in hand (pure but for the remembered fold). */
export function PageDishView({ dish, folded = false, className }: { dish: PageDish; folded?: boolean; className?: string }) {
  const [open, setOpen] = useState(() => readDishOpen(dish.page, !folded));
  const bodyId = useId();
  const shown = dishShown(dish);
  const n = shown.sections.length;
  const toggle = () => setOpen((o) => { writeDishOpen(dish.page, !o); return !o; });
  return (
    <section aria-label={shown.title} data-block="page-dish" data-dish={dish.page}
      className={cx(CARD, "p-space-4 md:p-space-5", className)}>
      <div className="flex items-start justify-between gap-space-3">
        <div className="min-w-0">
          <h2 className="text-t15 font-semibold text-fg">{shown.title}</h2>
          {shown.headline && <p data-dish-headline className="mt-space-1 text-t14 font-medium text-fg [overflow-wrap:anywhere]">{shown.headline}</p>}
        </div>
        {n > 0 && (
          <button type="button" aria-expanded={open} aria-controls={bodyId} onClick={toggle} data-dish-toggle
            className="-mr-space-2 inline-flex h-11 shrink-0 items-center gap-space-1 rounded-control px-space-2 text-t13 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9">
            {open ? "Hide details" : "Details"}
            <ChevronDown size={14} aria-hidden className={cx("transition-transform duration-fast", open && "rotate-180")} />
          </button>
        )}
      </div>
      {open && n > 0 && (
        <div id={bodyId}>
          <div className={cx("mt-space-3 divide-y divide-subtle md:grid md:gap-x-space-6 md:divide-y-0",
            n === 2 && "md:grid-cols-2", n >= 3 && "md:grid-cols-2 xl:grid-cols-3")}>
            {shown.sections.map((s) => (
              <div key={s.id} className="py-space-3 first:pt-0 last:pb-0 md:py-0 [&>div]:py-0">
                <RhythmSectionV2 section={s} typed />
              </div>
            ))}
          </div>
          {/* UX-11b: the page's footnote carries the disclaimer; the dish's own note sits in Details. */}
          <Details id={`dish.${dish.page}.note`} label="About this summary"><p>{shown.note}</p></Details>
        </div>
      )}
    </section>
  );
}
