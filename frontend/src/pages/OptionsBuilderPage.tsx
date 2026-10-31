/**
 * P1-c2 / Z5: the Options Strategy Builder (spec §3.3). The payoff canvas carries the page: expiry and T+0 curves,
 * max-loss shading, the probability cone and draggable strikes. Templates on top, legs below the canvas, the numbers,
 * "Place as basket" and "Save strategy" on the right, adjustments and compare (a later release) at the bottom; on a
 * phone the payoff comes first and the legs follow.
 *
 * Research only: nothing here places an order ("Review basket" shows the hedge-first basket; placing it in
 * PAPER comes with the plan-before-trade ticket). The instrument's numbers (spot, strike step, lot size, template width)
 * are the trader's inputs - nothing is defaulted to one instrument. REV-1 M2 (CLAUDE.md §3): the lots are the trader's
 * own (pre-filled from Settings > My trading defaults > Chain order ticket > Lots when set, else typed), and a blank
 * template width stays blank - no platform width. Template premiums are the model's until real ones
 * are typed, and are marked so.
 */
import { Details } from "../ui/Details";
import { useEffect, useMemo, useRef, useState } from "react";
import { Layers, Save } from "lucide-react";
import { ApiError } from "../api/errors";
import { Button, Input, PageHeader } from "../components/primitives";
import { useToast } from "../components/Toast";
import type { PageProps } from "../routes";
import { builderApi, wireLegs, type SavedStrategy } from "../optionsBuilder/api";
import { BasketDialog } from "../optionsBuilder/BasketDialog";
import { LegTable } from "../optionsBuilder/LegTable";
import { MetricsCard } from "../optionsBuilder/MetricsCard";
import {
  builderAreas, contractKey, daysBetween, evaluatedById, hedgeFirst, isCurrent, legId, rangePctFor, repriceModelLegs, snapStrike, type Evaluated, type Leg,
  type TemplateInfo,
} from "../optionsBuilder/model";
import { PayoffCanvas } from "../optionsBuilder/PayoffCanvas";
import { SaveDialog, savedAvailability, type SavedState } from "../optionsBuilder/SaveDialog";
import { TemplateGallery } from "../optionsBuilder/TemplateGallery";
import { takeHandoff } from "../fno/basketModel";
import { useMediaQuery } from "../utils/useMediaQuery";
import { ticketPrefill, useTradingDefaults } from "../settings/tradingDefaults";
import { useFeatures } from "../ui/useFeatures";

/** Desktop layout (legs left, numbers right) from Tailwind's lg breakpoint. */
const WIDE_QUERY = "(min-width: 1024px)";
const DEFAULT_BROKERAGE = "20";
const MAX_BROKERAGE = 1000;                                 // the API's limit on brokerage_per_order

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

function nonNegative(text: string): number | null {
  const n = Number(text);
  return text.trim() !== "" && Number.isFinite(n) && n >= 0 ? n : null;
}

const str = (v: number | null | undefined) => (v == null ? "" : String(v));

/** REV-1 M2: lots as typed - a whole number from 1 to 500 (the settings' own range), else null (nothing is assumed). */
export function lotsOf(text: string): number | null {
  const n = Number(text.trim());
  return text.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 500 ? n : null;
}

export default function OptionsBuilderPage(_props: PageProps) {
  const toast = useToast();
  const [catalog, setCatalog] = useState<{ families: Record<string, string[]>; templates: Record<string, TemplateInfo> } | null>(null);
  const [underlying, setUnderlying] = useState("");
  const [spotText, setSpotText] = useState("");
  const [stepText, setStepText] = useState("");
  const [lotText, setLotText] = useState("");
  const [widthText, setWidthText] = useState("");
  const [lotsText, setLotsText] = useState("");
  const lotsTouched = useRef(false);
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
  const [brokerageText, setBrokerageText] = useState(DEFAULT_BROKERAGE);
  const [basketOpen, setBasketOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saved, setSaved] = useState<SavedState>("unknown");
  const wide = useMediaQuery(WIDE_QUERY);
  const phone = useMediaQuery("(max-width: 767px)");
  const asOf = useMemo(todayIst, []);
  const brokerage = Math.min(nonNegative(brokerageText) ?? Number(DEFAULT_BROKERAGE), MAX_BROKERAGE);   // the API caps it

  const spot = positive(spotText);
  const step = positive(stepText);
  const lotSize = positive(lotText);
  const width = positive(widthText);                          // REV-1 M2: blank stays blank - no default width
  const lots = lotsOf(lotsText);
  const iv = positive(ivText);
  const ready = spot != null && step != null && lotSize != null && width != null && lots != null && iv != null && !!nearExpiry;
  // REV-1 M2: the trader's own lots (My trading defaults > Chain order ticket), once, unless already typed.
  const defaults = useTradingDefaults(useFeatures().on("ui_v2"));
  const savedLots = ticketPrefill(defaults).lots;
  useEffect(() => {
    if (savedLots !== undefined && !lotsTouched.current) setLotsText(String(savedLots));
  }, [savedLots]);
  const typeLots = (v: string) => { lotsTouched.current = true; setLotsText(v); };
  const nearest = legs.length ? legs.map((l) => l.expiry).sort()[0] : nearExpiry;
  const maxDays = nearest ? daysBetween(asOf, nearest) : 0;

  // FNO-2: "Open in Options Builder" on the basket panel hands its legs over once (src/fno/basketModel.ts).
  useEffect(() => {
    const h = takeHandoff();
    if (!h) return;
    setUnderlying(h.underlying);
    setSpotText(String(h.spot));
    if (h.step) setStepText(String(h.step));
    setLotText(String(h.lot_size));
    setNearExpiry(h.near_expiry);
    setNextExpiry(h.next_expiry ?? "");
    setLegs(h.legs.map((l) => ({ ...l, id: legId(), premium_source: "manual" as const })));
  }, []);

  useEffect(() => {
    builderApi.catalog().then(setCatalog).catch((e: unknown) => setProblem(errorText(e)));
    savedAvailability().then((a) => setSaved(a.state)).catch(() => setSaved("off"));
  }, []);
  useEffect(() => { if (daysForward > maxDays) setDaysForward(maxDays); }, [daysForward, maxDays]);

  // Every change re-evaluates on the server (debounced); an older reply never overwrites a newer one. The reply is
  // kept with the legs it was asked for, so rows and re-pricing match by leg id, never by position.
  useEffect(() => {
    const n = ++seq.current;
    if (!legs.length || spot == null) { setEvaluated(null); setProblem(null); return undefined; }
    const sent = legs;
    const params = { spot, daysForward, ivShift, brokerage };
    // while a strike is dragged the chart's x-range stays put (a reply must not rescale the axis under the pointer);
    // it widens to cover the new strikes once the drag ends
    const range = dragging && frozenRange.current != null ? frozenRange.current : rangePctFor(spot, sent);
    frozenRange.current = range;
    let retryTimer: number | undefined;
    const timer = window.setTimeout(() => {
      builderApi.evaluate({
        legs: wireLegs(sent), spot, as_of: asOf, days_forward: daysForward,
        iv_shift: ivShift / 100, range_pct: range, points: 241, brokerage_per_order: brokerage,
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
  }, [legs, spot, asOf, daysForward, ivShift, dragging, retry, brokerage]);
  const evaluation = evaluated?.evaluation ?? null;
  const rows = useMemo(() => evaluatedById(legs, evaluated), [legs, evaluated]);
  const fresh = spot != null && isCurrent(legs, evaluated, { spot, daysForward, ivShift }) && evaluated?.params?.brokerage === brokerage;

  const pick = async (name: string) => {
    if (!ready || spot == null || step == null || lotSize == null || width == null || lots == null || iv == null) return;
    try {
      const t = await builderApi.template({
        name, atm_strike: snapStrike(spot, step), width, near_expiry: nearExpiry, next_expiry: nextExpiry || null, lots,
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
    if (spot == null || step == null || lotSize == null || lots == null || !nearExpiry) {
      toast.error("Fill in the spot, strike step, lot size, lots and expiry first.");
      return;
    }
    setLegs((all) => [...all, { id: legId(), direction: "BUY", option_type: "CE", strike: snapStrike(spot, step), premium: 0, lots, lot_size: lotSize,
      expiry: nearExpiry, iv: iv != null ? iv / 100 : null, premium_source: "manual" }]);
  };

  const loadSaved = (s: SavedStrategy) => {
    const i = s.inputs;
    setUnderlying(s.underlying);
    setSpotText(str(i.spot));
    setStepText(str(i.step));
    setLotText(str(i.lot_size));
    setWidthText(str(i.width));
    setIvText(str(i.base_iv));
    setNearExpiry(i.near_expiry ?? "");
    setNextExpiry(i.next_expiry ?? "");
    setDaysForward(0);
    setLegs(hedgeFirst(s.legs.map((l) => {
      const leg: Leg = { ...l, id: legId(), iv: l.iv ?? null, premium_source: l.premium_source ?? "manual" };
      return leg.premium_source === "model" ? { ...leg, priced_for: contractKey(leg) } : leg;
    })));
    toast.success(`Loaded ${s.name}.`);
  };
  const savedInputs = { spot, step, lot_size: lotSize, width: positive(widthText), base_iv: iv, near_expiry: nearExpiry || null, next_expiry: nextExpiry || null };

  const field = (label: string, value: string, set: (v: string) => void, extra: Record<string, unknown> = {}) => (
    <Input label={label} value={value} onChange={(e) => set(e.target.value)} inputMode="decimal" {...extra} />
  );

  return (
    <div className="space-y-4">
      {/* UX-11b C5: one line; the model note on demand. */}
      <PageHeader title="Plan an options trade" description="Build a strategy and see its payoff." />
      <Details id="options-builder.about" label="About these numbers" className="mt-0">
        <p>See the payoff today and at expiry, and read the numbers before you trade. Model estimates for research, not advice.</p>
      </Details>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]" style={{ gridTemplateAreas: builderAreas(wide, legs.length > 0) }} data-testid="builder-grid">
        <section aria-label="Instrument" style={{ gridArea: "inst" }} className="grid grid-cols-2 gap-3 rounded-xl border border-border bg-surface-1 p-4 sm:grid-cols-4 xl:grid-cols-10">
          {field("Underlying", underlying, setUnderlying, { inputMode: "text", placeholder: "e.g. NIFTY" })}
          {field("Spot", spotText, setSpotText, { "data-testid": "spot" })}
          {field("Strike step", stepText, setStepText, { hint: "points between strikes" })}
          {field("Lot size", lotText, setLotText, { hint: "units in one lot" })}
          {field("Lots", lotsText, typeLots, { inputMode: "numeric", hint: savedLots !== undefined ? "from your trading defaults" : "1 to 500, per leg" })}
          {field("Template width", widthText, setWidthText, { placeholder: "points", hint: "points between template strikes" })}
          {field("Base IV %", ivText, setIvText, { hint: "prices template legs" })}
          <Input label="Near expiry" type="date" value={nearExpiry} onChange={(e) => setNearExpiry(e.target.value)} />
          <Input label="Next expiry" type="date" value={nextExpiry} onChange={(e) => setNextExpiry(e.target.value)} hint="calendars, diagonals" />
          {field("Brokerage / order", brokerageText, setBrokerageText, { hint: "₹, for the cost line", "data-testid": "brokerage" })}
        </section>

        <div style={{ gridArea: "tmpl" }} className="min-w-0 space-y-2">
          {catalog && (
            <TemplateGallery families={catalog.families} templates={catalog.templates} onPick={(n) => void pick(n)} disabled={!ready} />
          )}
          {!ready && <p className="text-xs text-fg-muted">Fill in the spot, strike step, lot size, lots, template width, base IV and near expiry to build from a template.</p>}
        </div>

        <section aria-label="Payoff" aria-busy={!!evaluation && !fresh} style={{ gridArea: "pay" }} className="min-w-0 rounded-xl border border-border bg-surface-1 p-3 sm:p-4">
          {problem && <p role="alert" className="mb-2 text-sm text-warn">{problem}</p>}
          {evaluation && !fresh && problem && (
            <p className="mb-2 text-xs text-warn" data-testid="payoff-stale">These curves are for the legs before your last change. Fix the problem above to update them.</p>
          )}
          {evaluation && spot != null && step != null ? (
            <div className={!fresh && problem ? "opacity-60" : undefined}>
              <PayoffCanvas evaluation={evaluation} legs={legs} spot={spot} step={step} onDragging={setDragging}
                            onStrikeChange={(id, strike) => setLegs((all) => all.map((l) => (l.id === id ? { ...l, strike } : l)))} />
            </div>
          ) : (
            <div className="flex h-48 items-center justify-center text-center text-sm text-fg-muted sm:h-64">The payoff appears here once there is a leg.</div>
          )}
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <label className="block text-xs text-fg-muted">
              Date: {daysForward === 0 ? "today" : `in ${daysForward} days`}
              <input type="range" min={0} max={maxDays} value={Math.min(daysForward, maxDays)} onChange={(e) => setDaysForward(Number(e.target.value))}
                     className="mt-1 h-11 w-full accent-[rgb(var(--brand))] sm:h-auto" aria-label="Days forward" disabled={!legs.length} />
            </label>
            <label className="block text-xs text-fg-muted">
              IV shift: {ivShift > 0 ? "+" : ""}{ivShift} points
              <input type="range" min={-10} max={20} value={ivShift} onChange={(e) => setIvShift(Number(e.target.value))}
                     className="mt-1 h-11 w-full accent-[rgb(var(--brand))] sm:h-auto" aria-label="IV shift in points" disabled={!legs.length} />
            </label>
          </div>
        </section>

        <div style={{ gridArea: "legs" }} className="min-w-0">
          <LegTable legs={legs} rows={rows} step={step ?? 0} onChange={(next) => setLegs(hedgeFirst(next))} onAdd={addLeg} compact={phone} />
        </div>

        <div style={{ gridArea: "side" }} className="min-w-0 space-y-3 lg:self-start">
          <MetricsCard evaluation={evaluation} stale={!!evaluation && !fresh} />
          <section aria-label="Actions" className="space-y-2 rounded-xl border border-border bg-surface-1 p-4">
            <div className="grid grid-cols-2 gap-2">
              <Button variant="primary" icon={<Layers size={14} />} disabled={!legs.length} onClick={() => setBasketOpen(true)} data-testid="place-basket">Review basket</Button>
              <Button icon={<Save size={14} />} disabled={saved !== "on"} onClick={() => setSaveOpen(true)} data-testid="save-strategy">Save strategy</Button>
            </div>
            <Details id="optionsBuilder.actions" className="mt-0"><p className="leading-snug">
              This page places no order: the basket shows the order the legs would go in, for review only.
              {saved === "off" && " Saving strategies is not turned on for your organisation yet."}
            </p></Details>
          </section>
        </div>

        <details style={{ gridArea: "drawer" }} className="group rounded-xl border border-border bg-surface-1" data-testid="adjust-drawer">
          <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-semibold text-fg">
            Adjustments and compare
            <span className="text-xs font-normal text-fg-muted group-open:hidden">Coming later</span>
          </summary>
          <p className="border-t border-border px-4 py-3 text-xs text-fg-muted">
            Roll a leg, add a hedge, convert to an iron condor or close one side - each with its before and after payoff - and compare up to three
            strategies side by side. These arrive in a later release.
          </p>
        </details>
      </div>

      <BasketDialog open={basketOpen} onOpenChange={setBasketOpen} underlying={underlying} legs={legs} errorText={errorText} />
      {saved === "on" && (
        <SaveDialog open={saveOpen} onOpenChange={setSaveOpen} underlying={underlying} inputs={savedInputs} legs={legs} onLoad={loadSaved} errorText={errorText} />
      )}
    </div>
  );
}
