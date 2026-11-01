import { CopilotBody } from "../../copilot/chat/CopilotPageV2";
import { copilotToolPath } from "../copilotPaths";
import type { PanelProps } from "../registry";
import { RouteGate } from "./RouteGate";

/** NAV-2: a Copilot tool in place - the chat, Read the chart, Coach or one of the older tools - the same body its own
 * address shows, without the tab bar (the panel's title names it). */
export default function CopilotPanel({ params }: PanelProps) {
  return <RouteGate path={copilotToolPath(params.tab)}><CopilotBody tab={params.tab} inPanel /></RouteGate>;
}
