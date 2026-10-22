import { Bell, Bot, Plug, Sparkles, X, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { motionReduced } from "../theme";
import { PAGE_TOURS, WELCOME, WELCOME_CTA } from "./content";
import type { CloseReason, TourRequest } from "./state";
import { stepsFor, targetSelector, type TourLayout, type TourStep, type WelcomeCard } from "./types";

/**
 * MO2 part C: the tour engine, its own lazy chunk (src/tours/TourHost.tsx loads it only when a tour plays), so the
 * first page load carries none of it.
 *
 * Never blocking: no modal backdrop, no focus trap. The spotlight is a ring with a dimmed surround that takes no
 * pointer events; a tap anywhere outside the card closes the tour and still reaches the page, and Esc closes it.
 * Focus moves to the card when a step opens and goes back where it was when the tour closes. With reduced motion (the
 * system's or the trader's setting) nothing scrolls smoothly and the ring does not glide (the duration tokens are 0).
 *
 * A page tour started by itself (`auto`) waits up to WAIT_MS for every step's element; if one is missing (the page is
 * still showing "Connect your broker", say) it does not play and is not marked seen, so it plays on a later visit. A
 * replay ("?") plays the steps whose element is there.
 */


export interface TourEngineProps {
  tour: TourRequest;
  auto: boolean;
  onClose: (reason: CloseReason) => void;
  /** An automatic page tour whose elements are not on the page: closed without a mark. */
  onUnavailable: () => void;
}

export const WAIT_MS = 4000;
const POLL_MS = 100;
const GAP = 12;
const EDGE = 16;
const RING_PAD = 6;
const PHONE_QUERY = "(min-width: 768px)";

const WELCOME_ICONS: Record<WelcomeCard["id"], LucideIcon> = { what: Sparkles, broker: Plug, paper: Bot, alerts: Bell };

const viewSize = () => (typeof window === "undefined" ? { width: 1440, height: 900 } : { width: window.innerWidth, height: window.innerHeight });
/** Measures the card before it paints in the browser (useEffect where there is no DOM, so a static render stays quiet). */
const useMeasureEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export type { CloseReason };

export function currentLayout(): TourLayout {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && !window.matchMedia(PHONE_QUERY).matches ? "phone" : "desktop";
}

/** The step's element when it is on the page and takes room (an element hidden on this layout does not count). */
export function findTarget(step: TourStep, root: ParentNode = document): HTMLElement | null {
  if (!step.target) return null;
  const el = root.querySelector<HTMLElement>(targetSelector(step.target));
  if (!el) return null;
  const box = el.getBoundingClientRect();
  return box.width > 0 || box.height > 0 ? el : null;
}

interface Rect { top: number; left: number; width: number; height: number }

const sameRect = (a: Rect | null, b: Rect | null) =>
  a === b || (!!a && !!b && a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height);

/** Where the card goes: under the element when it fits, else above it, else beside it (desktop), else docked at the
 * bottom; always on screen. */
export function placeCard(target: Rect | null, card: { width: number; height: number }, view: { width: number; height: number }, layout: TourLayout): CSSProperties {
  const width = Math.min(card.width, view.width - 2 * EDGE);
  if (!target) return { top: Math.max(EDGE, (view.height - card.height) / 2), left: (view.width - width) / 2, width };
  const left = layout === "phone" ? EDGE : Math.min(Math.max(EDGE, target.left), view.width - width - EDGE);
  const below = target.top + target.height + RING_PAD + GAP;
  const above = target.top - RING_PAD - GAP - card.height;
  if (below + card.height <= view.height - EDGE) return { top: below, left, width };
  if (above >= EDGE) return { top: above, left, width };
  const top = Math.min(Math.max(EDGE, target.top), view.height - card.height - EDGE);
  if (layout === "desktop") {
    const right = target.left + target.width + RING_PAD + GAP;
    if (right + width <= view.width - EDGE) return { top, left: right, width };
    const beside = target.left - RING_PAD - GAP - width;
    if (beside >= EDGE) return { top, left: beside, width };
  }
  return { top: Math.max(EDGE, view.height - card.height - EDGE), left, width };
}

/** A tall element is scrolled to the top of the screen (so the card fits under it), a short one to the middle. */
export function scrollBlock(height: number, viewHeight: number): ScrollLogicalPosition {
  return height > viewHeight * 0.4 ? "start" : "center";
}

export default function TourEngine({ tour, auto, onClose, onUnavailable }: TourEngineProps) {
  if (tour.kind === "welcome") return <WelcomeTour onClose={onClose} />;
  return <PageTourView tour={tour} auto={auto} onClose={onClose} onUnavailable={onUnavailable} />;
}

/** Esc and a tap outside the card close the tour; focus goes back where it was. */
function useLightDismiss(card: RefObject<HTMLElement>, close: () => void) {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    const onDown = (e: PointerEvent) => {
      if (card.current && e.target instanceof Node && !card.current.contains(e.target)) closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown, true);
      if (before && document.contains(before)) before.focus({ preventScroll: true });
    };
  }, [card]);
}

function CardShell({ cardRef, style, titleId, bodyId, label, children }: {
  cardRef: RefObject<HTMLDivElement>; style: CSSProperties; titleId: string; bodyId: string; label: string; children: ReactNode;
}) {
  return (
    <div ref={cardRef} role="dialog" aria-modal="false" aria-labelledby={titleId} aria-describedby={bodyId} aria-roledescription="tip" tabIndex={-1}
      data-tour-card={label} style={style}
      className="fixed z-[80] rounded-panel border border-subtle bg-surface-1 p-space-4 text-fg shadow-raised outline-none transition-[top,left] duration-base md:p-space-5">
      {children}
    </div>
  );
}

function Dots({ count, at }: { count: number; at: number }) {
  return (
    <span aria-hidden className="flex items-center gap-space-1">
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className={`h-1.5 rounded-pill ${i === at ? "w-4 bg-brand" : "w-1.5 bg-border"}`} />
      ))}
    </span>
  );
}

const TEXT_BUTTON = "inline-flex min-h-11 items-center rounded-control px-space-2 text-t14 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

function WelcomeTour({ onClose }: { onClose: (reason: CloseReason) => void }) {
  const [at, setAt] = useState(0);
  const card = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const [size, setSize] = useState({ width: 400, height: 280 });
  const [view, setView] = useState(viewSize);
  useLightDismiss(card, () => onClose("skip"));
  useEffect(() => {
    const onResize = () => setView(viewSize());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useMeasureEffect(() => {
    const el = card.current;
    if (el) setSize((s) => (s.height === el.offsetHeight ? s : { width: 400, height: el.offsetHeight }));
  }, [at, view]);
  useEffect(() => { card.current?.focus({ preventScroll: true }); }, [at]);

  const step = WELCOME[at];
  const last = at === WELCOME.length - 1;
  const Icon = WELCOME_ICONS[step.id];
  return (
    <>
    {/* A dim surround that takes no pointer events: the page stays usable (a tap on it closes the welcome). */}
    <div aria-hidden data-tour-dim className="pointer-events-none fixed inset-0 z-[79] bg-scrim/50" />
    <CardShell cardRef={card} style={placeCard(null, size, view, "desktop")} titleId={titleId} bodyId={bodyId} label={`welcome-${step.id}`}>
      <div className="flex items-start justify-between gap-space-3">
        <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-brand/10 text-brand"><Icon size={20} /></span>
        <button type="button" onClick={() => onClose("skip")} aria-label="Close the welcome tour"
          className="-mr-space-2 -mt-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <X size={16} aria-hidden />
        </button>
      </div>
      <p className="mt-space-3 text-t12 font-medium text-fg-muted">{at + 1} of {WELCOME.length}</p>
      <h2 id={titleId} className="mt-space-1 text-t18 font-semibold text-fg">{step.title}</h2>
      <p id={bodyId} className="mt-space-2 text-t14 text-fg-muted">{step.body}</p>
      <div className="mt-space-5 flex flex-wrap items-center justify-between gap-space-3">
        <Dots count={WELCOME.length} at={at} />
        <div className="flex flex-wrap items-center justify-end gap-space-2">
          {last ? (
            <>
              <button type="button" onClick={() => onClose("done")} className={TEXT_BUTTON}>Later</button>
              <button type="button" onClick={() => onClose("cta")} className={LINK_PRIMARY} data-tour-primary>{WELCOME_CTA.label}</button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => onClose("skip")} className={TEXT_BUTTON}>Skip</button>
              <button type="button" onClick={() => setAt(at + 1)} className={LINK_PRIMARY} data-tour-primary>Next</button>
            </>
          )}
        </div>
      </div>
      <div className="mt-space-3 border-t border-subtle pt-space-2">
        <button type="button" onClick={() => onClose("skip-all")} className={TEXT_BUTTON}>Skip all tips</button>
      </div>
    </CardShell>
    </>
  );
}

function PageTourView({ tour, auto, onClose, onUnavailable }: Omit<TourEngineProps, "tour"> & { tour: { kind: "page"; id: keyof typeof PAGE_TOURS } }) {
  const layout = useMemo(currentLayout, []);
  const all = useMemo(() => stepsFor(PAGE_TOURS[tour.id], layout), [tour.id, layout]);
  const [steps, setSteps] = useState<TourStep[] | null>(null);
  const [at, setAt] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [size, setSize] = useState({ width: 360, height: 200 });
  const [view, setView] = useState(viewSize);
  const card = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const unavailable = useRef(onUnavailable);
  unavailable.current = onUnavailable;

  // Wait for the steps' elements (the page may still be loading), then fix the list.
  useEffect(() => {
    let done = false;
    const started = Date.now();
    const check = () => {
      if (done) return;
      const found = all.filter((s) => !s.target || findTarget(s));
      if (found.length === all.length) { done = true; setSteps(all); return; }
      if (Date.now() - started < WAIT_MS) { timer = window.setTimeout(check, POLL_MS); return; }
      done = true;
      if (auto) { unavailable.current(); return; }
      setSteps(found.some((s) => s.target) ? found : all.map((s) => ({ ...s, target: undefined })));
    };
    let timer = window.setTimeout(check, 0);
    return () => { done = true; window.clearTimeout(timer); };
  }, [all, auto]);

  const step = steps?.[at] ?? null;

  // Bring the element into view, then follow it (scroll, resize, a block that loads above it) until the step changes.
  useEffect(() => {
    if (!step) return;
    const el = findTarget(step);
    const behavior: ScrollBehavior = motionReduced() ? "auto" : "smooth";
    if (el) {
      const box = el.getBoundingClientRect();
      // The window scrolls in the v2 shell: a tall element goes to just under the top edge, a short one to the middle.
      if (scrollBlock(box.height, window.innerHeight) === "start") window.scrollBy?.({ top: box.top - 2 * EDGE, behavior });
      else el.scrollIntoView?.({ block: "center", inline: "nearest", behavior });
    }
    let frame = 0;
    let last: Rect | null = null;
    const follow = () => {
      const now = findTarget(step);
      const box = now ? now.getBoundingClientRect() : null;
      const next = box ? { top: box.top, left: box.left, width: box.width, height: box.height } : null;
      if (!sameRect(next, last)) { last = next; setRect(next); }
      frame = window.requestAnimationFrame(follow);
    };
    follow();
    const onResize = () => setView(viewSize());
    window.addEventListener("resize", onResize);
    return () => { window.cancelAnimationFrame(frame); window.removeEventListener("resize", onResize); };
  }, [step]);

  useMeasureEffect(() => {
    const el = card.current;
    if (el) setSize((s) => (s.height === el.offsetHeight ? s : { width: 360, height: el.offsetHeight }));
  }, [step, view]);
  useEffect(() => { if (step) card.current?.focus({ preventScroll: true }); }, [step]);

  const close = useCallback((reason: CloseReason) => onClose(reason), [onClose]);
  useLightDismiss(card, () => close("skip"));

  if (!steps || !step) return null;
  const last = at === steps.length - 1;
  const ring: CSSProperties | null = rect ? {
    top: rect.top - RING_PAD, left: rect.left - RING_PAD, width: rect.width + 2 * RING_PAD, height: rect.height + 2 * RING_PAD,
    boxShadow: "0 0 0 9999px rgb(var(--scrim) / 0.5)",
  } : null;
  return (
    <>
      {ring && <div aria-hidden data-tour-spotlight={step.target} style={ring}
        className="pointer-events-none fixed z-[79] rounded-panel outline outline-2 outline-offset-0 outline-brand transition-[top,left,width,height] duration-base" />}
      <CardShell cardRef={card} style={placeCard(rect, size, view, layout)} titleId={titleId} bodyId={bodyId} label={`${tour.id}-${step.id}`}>
        <div data-tour-step={step.id} data-tour-target={step.target ?? ""} className="flex items-start justify-between gap-space-3">
          <p className="text-t12 font-medium text-fg-muted">Tip {at + 1} of {steps.length}</p>
          <button type="button" onClick={() => close("skip")} aria-label="Close the tips"
            className="-mr-space-2 -mt-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 md:w-9">
            <X size={16} aria-hidden />
          </button>
        </div>
        <h2 id={titleId} className="text-t15 font-semibold text-fg">{step.title}</h2>
        <p id={bodyId} className="mt-space-1 text-t14 text-fg-muted">{step.body}</p>
        {last && <p className="mt-space-2 text-t12 text-fg-muted">Press ? next to the page title to see these tips again.</p>}
        <div className="mt-space-4 flex flex-wrap items-center justify-between gap-space-2">
          <button type="button" onClick={() => close("skip")} className={TEXT_BUTTON}>Skip</button>
          <div className="flex items-center gap-space-2">
            {at > 0 && <button type="button" onClick={() => setAt(at - 1)} className={LINK_SECONDARY}>Back</button>}
            <button type="button" onClick={() => (last ? close("done") : setAt(at + 1))} className={LINK_PRIMARY} data-tour-primary>
              {last ? "Done" : "Next"}
            </button>
          </div>
        </div>
      </CardShell>
    </>
  );
}
