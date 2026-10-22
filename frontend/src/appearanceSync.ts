/**
 * Handover item 1: the Text size follows the user to every device. The server holds it (`/api/me/prefs`, namespace
 * `appearance`, backend app/me/prefs.py AppearancePrefs); this browser's appearance copy (src/theme.ts) only paints the
 * last known size before the server answers.
 *
 * - On sign-in the stored size is read and applied. Never saved on the server yet (version 0) while this browser has a
 *   size other than Normal: that choice is sent up once, so a size picked before this release is kept.
 * - A change is applied at once and sent; a failed send keeps the size on this device and says so.
 */
import { useEffect } from "react";
import { request } from "./api/client";
import { isTextSize, loadAppearance, saveAppearance, type TextSize } from "./theme";

interface PrefsRead { prefs: { appearance?: { text_size?: unknown } }; versions: { appearance?: number } }

/** Reads the signed-in user's text size and applies it. Returns the size in force afterwards. */
export async function pullTextSize(fetcher: typeof request = request): Promise<TextSize> {
  const local = loadAppearance();
  let read: PrefsRead;
  try {
    read = await fetcher<PrefsRead>("/me/prefs");
  } catch {
    return local.textSize;                                   // offline: the device's copy stays
  }
  const stored = read.prefs?.appearance?.text_size;
  if ((read.versions?.appearance ?? 0) === 0) {
    if (local.textSize !== "normal") await pushTextSize(local.textSize, fetcher);
    return local.textSize;
  }
  const size = isTextSize(stored) ? stored : "normal";
  if (size !== local.textSize) saveAppearance({ ...local, textSize: size });
  return size;
}

/** Saves the text size for the signed-in user. False when the server could not be reached. */
export async function pushTextSize(size: TextSize, fetcher: typeof request = request): Promise<boolean> {
  try {
    await fetcher("/me/prefs/appearance", { method: "PUT", body: JSON.stringify({ text_size: size }) });
    return true;
  } catch {
    return false;
  }
}

/** Applies the text size now and saves it for the user (signed out: this device only). */
export async function setTextSize(size: TextSize, signedIn: boolean, fetcher: typeof request = request): Promise<boolean> {
  saveAppearance({ ...loadAppearance(), textSize: size });
  return signedIn ? pushTextSize(size, fetcher) : true;
}

/** Mounted once in the shell: reads the user's text size when someone signs in. */
export function useTextSizeSync(userKey: number | string | null): void {
  useEffect(() => {
    if (userKey != null) void pullTextSize();
  }, [userKey]);
}
