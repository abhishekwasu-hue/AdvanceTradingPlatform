import { pageFromPath } from "../routes";
import { GET_STARTED_STEPS, type GetStartedKey, type SetupStored, type SetupView } from "./model";

/**
 * P3 (the owner's "simple front page"): the Dashboard's "Get started" card, in both shells - three steps, each with one
 * button and a tick when done, worked out by the server (`GET /api/me/setup` -> `get_started`, backend app/me/setup.py):
 * 1. Connect your broker - a verified broker, or an Upstox Analytics Token (enough for paper trading);
 * 2. Start your paper bot - a PAPER deployment of the trader's own;
 * 3. Get alerts on your phone (Telegram) - the organisation's Telegram channel is on and its last send went through.
 * When all three are done the card disappears for good (the app stores `get_started_hidden_at`); Help -> "Show Get
 * started" brings it back. Pure functions only, so every rule here is unit-tested.
 */

/** The new Broker page (built in its own change). Until the router knows it, the broker settings of today. */
export const BROKER_PATH = "/broker";
export const BROKER_FALLBACK_PATH = "/settings";

/** Where "Connect your broker" leads: `/broker` once that page exists, else today's broker settings. */
export function connectBrokerPath(known: (path: string) => boolean = (path) => pageFromPath(path) !== null): string {
  return known(BROKER_PATH) ? BROKER_PATH : BROKER_FALLBACK_PATH;
}

/** Settings → Alerts (the alert channels card) and Settings → Advanced → Go-live checklist. */
export const ALERTS_PATH = "/settings#alerts";
export const GO_LIVE_PATH = "/settings#go-live";

export interface GetStartedStep {
  key: GetStartedKey;
  title: string;
  done: boolean;
  /** The step's one button: what it says and where it leads. */
  action: { label: string; to: string };
}

export interface GetStartedCard {
  steps: GetStartedStep[];
  doneCount: number;
  complete: boolean;
}

const TEXT: Record<GetStartedKey, { title: string; todo: string; done: string }> = {
  broker: { title: "Connect your broker", todo: "Connect your broker", done: "Your broker" },
  paper_bot: { title: "Start your paper bot", todo: "Start paper bot", done: "See your bot" },
  telegram: { title: "Get alerts on your phone (Telegram)", todo: "Set up Telegram", done: "Alert settings" },
};

function target(key: GetStartedKey, brokerPath: string): string {
  if (key === "broker") return brokerPath;
  if (key === "paper_bot") return "/deployments";
  return ALERTS_PATH;
}

/** The steps as the card shows them (also while it is hidden: Help reads `complete`). Null without the server's view. */
export function getStartedSteps(view: SetupView | null, brokerPath: string = connectBrokerPath()): GetStartedCard | null {
  const done = view?.getStarted;
  if (!done) return null;
  const steps = GET_STARTED_STEPS.map((key) => ({
    key, title: TEXT[key].title, done: done[key],
    action: { label: done[key] ? TEXT[key].done : TEXT[key].todo, to: target(key, brokerPath) },
  }));
  const doneCount = steps.filter((s) => s.done).length;
  return { steps, doneCount, complete: doneCount === steps.length };
}

/**
 * The card, or null when it does not show: no server view yet, a VIEWER (who cannot do the steps), hidden for good, or
 * all three done (unless brought back from Help with every step done, `get_started_shown_at`). The backend's `visible`
 * is the same rule (app/me/setup.py get_started_view); it is worked out here too so a change shows at once.
 */
export function getStartedCard(view: SetupView | null, opts: { role?: string | null; brokerPath?: string } = {}): GetStartedCard | null {
  if (!view || opts.role === "VIEWER") return null;
  const card = getStartedSteps(view, opts.brokerPath);
  if (!card) return null;
  if (view.stored.get_started_hidden_at !== null) return null;
  if (card.complete && view.stored.get_started_shown_at === null) return null;
  return card;
}

/** True when the card should be hidden for good now: every step done and no mark yet (not brought back from Help). */
export function shouldHideForGood(view: SetupView | null): boolean {
  const card = getStartedSteps(view);
  return !!view && !!card?.complete && view.stored.get_started_hidden_at === null && view.stored.get_started_shown_at === null;
}

/** Hidden for good (all done, or "Hide"): the "brought back" mark goes with it, so the next completion hides it again. */
export const hideGetStarted = (stored: SetupStored, nowIso: string): SetupStored =>
  ({ ...stored, get_started_hidden_at: nowIso, get_started_shown_at: null });

/** Help -> "Show Get started": the hidden mark cleared; with every step done, `get_started_shown_at` keeps it showing. */
export const showGetStarted = (stored: SetupStored, complete: boolean, nowIso: string): SetupStored =>
  ({ ...stored, get_started_hidden_at: null, get_started_shown_at: complete ? nowIso : null });
