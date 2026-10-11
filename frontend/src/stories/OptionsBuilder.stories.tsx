import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { LegTable } from "../optionsBuilder/LegTable";
import { MetricsCard } from "../optionsBuilder/MetricsCard";
import { evaluatedById, type Evaluation, type Leg } from "../optionsBuilder/model";
import { PayoffCanvas } from "../optionsBuilder/PayoffCanvas";
import data from "./optionsBuilderCondor.json";

/** P1-c2 - the Options Builder's parts on an iron condor evaluated by the builder API (synthetic prices). Every story
 * follows the toolbar's theme and colour-blind setting. */
const meta: Meta = { title: "Options Builder/Parts" };
export default meta;
type Story = StoryObj;

const evaluation = data.evaluation as unknown as Evaluation;
const legs = data.legs as unknown as Leg[];

export const Payoff: Story = {
  render: function Payoff() {
    const [list, setList] = useState(legs);
    return <div className="max-w-4xl"><PayoffCanvas evaluation={evaluation} legs={list} spot={22000} step={50}
                                                     onStrikeChange={(id, strike) => setList((all) => all.map((l) => (l.id === id ? { ...l, strike } : l)))} /></div>;
  },
};

export const Legs: Story = {
  render: function Legs() {
    const [list, setList] = useState(legs);
    return <LegTable legs={list} rows={evaluatedById(list, { evaluation, sent: legs })} step={50} onChange={setList} onAdd={() => undefined} />;
  },
};

export const Metrics: Story = { render: () => <div className="max-w-xs"><MetricsCard evaluation={evaluation} legs={legs} /></div> };
