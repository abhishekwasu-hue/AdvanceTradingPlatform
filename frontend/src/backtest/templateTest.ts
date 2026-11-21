import type { StrategyTemplate } from "../strategyTemplates/api";
import { PICK_FIRST } from "../strategyTemplates/TimeframePick";

/** Day-one fix 1: testing a strategy template (an idea's own strategy) on Strategies › Test. */

/** The strategy select's value for a template (its strategy id comes from the server once the run starts). */
export const TEMPLATE_PICK = "template-pick:";

const DAILY = "1d";
/** DEF-0: what the page says while a template waits for his own chart timeframe. */
export const PICK_TIMEFRAME_NOTE = PICK_FIRST;

export interface TemplateTimeframe {
  /** The timeframe the run uses ("" when he has not picked one, or none can run here). */
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
 * - `linked`: the timeframe from the address (an idea's, or his pick on a template card);
 * - `storedOnly`: no broker prices today, so only ATP's stored Daily history can be read.
 * DEF-0 (owner: "trader select karel"): with neither, nothing is picked for him - the value stays "" and the note asks.
 * Without broker prices an intraday idea is tested on Daily when the template offers Daily, and the note says so.
 */
export function templateTimeframeFor(t: Pick<StrategyTemplate, "timeframes">,
  o: { chosen?: string | null; linked?: string | null; storedOnly: boolean }): TemplateTimeframe {
  const offered = (v: string | null | undefined): v is string => !!v && t.timeframes.some((x) => x.value === v);
  const hasDaily = offered(DAILY);
  if (offered(o.chosen)) {
    if (o.storedOnly && o.chosen !== DAILY) {
      return { value: o.chosen, note: `${words(t, o.chosen)} need prices from your broker: log in on the Broker page, or pick Daily to test on ATP's stored history.` };
    }
    return { value: o.chosen, note: null };
  }
  if (!t.timeframes.length) return { value: "", note: "This template has no timeframe that can be tested yet." };
  if (!offered(o.linked)) return { value: "", note: PICK_TIMEFRAME_NOTE };
  const wanted = o.linked;
  if (o.storedOnly && wanted !== DAILY) {
    if (hasDaily) {
      return { value: DAILY, note: `The idea is set to ${words(t, wanted)}. Without your broker's prices today only Daily history is stored, so this test runs on Daily. Log in on the Broker page to test it on ${words(t, wanted)}.` };
    }
    return { value: wanted, note: `This template reads intraday bars only, which need prices from your broker: log in on the Broker page first.` };
  }
  return { value: wanted, note: null };
}
