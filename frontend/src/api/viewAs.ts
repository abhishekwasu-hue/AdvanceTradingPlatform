import { request, setToken } from "./client";
import { currentView, setView, type ViewInfo } from "./viewState";

/**
 * MO3-A1: start and end the admin's read-only "View as user" (the server: app/admin/view_as_routes.py). Starting swaps
 * the access token in memory for the view's token; ending tells the server, then drops the view - the banner's host
 * (src/admin/ViewAsBanner.tsx) gets the admin's own sign-in back from the refresh cookie.
 */

export interface ServerView {
  id: number; user_id: number; tenant_id: number; name: string | null; reason: string; started_at: string | null;
  expires_at: string | null; ended_at: string | null; end_reason: string | null; active: boolean; seconds_left: number;
  requests: number; refused: number; last_seen_at: string | null;
}

/** One entry of the person's own record: when ATP support viewed the account. */
export interface SupportAccess {
  id: number; by: string; reason: string; started_at: string | null; ended_at: string | null; active: boolean; read_only: boolean; requests: number;
}

export async function startView(userId: number, reason: string): Promise<ViewInfo> {
  const started = await request<{ token: string; view: ServerView }>("/admin/view-sessions", {
    method: "POST", body: JSON.stringify({ user_id: userId, reason }),
  });
  const info: ViewInfo = {
    id: started.view.id, userId: started.view.user_id, name: started.view.name ?? "this person",
    expiresAt: started.view.expires_at ?? new Date(Date.now() + started.view.seconds_left * 1000).toISOString(),
  };
  setToken(started.token);
  setView(info);
  return info;
}

/** Ends the open view (if any). The server call is best effort: an expired view has nothing left to end. */
export async function endView(): Promise<void> {
  if (!currentView()) return;
  try {
    await request<{ ended: boolean }>("/view-session/end", { method: "POST" });
  } catch {
    // intentional: already ended on the server
  }
  setView(null);
}

export const supportAccess = () => request<{ views: SupportAccess[] }>("/me/support-access");
