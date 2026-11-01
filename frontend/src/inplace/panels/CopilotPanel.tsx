import { askContextFrom } from "../../copilot/askContext";
import { CopilotBody } from "../../copilot/chat/CopilotPageV2";
import { copilotToolPath } from "../copilotPaths";
import type { PanelProps } from "../registry";
import { RouteGate } from "./RouteGate";

/** NAV-2: a Copilot tool in place - the chat, Read the chart, Coach or one of the older tools - the same body its own
 * address shows, without the tab bar (the panel's title names it). */
export default function CopilotPanel({ params }: PanelProps) {
  // M72 PR-3: the chat opens with the context the "Ask AI" action handed it (symbol, timeframe, one object)
  const context = params.tab ? null : askContextFrom((k) => params[k]);
  return <RouteGate path={copilotToolPath(params.tab)}><CopilotBody tab={params.tab} inPanel context={context} /></RouteGate>;
}
