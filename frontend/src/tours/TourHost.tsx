import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import type { TourId } from "./pages";
import { autoTour, closeTour, useTours, type CloseReason, type TourRequest } from "./state";

const TourEngine = lazy(() => import("./TourEngine"));

/** How long a page has to settle before its tips start by themselves (the engine then waits for the elements too). */
export const START_DELAY_MS = 600;

/**
 * MO2 part C: mounted once in the v2 shell. It decides when a tour plays (src/tours/state.ts autoTour / closeTour) and
 * loads the engine only then.
 * - The welcome tour on the first sign-in (server marks read, nothing skipped), wherever the trader lands; its last card
 *   leads to the Broker page. Closing it early leaves this page's own tips for the next visit.
 * - A page's tips on its first visit, once the welcome is out of the way and unless "Skip all tips" is on.
 * - A replay from the "?" button or Help (a request on the store), whatever the marks say.
 * Finishing or closing a tour marks it on the server; a page whose elements never showed is not marked. Moving to
 * another page closes a tour without a mark.
 */
export default function TourHost({ userKey, role, tourId }: { userKey: number | string | null; role: string | null; tourId: TourId | null }) {
  const tours = useTours(userKey);
  const { store } = tours;
  const location = useLocation();
  const navigate = useNavigate();
  const path = location.pathname;
  const [active, setActive] = useState<{ tour: TourRequest; auto: boolean; path: string } | null>(null);
  // The address where no tip opens by itself again in this visit (the welcome was closed there, or the page's elements
  // never showed). Lifted on the next page.
  const [held, setHeld] = useState<string | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;

  useEffect(() => store?.onRequest((tour) => setActive({ tour, auto: false, path: window.location.pathname })), [store]);

  useEffect(() => {
    setActive((a) => (a && a.path !== path ? null : a));
    setHeld((h) => (h === path ? h : null));
  }, [path]);

  const due = held === path ? null : autoTour(tours, role, tourId);
  const dueKey = due ? (due.kind === "welcome" ? "welcome" : due.id) : null;
  useEffect(() => {
    if (active || !due) return;
    const timer = window.setTimeout(() => setActive((a) => a ?? { tour: due, auto: true, path }), START_DELAY_MS);
    return () => window.clearTimeout(timer);
    // `due` is rebuilt on every render; `dueKey` names it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, dueKey, path]);

  const onClose = useCallback((reason: CloseReason) => {
    const playing = activeRef.current;
    setActive(null);
    if (!playing || !store) return;
    const closed = closeTour(playing.tour, reason, new Date().toISOString());
    void store.update(closed.change);
    if (closed.hold) setHeld(playing.path);
    if (closed.go) navigate(closed.go);
  }, [store, navigate]);

  const onUnavailable = useCallback(() => {
    setHeld(activeRef.current?.path ?? null);
    setActive(null);
  }, []);

  if (!active || !userKey) return null;
  return (
    <Suspense fallback={null}>
      <TourEngine key={`${active.tour.kind}:${active.tour.kind === "page" ? active.tour.id : ""}:${active.path}`} tour={active.tour} auto={active.auto}
        onClose={onClose} onUnavailable={onUnavailable} />
    </Suspense>
  );
}
