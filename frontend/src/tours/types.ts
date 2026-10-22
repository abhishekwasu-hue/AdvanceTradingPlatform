import type { TourId } from "./pages";

/**
 * MO2 part C: what a tour is made of. The words live in one English file per page (src/tours/content/<page>.ts); the
 * engine (src/tours/TourEngine.tsx) only shows them.
 */

export type TourLayout = "desktop" | "phone";

export interface TourStep {
  /** Stable step id (tests and the step counter). */
  id: string;
  /** The `data-tour="..."` value of the element the step points at. Left out: a card in the middle of the screen. */
  target?: string;
  /** Only on this layout (the phone layout is below the md breakpoint, 768 px). Left out: both. */
  only?: TourLayout;
  title: string;
  /** One or two short sentences, plain words. */
  body: string;
}

export interface PageTour {
  id: TourId;
  /** 3 to 5 steps on each layout (a test checks it): what is here, where to start, what the result means. */
  steps: readonly TourStep[];
}

export interface WelcomeCard {
  id: "what" | "broker" | "paper" | "alerts";
  title: string;
  body: string;
}

/** The steps of a tour for one layout, in order. */
export function stepsFor(tour: PageTour, layout: TourLayout): TourStep[] {
  return tour.steps.filter((s) => !s.only || s.only === layout);
}

/** The CSS selector of a step's target. */
export const targetSelector = (target: string): string => `[data-tour="${target}"]`;
