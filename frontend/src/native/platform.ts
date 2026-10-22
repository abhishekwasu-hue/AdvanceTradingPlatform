/**
 * Mobile B1 (docs/design/MOBILE_APP_PLAN.md §3.2): "is this the Android app?" for code that runs on both.
 *
 * Safe to import anywhere. In the web build `__ATP_NATIVE__` is the constant false, so `isNativeApp()` is false and the
 * bundler drops everything behind it; nothing from Capacitor is ever loaded on the web. In the app's bundle the native
 * runtime (Capacitor) is connected by src/native/boot.ts before the first render.
 */

/** The part of Capacitor's runtime this module reads (`Capacitor` from @capacitor/core). */
export interface NativeRuntime {
  isNativePlatform(): boolean;
  getPlatform(): string;
}

export type AppPlatform = "web" | "android" | "ios";

let runtime: NativeRuntime | null = null;

/** Called once by src/native/boot.ts (the app's bundle only). */
export function setNativeRuntime(next: NativeRuntime | null): void {
  runtime = next;
}

/** Pure: native only in a native build whose runtime says so; any failure reads as web. */
export function detectNative(nativeBuild: boolean, rt: NativeRuntime | null): boolean {
  if (!nativeBuild || !rt) return false;
  try {
    return rt.isNativePlatform() === true;
  } catch {
    return false;
  }
}

/** True inside the Android (later iOS) app; always false on the web. Wraps `Capacitor.isNativePlatform()`. */
export function isNativeApp(): boolean {
  return __ATP_NATIVE__ && detectNative(true, runtime);
}

/** "android" / "ios" inside the app, "web" everywhere else. */
export function appPlatform(): AppPlatform {
  if (!isNativeApp() || !runtime) return "web";
  const p = runtime.getPlatform();
  return p === "android" || p === "ios" ? p : "web";
}
