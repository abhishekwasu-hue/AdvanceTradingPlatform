/**
 * M59 CR-3: what ATP says when the chart analysis engine is not installed on this server - the user's plain line (no
 * internal names) and the admin's headline (the server sends the reason and the owner's steps to SUPER_ADMIN only).
 * Shared by the Analyze a chart page and the Copilot's chart attach (CR-2), so both say the same words.
 */
export const ENGINE_USER_TITLE = "Chart upload analysis is being switched on";
export const ENGINE_USER_LINE = "Your saved analyses are kept. Please check again later.";
export const ENGINE_ADMIN_TITLE = "Chart analysis engine not installed — owner action";

/** The admin's view: the server sent the reason or the owner's steps (it sends them to SUPER_ADMIN only). */
export function engineAudience(engine: { reason?: string; owner_steps?: string }): "admin" | "user" {
  return engine.reason || engine.owner_steps ? "admin" : "user";
}
