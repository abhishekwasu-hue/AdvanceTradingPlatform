import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import StaticIpCard from "./StaticIpCard";

/** The rendered Broker select of the Static IP (SEBI) card: its option values are what PUT /api/compliance/static-ips
 * stores and what the LIVE gate compares with a deployment's broker, so they must be the registry's names. */
describe("StaticIpCard broker select", () => {
  const html = renderToStaticMarkup(<StaticIpCard />);
  const options = Array.from(html.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)).map((m) => ({ value: m[1], label: m[2] }));
  const brokers = options.filter((o) => o.value !== "PRIMARY" && o.value !== "BACKUP");

  it("offers angel_one (labelled Angel One), never angelone", () => {
    expect(brokers).toContainEqual({ value: "angel_one", label: "Angel One" });
    expect(brokers.map((o) => o.value)).not.toContain("angelone");
  });

  it("uses registry ids as values and display names as labels", () => {
    expect(brokers.length).toBeGreaterThan(0);
    for (const o of brokers) {
      expect(o.value).toMatch(/^[a-z0-9_]+$/);
      expect(o.label).not.toBe(o.value);
    }
  });
});
