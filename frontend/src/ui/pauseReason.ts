/**
 * REV-1 M2: a bot's stored pause reason in plain words, or null when it does not read as words.
 * The server stores "Paused by user: <note>" (deployments/routes.py pause) and "Paused on AI proposal #<id> ...";
 * neither the prefix nor the page note nor an id is ever shown.
 */
export function pauseReasonText(raw: string | null | undefined): string | null {
  const reason = (raw ?? "").trim();
  if (!reason) return null;
  if (/^paused by user\b/i.test(reason) || /^paused from\b/i.test(reason)) return "Paused by you";
  if (/^paused on ai proposal\b/i.test(reason)) return "Paused by you (you approved a suggestion to pause it)";
  if (reason.length <= 80 && !/[_{}[\]<>=]|#\d/.test(reason)) {
    return `Paused: ${reason.charAt(0).toLowerCase()}${reason.slice(1).replace(/\.$/, "")}`;
  }
  return null;
}

/** Today's attention row: the reason as one sentence ("You paused it."), or null when it does not read as words. */
export function pauseReasonDetail(raw: string | null | undefined): string | null {
  const reason = (raw ?? "").trim();
  if (!reason) return null;
  if (/^paused by user\b/i.test(reason) || /^paused from\b/i.test(reason)) return "You paused it.";
  if (/^paused on ai proposal\b/i.test(reason)) return "You approved a suggestion to pause it.";
  if (reason.length <= 80 && !/[_{}[\]<>=]|#\d/.test(reason)) return /[.!?]$/.test(reason) ? reason : `${reason}.`;
  return null;
}
