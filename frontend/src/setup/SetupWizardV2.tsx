import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { CARD, LINK_SECONDARY } from "../dashboard/CardV2";
import {
  SETUP_STEPS, STEP_TEXT, markOpened, openingStep, skipStep, stepAfter, withStored, type SetupStepKey, type SetupView,
} from "./model";
import { ActionV2, AlertsStepV2, BrokerStepV2, RiskStepV2, StrategyStepV2 } from "./StepsV2";
import { useSetup } from "./useSetup";

/**
 * UI2-7 (UI v2 proposal §11): the setup wizard - four steps, each skippable ("Skip for now") and resumable: the progress
 * is the trader's own, stored on the server (src/setup/useSetup.ts), so it opens on the next open step on any device.
 * Opening it records that it opened, so the first-sign-in opening happens once.
 */

const SHORT: Record<SetupStepKey, string> = { broker: "Broker", risk: "Risk", strategy: "Strategy", alerts: "Alerts" };
type Shown = SetupStepKey | "finished";

function Stepper({ view, current, onPick }: { view: SetupView; current: Shown; onPick: (k: SetupStepKey) => void }) {
  return (
    <ol className="grid grid-cols-4 gap-space-1" aria-label="Setup steps">
      {view.steps.map((s, i) => {
        const here = s.key === current;
        const state = s.done ? "done" : s.skipped ? "skipped" : "open";
        return (
          <li key={s.key}>
            <button type="button" onClick={() => onPick(s.key)} aria-current={here ? "step" : undefined} data-step-state={state}
              aria-label={`Step ${i + 1}: ${STEP_TEXT[s.key].title}${s.done ? ", done" : s.skipped ? ", skipped" : ""}`}
              className={`flex min-h-11 w-full flex-col items-center justify-center gap-px rounded-control px-space-1 py-space-1 text-t12 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:flex-row md:gap-space-2 md:text-t13 ${here ? "bg-brand/10 font-semibold text-brand" : "text-fg-muted hover:bg-surface-2"}`}>
              <span className={`inline-flex h-6 w-6 items-center justify-center rounded-pill text-t12 font-semibold ${s.done ? "bg-brand text-on-brand" : `border ${s.skipped ? "border-dashed" : ""} ${here ? "border-brand" : "border-border"}`}`}>
                {s.done ? <Check size={14} aria-hidden /> : i + 1}
              </span>
              <span>{SHORT[s.key]}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export default function SetupWizardV2() {
  const { user } = useAuth();
  const setup = useSetup(user?.id ?? null);
  const view = setup.view;
  const [shown, setShown] = useState<Shown | null>(null);

  // Opens on the next open step (or "all set"), once the progress is known; a cached copy is good enough to start.
  useEffect(() => {
    if (shown === null && view) setShown(view.steps.every((s) => s.done || s.skipped) ? "finished" : openingStep(view));
  }, [shown, view]);
  // The first sign-in's automatic opening happens once: record that the wizard has been seen (server answer only).
  useEffect(() => {
    if (setup.fresh && view && view.stored.opened_at === null) setup.update((s) => markOpened(s, new Date().toISOString()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setup.fresh, view?.stored.opened_at]);

  if (!view || shown === null) {
    if (setup.loaded && !view) {
      return <section className={`${CARD} p-space-4 md:p-space-5`}><p className="text-t14 text-fg-muted">We could not load your setup progress. We will try again when you come back to this page.</p></section>;
    }
    return <div aria-busy="true" className={`${CARD} h-64 animate-pulse`}><span className="sr-only">Loading</span></div>;
  }

  const at = shown === "finished" ? -1 : SETUP_STEPS.indexOf(shown);
  const step = shown === "finished" ? null : view.steps[at];
  const skip = (key: SetupStepKey) => {
    const now = new Date().toISOString();
    setup.update((s) => skipStep(s, key, now));
    setShown(stepAfter(withStored(view, skipStep(view.stored, key, now)), key) ?? "finished");
  };
  const forward = () => { if (step) setShown(stepAfter(view, step.key) ?? "finished"); };
  const props = { done: !!step?.done, onChanged: setup.reload };

  return (
    <section className={`${CARD} space-y-space-4 p-space-4 md:p-space-5`} data-block="wizard" data-step={shown}>
      <p className="text-t14 text-fg-muted">Four short steps to start trading on paper. Skip any of them and come back here later.</p>
      <Stepper view={view} current={shown} onPick={setShown} />
      {step ? (
        <>
          <header>
            <p className="text-t13 text-fg-muted">Step {at + 1} of {SETUP_STEPS.length}{step.done ? " · Done" : step.skipped ? " · Skipped for now" : ""}</p>
            <h2 className="mt-px text-t18 font-semibold text-fg">{STEP_TEXT[step.key].title}</h2>
          </header>
          {step.key === "broker" && <BrokerStepV2 {...props} onSkip={() => skip("broker")} />}
          {step.key === "risk" && <RiskStepV2 key="risk" {...props} />}
          {step.key === "strategy" && <StrategyStepV2 {...props} />}
          {step.key === "alerts" && <AlertsStepV2 {...props} />}
          {(at > 0 || step.done || step.skipped || step.key !== "broker") && (
            <footer className="flex items-center justify-between gap-space-2 border-t border-subtle pt-space-3">
              {at > 0 ? <ActionV2 className="border-transparent bg-transparent" onClick={() => setShown(SETUP_STEPS[at - 1])}>Back</ActionV2> : <span />}
              {step.done || step.skipped
                ? <ActionV2 onClick={forward}>{stepAfter(view, step.key) ? "Next step" : "Finish"}</ActionV2>
                : step.key !== "broker" && <ActionV2 onClick={() => skip(step.key)}>Skip for now</ActionV2>}
            </footer>
          )}
        </>
      ) : (
        <div className="space-y-space-3" data-finished>
          <h2 className="text-t18 font-semibold text-fg">{view.steps.every((s) => s.done) ? "Setup is complete." : "You are set up for now."}</h2>
          <p className="text-t14 text-fg-muted">
            {view.steps.some((s) => s.skipped)
              ? "Steps you skipped stay here, in Settings under Setup. Pick one above to finish it when you are ready."
              : "Every step is done. You can change any of them above."}
          </p>
          <Link to="/" className={LINK_SECONDARY}>Go to Today</Link>
        </div>
      )}
      {setup.error && <p role="alert" className="text-t13 text-warn">{setup.error}</p>}
    </section>
  );
}
