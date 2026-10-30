import { useCallback, useEffect, useRef } from "react";
import { api } from "../api/client";
import type { NotificationEntry, VisionReportsResponse } from "../types";
import { useLiveEvents } from "./useLiveEvents";

export interface NotificationReadTargets {
  setNotifications: (rows: NotificationEntry[]) => void;
  setError: (e: unknown) => void;
  setScanVision: (r: VisionReportsResponse | null) => void;
  setDailyVision: (r: VisionReportsResponse | null) => void;
}

/**
 * REV-1 M3 (review: "Every live event re-fetches all three lists"): the Notifications page's reads. On open: the feed
 * and the two Vision report reads. A live "notifications" event (a new alert; useLiveEvents already folds a burst into
 * one call) and Mark read re-read only the feed. The Vision reports are read again only when one of them changes
 * (`refresh`).
 */
export function useNotificationReads(enabled: boolean, targets: NotificationReadTargets): { refresh: () => void; refreshFeed: () => void } {
  const ref = useRef(targets);
  ref.current = targets;
  const refreshFeed = useCallback(() => {
    if (!enabled) return;
    api.listNotifications().then((rows) => ref.current.setNotifications(rows)).catch((e: unknown) => ref.current.setError(e));
  }, [enabled]);
  const refresh = useCallback(() => {
    if (!enabled) return;
    refreshFeed();
    api.byokReportsBy({ kind: "scan_alert" }).then((r) => ref.current.setScanVision(r)).catch(() => ref.current.setScanVision(null));
    api.byokReportsBy({ kind: "daily_review" }).then((r) => ref.current.setDailyVision(r)).catch(() => ref.current.setDailyVision(null));
  }, [enabled, refreshFeed]);
  useEffect(refresh, [refresh]);
  useLiveEvents(["notifications"], refreshFeed);                 // PW4: a new alert appears without a reload
  return { refresh, refreshFeed };
}
