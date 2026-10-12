import type { Meta, StoryObj } from "@storybook/react";
import LadderEvidence, { DisciplineWeeks } from "../progression/LadderEvidence";
import { LOSING, OK, TOO_FEW } from "../progression/samples";

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
