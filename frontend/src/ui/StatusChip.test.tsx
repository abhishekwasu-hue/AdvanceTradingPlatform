import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusChip, statusTone, type Status } from "./StatusChip";

/** Chart + colour order Part 3, CC-1 / CC-2 (provisional defaults): states never take the P&L colours. */
describe("StatusChip", () => {
  const html = (status: Status, atStake = false) => renderToStaticMarkup(<StatusChip status={status} atStake={atStake} />);

  it("Connected is neutral with a check - never green", () => {
    const h = html("connected");
    expect(h).toContain("Connected");
    expect(h).toContain("lucide-check");
    expect(h).not.toMatch(/(?<![\w-])(?:text|bg|border)-up\b/);
  });

  it("Needs action is warn; an error is warn unless money or an order is at stake, then down", () => {
    expect(html("needs-action")).toContain("text-warn");
    expect(html("error")).toContain("text-warn");
    expect(html("error")).not.toContain("text-down");
    expect(html("error", true)).toContain("text-down");
    expect(statusTone("error", true)).toBe("down");
    expect(statusTone("needs-action", true)).toBe("warn");          // atStake only changes an error
    expect(statusTone("connected", true)).toBe("neutral");
  });

  it("Info is the info tone with its tint", () => {
    expect(html("info")).toContain("bg-info-tint text-info");
  });

  it("Paper is the neutral outline chip with its word - no orange, no armed (CC-2)", () => {
    const h = html("paper");
    expect(h).toContain(">Paper<");
    expect(h).toContain("border border-border text-fg-muted");
    expect(h).not.toMatch(/armed|warn|down|orange/);
  });

  it("Live is the solid chip; every status carries an icon and plain words", () => {
    expect(html("live")).toContain("bg-fg text-surface-1");
    for (const s of ["connected", "needs-action", "error", "info", "paper", "live"] as Status[]) {
      const h = html(s);
      expect(h, s).toContain(`data-chip="${s}"`);
      expect(h, s).toMatch(/<svg[^>]*lucide/);
      expect(h, s).toMatch(/>[A-Z][a-z]+( [a-z]+)?<\/span>$/);
    }
    expect(renderToStaticMarkup(<StatusChip status="connected">Upstox connected</StatusChip>)).toContain("Upstox connected");
  });
});
