import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import NativeFrame, { NOT_CONNECTED_TEXT, isPlaceholderOrigin } from "./NativeFrame";

/** Mobile B1: the app's frame (never rendered by the web build). A debug build is not connected to any server and
 * says so instead of showing a sign-in that silently fails. */
describe("NativeFrame", () => {
  it("knows the debug build's placeholder origin", () => {
    expect(isPlaceholderOrigin("https://atp-not-configured.invalid")).toBe(true);
    expect(isPlaceholderOrigin("")).toBe(true);
    expect(isPlaceholderOrigin("https://atp.example.com")).toBe(false);
  });

  it("shows the app with the not-connected notice above it", () => {
    const html = renderToStaticMarkup(<NativeFrame><p>the app</p></NativeFrame>);
    expect(html).toContain("<p>the app</p>");
    expect(html).toContain(NOT_CONNECTED_TEXT);
    expect(html).toContain(`data-native-notice="not-connected"`);
    expect(html).toContain(`aria-label="Close"`);
  });
});
