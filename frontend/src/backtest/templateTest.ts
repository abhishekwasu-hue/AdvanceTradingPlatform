import type { StrategyTemplate } from "../strategyTemplates/api";

/** Day-one fix 1: testing a strategy template (an idea's own strategy) on Strategies › Test. */

/** The strategy select's value for a template (its strategy id comes from the server once the run starts). */
export const TEMPLATE_PICK = "template-pick:";

const DAILY = "1d";
/** DEF-0: what the page says while a linked template waits for his own chart timeframe. */
export const PICK_TIMEFRAME_NOTE = "Pick a chart timeframe first.";

export interface TemplateTimeframe {
  /** The timeframe the run uses ("" when none can run here). */
  value: string;
  /** One plain sentence about the timeframe and where the prices come from, or null when there is nothing to say. */
  note: string | null;
}

function words(t: Pick<StrategyTemplate, "timeframes">, value: string): string {
  const label = t.timeframes.find((x) => x.value === value)?.label ?? value;
  return label === "Daily" ? "Daily" : `${label} bars`;
}

/**
 * The timeframe a template test runs on, and what to tell the trader about it.
 * - `chosen`: the trader's own pick on the page (wins when the template offers it);
 * - `linked`: the idea's timeframe from the address;
 * - `storedOnly`: no broker prices today, so only ATP's stored Daily history can be read.
 * - `fromLink`: the address named this template (a template card's "Test it first"): without a timeframe of its own
 *   nothing is picked for him (DEF-0) - the value stays "" and the note asks him to pick one.
 * Without broker prices an intraday idea is tested on Daily when the template offers Daily, and the note says so.
 */
export function templateTimeframeFor(t: Pick<StrategyTemplate, "timeframes" | "default_timeframe">,
  o: { chosen?: string | null; linked?: string | null; storedOnly: boolean; fromLink?: boolean }): TemplateTimeframe {
  const offered = (v: string | null | undefined): v is string => !!v && t.timeframes.some((x) => x.value === v);
  const hasDaily = offered(DAILY);
  if (offered(o.chosen)) {
    if (o.storedOnly && o.chosen !== DAILY) {
      return { value: o.chosen, note: `${words(t, o.chosen)} need prices from your broker: log in on the Broker page, or pick Daily to test on ATP's stored history.` };
    }
    return { value: o.chosen, note: null };
  }
  if (o.fromLink && !offered(o.linked)) return { value: "", note: PICK_TIMEFRAME_NOTE };
  const wanted = offered(o.linked) ? o.linked : t.default_timeframe && offered(t.default_timeframe) ? t.default_timeframe : t.timeframes[0]?.value ?? "";
  if (!wanted) return { value: "", note: "This template has no timeframe that can be tested yet." };
  if (o.storedOnly && wanted !== DAILY) {
    if (hasDaily) {
      const why = offered(o.linked) ? `The idea is set to ${words(t, wanted)}.` : `This template usually reads ${words(t, wanted)}.`;
      return { value: DAILY, note: `${why} Without your broker's prices today only Daily history is stored, so this test runs on Daily. Log in on the Broker page to test it on ${words(t, wanted)}.` };
    }
    return { value: wanted, note: `This template reads intraday bars only, which need prices from your broker: log in on the Broker page first.` };
  }
  return { value: wanted, note: null };
}
