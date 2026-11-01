import SignalsV2 from "../../signals/SignalsV2";
import { signalsPanelPath } from "../copilotPaths";
import type { PanelProps } from "../registry";
import { RouteGate } from "./RouteGate";

/** NAV-2: the Signals list in place; a Watchtower proposal handed over opens on it (its own `?proposal=`). */
export default function SignalsPanel({ params }: PanelProps) {
  return <RouteGate path={signalsPanelPath(params.proposal)}><SignalsV2 /></RouteGate>;
}
