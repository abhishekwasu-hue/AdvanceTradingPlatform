import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RangeBar, position } from "./RangeBar";

/** Chart + colour order Part 3, CC-4: a neutral track, a range marker, the low / high as text. */
describe("RangeBar", () => {
  it("places the marker between the low and the high, clamped", () => {
    expect(position(100, 200, 150)).toBe(50);
    expect(position(100, 200, 100)).toBe(0);
    expect(position(100, 200, 200)).toBe(100);
    expect(position(100, 200, 90)).toBe(0);
    expect(position(100, 200, 260)).toBe(100);
    expect(position(100, 100, 100)).toBe(50);                       // a flat range: the middle
  });

  it("an unknown or broken range has no marker", () => {
    expect(position(null, 200, 150)).toBeNull();
    expect(position(100, undefined, 150)).toBeNull();
    expect(position(100, 200, null)).toBeNull();
    expect(position(200, 100, 150)).toBeNull();                     // high below low
    expect(position(100, Number.NaN, 150)).toBeNull();
  });

  it("draws a neutral track with a range marker, never the warning colour, and says the numbers in text", () => {
    const html = renderToStaticMarkup(<RangeBar label="52-week range" low={100} high={200} value={175} format={(v) => v.toFixed(0)} />);
    expect(html).toContain("bg-range-track");
    expect(html).toContain("bg-range ");
    expect(html).toContain("left:75%");
    expect(html).not.toMatch(/warn/);
    expect(html).toContain('role="meter"');
    expect(html).toContain('aria-valuetext="52-week range: 175, between 100 and 200"');
    expect(html).toContain('aria-valuemin="100"');
    expect(html).toContain('aria-valuemax="200"');
    expect(html).toContain('aria-valuenow="175"');
    expect(html).toMatch(/<span>100<\/span><span>200<\/span>/);
  });

  it("without a value it shows the range only; without a range it says so in plain words", () => {
    const rangeOnly = renderToStaticMarkup(<RangeBar label="Today's range" low={10} high={20} format={(v) => String(v)} />);
    expect(rangeOnly).not.toContain("data-range-marker");
    expect(rangeOnly).toContain("Today&#x27;s range: 10 to 20");
    const none = renderToStaticMarkup(<RangeBar label="IV range" low={null} high={null} value={3} />);
    expect(none).toContain("Not available yet");
    expect(none).not.toContain("aria-valuemin");
    expect(none).not.toContain("data-range-marker");
  });
});
