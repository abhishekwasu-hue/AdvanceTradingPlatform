import PaperBotV2 from "../../paperbot/PaperBotV2";
import { RouteGate } from "./RouteGate";

/** NAV-3: the Trading bot page in place (a drawer over the tab) - the same start form and the bots as cards as at
 * /deployments, behind the same broker gate the shell puts in front of that address. */
export default function BotsPanel() {
  return <RouteGate path="/deployments"><PaperBotV2 /></RouteGate>;
}
