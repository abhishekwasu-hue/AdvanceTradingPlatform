import { api } from "../api/client";

/**
 * TT12-1 (owner, DECISIONS 24 Oct): one-time notices for the signed-in user, in the v2 shell (`ui_v2`). The server
 * decides who gets one and remembers that it was seen (GET /api/me/notices, POST /api/me/notices/{id}/seen; the mark
 * lives in the user's prefs, so it never shows twice on any device). Dismissing hides it at once, even before the
 * server answers, and it is not shown again in this tab whatever a later read says.
 */
export interface OneTimeNotice {
  id: string;
  text: string;
  link: string;
}

const dismissed = new Set<string>();

/** The notices still to show: the server's list without the ones dismissed in this tab. */
export function visibleNotices(list: readonly OneTimeNotice[]): OneTimeNotice[] {
  return list.filter((n) => !dismissed.has(n.id));
}

/** The notices due now (best effort: a failed read shows nothing, never an error). */
export async function readNotices(): Promise<OneTimeNotice[]> {
  try {
    return visibleNotices((await api.meNotices()).notices ?? []);
  } catch {
    return [];
  }
}

/** Marks a notice seen: hidden at once in this tab, stored on the server for every device. */
export async function dismissNotice(id: string): Promise<void> {
  dismissed.add(id);
  try {
    await api.noticeSeen(id);
  } catch {
    /* best effort: it stays hidden in this tab; the server shows it once more next time */
  }
}

/** Tests only: forget this tab's dismissals. */
export function resetDismissedForTests(): void {
  dismissed.clear();
}
