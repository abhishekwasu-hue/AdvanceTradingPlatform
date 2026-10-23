import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusChip, statusTone, type Status } from "./StatusChip";
import { ColourV2Provider } from "./colourV2";

/** Chart + colour order Part 3, CC-1 / CC-2 (provisional defaults): states never take the P&L colours. */
describe("StatusChip", () => {
  const html = (status: Status, atStake = false) => renderToStaticMarkup(<StatusChip status={status} atStake={atStake} />);

  it("Connected is neutral with a check - never green - while colour v2 is off (no visual change)", () => {
    const h = html("connected");
    expect(h).toContain("Connected");
    expect(h).toContain("lucide-check");
    expect(h).not.toMatch(/(?<![\w-])(?:text|bg|border)-up\b/);
  });

  it("Connected is a neutral chip with a green check (colour v2): only the icon takes the up colour", () => {
    const h = renderToStaticMarkup(<ColourV2Provider on><StatusChip status="connected" /></ColourV2Provider>);
    expect(h).toContain("Connected");
    expect(h).toContain("lucide-check");
    expect(h).toContain("bg-surface-2 text-fg-muted");
    expect(h).not.toMatch(/(?<![\w-])(?:bg|border)-up\b/);
    // The one up class sits on the icon's wrapper, not on the words.
    expect(h.match(/text-up\b/g)).toHaveLength(1);
    expect(h).toMatch(/<span aria-hidden="true" class="inline-flex shrink-0 text-up"><svg[^>]*lucide-check/);
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
