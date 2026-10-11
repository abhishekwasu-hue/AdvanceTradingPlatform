/**
 * P1-c2: the Options Strategy Builder (spec §3.3). The payoff canvas carries the page: expiry and the chosen-date
 * curves, the probability cone and draggable strikes. Templates on top, legs below the canvas, the numbers on the
 * right; on a phone the payoff comes first and the legs follow.
 *
 * Research only: nothing here places an order. The instrument's numbers (spot, strike step, lot size, template width)
 * are the trader's inputs - nothing is defaulted to one instrument. Template premiums are the model's until real ones
 * are typed, and are marked so.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { Input, PageHeader } from "../components/primitives";
import { useToast } from "../components/Toast";
import type { PageProps } from "../routes";
import { builderApi } from "../optionsBuilder/api";
import { LegTable } from "../optionsBuilder/LegTable";
import { MetricsCard } from "../optionsBuilder/MetricsCard";
import { contractKey, daysBetween, evaluatedById, hedgeFirst, isCurrent, legId, rangePctFor, repriceModelLegs, snapStrike, type Evaluated, type Leg, type TemplateInfo } from "../optionsBuilder/model";
import { PayoffCanvas } from "../optionsBuilder/PayoffCanvas";
import { TemplateGallery } from "../optionsBuilder/TemplateGallery";

const EVALUATE_DELAY_MS = 200;
/** After a 429 the page waits this long and asks again, once per wait (the server's limit is per minute). */
const RATE_LIMIT_RETRY_MS = 3000;

function todayIst(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 503) return "The Options Builder is turned off for this organisation.";
  return e instanceof Error ? e.message : String(e);
}

function positive(text: string): number | null {
  const n = Number(text);
  return text.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

export default function OptionsBuilderPage(_props: PageProps) {
  const toast = useToast();
  const [catalog, setCatalog] = useState<{ families: Record<string, string[]>; templates: Record<string, TemplateInfo> } | null>(null);
  const [underlying, setUnderlying] = useState("");
  const [spotText, setSpotText] = useState("");
  const [stepText, setStepText] = useState("");
  const [lotText, setLotText] = useState("");
  const [widthText, setWidthText] = useState("");
  const [ivText, setIvText] = useState("");
  const [nearExpiry, setNearExpiry] = useState("");
  const [nextExpiry, setNextExpiry] = useState("");
  const [legs, setLegs] = useState<Leg[]>([]);
  const [daysForward, setDaysForward] = useState(0);
  const [ivShift, setIvShift] = useState(0);
  const [evaluated, setEvaluated] = useState<Evaluated | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const seq = useRef(0);
  const [dragging, setDragging] = useState(false);
  const frozenRange = useRef<number | null>(null);          // the x-range held while a strike is dragged
  const [retry, setRetry] = useState(0);
  const asOf = useMemo(todayIst, []);

  const spot = positive(spotText);
  const step = positive(stepText);
  const lotSize = positive(lotText);
  const width = positive(widthText) ?? (step ? 2 * step : null);
  const iv = positive(ivText);
  const ready = spot != null && step != null && lotSize != null && width != null && iv != null && !!nearExpiry;
  const nearest = legs.length ? legs.map((l) => l.expiry).sort()[0] : nearExpiry;
  const maxDays = nearest ? daysBetween(asOf, nearest) : 0;

  useEffect(() => {
    builderApi.catalog().then(setCatalog).catch((e: unknown) => setProblem(errorText(e)));
  }, []);
  useEffect(() => { if (daysForward > maxDays) setDaysForward(maxDays); }, [daysForward, maxDays]);

  // Every change re-evaluates on the server (debounced); an older reply never overwrites a newer one. The reply is
  // kept with the legs it was asked for, so rows and re-pricing match by leg id, never by position.
  useEffect(() => {
    const n = ++seq.current;
    if (!legs.length || spot == null) { setEvaluated(null); setProblem(null); return undefined; }
    const sent = legs;
    const params = { spot, daysForward, ivShift };
    // while a strike is dragged the chart's x-range stays put (a reply must not rescale the axis under the pointer);
    // it widens to cover the new strikes once the drag ends
    const range = dragging && frozenRange.current != null ? frozenRange.current : rangePctFor(spot, sent);
    frozenRange.current = range;
    let retryTimer: number | undefined;
    const timer = window.setTimeout(() => {
      builderApi.evaluate({
        legs: sent.map(({ id: _id, premium_source: _src, priced_for: _key, ...l }) => l), spot, as_of: asOf, days_forward: daysForward,
        iv_shift: ivShift / 100, range_pct: range, points: 241,
      }).then((evaluation) => {
        if (n !== seq.current) return;
        setProblem(null);
        const reply = { evaluation, sent, params };
        setEvaluated(reply);
        setLegs((cur) => repriceModelLegs(cur, reply));        // a model price for the old contract: re-price, re-evaluate
      })
        .catch((e: unknown) => {
          if (n !== seq.current) return;
          // Second review: keep the chart (and a drag or a focused handle) in place; the last numbers stay, dimmed and
          // marked out of date, and rows no longer matching their leg are blank (evaluatedById). A 429 asks again.
          setProblem(errorText(e));
          if (e instanceof ApiError && e.status === 429) retryTimer = window.setTimeout(() => setRetry((r) => r + 1), RATE_LIMIT_RETRY_MS);
        });
    }, EVALUATE_DELAY_MS);
    return () => { window.clearTimeout(timer); window.clearTimeout(retryTimer); };
  }, [legs, spot, asOf, daysForward, ivShift, dragging, retry]);
  const evaluation = evaluated?.evaluation ?? null;
  const rows = useMemo(() => evaluatedById(legs, evaluated), [legs, evaluated]);
  const fresh = spot != null && isCurrent(legs, evaluated, { spot, daysForward, ivShift });

  const pick = async (name: string) => {
    if (!ready || spot == null || step == null || lotSize == null || width == null || iv == null) return;
    try {
      const t = await builderApi.template({
        name, atm_strike: snapStrike(spot, step), width, near_expiry: nearExpiry, next_expiry: nextExpiry || null, lots: 1,
        lot_size: lotSize, spot, iv: iv / 100, as_of: asOf,
      });
      setLegs(hedgeFirst(t.legs.map((l) => {
        const leg = { ...l, id: legId(), iv: l.iv ?? null } as Leg;
        return leg.premium_source === "model" ? { ...leg, priced_for: contractKey(leg) } : leg;
      })));
      setDaysForward(0);
    } catch (e) {
      toast.error(errorText(e));
    }
  };

  const addLeg = () => {
    if (spot == null || step == null || lotSize == null || !nearExpiry) {
      toast.error("Fill in the spot, strike step, lot size and expiry first.");
      return;
    }
    setLegs((all) => [...all, { id: legId(), direction: "BUY", option_type: "CE", strike: snapStrike(spot, step), premium: 0, lots: 1, lot_size: lotSize,
      expiry: nearExpiry, iv: iv != null ? iv / 100 : null, premium_source: "manual" }]);
  };

  const field = (label: string, value: string, set: (v: string) => void, extra: Record<string, unknown> = {}) => (
    <Input label={label} value={value} onChange={(e) => set(e.target.value)} inputMode="decimal" {...extra} />
  );

  return (
    <div className="space-y-4">
      <PageHeader title="Options Builder" description="Build a strategy, see its payoff today and at expiry, and read the numbers before you trade. Model estimates for research, not advice." />

      <section aria-label="Instrument" className="grid grid-cols-2 gap-3 rounded-xl border border-border bg-surface-1 p-4 sm:grid-cols-4 xl:grid-cols-8">
        {field("Underlying", underlying, setUnderlying, { inputMode: "text", placeholder: "name (label only)" })}
        {field("Spot", spotText, setSpotText, { "data-testid": "spot" })}
        {field("Strike step", stepText, setStepText, { hint: "from the instrument master" })}
        {field("Lot size", lotText, setLotText, { hint: "from the instrument master" })}
        {field("Template width", widthText, setWidthText, { placeholder: step ? `${2 * step}` : "points", hint: "default: 2 strike steps" })}
        {field("Base IV %", ivText, setIvText, { hint: "prices template legs" })}
        <Input label="Near expiry" type="date" value={nearExpiry} onChange={(e) => setNearExpiry(e.target.value)} />
        <Input label="Next expiry" type="date" value={nextExpiry} onChange={(e) => setNextExpiry(e.target.value)} hint="calendars, diagonals" />
      </section>

      {catalog && (
        <TemplateGallery families={catalog.families} templates={catalog.templates} onPick={(n) => void pick(n)} disabled={!ready} />
      )}
      {!ready && <p className="text-xs text-fg-muted">Fill in the spot, strike step, lot size, base IV and near expiry to build from a template.</p>}
      {problem && <p role="alert" className="text-sm text-warn">{problem}</p>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          <section aria-label="Payoff" aria-busy={!!evaluation && !fresh} className="rounded-xl border border-border bg-surface-1 p-4">
            {evaluation && !fresh && problem && (
              <p className="mb-2 text-xs text-warn" data-testid="payoff-stale">These curves are for the legs before your last change. Fix the problem above to update them.</p>
            )}
            {evaluation && spot != null && step != null ? (
              <div className={!fresh && problem ? "opacity-60" : undefined}>
                <PayoffCanvas evaluation={evaluation} legs={legs} spot={spot} step={step} onDragging={setDragging}
                              onStrikeChange={(id, strike) => setLegs((all) => all.map((l) => (l.id === id ? { ...l, strike } : l)))} />
              </div>
            ) : (
              <div className="flex h-64 items-center justify-center text-sm text-fg-muted">The payoff appears here once there is a leg.</div>
            )}
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <label className="block text-xs text-fg-muted">
                Date: {daysForward === 0 ? "today" : `in ${daysForward} days`}
                <input type="range" min={0} max={maxDays} value={Math.min(daysForward, maxDays)} onChange={(e) => setDaysForward(Number(e.target.value))}
                       className="mt-1 w-full accent-[rgb(var(--brand))]" aria-label="Days forward" disabled={!legs.length} />
              </label>
              <label className="block text-xs text-fg-muted">
                IV shift: {ivShift > 0 ? "+" : ""}{ivShift} points
                <input type="range" min={-10} max={20} value={ivShift} onChange={(e) => setIvShift(Number(e.target.value))}
                       className="mt-1 w-full accent-[rgb(var(--brand))]" aria-label="IV shift in points" disabled={!legs.length} />
              </label>
            </div>
          </section>
          <LegTable legs={legs} rows={rows} step={step ?? 0} onChange={(next) => setLegs(hedgeFirst(next))} onAdd={addLeg} />
        </div>
        <MetricsCard evaluation={evaluation} legs={legs} stale={!!evaluation && !fresh} />
      </div>
    </div>
  );
}
