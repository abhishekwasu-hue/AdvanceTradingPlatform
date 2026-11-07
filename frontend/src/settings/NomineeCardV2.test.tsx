import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import NomineeCardV2 from "./NomineeCardV2";

// P3-4-B10: the card shows nothing until the server has answered (an older server has no nominee route).
describe("NomineeCardV2", () => {
  it("renders nothing before the server answers", () => {
    expect(renderToStaticMarkup(<NomineeCardV2 />)).toBe("");
  });
});
