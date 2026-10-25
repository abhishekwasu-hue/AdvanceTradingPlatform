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

interface PrefsRead { prefs: { appearance?: { text_size?: unknown; high_contrast?: unknown } }; versions: { appearance?: number } }

/** What the server stores for the appearance namespace (it is written whole, so both fields go in every save). */
interface StoredAppearance { text_size: TextSize; high_contrast: boolean }

/** Reads the signed-in user's text size and High contrast choice (UI-H-DM DM4) and applies them. Returns the size in
 * force afterwards. */
export async function pullTextSize(fetcher: typeof request = request): Promise<TextSize> {
  const local = loadAppearance();
  let read: PrefsRead;
  try {
    read = await fetcher<PrefsRead>("/me/prefs");
  } catch {
    return local.textSize;                                   // offline: the device's copy stays
  }
  const stored = read.prefs?.appearance;
  if ((read.versions?.appearance ?? 0) === 0) {
    if (local.textSize !== "normal" || local.highContrast) await pushAppearance(toStored(local), fetcher);
    return local.textSize;
  }
  const size = isTextSize(stored?.text_size) ? stored.text_size : "normal";
  const high = stored?.high_contrast === true;
  if (size !== local.textSize || high !== local.highContrast) saveAppearance({ ...local, textSize: size, highContrast: high });
  return size;
}

function toStored(a: { textSize: TextSize; highContrast: boolean }): StoredAppearance {
  return { text_size: a.textSize, high_contrast: a.highContrast };
}

async function pushAppearance(value: StoredAppearance, fetcher: typeof request): Promise<boolean> {
  try {
    await fetcher("/me/prefs/appearance", { method: "PUT", body: JSON.stringify(value) });
    return true;
  } catch {
    return false;
  }
}

/** Saves the text size (with this device's High contrast choice) for the signed-in user. False when the server could
 * not be reached. */
export async function pushTextSize(size: TextSize, fetcher: typeof request = request): Promise<boolean> {
  return pushAppearance({ text_size: size, high_contrast: loadAppearance().highContrast }, fetcher);
}

/** Applies the text size now and saves it for the user (signed out: this device only). */
export async function setTextSize(size: TextSize, signedIn: boolean, fetcher: typeof request = request): Promise<boolean> {
  saveAppearance({ ...loadAppearance(), textSize: size });
  return signedIn ? pushTextSize(size, fetcher) : true;
}

/** UI-H-DM DM4: turns High contrast on or off now and saves it for the user (signed out: this device only). */
export async function setHighContrast(on: boolean, signedIn: boolean, fetcher: typeof request = request): Promise<boolean> {
  const next = { ...loadAppearance(), highContrast: on };
  saveAppearance(next);
  return signedIn ? pushAppearance(toStored(next), fetcher) : true;
}

/** Mounted once in the shell: reads the user's text size when someone signs in. */
export function useTextSizeSync(userKey: number | string | null): void {
  useEffect(() => {
    if (userKey != null) void pullTextSize();
  }, [userKey]);
}
