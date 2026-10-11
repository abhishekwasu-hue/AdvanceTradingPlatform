import { useEffect, useState } from "react";
import { api } from "../api/client";
import { OiBannerView } from "./OiBanner";
import { Card } from "./ui";
import type { OiBannerResponse } from "../oi/types";

/** OI Banner (O3): the dashboard's banners - one per underlying the organisation follows (Option Chain page). Hidden
 *  when none is followed. Refreshed every minute. */
export default function OiBannersCard() {
  const [banners, setBanners] = useState<OiBannerResponse[] | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.oiBanners().then((d) => { if (alive) setBanners(d.banners); }).catch(() => { if (alive) setBanners([]); });
    load();
    const id = window.setInterval(load, 60_000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  if (!banners || banners.length === 0) return null;
  return (
    <Card title="Option-chain OI">
      <div className="grid gap-2 md:grid-cols-2">
        {banners.map((b) => <OiBannerView key={b.underlying} data={b} compact />)}
      </div>
    </Card>
  );
}
