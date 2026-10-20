import { Capacitor } from "@capacitor/core";
import NativeFrame from "./NativeFrame";
import { setNativeRuntime } from "./platform";

/**
 * Mobile B1: the Android app's start-up, loaded by main.tsx only in the app's bundle (`__ATP_NATIVE__`), so the web
 * build never contains it or Capacitor. Connects the native runtime (isNativeApp) and returns the frame the app renders
 * in. Nothing here signs in or talks to the server.
 */
export function bootNative(): typeof NativeFrame {
  setNativeRuntime(Capacitor);
  return NativeFrame;
}
