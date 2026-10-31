import ScreenerPage from "../pages/ScreenerPage";
import ScannerPage from "../pages/ScannerPage";
import type { PageProps } from "../routes";
import { SkeletonRows } from "../components/primitives";
import { useFeatures } from "../ui/useFeatures";

/**
 * M49 U6 (owner-approved mockup docs/ui/U6_SCREENER.html): the old "Custom scan" page is folded into Find setups as its
 * "Conditions" block. `/screener/custom` keeps its address (and `/scanner` still forwards here) and opens Find setups
 * with Conditions open; the custom scanner itself stays inside that block (CLAUDE.md §8: nothing is deleted).
 * While the screener is off for the account (`screener_v2`), Find setups only says "Coming soon" - the address then
 * keeps showing the custom scanner on its own, exactly as before.
 */
export default function CustomScanV2(props: PageProps) {
  const features = useFeatures();
  if (!features.loaded) return <SkeletonRows rows={3} label="Loading" />;
  return features.on("screener_v2") ? <ScreenerPage {...props} conditionsOpen /> : <ScannerPage {...props} />;
}
