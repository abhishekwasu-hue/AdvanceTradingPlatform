import { describe, expect, it } from "vitest";
import { API_ORIGIN, API_V1, apiUrl } from "./base";
import { STATUS_PATH } from "../status/publicStatus";

/** Mobile B1: the web build keeps its relative API URLs (the Android build's origin is checked by
 * scripts/mobile-rules.mjs, see test/mobile-rules.test.ts). */
describe("api base", () => {
  it("is this page's own origin on the web", () => {
    expect(API_ORIGIN).toBe("");
    expect(API_V1).toBe("/api/v1");
    expect(apiUrl("/status")).toBe("/api/v1/status");
    expect(apiUrl("/auth/refresh")).toBe("/api/v1/auth/refresh");
    expect(STATUS_PATH).toBe("/api/v1/status");
  });
});
