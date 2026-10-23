import { TriangleAlert, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { API_ORIGIN } from "../api/base";
import { Button } from "../components/primitives";
import { appVersions, versionLine } from "./version";

/** A debug build's API origin is the `.invalid` placeholder (scripts/mobile-rules.mjs): it can never reach a server. */
export function isPlaceholderOrigin(origin: string): boolean {
  try {
    return new URL(origin).hostname.toLowerCase().endsWith(".invalid");
  } catch {
    return true;
  }
}

export const NOT_CONNECTED_TEXT =
  "Test build: this app is not connected to ATP. It is for checking the layout on a phone and cannot sign in.";

/**
 * Mobile B1: the Android app's frame around the same React app (the web never renders it). In a debug build, which is
 * never pointed at a real server (ADR-0028 decision 10), a notice says so plainly instead of leaving a sign-in screen
 * that silently fails; it can be closed to look at the layout underneath. B8: it also starts the app's link handling
 * (App Links, notification taps), so a link opens the right screen.
 */
export default function NativeFrame({ children }: { children: ReactNode }) {
  const notConnected = isPlaceholderOrigin(API_ORIGIN);
  const [open, setOpen] = useState(notConnected);
  const [version, setVersion] = useState("");
  useEffect(() => {
    // B8: App Links and notification taps open their screen (src/native/links.ts; allow-listed paths only).
    let stop: (() => void) | null = null;
    let live = true;
    void import("./links").then((m) => m.startNativeLinks()).then((s) => { if (live) stop = s; else s(); }, () => undefined);
    return () => { live = false; stop?.(); };
  }, []);
  useEffect(() => {
    if (!notConnected) return;
    let live = true;
    void appVersions().then((v) => { if (live) setVersion(versionLine(v)); });
    return () => { live = false; };
  }, [notConnected]);

  return (
    <>
      {children}
      {open && (
        <div
          role="status"
          data-native-notice="not-connected"
          className="fixed inset-x-0 top-0 z-50 pt-[env(safe-area-inset-top)]"
        >
          <div className="p-3">
            <div className="flex items-start gap-3 rounded-lg border border-border bg-warn-tint px-3 py-2 text-sm text-fg shadow-raised">
              <TriangleAlert size={16} className="mt-0.5 shrink-0 text-warn" aria-hidden />
              <span className="min-w-0 flex-1">
                {NOT_CONNECTED_TEXT}
                {version && <span className="mt-1 block text-xs text-fg-muted">Version {version}</span>}
              </span>
              <Button variant="ghost" className="h-11 w-11 px-0" aria-label="Close" onClick={() => setOpen(false)} icon={<X size={16} />} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
