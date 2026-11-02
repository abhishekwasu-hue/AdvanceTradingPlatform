import OptionsBuilderPage from "../../pages/OptionsBuilderPage";

/** NAV-4: "Plan an options trade" (the Options Builder, /option-chain/plan) beside the basket that opened it: the basket
 * hands its legs over once (fno/basketModel.ts writeHandoff / takeHandoff), the basket stays as it was underneath. */
export default function OptionsPlanPanel() {
  return <OptionsBuilderPage />;
}
