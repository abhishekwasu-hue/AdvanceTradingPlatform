import { App } from "@capacitor/app";
import { API_ORIGIN } from "../api/base";
import { appHosts, navigateInApp, pathFromNotification, resolveDeepLink } from "./deepLinks";
import { onNotificationTap } from "./push";

/**
 * B8: the app's link handling (Android only; loaded by src/native/NativeFrame.tsx inside the app's bundle).
 * - an App Link opened while the app runs (`appUrlOpen`) or the link that launched it (`getLaunchUrl`);
 * - a tap on a phone notification (its `data.path`).
 * Each goes through resolveDeepLink (allow-list, own domain only, ids as whole numbers; anything else opens Home) and
 * opens the screen. Nothing is approved, placed or changed by a link.
 */
export async function startNativeLinks(open: (path: string) => void = navigateInApp): Promise<() => void> {
  const hosts = appHosts(API_ORIGIN);
  const stops: Array<() => void> = [];
  try {
    const handle = await App.addListener("appUrlOpen", (event) => open(resolveDeepLink(event.url, hosts)));
    stops.push(() => void handle.remove());
    const launch = await App.getLaunchUrl();
    if (launch?.url) open(resolveDeepLink(launch.url, hosts));
  } catch { /* intentional: no App plug-in: links simply open Home */ }
  const tap = await onNotificationTap((data) => open(pathFromNotification(data, hosts)));
  if (tap) stops.push(() => void tap.remove());
  return () => stops.forEach((stop) => stop());
}
