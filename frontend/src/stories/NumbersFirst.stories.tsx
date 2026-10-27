import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router";
import { CardV2 } from "../dashboard/CardV2";
import { Details, EmptyState, KeyValueTable, PageFootnote, ProgressStat, Signed } from "../ui/numbersFirst";

/** UX-11 "Numbers first, text on demand": the shared pieces (made-up figures). */
const meta: Meta = { title: "Design system/Numbers first (UX-11)", decorators: [(S) => <MemoryRouter><div className="max-w-md space-y-4"><S /></div></MemoryRouter>] };
export default meta;
type Story = StoryObj;

export const SignedValues: Story = {
  render: () => (
    <div className="flex flex-wrap gap-4 text-t15">
      <Signed value={1500} /> <Signed value={-2540} /> <Signed value={0} /> <Signed value={null} />
      <Signed value={1.25} kind="pct" /> <Signed value={-0.8} kind="pct" /> <Signed value={-125000} kind="count" compact />
    </div>
  ),
};

export const Card: Story = {
  render: () => (
    <CardV2 title="Your figures">
      <KeyValueTable caption="Your figures" rows={[
        { label: "Win rate", value: "54%" }, { label: "Booked P&L", value: <Signed value={12500} /> }, { label: "Worst drawdown", value: <Signed value={-4200} /> },
      ]} />
      <div className="mt-space-3"><ProgressStat label="Closed PAPER trades" done={12} of={20} /></div>
      <Details id="story.figures"><p>Worked out from your closed PAPER trades; charges are included. Figures appear at 20 closed trades.</p></Details>
    </CardV2>
  ),
};

export const Empty: Story = {
  render: () => <EmptyState text="No PAPER trades yet · figures appear at 20 closed trades" action={{ label: "Start a PAPER trade", to: "/signals" }} />,
};

export const Footnote: Story = {
  render: () => <PageFootnote>Signals, not advice. Information only - nothing here places an order. Phase 0 has PAPER only.</PageFootnote>,
};
