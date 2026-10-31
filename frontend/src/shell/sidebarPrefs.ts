import { useCallback, useEffect, useState } from "react";
import { request } from "../api/client";

/**
 * M57 U7-1 (owner order, 30 Oct): the sidebar's show / hide state. From md up the sidebar is either the full menu
 * ("expanded") or a 56 px rail of icons ("rail"). The choice follows the user to every device: the server holds it
 * (`/api/me/prefs`, namespace `shell`, backend app/me/prefs.py ShellPrefs); this browser keeps a copy for the first paint
 * (localStorage `atp_sidebar`). Never chosen (null): expanded from 1024 px (lg), the rail on a tablet (md).
 */
export type SidebarMode = "expanded" | "rail";

export const SIDEBAR_STORAGE_KEY = "atp_sidebar";
export const SIDEBAR_NAMESPACE = "shell";
/** From this width the default is the full menu (Tailwind lg). */
export const WIDE_QUERY = "(min-width: 1024px)";
/** The keyboard shortcut that hides and shows the menu. */
export const SIDEBAR_SHORTCUT = "[";

export const isSidebarMode = (v: unknown): v is SidebarMode => v === "expanded" || v === "rail";

/** The mode on screen: the user's own choice, else the default for the width. */
export function sidebarModeFor(choice: SidebarMode | null, wide: boolean): SidebarMode {
  return choice ?? (wide ? "expanded" : "rail");
}

export function readStoredSidebar(): SidebarMode | null {
  try {
    const v = localStorage.getItem(SIDEBAR_STORAGE_KEY);
    return isSidebarMode(v) ? v : null;
  } catch {
    return null;
  }
}

function storeSidebar(mode: SidebarMode): void {
  try { localStorage.setItem(SIDEBAR_STORAGE_KEY, mode); } catch { /* intentional: storage unavailable - the server copy still holds it */ }
}

interface PrefsRead { prefs?: { shell?: { sidebar?: unknown } }; versions?: { shell?: number } }

/** The user's stored choice on the server (null: never chosen, or the server could not be reached). */
export async function pullSidebar(fetcher: typeof request = request): Promise<SidebarMode | null> {
  try {
    const read = await fetcher<PrefsRead>("/me/prefs");
    const v = read.prefs?.shell?.sidebar;
    return isSidebarMode(v) ? v : null;
  } catch {
    return null;
  }
}

/** Saves the choice for the user. False when the server could not be reached (the device keeps it). */
export async function pushSidebar(mode: SidebarMode, fetcher: typeof request = request): Promise<boolean> {
  try {
    await fetcher(`/me/prefs/${SIDEBAR_NAMESPACE}`, { method: "PUT", body: JSON.stringify({ sidebar: mode }) });
    return true;
  } catch {
    return false;
  }
}

/** True when a key press is the shortcut `[` on its own, outside a text field (so typing "[" never hides the menu). */
export function isSidebarShortcut(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "target">): boolean {
  if (e.key !== SIDEBAR_SHORTCUT || e.ctrlKey || e.metaKey || e.altKey) return false;
  const t = e.target;
  if (t && typeof (t as HTMLElement).closest === "function") {
    const el = t as HTMLElement;
    if (el.isContentEditable || el.closest("input, textarea, select, [contenteditable=''], [contenteditable='true'], [role='textbox']")) return false;
  }
  return true;
}

/**
 * The shell's sidebar state: `mode` on screen and `toggle`. Signed in (`userKey`): the server's choice is read once and
 * wins over this browser's copy; every change is saved on both. Signed out: this browser only.
 */
export function useSidebarMode(userKey: number | string | null, wide: boolean, fetcher: typeof request = request): {
  mode: SidebarMode; toggle: () => void;
} {
  const [choice, setChoice] = useState<SidebarMode | null>(() => readStoredSidebar());
  useEffect(() => {
    if (userKey == null) return;
    let live = true;
    void pullSidebar(fetcher).then((stored) => {
      if (!live || stored === null) return;
      storeSidebar(stored);
      setChoice(stored);
    });
    return () => { live = false; };
  }, [userKey, fetcher]);
  const mode = sidebarModeFor(choice, wide);
  const toggle = useCallback(() => {
    const next: SidebarMode = mode === "expanded" ? "rail" : "expanded";
    setChoice(next);
    storeSidebar(next);
    if (userKey != null) void pushSidebar(next, fetcher);
  }, [mode, userKey, fetcher]);
  return { mode, toggle };
}
