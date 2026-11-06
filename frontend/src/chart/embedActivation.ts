import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

/**
 * P1-4 (owner order "ATP session: chart and colour", Part 2.3): an embedded chart inside a scrolling page works like a
 * map embed on a touch screen. Before it is activated a one-finger vertical swipe scrolls the PAGE (the chart still
 * answers to a sideways drag and a pinch, through the library); a tap activates it (a subtle outline) and from then on
 * every touch moves the chart (ChartV2 `captureTouch`); a tap outside it, or EMBED_IDLE_MS with no touch on it,
 * deactivates it. The first time, the hint "Tap to move the chart" shows; once he has tapped, it is remembered on this
 * device (localStorage, every access in try / catch; a broken store shows the hint again, nothing else).
 * A mouse never activates (a mouse drag moves the price either way) and a device without touch shows no hint.
 * Full-screen charts (the Charts page, ChartWindow) do not use this: they capture touch all the time.
 */
export const EMBED_HINT_KEY = "atp_chart_embed_hint:1";
export const EMBED_IDLE_MS = 3000;
/** A press that moves less than this (CSS px) and is lifted is a tap; more is a drag. */
export const TAP_SLOP_PX = 10;
export const EMBED_HINT_TEXT = "Tap to move the chart";

export interface EmbedState { active: boolean; hint: boolean }

export function embedHintSeen(): boolean {
  try { return localStorage.getItem(EMBED_HINT_KEY) === "seen"; } catch { return false; }
}

export function rememberEmbedHint(): void {
  try { localStorage.setItem(EMBED_HINT_KEY, "seen"); } catch { /* intentional: storage unavailable - the hint shows again next time */ }
}

const isTouch = (pointerType: string) => pointerType === "touch" || pointerType === "pen";

/** The activation state of one embedded chart; `onChange` gets every new state. No DOM: the host feeds it pointers. */
export class EmbedActivation {
  private state: EmbedState;
  private press: { id: number; x: number; y: number; moved: boolean } | null = null;
  private down = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  /** `touchDevice`: the device has a touch screen (only then is the hint shown). */
  constructor(private readonly onChange: (s: EmbedState) => void, touchDevice: boolean) {
    this.state = { active: false, hint: touchDevice && !embedHintSeen() };
  }

  get current(): EmbedState { return this.state; }

  /** A pointer went down on the chart. */
  pointerDown(x: number, y: number, pointerType: string, id: number): void {
    if (!isTouch(pointerType)) return;
    this.down += 1;
    this.clearTimer();                                   // a finger on the chart is activity: no idle end under it
    if (!this.press) this.press = { id, x, y, moved: false };
    else this.press.moved = true;                       // a second finger: a pinch, not a tap
  }

  pointerMove(x: number, y: number, id: number): void {
    const p = this.press;
    if (p && p.id === id && Math.hypot(x - p.x, y - p.y) > TAP_SLOP_PX) p.moved = true;
  }

  /** A pointer was lifted on the chart (`cancelled`: the browser took the gesture, e.g. it scrolled the page). */
  pointerUp(x: number, y: number, id: number, cancelled = false): void {
    const p = this.press;
    if (p && p.id === id) {
      this.pointerMove(x, y, id);
      if (!cancelled && !p.moved) this.activate();
      this.press = null;
    }
    if (this.down > 0) this.down -= 1;
    if (this.down === 0 && this.state.active) this.armTimer();
  }

  /** A pointer went down outside the chart. */
  outside(): void { if (this.state.active) this.deactivate(); }

  dispose(): void { this.clearTimer(); }

  private activate(): void {
    if (this.state.hint) rememberEmbedHint();
    this.set({ active: true, hint: false });
  }

  private deactivate(): void {
    this.clearTimer();
    this.press = null;
    this.down = 0;
    this.set({ ...this.state, active: false });
  }

  private armTimer(): void {
    this.clearTimer();
    this.timer = setTimeout(() => { this.timer = null; this.deactivate(); }, EMBED_IDLE_MS);
  }

  private clearTimer(): void {
    if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; }
  }

  private set(next: EmbedState): void {
    if (next.active === this.state.active && next.hint === this.state.hint) return;
    this.state = next;
    this.onChange(next);
  }
}

function touchDevice(): boolean {
  try {
    return (typeof navigator !== "undefined" && navigator.maxTouchPoints > 0)
      || (typeof window !== "undefined" && !!window.matchMedia?.("(any-pointer: coarse)").matches);
  } catch {
    return false;
  }
}

type PointerHandler = (e: ReactPointerEvent<HTMLElement>) => void;
export interface EmbedActivationBinding extends EmbedState {
  /** Spread on the element around the chart (capture phase: the library's own handlers never hide a touch from it). */
  handlers: { onPointerDownCapture: PointerHandler; onPointerMoveCapture: PointerHandler; onPointerUpCapture: PointerHandler; onPointerCancelCapture: PointerHandler };
}

/** The React side: the state, the pointer handlers for the chart's box (`ref`) and the outside-tap listener. */
export function useEmbedActivation(ref: RefObject<HTMLElement | null>): EmbedActivationBinding {
  const [state, setState] = useState<EmbedState>({ active: false, hint: false });
  const machine = useRef<EmbedActivation | null>(null);
  useEffect(() => {
    const m = new EmbedActivation(setState, touchDevice());
    machine.current = m;
    setState(m.current);
    return () => { m.dispose(); machine.current = null; };
  }, []);
  useEffect(() => {
    if (!state.active) return;
    const onDown = (e: PointerEvent) => {
      const box = ref.current;
      if (box && e.target instanceof Node && box.contains(e.target)) return;
      machine.current?.outside();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [state.active, ref]);
  const handlers = useRef<EmbedActivationBinding["handlers"]>({
    onPointerDownCapture: (e) => machine.current?.pointerDown(e.clientX, e.clientY, e.pointerType, e.pointerId),
    onPointerMoveCapture: (e) => machine.current?.pointerMove(e.clientX, e.clientY, e.pointerId),
    onPointerUpCapture: (e) => machine.current?.pointerUp(e.clientX, e.clientY, e.pointerId),
    onPointerCancelCapture: (e) => machine.current?.pointerUp(e.clientX, e.clientY, e.pointerId, true),
  }).current;
  return { ...state, handlers };
}
