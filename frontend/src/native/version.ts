import { appVersion } from "../pwa/events";
import { isNativeApp } from "./platform";

/**
 * Mobile B1 (plan §3.2 "About", §9): the versions to show. The web build id (`__APP_VERSION__`: package version plus
 * short commit) everywhere; inside the app also the native version name and build number (versionCode) from
 * `App.getInfo()`. On the web this resolves at once with `native: null` and loads nothing.
 */
export interface AppVersions {
  web: string;
  native: { version: string; build: string } | null;
}

export async function appVersions(): Promise<AppVersions> {
  const web = appVersion();
  if (!__ATP_NATIVE__ || !isNativeApp()) return { web, native: null };
  try {
    const { App } = await import("@capacitor/app");
    const info = await App.getInfo();
    return { web, native: { version: info.version, build: info.build } };
  } catch {
    return { web, native: null };
  }
}

/** One line for an About row or a bug report: "0.1.0+abc1234" or "0.1.0+abc1234 (app 0.1.0, build 12)". */
export function versionLine(v: AppVersions): string {
  return v.native ? `${v.web} (app ${v.native.version}, build ${v.native.build})` : v.web;
}
