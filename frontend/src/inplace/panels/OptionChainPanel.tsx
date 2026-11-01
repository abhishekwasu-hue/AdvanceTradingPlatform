import OptionChainPageV2 from "../../optionChain/OptionChainPageV2";
import { optionChainPanelPath } from "../fnoPaths";
import type { PanelProps } from "../registry";
import { RouteGate } from "./RouteGate";

/** NAV-4: the Option chain in place (a wide side panel over the tab; a bottom sheet on a phone) - the same page as
 * /option-chain, on its own address inside the panel: the symbol, expiry, a signal's strikes, the basket opened by the
 * futures chain's badge and "Trade this setup" read from it exactly as on the full route. */
export default function OptionChainPanel({ params }: PanelProps) {
  return <RouteGate path={optionChainPanelPath(params)}><OptionChainPageV2 /></RouteGate>;
}
