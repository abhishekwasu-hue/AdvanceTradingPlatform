import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, friendlyError } from "../api/errors";

/** P1.1: short, non-blocking notices (saved, copied, failed) in the corner of the screen, instead of `window.alert`
 * or nothing at all. `useToast()` inside React; `toast` for code outside components. A failed request nobody caught
 * (an unhandled promise rejection carrying an ApiError) is shown as an error toast so it is never silently lost. */
export type ToastKind = "success" | "error" | "info";
interface ToastItem { id: number; kind: ToastKind; text: string }
interface ToastApi { success: (text: string) => void; error: (text: string) => void; info: (text: string) => void }

const TOAST_EVENT = "atp:toast";
const ToastContext = createContext<ToastApi | null>(null);
const DURATION: Record<ToastKind, number> = { success: 4000, info: 6000, error: 9000 };
const MAX_VISIBLE = 4;

function emit(kind: ToastKind, text: string) {
  window.dispatchEvent(new CustomEvent<{ kind: ToastKind; text: string }>(TOAST_EVENT, { detail: { kind, text } }));
}

/** For non-React code (the API client, event handlers in plain modules). */
export const toast: ToastApi = {
  success: (text) => emit("success", text),
  error: (text) => emit("error", text),
  info: (text) => emit("info", text),
};

const STYLE: Record<ToastKind, { cls: string; icon: typeof Info }> = {
  // P1.2: one surface for every toast; the edge and icon carry the meaning.
  success: { cls: "border-l-4 border-border border-l-up bg-surface-1 text-fg [&_svg:first-child]:text-up", icon: CheckCircle2 },
  error: { cls: "border-l-4 border-border border-l-down bg-surface-1 text-fg [&_svg:first-child]:text-down", icon: TriangleAlert },
  info: { cls: "border-l-4 border-border border-l-brand bg-surface-1 text-fg [&_svg:first-child]:text-brand", icon: Info },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    setItems((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) window.clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const push = useCallback((kind: ToastKind, text: string) => {
    const clean = text.trim();
    if (!clean) return;
    const id = nextId.current++;
    // The updater stays pure (StrictMode may run it twice); a timer for a duplicate that was not added is harmless.
    setItems((list) => {
      // The same message already on screen (a poll failing every few seconds) is not stacked again.
      if (list.some((t) => t.kind === kind && t.text === clean)) return list;
      return [...list, { id, kind, text: clean }].slice(-MAX_VISIBLE);
    });
    timers.current.set(id, window.setTimeout(() => dismiss(id), DURATION[kind]));
  }, [dismiss]);

  useEffect(() => {
    const onToast = (e: Event) => {
      const d = (e as CustomEvent<{ kind: ToastKind; text: string }>).detail;
      if (d) push(d.kind, d.text);
    };
    const onRejection = (e: PromiseRejectionEvent) => {
      if (e.reason instanceof ApiError) push("error", friendlyError(e.reason));
    };
    const pending = timers.current;
    window.addEventListener(TOAST_EVENT, onToast);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast);
      window.removeEventListener("unhandledrejection", onRejection);
      pending.forEach((t) => window.clearTimeout(t));
    };
  }, [push]);

  const value = useMemo<ToastApi>(() => ({
    success: (t) => push("success", t), error: (t) => push("error", t), info: (t) => push("info", t),
  }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-3 bottom-3 z-50 flex flex-col items-end gap-2 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-96"
           role="region" aria-label="Notifications">
        {items.map((t) => {
          const { cls, icon: Icon } = STYLE[t.kind];
          return (
            <div key={t.id} role={t.kind === "error" ? "alert" : "status"}
                 className={`pointer-events-auto flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-sm shadow-lg backdrop-blur ${cls}`}>
              <Icon size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
              <span className="flex-1 break-words">{t.text}</span>
              <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="rounded p-0.5 opacity-70 hover:opacity-100"><X size={14} /></button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext) ?? toast;
}
