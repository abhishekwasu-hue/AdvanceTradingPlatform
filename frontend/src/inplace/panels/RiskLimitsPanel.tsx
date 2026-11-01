import RiskLimitsV2 from "../../settings/RiskLimitsV2";

/** NAV-4: the trader's own Risk limits (Settings › Risk limits) in a side panel - the ticket's / basket's "Change limit"
 * opens it over the order he is filling in; saving here and closing returns to the same, still-filled order. */
export default function RiskLimitsPanel() {
  return <RiskLimitsV2 />;
}
