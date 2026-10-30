import type { RhythmSection } from "../rhythm/types";

/** MO4 slice 3 (`ui_v2` + `growth_v2`): what `/api/growth/*` and `/api/admin/console/activation` answer. */

export interface PlanMeter {
  key: string;
  label: string;
  used: number | null;
  /** null: the plan sets no limit on it (the row shows the count only). */
  limit: number | null;
  period: string;
  text: string;
  state: "ok" | "near" | "full";
  note: string | null;
}

export interface PlanPage {
  plan: { id: string; name: string; status: string };
  trial: { plan_name: string; ends_on: string; days_left: number } | null;
  includes: { key: string; text: string }[];
  meters: PlanMeter[];
  month_starts: string;
  resets: string;
}

export interface CardFact { key: string; label: string; value: string }

export interface DisciplineCard {
  title: string;
  week_label: string;
  facts: CardFact[];
  lines: string[];
  footer: string;
  empty: boolean;
  /** In-app only, never on the card image. */
  hints: string[];
}

export interface ActivationPerson {
  id: number; email: string; tenant: string; plan: string; signed_up_at: string | null;
  broker_at: string | null; paper_bot_at: string | null; journal_at: string | null; activated_at: string | null;
  /** Readiness C13: came back between day 1 and day 7; null while the first week is still running. */
  active_week1?: boolean | null;
}

/** Readiness C13: signed up -> broker -> trading bot -> active in week 1, each step counting only the people who also took
 * every step before it, over the people whose first week is over (`week1_open` are still inside it). */
export interface GrowthFunnel {
  steps: { key: "signed_up" | "broker" | "paper_bot" | "active_week1"; label: string; count: number }[];
  week1_open: number;
}

/** AUD-G2.4: time to first value - the five activation steps (each counted once per person), the median hours from
 * signing up to each, and the share whose first paper trade came within their first session (`hours` after signing up;
 * `decided` = first session over or already holding that trade, `open` = still inside it). */
export interface ValueFunnel {
  steps: { key: "signup" | "broker_connect" | "first_chart" | "first_paper_trade" | "first_journal_review"; label: string; count: number; median_hours: number | null }[];
  first_session: { hours: number; decided: number; within: number; open: number; share_pct: number | null };
}

export interface Activation {
  days: number;
  since: string;
  funnel: { key: string; label: string; count: number }[];
  median_hours_to_activation: number | null;
  growth_funnel?: GrowthFunnel;
  value_funnel?: ValueFunnel;
  people: ActivationPerson[];
}

/** MO4 slice 4: the "ready-made dish" at the top of a page, as `/api/growth/dish/<page>` sends it. */
export type DishPage = "signals" | "positions" | "journal" | "charts" | "screener";

export interface PageDish {
  page: DishPage;
  title: string;
  /** The one line that stands out (null when there is nothing to say yet). */
  headline: string | null;
  /** False for the journal while the journal is off: the page says so itself, so the dish shows nothing. */
  available: boolean;
  sections: RhythmSection[];
  as_of: string;
  note: string;
}
