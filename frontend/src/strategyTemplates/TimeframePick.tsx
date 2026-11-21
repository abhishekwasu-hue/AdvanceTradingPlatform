import { useId, type ReactNode } from "react";
import { LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { InPlaceLink } from "../inplace/InPlaceLink";
import { cx } from "../components/primitives/cx";
import { FilterChip } from "../ui/Chip";
import type { StrategyTemplate } from "./api";
import { paperBotPathFor, testPathFor } from "./link";

/**
 * DEF-0 (owner, the Templates list: "the trader will select"): a template card picks no chart timeframe for him. He picks
 * one of the template's timeframes as a chip; until then the sentence a signal reads names no timeframe ("on the chart
 * you choose") and "Use on the trading bot" / "Test it first" wait for his pick, with one plain hint next to the chips.
 */
type Pickable = Pick<StrategyTemplate, "timeframes">;
type Readable = Pick<StrategyTemplate, "timeframes" | "reads" | "reads_by_timeframe">;

/** The hint while nothing is picked. */
export const PICK_HINT = "Pick a chart timeframe to use or test it.";
/** The label above the chips. */
export const PICK_LABEL = "Chart timeframe";

/** His pick when the template offers it, else null (nothing is ever picked for him). */
export function offeredPick(t: Pickable, picked: string | null | undefined): string | null {
  return picked && t.timeframes.some((x) => x.value === picked) ? picked : null;
}

/** The sentences a signal reads: the picked timeframe's own (naming it), else the neutral ones ("the chart you choose").
 * A timeframe without a sentence of its own (or an older server) keeps the neutral ones. */
export function readsFor(t: Readable, picked: string | null | undefined): string[] {
  const p = offeredPick(t, picked);
  const named = p ? t.reads_by_timeframe?.[p] : undefined;
  const r = named && (named.long || named.short) ? named : t.reads;
  return [r?.long, r?.short].filter((s): s is string => !!s);
}

/** Where the two buttons go with his pick, or null while nothing is picked (no default timeframe, DEF-0). */
export function templateLinksFor(t: Pick<StrategyTemplate, "id" | "timeframes">, picked: string | null | undefined): { use: string; test: string } | null {
  const p = offeredPick(t, picked);
  if (!p) return null;
  const link = { template: t.id, timeframe: p, params: {} };
  return { use: paperBotPathFor(link), test: testPathFor(link) };
}

const UNIT_MINUTES: Record<string, number> = { m: 1, h: 60, d: 1440, w: 10080, M: 43200 };
/** A timeframe value's length in minutes ("15m" 15, "1h" 60, "1d" 1440); an unknown shape sorts last. */
export function timeframeMinutes(value: string): number {
  const m = /^(\d+)([mhdwM])$/.exec(value);
  return m ? Number(m[1]) * UNIT_MINUTES[m[2]] : Number.MAX_SAFE_INTEGER;
}

/** The template's timeframes shortest first (the server lists its usual one first; no order hints at a choice). */
export function chipOrder<T extends { value: string }>(timeframes: readonly T[]): T[] {
  return [...timeframes].sort((a, b) => timeframeMinutes(a.value) - timeframeMinutes(b.value));
}

/** The template's timeframes as chips (none selected at first); they wrap on a phone, 44 px tall. */
export function TimeframeChips({ t, picked, onPick, labelId, describedBy }: {
  t: Pick<StrategyTemplate, "id" | "timeframes">; picked: string | null; onPick: (tf: string) => void; labelId: string; describedBy?: string;
}) {
  return (
    <div role="group" aria-labelledby={labelId} aria-describedby={describedBy} data-template-timeframe-chips={t.id} data-chip-group
      data-chip-value={picked ?? ""} className="mt-space-1 flex flex-wrap gap-space-2">
      {chipOrder(t.timeframes).map((tf) => (
        <FilterChip key={tf.value} value={tf.value} selected={picked === tf.value} onClick={() => onPick(tf.value)}>{tf.label}</FilterChip>
      ))}
    </div>
  );
}

/** What a form says while a template waits for his chart timeframe (the trading bot form, Strategies › Test; DEF-0). */
export const PICK_FIRST = "Pick a chart timeframe first.";

/** A form's template timeframe field: the label, the chips (none picked at first) and PICK_FIRST until he picks. */
export function TemplateTimeframeField({ t, picked, onPick, labelClassName, hint = true }: {
  t: Pick<StrategyTemplate, "id" | "timeframes">; picked: string | null; onPick: (tf: string) => void;
  /** The form's own label style. */
  labelClassName?: string;
  /** Show PICK_FIRST under the chips while nothing is picked (off where the form says it elsewhere). */
  hint?: boolean;
}) {
  const labelId = useId();
  const hintId = useId();
  const p = offeredPick(t, picked);
  return (
    <div data-template-timeframe-field={t.id}>
      <div id={labelId} className={labelClassName ?? "text-t13 font-semibold text-fg-muted"}>{PICK_LABEL}</div>
      <TimeframeChips t={t} picked={p} onPick={onPick} labelId={labelId} describedBy={!p && hint ? hintId : undefined} />
      {!p && hint && <p id={hintId} data-template-pick-hint className="mt-space-1 text-t13 text-fg-muted">{PICK_FIRST}</p>}
    </div>
  );
}

/** The label, the chips, the hint while nothing is picked, and the two buttons (disabled until he picks). */
export function TemplatePickActions({ t, picked, onPick, className, children }: {
  t: Pick<StrategyTemplate, "id" | "name" | "timeframes">; picked: string | null; onPick: (tf: string) => void; className?: string;
  /** More links after the two (the "how it works" screen's scanner and chart links). */
  children?: ReactNode;
}) {
  const labelId = useId();
  const hintId = useId();
  const p = offeredPick(t, picked);
  const links = templateLinksFor(t, p);
  const off = "disabled:cursor-not-allowed disabled:opacity-50";
  return (
    <div className={className} data-template-pick={t.id}>
      <div id={labelId} className="text-t13 font-semibold text-fg-muted">{PICK_LABEL}</div>
      <TimeframeChips t={t} picked={p} onPick={onPick} labelId={labelId} />
      {!links && <p id={hintId} data-template-pick-hint className="mt-space-1 text-t13 text-fg-muted">{PICK_HINT}</p>}
      <div className="mt-space-3 flex flex-wrap items-center gap-space-2" data-template-actions>
        {links ? (
          <>
            <InPlaceLink to={links.use} className={LINK_PRIMARY} data-use-template={t.id} aria-label={`Use on the trading bot: ${t.name}`}>Use on the trading bot</InPlaceLink>
            <InPlaceLink to={links.test} className={LINK_SECONDARY} data-test-template={t.id} aria-label={`Test it first: ${t.name}`}>Test it first</InPlaceLink>
          </>
        ) : (
          <>
            <button type="button" disabled className={cx(LINK_PRIMARY, off)} data-use-template={t.id} aria-describedby={hintId}
              aria-label={`Use on the trading bot: ${t.name}`}>Use on the trading bot</button>
            <button type="button" disabled className={cx(LINK_SECONDARY, off)} data-test-template={t.id} aria-describedby={hintId}
              aria-label={`Test it first: ${t.name}`}>Test it first</button>
          </>
        )}
        {children}
      </div>
    </div>
  );
}
