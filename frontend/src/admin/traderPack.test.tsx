import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { orderRows, summary, TraderPackView, type TraderPackStatus } from "./TraderPackCard";

const STATUS: TraderPackStatus = {
  flags: [
    { name: "ui_v2", plain: "The redesigned app (navigation and pages)", on: true, state: "on", check: "On, nothing to check" },
    { name: "watchlists", plain: "Your own symbol lists", on: false, state: "off", check: "Off" },
    { name: "screener_v2", plain: "The screener: saved screens and runs on broker data", on: true, state: "working", check: "2 organisations with a broker connection for screen data" },
    { name: "ai_copilot", plain: "The AI Copilot page and AI strategy drafts", on: true, state: "needs_setup", check: "No AI key yet: answers come from the built-in rules until a key is connected" },
  ],
  all_on: false, on_count: 3, total: 4, can_undo: false, enabled_at: null,
};

describe("Trader features card", () => {
  it("puts what needs setup first, then what is off, then the rest in pack order", () => {
    expect(orderRows(STATUS.flags).map((f) => f.name)).toEqual(["ai_copilot", "watchlists", "screener_v2", "ui_v2"]);
  });

  it("sums up in plain words", () => {
    expect(summary(STATUS)).toBe("3 of 4 trader features are on; 1 needs setup.");
    expect(summary({ ...STATUS, all_on: true, flags: STATUS.flags.map((f) => ({ ...f, on: true, state: "on" })) })).toBe("All trader features are on.");
  });

  it("shows the check text, the two buttons, and Undo only once there is something to undo", () => {
    const html = renderToStaticMarkup(<TraderPackView status={STATUS} />);
    expect(html).toContain("Enable trader features");
    expect(html).toContain("No AI key yet");
    expect(html).toContain("Needs setup");
    expect(html).not.toContain("On, nothing to check");      // a plain "On" row carries no extra line
    const button = (markup: string, label: string) => markup.split("</button>").find((b) => b.includes(label)) ?? "";
    expect(button(html, "</svg> Undo")).toContain('disabled=""');
    expect(button(html, "Enable trader features")).not.toContain('disabled=""');
    const after = renderToStaticMarkup(<TraderPackView status={{ ...STATUS, can_undo: true, all_on: true }} />);
    expect(button(after, "</svg> Undo")).not.toContain('disabled=""');
    expect(button(after, "Enable trader features")).toContain('disabled=""');
  });
});
