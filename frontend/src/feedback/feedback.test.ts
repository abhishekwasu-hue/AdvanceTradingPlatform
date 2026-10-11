import { describe, expect, it } from "vitest";
import { FEEDBACK_MAX_CHARS, feedbackMessage, feedbackScreen, feedbackVersion } from "./feedback";

describe("feedback attachments", () => {
  it("sends the screen as a path only - no query string or fragment", () => {
    expect(feedbackScreen("/option-chain")).toBe("/option-chain");
    expect(feedbackScreen("/account?reset=secret-token#top")).toBe("/account");
    expect(feedbackScreen("/copilot/study#x")).toBe("/copilot/study");
    expect(feedbackScreen("")).toBe("/");
    expect(feedbackScreen("positions")).toBe("/positions");
  });

  it("keeps the path within 200 path characters", () => {
    expect(feedbackScreen("/" + "a".repeat(400))).toHaveLength(200);
    expect(feedbackScreen("/a b<script>")).toBe("/abscript");
  });

  it("keeps the version a short label the server accepts", () => {
    expect(feedbackVersion("0.1.0+abc1234")).toBe("0.1.0+abc1234");
    expect(feedbackVersion("")).toBe("dev");
    expect(feedbackVersion("v1 <b>")).toBe("v1b");
    expect(feedbackVersion("x".repeat(60))).toHaveLength(40);
  });

  it("refuses an empty or over-long message", () => {
    expect(feedbackMessage("   ")).toBeNull();
    expect(feedbackMessage("  slow chain  ")).toBe("slow chain");
    expect(feedbackMessage("x".repeat(FEEDBACK_MAX_CHARS))).toHaveLength(FEEDBACK_MAX_CHARS);
    expect(feedbackMessage("x".repeat(FEEDBACK_MAX_CHARS + 1))).toBeNull();
  });
});
