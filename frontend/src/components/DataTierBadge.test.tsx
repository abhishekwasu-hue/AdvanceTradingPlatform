import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { DataTierBadgeInfo } from "../types";
import { DataTierBadge } from "./DataTierBadge";

const live: DataTierBadgeInfo = {
  tier: "A", label: "Live · your Upstox", source: "broker:upstox", broker: "upstox", display_name: "Upstox",
  as_of: "2026-10-12T05:12:05Z", eod_date: null, stale: false, stale_reason: null, alerts_suppressed: false,
};

describe("DataTierBadge (BT1)", () => {
  it("renders nothing without a badge (flag off: the backend sends none)", () => {
    expect(renderToStaticMarkup(<DataTierBadge badge={undefined} />)).toBe("");
    expect(renderToStaticMarkup(<DataTierBadge badge={null} />)).toBe("");
  });

  it("shows the tier and timestamp, toned by tier and staleness", () => {
    const html = renderToStaticMarkup(<DataTierBadge badge={live} />);
    expect(html).toContain("Live · your Upstox · 10:42:05 IST");
    expect(html).toContain('data-tier="A"');
    expect(html).toContain("text-up");
    const eod = renderToStaticMarkup(<DataTierBadge badge={{ ...live, tier: "C", label: "EOD · 2026-10-09", eod_date: "2026-10-09", as_of: null }} />);
    expect(eod).toContain("EOD · 9 Oct 2026");
    expect(eod).toContain('data-tier="C"');
    const stale = renderToStaticMarkup(<DataTierBadge badge={{ ...live, stale: true, stale_reason: "quote stale", alerts_suppressed: true }} />);
    expect(stale).toContain("text-warn");
    expect(stale).toContain("Alerts on this data are held back");
  });
});
