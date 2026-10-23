import { describe, expect, it } from "vitest";
import { PHONE_KINDS, PHONE_STATE_TEXT, phoneState, toggleKind } from "./phonePush";

/** B8: the "Notifications on this phone" card says plainly why it cannot turn on, and keeps the kinds in order. */
describe("phone notifications card (B8)", () => {
  it("says why before asking Android anything", () => {
    expect(phoneState({ configured: false, available: true, permission: "granted" })).toBe("server-off");
    expect(phoneState({ configured: true, available: false, permission: "unavailable" })).toBe("needs-update");
    expect(phoneState({ configured: true, available: true, permission: "denied" })).toBe("denied");
    expect(phoneState({ configured: true, available: true, permission: "prompt" })).toBe("ready");
    expect(phoneState({ configured: true, available: true, permission: "granted" })).toBe("ready");
    for (const text of Object.values(PHONE_STATE_TEXT)) expect(text).not.toMatch(/FCM|VAPID|token|plug-in|error/i);
  });

  it("toggles one kind and keeps the listed order", () => {
    expect(PHONE_KINDS.map((k) => k.id)).toEqual(["signals", "approvals", "exits", "renew"]);
    expect(toggleKind(["renew", "signals"], "exits")).toEqual(["signals", "exits", "renew"]);
    expect(toggleKind(["signals", "exits"], "signals")).toEqual(["exits"]);
  });
});
