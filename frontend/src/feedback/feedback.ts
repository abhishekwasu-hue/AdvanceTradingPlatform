// PW6: what "Send feedback" attaches - the app version and the screen path, nothing else. The path never carries the
// query string or fragment (reset, invite and verification links put tokens there).

export const FEEDBACK_MAX_CHARS = 2000;
export const SCREEN_MAX_CHARS = 200;

/** The screen a feedback is sent from: the path only, at most 200 characters, always starting with "/". */
export function feedbackScreen(pathname: string): string {
  let path = (pathname || "").split("#", 1)[0].split("?", 1)[0].trim();
  if (!path.startsWith("/")) path = `/${path}`;
  path = path.replace(/[^A-Za-z0-9/_.~%:@!$&'()*+,;=-]/g, "");
  return path.length > SCREEN_MAX_CHARS ? path.slice(0, SCREEN_MAX_CHARS) : path;
}

/** The build id, safe for the server's version check (letters, digits, '.', '_', '+', '-'; at most 40). */
export function feedbackVersion(version: string): string {
  const clean = (version || "").replace(/[^A-Za-z0-9._+-]/g, "").replace(/^[^A-Za-z0-9]+/, "").slice(0, 40);
  return clean || "dev";
}

/** The trimmed message, or null when it cannot be sent (empty or too long). */
export function feedbackMessage(text: string): string | null {
  const trimmed = (text || "").trim();
  return trimmed && trimmed.length <= FEEDBACK_MAX_CHARS ? trimmed : null;
}
