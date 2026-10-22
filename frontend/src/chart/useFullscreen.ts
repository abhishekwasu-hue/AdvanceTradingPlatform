/**
 * UI2-4b2 (Charts page plan §3): full screen for the Charts page. The page root goes full screen (so the top bar and,
 * from UI2-4c, the drawing rail stay with the chart) through the Fullscreen API; where the browser has none (iPhone
 * Safari) the root covers the window instead (`fixed inset-0`).
 *
 * Keys, read on the window before anything else sees them:
 * - F toggles - not while typing (a field, a text area, editable text), not with Ctrl / Alt / Cmd, not while a drawing
 *   tool is placing a drawing or a menu is open;
 * - Escape leaves - but a drawing tool's own Escape (cancel the drawing) and a menu's (close it) go first: while either
 *   is active, Escape is left to them.
 * In the browser's own full screen, the browser also leaves on Escape by itself.
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/** True when a key press goes to text the trader is typing. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as (HTMLElement & { isContentEditable?: boolean }) | null;
  if (!el || typeof el !== "object" || !("tagName" in el)) return false;
  const tag = String(el.tagName).toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!el.isContentEditable;
}

export interface KeyLike {
  key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean; repeat?: boolean; target: EventTarget | null; defaultPrevented?: boolean;
}

/** What a key press does to full screen. `busy`: a drawing tool is placing, or a menu is open. A held key (auto-repeat)
 * does nothing more, so holding F does not flicker in and out. */
export function fullscreenKey(e: KeyLike, state: { on: boolean; busy: boolean }): "toggle" | "exit" | null {
  if (e.defaultPrevented || e.repeat || state.busy) return null;
  if (e.key === "Escape") return state.on ? "exit" : null;
  if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(e.target)) return "toggle";
  return null;
}

export interface Fullscreen {
  on: boolean;
  /** True while the window-covering fallback is used (no Fullscreen API). */
  fallback: boolean;
  toggle: () => void;
  exit: () => void;
}

const nativeOf = (el: HTMLElement | null) => !!el && typeof document !== "undefined" && document.fullscreenElement === el;

/** `busy()`: a drawing tool is placing or a menu is open (read at the moment of the key press). `keys`: F and Escape
 * are listened to (off while the page shows something other than the chart, e.g. the Multi-chart tab). */
export function useFullscreen(root: RefObject<HTMLElement | null>, busy: () => boolean, keys = true): Fullscreen {
  const [native, setNative] = useState(false);
  const [fallback, setFallback] = useState(false);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const on = native || fallback;
  const onRef = useRef(on);
  onRef.current = on;

  useEffect(() => {
    const sync = () => setNative(nativeOf(root.current));
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [root]);

  const enter = useCallback(() => {
    const el = root.current;
    if (!el) return;
    if (typeof el.requestFullscreen === "function" && document.fullscreenEnabled !== false) {
      el.requestFullscreen().catch(() => setFallback(true));        // refused (no user gesture, a frame): cover the window
    } else {
      setFallback(true);
    }
  }, [root]);

  const exit = useCallback(() => {
    setFallback(false);
    if (nativeOf(root.current)) void document.exitFullscreen().catch(() => undefined);
  }, [root]);

  const toggle = useCallback(() => { if (onRef.current) exit(); else enter(); }, [enter, exit]);

  useEffect(() => {
    if (!keys) return undefined;
    const onKey = (e: KeyboardEvent) => {
      const action = fullscreenKey(e, { on: onRef.current, busy: busyRef.current() });
      if (!action) return;
      e.preventDefault();
      if (action === "exit") exit(); else toggle();
    };
    window.addEventListener("keydown", onKey, true);               // capture: before the drawing tools' own keys
    return () => window.removeEventListener("keydown", onKey, true);
  }, [exit, toggle, keys]);

  // Leaving the page while full screen: the browser's full screen ends with it.
  useEffect(() => () => { if (document.fullscreenElement && document.fullscreenElement === root.current) void document.exitFullscreen().catch(() => undefined); }, [root]);

  return { on, fallback, toggle, exit };
}
