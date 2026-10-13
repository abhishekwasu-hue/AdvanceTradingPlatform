import type { Meta, StoryObj } from "@storybook/react";
import LadderEvidence, { DisciplineWeeks } from "../progression/LadderEvidence";
import { RuinResultView } from "../progression/DecisionToolsCard";
import { ExperiencePanel } from "../progression/ExperienceCard";
import { LOSING, OK, TOO_FEW } from "../progression/samples";
import { BEGINNER_STATUS } from "../progression/samples.experience";

/** N2-1 (flag `ladder_evidence`): the ladder card's "Your figures" block and the discipline week strip. Use the toolbar
 * for dark / light. Payloads as the server builds them (progression/samples.ts). */
const meta: Meta = { title: "Progression/Ladder evidence" };
export default meta;
type Story = StoryObj;

const frame = (child: JSX.Element) => <div className="max-w-xl rounded-lg border border-border bg-surface-1 p-3">{child}</div>;

export const Figures: Story = { render: () => frame(<LadderEvidence evidence={OK} />) };
export const LosingRecord: Story = { render: () => frame(<LadderEvidence evidence={LOSING} />) };
export const TooFewTrades: Story = { render: () => frame(<LadderEvidence evidence={TOO_FEW} />) };
export const DisciplineWeekStrip: Story = {
  render: () => frame(
    <DisciplineWeeks criterion={{ key: "discipline_score", label: "Discipline score (mean of recent weekly scores)", value: 72.5, target: 70,
                                  comparison: "at_least", met: false, weeks_needed: 4, status: "too_few_weeks",
                                  weeks: [{ week: "2026-W39", score: 64 }, { week: "2026-W40", score: 76 }, { week: "2026-W41", score: 78 }] }} />,
  ),
};

/** N2-2 (flag `experience_levels`): the experience level card - a Beginner with futures opted out for today. */
export const ExperienceLevel: Story = {
  render: () => <ExperiencePanel status={BEGINNER_STATUS} canTrade onOptOut={async () => true} />,
};

/** N2-3 (flag `decision_tools`): the calculator's answer for 60 % wins at 1:1, 2 % risk, ruin at 20 %. */
export const RuinCalculator: Story = {
  render: () => frame(
    <RuinResultView calc={{
      inputs: { win_rate: 0.6, payoff_ratio: 1, risk_pct: 2, ruin_pct: 20 }, expectancy_r: 0.2,
      kelly: { fraction: 0.2, status: "ok", formula: "f = (p·b − q) / (b·(p + q))" },
      rows: [
        { risk_pct: 0.5, ruin_probability: 8.97e-8, ruin_status: "ok", losses_to_ruin: 40, growth_per_trade_pct: 0.0975, chosen: false },
        { risk_pct: 2, ruin_probability: 0.01734, ruin_status: "ok", losses_to_ruin: 10, growth_per_trade_pct: 0.3801, chosen: true },
        { risk_pct: 50, ruin_probability: 0.6, ruin_status: "ok", losses_to_ruin: 1, growth_per_trade_pct: -3.42, chosen: false },
      ],
      method: "lundberg_upper_bound", note: "A model of these inputs, not a forecast; nothing here sizes or places a trade",
    }} />,
  ),
};
