import { useEffect, useState } from "react";

/**
 * UI2-1 review R2-1: the neutral waiting screen (shellMode "wait") is bounded. If the auth check or the flags read has
 * not answered SHELL_WAIT_MS after the wait began, the app falls to today's shell - exactly as a failed flags read
 * does (the kill switch) - instead of a blank screen forever. The flags read itself also gives up after
 * FEATURES_TIMEOUT_MS (api.myFeatures), so it fails and answers "off" rather than hanging.
 */
export const SHELL_WAIT_MS = 3000;

/** Calls `onCap` once `ms` after now; the returned function cancels it. */
export function startWaitCap(onCap: () => void, ms = SHELL_WAIT_MS): () => void {
  const t = setTimeout(onCap, ms);
  return () => clearTimeout(t);
}

/** True once the shell has been waiting (`waiting`) for SHELL_WAIT_MS; back to false when the wait ends, so a later
 * wait (signed in again, another account) gets its own full allowance. */
export function useWaitCap(waiting: boolean, ms = SHELL_WAIT_MS): boolean {
  const [capped, setCapped] = useState(false);
  useEffect(() => {
    if (!waiting) { setCapped(false); return undefined; }
    return startWaitCap(() => setCapped(true), ms);
  }, [waiting, ms]);
  return capped;
}
